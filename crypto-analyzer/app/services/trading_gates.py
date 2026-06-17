"""
实盘开仓闸门 & 黑名单等级 — 统一读取 system_settings，避免各模块硬编码。

按 source 控制实盘（其余策略仅模拟）:
  - gemini_explore, gemini_predict, deepseek_explore, deepseek_predict → 可开实盘（L0 白名单等 symbol 闸门）
  - 总开关: live_trading_enabled（开仓）, live_close_enabled（平仓）
"""
from __future__ import annotations

from typing import Optional, Set, Tuple

import pymysql
from loguru import logger

from app.utils.config_loader import get_db_config
from app.utils.futures_symbol import (
    futures_symbol_clean,
    sql_rating_l3_clean_subquery,
    sql_rating_symbol_clean,
)

# 仅以下策略同步 Binance 实盘；其它 source 只走模拟仓
LIVE_SYNC_SOURCES: frozenset[str] = frozenset({
    "gemini_explore",
    "gemini_predict",
    "deepseek_explore",
    "deepseek_predict",
})

SYMBOL_STOP_LOSS_COOLDOWN_HOURS = 4
SYMBOL_LOSS_COOLDOWN_HOURS = 12
SYMBOL_LOSS_COOLDOWN_LIMIT_USD = 100.0
SYMBOL_LOSS_ROLLING_HOURS = 24
SYMBOL_LOSS_TRADE_LIMIT = 3
SYMBOL_LOSS_NET_PNL_LIMIT = -120.0
SOURCE_SYMBOL_LOSS_TRADE_LIMIT = 2
SOURCE_SYMBOL_LOSS_NET_PNL_LIMIT = -100.0

def _coerce_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _bool_setting(key: str, default: bool = True, cursor=None) -> bool:
    if cursor is not None:
        try:
            cursor.execute(
                "SELECT setting_value FROM system_settings WHERE setting_key=%s LIMIT 1",
                (key,),
            )
            row = cursor.fetchone()
            if row:
                val = row.get('setting_value') if isinstance(row, dict) else row[0]
                return _coerce_bool(val, default)
            return default
        except Exception as e:
            logger.warning(f"[trading_gates] direct setting read failed {key}: {e}")

    try:
        from app.services.system_settings_loader import get_setting
        val = get_setting(key)
    except Exception:
        val = None
    return _coerce_bool(val, default)


def is_blacklist_level3_enforced() -> bool:
    """L3 禁止开仓 — 固定开启，不再提供设置开关."""
    return True


def is_live_top50_required() -> bool:
    """已废弃：实盘开仓不再使用 TOP50 条件，保留设置项仅为兼容旧配置."""
    return False


def is_live_whitelist_enabled(cursor=None) -> bool:
    """实盘同步是否允许白名单 (rating_level=0) 开实仓 (默认开启)."""
    return _bool_setting('live_whitelist_enabled', True, cursor)


def _normalize_source(source: str) -> str:
    return (source or "").strip().lower()


def is_live_trading_enabled() -> bool:
    """system_settings.live_trading_enabled 总开关."""
    return _bool_setting("live_trading_enabled", False)


def is_live_close_enabled() -> bool:
    """system_settings.live_close_enabled — 模拟平仓时是否同步平交易所仓位."""
    return _bool_setting("live_close_enabled", False)


def get_beijing_open_window_status(now_utc=None) -> Tuple[bool, str]:
    """实盘开仓时段闸门 — 已解除，全天允许。"""
    return True, ""


def should_sync_live_for_source(source: str) -> bool:
    """该订单 source 是否允许参与实盘开仓同步."""
    return _normalize_source(source) in LIVE_SYNC_SOURCES


def check_live_open_allowed(
    symbol: str, source: str, cursor=None,
) -> Tuple[bool, str]:
    """
    开仓同步实盘前的完整检查: 总开关 + source 白名单 + L0 白名单评级.

    Returns: (allowed, reject_reason)
    """
    time_allowed, time_reason = get_beijing_open_window_status()
    if not time_allowed:
        return False, time_reason
    if not _bool_setting("live_trading_enabled", False, cursor):
        return False, "live_trading_enabled=0"
    if not should_sync_live_for_source(source):
        return False, f"策略 {source} 仅模拟盘"
    symbol_allowed, symbol_reason = check_symbol_loss_cooldown(symbol, cursor, source=source)
    if not symbol_allowed:
        return False, symbol_reason
    return check_live_symbol_allowed(symbol, cursor)


def is_symbol_blocked_level3(symbol: str, rating_level: Optional[int] = None) -> bool:
    """True = 应拒绝开仓 (L3 且开关开启)."""
    if not is_blacklist_level3_enforced():
        return False
    if rating_level is not None:
        return int(rating_level) >= 3
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
        cur = conn.cursor()
        clean = futures_symbol_clean(symbol)
        cur.execute(
            f"SELECT rating_level FROM trading_symbol_rating "
            f"WHERE {sql_rating_symbol_clean('symbol')} = %s "
            f"ORDER BY rating_level DESC LIMIT 1",
            (clean,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row and row.get('rating_level') is not None:
            return int(row['rating_level']) >= 3
    except Exception as e:
        logger.warning(f"[trading_gates] L3 检查失败 {symbol}: {e}")
    return False


def _as_cursor(conn_or_cursor):
    """paper_open_gate 等传入 pymysql Connection；部分调用方传入 Cursor."""
    if conn_or_cursor is None:
        return None
    if hasattr(conn_or_cursor, "execute"):
        return conn_or_cursor
    if hasattr(conn_or_cursor, "cursor"):
        return conn_or_cursor.cursor()
    return conn_or_cursor


def _row_get(row, key: str, index: int, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except Exception:
        return default


def _symbol_loss_cooldown_label(symbol: str, clean: str) -> str:
    return symbol if "/" in (symbol or "") else f"{clean}/USDT"


def _evaluate_symbol_loss_cooldowns(
    row: dict,
    symbol: str,
    clean: str,
    source: str = "",
) -> Tuple[bool, str]:
    """按优先级评估单币亏损冷却规则（纯逻辑，无 DB）。"""
    stop_n_4h = int(_row_get(row, "stop_n_4h", 0, 0) or 0)
    if stop_n_4h > 0:
        return (
            False,
            f"近{SYMBOL_STOP_LOSS_COOLDOWN_HOURS}小时同币已止损{stop_n_4h}笔，冷却禁止开仓",
        )

    loss_abs_12h = float(_row_get(row, "loss_abs_12h", 1, 0) or 0)
    loss_n_12h = int(_row_get(row, "loss_n_12h", 2, 0) or 0)
    if loss_abs_12h >= SYMBOL_LOSS_COOLDOWN_LIMIT_USD:
        label = _symbol_loss_cooldown_label(symbol, clean)
        return (
            False,
            f"{label} 近{SYMBOL_LOSS_COOLDOWN_HOURS}小时已实现亏损{loss_abs_12h:.2f}U"
            f"({loss_n_12h}笔)，达到{SYMBOL_LOSS_COOLDOWN_LIMIT_USD:.0f}U风控阈值，冷却禁止开仓",
        )

    loss_n_24h = int(_row_get(row, "loss_n_24h", 3, 0) or 0)
    net_pnl_24h = float(_row_get(row, "net_pnl_24h", 4, 0) or 0)
    if loss_n_24h >= SYMBOL_LOSS_TRADE_LIMIT:
        return (
            False,
            f"近{SYMBOL_LOSS_ROLLING_HOURS}小时同币亏损{loss_n_24h}笔，冷却禁止开仓",
        )
    if net_pnl_24h <= SYMBOL_LOSS_NET_PNL_LIMIT:
        return (
            False,
            f"近{SYMBOL_LOSS_ROLLING_HOURS}小时同币净亏{net_pnl_24h:.2f}U，冷却禁止开仓",
        )

    src = (source or "").strip()
    if src:
        src_loss_n = int(_row_get(row, "src_loss_n_24h", 6, 0) or 0)
        src_net_pnl = float(_row_get(row, "src_net_pnl_24h", 7, 0) or 0)
        if src_loss_n >= SOURCE_SYMBOL_LOSS_TRADE_LIMIT:
            return (
                False,
                f"近{SYMBOL_LOSS_ROLLING_HOURS}小时{src}同币亏损{src_loss_n}笔，"
                f"策略连续亏损冷静禁止开仓",
            )
        if src_net_pnl <= SOURCE_SYMBOL_LOSS_NET_PNL_LIMIT:
            return (
                False,
                f"近{SYMBOL_LOSS_ROLLING_HOURS}小时{src}同币净亏{src_net_pnl:.2f}U，"
                f"策略连续亏损冷静禁止开仓",
            )
    return True, ""


def check_symbol_loss_cooldown(
    symbol: str,
    conn_or_cursor=None,
    account_id: int = 2,
    source: str = "",
) -> Tuple[bool, str]:
    """
    单币开仓亏损冷却（一次 SQL 聚合）:
    - 近 4h 同币止损 → 禁开；
    - 近 12h 同币已实现亏损累计 ≥ 100U → 禁开（滚动窗口）；
    - 近 24h 同币亏损 ≥ 3 笔或净亏 ≤ -120U → 禁开；
    - 近 24h 同策略同币亏损 ≥ 2 笔或净亏 ≤ -100U → 禁开。
    """
    clean = futures_symbol_clean(symbol)
    if not clean:
        return True, ""

    own_conn = conn_or_cursor is None
    conn = None
    cur = None
    close_cursor = False
    src = (source or "").strip()
    try:
        if own_conn:
            conn = pymysql.connect(
                **get_db_config(),
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True,
            )
            cur = conn.cursor()
            close_cursor = True
        else:
            cur = _as_cursor(conn_or_cursor)
            close_cursor = hasattr(conn_or_cursor, "cursor") and cur is not conn_or_cursor

        clean_expr = sql_rating_symbol_clean("symbol")
        cur.execute(
            f"""
            SELECT
              SUM(
                CASE WHEN close_time >= DATE_SUB(NOW(), INTERVAL %s HOUR)
                  AND (
                    notes = '止损'
                    OR notes LIKE '%%止损%%'
                    OR notes LIKE '%%code:SL%%'
                  )
                THEN 1 ELSE 0 END
              ) AS stop_n_4h,
              SUM(
                CASE WHEN realized_pnl IS NOT NULL AND realized_pnl < 0 THEN 1 ELSE 0 END
              ) AS loss_n_24h,
              COALESCE(SUM(
                CASE WHEN realized_pnl IS NOT NULL THEN realized_pnl ELSE 0 END
              ), 0) AS net_pnl_24h,
              COALESCE(SUM(
                CASE WHEN close_time >= DATE_SUB(NOW(), INTERVAL %s HOUR)
                  AND realized_pnl IS NOT NULL AND realized_pnl < 0
                THEN -realized_pnl ELSE 0 END
              ), 0) AS loss_abs_12h,
              SUM(
                CASE WHEN close_time >= DATE_SUB(NOW(), INTERVAL %s HOUR)
                  AND realized_pnl IS NOT NULL AND realized_pnl < 0
                THEN 1 ELSE 0 END
              ) AS loss_n_12h,
              SUM(
                CASE WHEN realized_pnl IS NOT NULL AND realized_pnl < 0
                  AND source = %s
                THEN 1 ELSE 0 END
              ) AS src_loss_n_24h,
              COALESCE(SUM(
                CASE WHEN realized_pnl IS NOT NULL AND source = %s
                THEN realized_pnl ELSE 0 END
              ), 0) AS src_net_pnl_24h
            FROM futures_positions
            WHERE account_id=%s
              AND status='closed'
              AND close_time >= DATE_SUB(NOW(), INTERVAL %s HOUR)
              AND {clean_expr} = %s
            """,
            (
                SYMBOL_STOP_LOSS_COOLDOWN_HOURS,
                SYMBOL_LOSS_COOLDOWN_HOURS,
                SYMBOL_LOSS_COOLDOWN_HOURS,
                src,
                src,
                account_id,
                SYMBOL_LOSS_ROLLING_HOURS,
                clean,
            ),
        )
        return _evaluate_symbol_loss_cooldowns(cur.fetchone() or {}, symbol, clean, src)
    finally:
        if close_cursor and cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if own_conn and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _symbol_in_candidate_pool(symbol: str) -> bool:
    """AI 探索/预测候选池内的币种允许模拟开仓."""
    clean = futures_symbol_clean(symbol)
    try:
        from app.services.data_cache_service import DATA_CACHE_DB, _get_conn

        conn = _get_conn(DATA_CACHE_DB)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT 1 FROM candidate_pool_snapshot "
                    f"WHERE {sql_rating_symbol_clean('symbol')} = %s LIMIT 1",
                    (clean,),
                )
                return cur.fetchone() is not None
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[trading_gates] candidate_pool 查询失败 {symbol}: {e}")
        return False


def load_blacklist_level3_symbols(conn=None) -> Set[str]:
    """返回 L3 禁止交易 symbol 集合；开关关闭时返回空集."""
    if not is_blacklist_level3_enforced():
        return set()
    own_conn = conn is None
    try:
        if own_conn:
            db_config = get_db_config()
            conn = pymysql.connect(**db_config, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
        cur = conn.cursor()
        cur.execute("SELECT symbol FROM trading_symbol_rating WHERE rating_level >= 3")
        rows = cur.fetchall()
        if own_conn:
            cur.close()
            conn.close()
        return {futures_symbol_clean(r['symbol'] if isinstance(r, dict) else r[0]) for r in rows}
    except Exception as e:
        logger.warning(f"[trading_gates] 读取黑名单3级失败: {e}")
        if own_conn and conn:
            try:
                conn.close()
            except Exception:
                pass
        return set()


def get_symbol_rating_info(
    symbol: str, cursor=None,
) -> Tuple[Optional[int], bool]:
    """
    统一查询 symbol 的评级等级与 TOP50 状态.

    cursor 可为 pymysql Cursor 或 Connection（与 gate_simulated_open 一致）.

    Returns:
        (rating_level, in_top50):
          rating_level: None=无评级(未在评级表中), 0/1/2/3=评级等级
          in_top50: 是否在 top_performing_symbols 中
    """
    clean = futures_symbol_clean(symbol)
    cur = _as_cursor(cursor)
    try:
        if cur is not None:
            cur.execute(
                f"SELECT "
                f"  (SELECT 1 FROM top_performing_symbols "
                f"   WHERE {sql_rating_symbol_clean('symbol')} = %s LIMIT 1) AS in_top100,"
                f"  (SELECT rating_level FROM trading_symbol_rating "
                f"   WHERE {sql_rating_symbol_clean('symbol')} = %s "
                f"   ORDER BY rating_level DESC LIMIT 1) AS rating_level",
                (clean, clean),
            )
            row = cur.fetchone()
        else:
            db_config = get_db_config()
            conn = pymysql.connect(**db_config, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
            cur = conn.cursor()
            cur.execute(
                f"SELECT "
                f"  (SELECT 1 FROM top_performing_symbols "
                f"   WHERE {sql_rating_symbol_clean('symbol')} = %s LIMIT 1) AS in_top100,"
                f"  (SELECT rating_level FROM trading_symbol_rating "
                f"   WHERE {sql_rating_symbol_clean('symbol')} = %s "
                f"   ORDER BY rating_level DESC LIMIT 1) AS rating_level",
                (clean, clean),
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
        if row:
            in_top50 = (row.get('in_top100') if isinstance(row, dict) else row[0]) == 1
            rl = row.get('rating_level') if isinstance(row, dict) else row[1]
            return (int(rl) if rl is not None else None, in_top50)
    except Exception as e:
        logger.warning(f"[trading_gates] 评级查询失败 {symbol}: {e}")
    return (None, False)


def get_live_margin_ratio(symbol: str, cursor=None) -> float:
    """
    根据 symbol 评级等级获取实盘保证金比例.

    规则:
      - L0 (白名单) → 1.0 (100%)
      - 其它 (无评级 / TOP50 / L1/L2/L3) → 0.0 (禁止实盘)
    """
    rating_level, _ = get_symbol_rating_info(symbol, cursor)

    if rating_level is not None and rating_level >= 1:
        return 0.0

    if rating_level == 0:
        return 1.0
    return 0.0


def live_margin_ratio_str(ratio: float) -> str:
    """保证金比例的友好显示."""
    if ratio >= 1.0:
        return "100%"
    if ratio >= 0.8:
        return "80%"
    if ratio >= 0.6:
        return "60%"
    if ratio >= 0.5:
        return "50%"
    return "禁止"


def check_live_symbol_allowed(symbol: str, cursor=None) -> Tuple[bool, str]:
    """
    检查 symbol 是否允许实盘开仓 (不含 live_trading_enabled 总开关).

    规则:
      - 仅 L0 白名单 (rating_level=0) 允许实盘
      - L1/L2/L3 黑名单 → 拒绝
      - 无评级 / 仅在 TOP50 → 拒绝

    Returns: (allowed, reject_reason)
    """
    if not is_live_whitelist_enabled(cursor):
        return False, '实盘白名单闸门未开启'

    rating_level, _ = get_symbol_rating_info(symbol, cursor)

    if rating_level is not None and rating_level >= 1:
        return False, f'黑名单{rating_level}级禁止实盘'

    if rating_level == 0:
        return True, ''

    return False, '非白名单(rating_level=0)，禁止实盘'


def check_simulated_symbol_allowed(symbol: str, cursor=None) -> Tuple[bool, str]:
    """
    模拟盘开仓基础币种闸门。

    允许: TOP50 / 已有评级(非 L3) / candidate_pool_snapshot 候选池内币种。
    拒绝 L3 及完全未知的币种。
    """
    rating_level, in_top50 = get_symbol_rating_info(symbol, cursor)

    if rating_level is not None and rating_level >= 3:
        return False, f'黑名单{rating_level}级禁止模拟盘'

    if in_top50 or rating_level is not None:
        return True, ''

    if _symbol_in_candidate_pool(symbol):
        return True, ''

    return False, '不在TOP名单且未在黑白名单评级表中，禁止模拟盘'


def count_paper_open_slots(conn, account_id: int = 2) -> int:
    """模拟盘已占槽位：OPEN 持仓 + PENDING 限价开仓单。"""
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM futures_positions "
            "WHERE account_id=%s AND LOWER(status)='open'",
            (account_id,),
        )
        pos_cnt = int((cur.fetchone() or [0])[0] or 0)
        cur.execute(
            "SELECT COUNT(*) FROM futures_orders "
            "WHERE account_id=%s AND status='PENDING' AND order_type='LIMIT' "
            "AND side IN ('OPEN_LONG','OPEN_SHORT')",
            (account_id,),
        )
        pending_cnt = int((cur.fetchone() or [0])[0] or 0)
        cur.close()
        return pos_cnt + pending_cnt
    except Exception as e:
        logger.warning(f"[trading_gates] 统计持仓槽位失败 account={account_id}: {e}")
        return 0


def check_max_positions_allowed(conn, account_id: int = 2) -> tuple[bool, str]:
    """检查模拟盘是否未达 max_positions 上限。"""
    from app.services.system_settings_loader import get_max_positions
    max_pos = get_max_positions()
    used = count_paper_open_slots(conn, account_id)
    if used >= max_pos:
        return False, f"已达最大持仓 {used}/{max_pos}"
    return True, ""


def has_open_futures_position(conn, source: str, symbol: str, account_id: Optional[int] = None) -> bool:
    """按 clean key 检查是否已有 OPEN 仓或同 source 挂单（跨 XXX/USDT 与 XXXUSDT）。"""
    clean = futures_symbol_clean(symbol)
    try:
        cur = conn.cursor()
        sql = (
            f"SELECT 1 FROM futures_positions WHERE source=%s AND status='open' "
            f"AND {sql_rating_symbol_clean('symbol')} = %s"
        )
        params: list = [source, clean]
        if account_id is not None:
            sql += " AND account_id=%s"
            params.append(account_id)
        sql += " LIMIT 1"
        cur.execute(sql, params)
        if cur.fetchone() is not None:
            cur.close()
            return True

        pending_sql = (
            f"SELECT 1 FROM futures_orders WHERE order_source=%s AND status='PENDING' "
            f"AND order_type='LIMIT' AND side IN ('OPEN_LONG','OPEN_SHORT') "
            f"AND {sql_rating_symbol_clean('symbol')} = %s"
        )
        pending_params: list = [source, clean]
        if account_id is not None:
            pending_sql += " AND account_id=%s"
            pending_params.append(account_id)
        pending_sql += " LIMIT 1"
        cur.execute(pending_sql, pending_params)
        found = cur.fetchone() is not None
        cur.close()
        return found
    except Exception as e:
        logger.warning(f"[trading_gates] OPEN 仓检查失败 {source} {symbol}: {e}")
        return False


def sql_exclude_level3_filter(column: str = "symbol") -> str:
    """动态 SQL：排除 L3 的 AND 子句；开关关闭时返回空串."""
    if not is_blacklist_level3_enforced():
        return ""
    return (
        f" AND {sql_rating_symbol_clean(column)} NOT IN ({sql_rating_l3_clean_subquery()})"
    )
