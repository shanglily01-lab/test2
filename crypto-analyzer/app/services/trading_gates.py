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
    sql_rating_symbol_clean,
)

# 仅以下策略同步 Binance 实盘；其它 source 只走模拟仓
LIVE_SYNC_SOURCES: frozenset[str] = frozenset({
    "gemini_explore",
    "gemini_predict",
    "deepseek_explore",
    "deepseek_predict",
    "gemini_midline_long",
    "gemini_midline_short",
    "deepseek_midline_long",
    "deepseek_midline_short",
})

SYMBOL_STOP_LOSS_COOLDOWN_HOURS = 4
SYMBOL_LOSS_COOLDOWN_HOURS = 12
SYMBOL_LOSS_COOLDOWN_LIMIT_USD = 100.0
SYMBOL_LOSS_ROLLING_HOURS = 24
SYMBOL_LOSS_TRADE_LIMIT = 3
SYMBOL_LOSS_NET_PNL_LIMIT = -120.0
SOURCE_SYMBOL_LOSS_TRADE_LIMIT = 2
SOURCE_SYMBOL_LOSS_NET_PNL_LIMIT = -100.0
SOURCE_SIDE_PERFORMANCE_HOURS = 72
SOURCE_SIDE_PERFORMANCE_MIN_TRADES = 20
SOURCE_SIDE_MIN_WIN_RATE = 35.0
SOURCE_SIDE_NET_PNL_LIMIT = -300.0

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
    """向后兼容：L3/手动锁定已恒禁模拟+实盘；此开关不再改变闸门行为。"""
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
    """True = 应拒绝开仓（L3 或 rating_locked=1）。"""
    _ = symbol, rating_level
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


def load_trading_forbidden_symbols(conn=None) -> Set[str]:
    """返回禁止开仓 symbol 集合：L3 + rating_locked=1。"""
    own_conn = conn is None
    try:
        if own_conn:
            db_config = get_db_config()
            conn = pymysql.connect(**db_config, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
        cur = conn.cursor()
        cur.execute(
            "SELECT symbol FROM trading_symbol_rating "
            "WHERE rating_level >= 3 OR COALESCE(rating_locked, 0) = 1"
        )
        rows = cur.fetchall()
        if own_conn:
            cur.close()
            conn.close()
        return {
            futures_symbol_clean(r['symbol'] if isinstance(r, dict) else r[0])
            for r in rows
        }
    except Exception as e:
        logger.warning(f"[trading_gates] 读取禁止交易名单失败: {e}")
        if own_conn and conn:
            try:
                conn.close()
            except Exception:
                pass
        return set()


def load_blacklist_level3_symbols(conn=None) -> Set[str]:
    """向后兼容：返回 L3 + 手动锁定 symbol 集合。"""
    _ = conn
    return set()


def get_symbol_rating_info(
    symbol: str, cursor=None,
) -> Tuple[Optional[int], bool, bool]:
    """
    统一查询 symbol 的评级等级、TOP50 与手动锁定状态.

    cursor 可为 pymysql Cursor 或 Connection（与 gate_simulated_open 一致）.

    Returns:
        (rating_level, in_top50, rating_locked):
          rating_level: None=无评级(未在评级表中), 0/1/2/3=评级等级
          in_top50: 是否在 top_performing_symbols 中
          rating_locked: 是否手动锁定（锁定后禁止模拟+实盘开仓）
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
                f"   ORDER BY rating_level DESC LIMIT 1) AS rating_level,"
                f"  (SELECT COALESCE(rating_locked, 0) FROM trading_symbol_rating "
                f"   WHERE {sql_rating_symbol_clean('symbol')} = %s "
                f"   ORDER BY rating_level DESC LIMIT 1) AS rating_locked",
                (clean, clean, clean),
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
                f"   ORDER BY rating_level DESC LIMIT 1) AS rating_level,"
                f"  (SELECT COALESCE(rating_locked, 0) FROM trading_symbol_rating "
                f"   WHERE {sql_rating_symbol_clean('symbol')} = %s "
                f"   ORDER BY rating_level DESC LIMIT 1) AS rating_locked",
                (clean, clean, clean),
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
        if row:
            in_top50 = (row.get('in_top100') if isinstance(row, dict) else row[0]) == 1
            rl = row.get('rating_level') if isinstance(row, dict) else row[1]
            locked_raw = row.get('rating_locked') if isinstance(row, dict) else row[2]
            rating_locked = bool(int(locked_raw or 0))
            return (
                int(rl) if rl is not None else None,
                in_top50,
                rating_locked,
            )
    except Exception as e:
        logger.warning(f"[trading_gates] 评级查询失败 {symbol}: {e}")
    return (None, False, False)


def check_symbol_trading_forbidden(
    symbol: str,
    cursor=None,
    rating_level: Optional[int] = None,
    rating_locked: Optional[bool] = None,
) -> Tuple[bool, str]:
    """
    L3 或 rating_locked=1 → 禁止模拟盘与实盘开仓（不依赖 blacklist_level3_enabled）。

    Returns: (forbidden, reason)
    """
    if rating_level is None or rating_locked is None:
        rl, _, locked = get_symbol_rating_info(symbol, cursor)
        if rating_level is None:
            rating_level = rl
        if rating_locked is None:
            rating_locked = locked
    if rating_locked:
        return True, "交易对已手动锁定，禁止开仓"
    if rating_level is not None and int(rating_level) >= 3:
        return True, f"黑名单{int(rating_level)}级禁止交易"
    return False, ""


DEFAULT_LIVE_BASE_MARGIN_USD = 100.0


def get_live_base_margin_usd(user_id: Optional[int] = None, cursor=None) -> float:
    """
    实盘单笔基础保证金（USDT），来自 user_api_keys.max_position_value。
    多 key 取最小正值；无配置时 DEFAULT_LIVE_BASE_MARGIN_USD。
    """
    own_conn = cursor is None
    conn = None
    try:
        if cursor is not None:
            cur = cursor
        else:
            conn = pymysql.connect(**get_db_config(), cursorclass=pymysql.cursors.DictCursor)
            cur = conn.cursor()
        if user_id is not None:
            cur.execute(
                """SELECT max_position_value FROM user_api_keys
                   WHERE user_id=%s AND status='active'
                   ORDER BY id ASC LIMIT 1""",
                (int(user_id),),
            )
            row = cur.fetchone()
            if row:
                v = float(row.get("max_position_value") or 0)
                return v if v > 0 else DEFAULT_LIVE_BASE_MARGIN_USD
            return DEFAULT_LIVE_BASE_MARGIN_USD
        cur.execute(
            """SELECT max_position_value FROM user_api_keys
               WHERE exchange='binance' AND status='active' ORDER BY id""",
        )
        vals = [
            float(r.get("max_position_value") or 0)
            for r in (cur.fetchall() or [])
            if float(r.get("max_position_value") or 0) > 0
        ]
        if vals:
            return min(vals)
        return DEFAULT_LIVE_BASE_MARGIN_USD
    except Exception as e:
        logger.warning(f"[trading_gates] 读取 max_position_value 失败: {e}")
        return DEFAULT_LIVE_BASE_MARGIN_USD
    finally:
        if own_conn and conn is not None:
            conn.close()


def get_live_margin_ratio(symbol: str, cursor=None) -> float:
    """
    根据 symbol 评级等级获取实盘保证金比例.

    规则:
      - L0 (白名单) → 1.0 (100%)
      - 其它 (无评级 / TOP50 / L1/L2/L3) → 0.0 (禁止实盘)
    """
    rating_level, _, rating_locked = get_symbol_rating_info(symbol, cursor)
    forbidden, _ = check_symbol_trading_forbidden(
        symbol, cursor, rating_level=rating_level, rating_locked=rating_locked,
    )
    if forbidden:
        return 0.0

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

    rating_level, _, rating_locked = get_symbol_rating_info(symbol, cursor)
    forbidden, forbid_reason = check_symbol_trading_forbidden(
        symbol, cursor, rating_level=rating_level, rating_locked=rating_locked,
    )
    if forbidden:
        return False, forbid_reason

    if rating_level is not None and rating_level >= 1:
        return False, f'黑名单{rating_level}级禁止实盘'

    if rating_level == 0:
        return True, ''

    return False, '非白名单(rating_level=0)，禁止实盘'


def check_simulated_symbol_allowed(symbol: str, cursor=None) -> Tuple[bool, str]:
    """
    模拟盘开仓基础币种闸门。

    拒绝: L3 / rating_locked=1。
    允许: TOP50 / 已有评级且非禁止 / candidate_pool_snapshot 候选池内币种。
    """
    rating_level, in_top50, rating_locked = get_symbol_rating_info(symbol, cursor)
    _ = rating_locked

    if in_top50 or rating_level is not None:
        return True, ''

    if _symbol_in_candidate_pool(symbol):
        return True, ''

    return False, '不在TOP名单且未在黑白名单评级表中，禁止模拟盘'


DEFAULT_PAPER_MARGIN_USD = 1000.0
PAPER_MARGIN_BY_RATING_LEVEL = {
    0: 1000.0,
    1: 400.0,
    2: 200.0,
    3: 100.0,
}


def get_paper_margin_usd(symbol: str, cursor=None) -> float:
    """
    Return simulated-order margin by symbol rating.

    Rules:
      - L0 whitelist or unrated/default: 1000U
      - L1 blacklist: 400U
      - L2 blacklist: 200U
      - L3 blacklist: 100U

    Live trading keeps using check_live_symbol_allowed/get_live_margin_ratio,
    which only allow L0.
    """
    rating_level, _, _ = get_symbol_rating_info(symbol, cursor)
    if rating_level is None:
        return DEFAULT_PAPER_MARGIN_USD
    level = int(rating_level)
    if level >= 3:
        return PAPER_MARGIN_BY_RATING_LEVEL[3]
    return PAPER_MARGIN_BY_RATING_LEVEL.get(level, DEFAULT_PAPER_MARGIN_USD)


def count_paper_open_slots(conn, account_id: int = 2) -> int:
    """模拟盘已占槽位：OPEN 持仓 + PENDING 限价开仓单。"""
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM futures_positions "
            "WHERE account_id=%s AND LOWER(status)='open'",
            (account_id,),
        )
        pos_cnt = int(_row_get(cur.fetchone(), "cnt", 0, 0) or 0)
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM futures_orders "
            "WHERE account_id=%s AND status='PENDING' AND order_type='LIMIT' "
            "AND side IN ('OPEN_LONG','OPEN_SHORT')",
            (account_id,),
        )
        pending_cnt = int(_row_get(cur.fetchone(), "cnt", 0, 0) or 0)
        cur.close()
        return pos_cnt + pending_cnt
    except Exception as e:
        logger.warning(f"[trading_gates] 统计持仓槽位失败 account={account_id}: {e}")
        return 0


def check_max_positions_allowed(conn, account_id: int = 2) -> tuple[bool, str]:
    """Enforce the paper account slot cap from system_settings.max_positions."""
    try:
        from app.services.system_settings_loader import get_max_positions

        max_positions = int(get_max_positions())
    except Exception as e:
        logger.warning(f"[trading_gates] read max_positions failed: {e}")
        max_positions = 50

    if max_positions <= 0:
        return True, "max_positions disabled"

    used_slots = count_paper_open_slots(conn, account_id)
    if used_slots >= max_positions:
        return False, f"paper_slots_full:{used_slots}/{max_positions}"
    return True, f"paper_slots:{used_slots}/{max_positions}"


def check_source_side_performance_allowed(
    conn,
    source: str,
    side: str,
    account_id: int = 2,
) -> tuple[bool, str]:
    """Block a source+side after clear short-term win-rate decay."""
    src = _normalize_source(source)
    s = (side or "").strip().upper()
    if not src or s not in ("LONG", "SHORT"):
        return True, ""

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
              COUNT(*) AS trades,
              SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
              COALESCE(SUM(realized_pnl), 0) AS pnl
            FROM futures_positions
            WHERE account_id=%s
              AND source=%s
              AND position_side=%s
              AND status='closed'
              AND close_time >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s HOUR)
              AND realized_pnl IS NOT NULL
            """,
            (account_id, src, s, SOURCE_SIDE_PERFORMANCE_HOURS),
        )
        row = cur.fetchone() or {}
        cur.close()
    except Exception as e:
        logger.warning(f"[trading_gates] source-side performance check failed {src} {s}: {e}")
        return False, "source_side_performance_error"

    trades = int(_row_get(row, "trades", 0, 0) or 0)
    if trades < SOURCE_SIDE_PERFORMANCE_MIN_TRADES:
        return True, ""

    wins = int(_row_get(row, "wins", 1, 0) or 0)
    pnl = float(_row_get(row, "pnl", 2, 0) or 0)
    win_rate = wins / trades * 100.0 if trades else 0.0
    if win_rate < SOURCE_SIDE_MIN_WIN_RATE or pnl <= SOURCE_SIDE_NET_PNL_LIMIT:
        return (
            False,
            f"source_side_circuit_breaker:{src} {s} {SOURCE_SIDE_PERFORMANCE_HOURS}h "
            f"trades={trades} win_rate={win_rate:.1f}% pnl={pnl:.2f}U",
        )
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


def has_open_futures_position_same_side(
    conn,
    symbol: str,
    side: str,
    account_id: Optional[int] = None,
) -> tuple[bool, str]:
    """跨 source 检查同币同方向是否已有 OPEN 仓或 PENDING 开仓单。"""
    clean = futures_symbol_clean(symbol)
    s = (side or "").strip().upper()
    if s not in ("LONG", "SHORT"):
        return False, ""
    order_side = "OPEN_LONG" if s == "LONG" else "OPEN_SHORT"
    try:
        cur = conn.cursor()
        sql = (
            f"SELECT id, source FROM futures_positions "
            f"WHERE LOWER(status)='open' "
            f"AND position_side=%s "
            f"AND {sql_rating_symbol_clean('symbol')} = %s"
        )
        params: list = [s, clean]
        if account_id is not None:
            sql += " AND account_id=%s"
            params.append(account_id)
        sql += " ORDER BY id DESC LIMIT 1"
        cur.execute(sql, params)
        row = cur.fetchone()
        if row is not None:
            pid = row.get("id") if isinstance(row, dict) else row[0]
            src = row.get("source") if isinstance(row, dict) else row[1]
            cur.close()
            return True, f"same_symbol_side_open:{clean} {s} existing_position_id={pid} source={src}"

        pending_sql = (
            f"SELECT id, order_source FROM futures_orders "
            f"WHERE status='PENDING' AND order_type='LIMIT' "
            f"AND side=%s "
            f"AND {sql_rating_symbol_clean('symbol')} = %s"
        )
        pending_params: list = [order_side, clean]
        if account_id is not None:
            pending_sql += " AND account_id=%s"
            pending_params.append(account_id)
        pending_sql += " ORDER BY id DESC LIMIT 1"
        cur.execute(pending_sql, pending_params)
        row = cur.fetchone()
        cur.close()
        if row is not None:
            oid = row.get("id") if isinstance(row, dict) else row[0]
            src = row.get("order_source") if isinstance(row, dict) else row[1]
            return True, f"same_symbol_side_pending:{clean} {s} existing_order_id={oid} source={src}"
        return False, ""
    except Exception as e:
        logger.warning(f"[trading_gates] 同币同方向去重检查失败 {symbol} {side}: {e}")
        raise


def sql_exclude_forbidden_symbols_filter(column: str = "symbol") -> str:
    """动态 SQL：排除 L3 与手动锁定 symbol 的 AND 子句。"""
    clean_col = sql_rating_symbol_clean(column)
    return (
        f" AND {clean_col} NOT IN ("
        f"SELECT {sql_rating_symbol_clean('symbol')} FROM trading_symbol_rating "
        f"WHERE rating_level >= 3 OR COALESCE(rating_locked, 0) = 1)"
    )


def sql_exclude_level3_filter(column: str = "symbol") -> str:
    """向后兼容：排除 L3 + 手动锁定。"""
    return sql_exclude_forbidden_symbols_filter(column)
