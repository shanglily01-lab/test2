"""
实盘开仓闸门 & 黑名单等级 — 统一读取 system_settings，避免各模块硬编码。

按 source 控制实盘（其余策略仅模拟）:
  - gemini_explore, deepseek_explore, deepseek_predict → 可开实盘
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
    "deepseek_explore",
    "deepseek_predict",
})

# 无评级（不在 trading_symbol_rating）且不在 TOP50 时的实盘保证金比例
LIVE_MARGIN_UNRATED = 0.6


def _bool_setting(key: str, default: bool = True) -> bool:
    try:
        from app.services.system_settings_loader import get_setting
        val = get_setting(key)
    except Exception:
        val = None
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ('1', 'true', 'yes', 'on')


def is_blacklist_level3_enforced() -> bool:
    """黑名单3级禁止开仓 (默认开启)."""
    return _bool_setting('blacklist_level3_enabled', True)


def is_live_top50_required() -> bool:
    """实盘同步是否允许 TOP50 内交易对开实仓 (默认开启)."""
    return _bool_setting('live_top50_required', True)


def is_live_whitelist_enabled() -> bool:
    """实盘同步是否允许白名单 (rating_level=0) 开实仓 (默认开启)."""
    return _bool_setting('live_whitelist_enabled', True)


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
    开仓同步实盘前的完整检查: 总开关 + source 白名单 + TOP50/评级白名单.

    Returns: (allowed, reject_reason)
    """
    time_allowed, time_reason = get_beijing_open_window_status()
    if not time_allowed:
        return False, time_reason
    if not is_live_trading_enabled():
        return False, "live_trading_enabled=0"
    if not should_sync_live_for_source(source):
        return False, f"策略 {source} 仅模拟盘"
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

    Returns:
        (rating_level, in_top50):
          rating_level: None=无评级(未在评级表中), 0/1/2/3=评级等级
          in_top50: 是否在 top_performing_symbols 中
    """
    clean = futures_symbol_clean(symbol)
    try:
        if cursor is not None:
            cursor.execute(
                f"SELECT "
                f"  (SELECT 1 FROM top_performing_symbols "
                f"   WHERE {sql_rating_symbol_clean('symbol')} = %s LIMIT 1) AS in_top100,"
                f"  (SELECT rating_level FROM trading_symbol_rating "
                f"   WHERE {sql_rating_symbol_clean('symbol')} = %s "
                f"   ORDER BY rating_level DESC LIMIT 1) AS rating_level",
                (clean, clean),
            )
            row = cursor.fetchone()
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
      - L0 (白名单) 或 TOP50 → 1.0 (100%)
      - 无评级且不在 TOP50 → LIVE_MARGIN_UNRATED (默认 60%)
      - L1/L2/L3 (黑名单) → 0.0 (禁止实盘)

    Args:
        symbol: 交易对
        cursor: 可选数据库游标

    Returns:
        float: 保证金比例 (0.0 = 禁止实盘)
    """
    rating_level, in_top50 = get_symbol_rating_info(symbol, cursor)

    if rating_level is not None and rating_level >= 1:
        return 0.0

    if in_top50 or rating_level == 0:
        return 1.0
    # 无评级 (None) 且不在 TOP50
    return LIVE_MARGIN_UNRATED


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

    新规则 (2026-06-06):
      - L0 (白名单), TOP50, 无评级 → 允许 (按不同保证金比例)
      - L1/L2/L3 (黑名单) → 拒绝实盘

    保留 TOP50/白名单 开关作为主闸门:
      - 两者都关 → 一律拒绝

    Returns: (allowed, reject_reason)
    """
    top50_gate = is_live_top50_required()
    whitelist_gate = is_live_whitelist_enabled()

    if not top50_gate and not whitelist_gate:
        return False, '实盘TOP50与白名单闸门均未开启'

    rating_level, in_top50 = get_symbol_rating_info(symbol, cursor)

    # L1 / L2 / L3 直接拒绝
    if rating_level is not None and rating_level >= 1:
        return False, f'黑名单{rating_level}级禁止实盘'

    # TOP50 或 L0 白名单 — 100% 保证金
    if top50_gate and in_top50:
        return True, ''
    if whitelist_gate and rating_level == 0:
        return True, ''

    # 无评级且不在 TOP50：允许实盘，保证金 60%
    if rating_level is None:
        if top50_gate or whitelist_gate:
            return True, f'无评级默认{int(LIVE_MARGIN_UNRATED * 100)}%保证金'
        return False, '不满足任何实盘条件'

    return False, '不在TOP名单且不是rating_level=0白名单，禁止实盘'


def check_simulated_symbol_allowed(symbol: str, cursor=None) -> Tuple[bool, str]:
    """
    模拟盘开仓基础币种闸门。

    允许 TOP 名单内币种，或 trading_symbol_rating 中已有评级且不是 L3 的币种。
    拒绝 L3，以及既不在 TOP、也不在黑白名单评级表中的未知币种。
    """
    rating_level, in_top50 = get_symbol_rating_info(symbol, cursor)

    if rating_level is not None and rating_level >= 3:
        return False, f'黑名单{rating_level}级禁止模拟盘'

    if in_top50 or rating_level is not None:
        return True, ''

    return False, '不在TOP名单且未在黑白名单评级表中，禁止模拟盘'


def has_open_futures_position(conn, source: str, symbol: str, account_id: Optional[int] = None) -> bool:
    """按 clean key 检查是否已有 OPEN 仓（跨 XXX/USDT 与 XXXUSDT）。"""
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
