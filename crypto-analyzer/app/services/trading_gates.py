"""
实盘开仓闸门 & 黑名单3级 — 统一读取 system_settings，避免各模块硬编码。
"""
from __future__ import annotations

from typing import Optional, Set, Tuple

import pymysql
from loguru import logger

from app.utils.config_loader import get_db_config


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
        _clean = symbol.replace('/', '')
        cur.execute(
            "SELECT rating_level FROM trading_symbol_rating "
            "WHERE symbol=%s OR symbol=%s LIMIT 1",
            (symbol, _clean),
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
        return {r['symbol'] if isinstance(r, dict) else r[0] for r in rows}
    except Exception as e:
        logger.warning(f"[trading_gates] 读取黑名单3级失败: {e}")
        if own_conn and conn:
            try:
                conn.close()
            except Exception:
                pass
        return set()


def check_live_symbol_allowed(symbol: str, cursor=None) -> Tuple[bool, str]:
    """
    检查 symbol 是否满足实盘 TOP50/白名单闸门 (不含 live_trading_enabled 总开关).

    TOP50 开关与白名单开关为「或」关系：
    - TOP50 开 → TOP50 内可同步实盘
    - 白名单开 → rating_level=0 可同步实盘
    - 两者都关 → 一律不同步实盘

    Returns: (allowed, reject_reason)
    """
    top50_gate = is_live_top50_required()
    whitelist_gate = is_live_whitelist_enabled()

    if not top50_gate and not whitelist_gate:
        return False, '实盘TOP50与白名单闸门均未开启'

    in_top50 = False
    is_whitelist = False
    _clean = symbol.replace('/', '')
    try:
        if cursor is not None:
            cursor.execute(
                "SELECT "
                "  (SELECT 1 FROM top_performing_symbols WHERE symbol=%s LIMIT 1) AS in_top100,"
                "  (SELECT rating_level FROM trading_symbol_rating WHERE symbol=%s OR symbol=%s LIMIT 1) AS rating_level",
                (symbol, symbol, _clean),
            )
            row = cursor.fetchone()
        else:
            db_config = get_db_config()
            conn = pymysql.connect(**db_config, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
            cur = conn.cursor()
            cur.execute(
                "SELECT "
                "  (SELECT 1 FROM top_performing_symbols WHERE symbol=%s LIMIT 1) AS in_top100,"
                "  (SELECT rating_level FROM trading_symbol_rating WHERE symbol=%s OR symbol=%s LIMIT 1) AS rating_level",
                (symbol, symbol, _clean),
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
        if row:
            in_top50 = (row.get('in_top100') if isinstance(row, dict) else row[0]) == 1
            rl = row.get('rating_level') if isinstance(row, dict) else row[1]
            is_whitelist = rl is not None and int(rl) == 0
    except Exception as e:
        logger.warning(f"[trading_gates] TOP50/白名单查询失败 {symbol}: {e}, 默认拒绝实盘")
        return False, 'TOP50/白名单查询失败'

    if top50_gate and in_top50:
        return True, ''
    if whitelist_gate and is_whitelist:
        return True, ''

    if top50_gate and whitelist_gate:
        return False, '不在 TOP 50 也非白名单'
    if top50_gate:
        return False, '不在 TOP 50'
    return False, '不在白名单'


def sql_exclude_level3_filter(column: str = "symbol") -> str:
    """动态 SQL：排除 L3 的 AND 子句；开关关闭时返回空串."""
    if not is_blacklist_level3_enforced():
        return ""
    return (
        f" AND {column} NOT IN ("
        f"SELECT symbol FROM trading_symbol_rating WHERE rating_level >= 3)"
    )
