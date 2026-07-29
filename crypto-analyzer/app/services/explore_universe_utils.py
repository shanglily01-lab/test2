"""Shared explore/universe helpers (neutral).

Historically lived in gemini_swan_worker.py. Used by DeepSeek explore/predict,
explore_worker_impl, and legacy swan/Gemini paths.
"""
from __future__ import annotations

from app.services.securities_filter import is_security

# ------------------ 配置常量 ------------------
EXCLUDE_BASES = {"BTC", "ETH", "BNB", "SOL", "XRP"}
STABLECOINS = {"USDT", "USDC", "DAI", "FDUSD", "BUSD", "TUSD", "USDE", "USD1", "PYUSD"}
MIN_QUOTE_VOLUME = 10_000_000  # 1000 万 USDT 24h 成交额下限
TOP_MOVER = 12                  # 24h 涨幅 / 跌幅 各取 top 12
TOP_FUNDING = 10                # 资金费率 极正 / 极负 各取 top 10

# ── data_cache 层: 带本地内存缓存的 setting 读取 ──
from app.services.data_cache_service import get_setting as _cached_get_setting

_DATA_CACHE_SETTINGS = True


def _read_setting(cur, key: str, default: str) -> str:
    """
    尝试从 data_cache.settings_cache 读取 (带 60s 本地缓存),
    失败时回退到 system_settings 直接查询.
    """
    if _DATA_CACHE_SETTINGS:
        try:
            return _cached_get_setting(key, default)
        except Exception:
            pass
    cur.execute(
        "SELECT setting_value FROM system_settings WHERE setting_key = %s LIMIT 1",
        (key,),
    )
    row = cur.fetchone()
    if not row:
        return default
    val = row.get("setting_value")
    return str(val) if val is not None else default


def _base_of(symbol: str) -> str:
    s = symbol.upper()
    if "/" in s:
        return s.split("/")[0]
    if s.endswith("USDT"):
        return s[:-4]
    return s


def _is_excluded(symbol: str) -> bool:
    b = _base_of(symbol)
    if b in EXCLUDE_BASES or b in STABLECOINS:
        return True
    return is_security(symbol)


def _merge_universe(gainers, losers, fund_pos, fund_neg) -> dict:
    uni: dict = {}

    def upsert(sym, **fields):
        sym = sym.upper()
        if sym not in uni:
            uni[sym] = {
                "symbol": sym,
                "triggers": [],
                "current_price": None,
                "change_24h": None,
                "quote_volume_24h": None,
                "current_rate": None,
                "rate_avg_7d": None,
            }
        for k, v in fields.items():
            if k == "trigger":
                uni[sym]["triggers"].append(v)
            elif uni[sym].get(k) is None:
                uni[sym][k] = v

    for r in gainers:
        upsert(
            r["symbol"],
            trigger="24h_gainer",
            current_price=float(r["current_price"]) if r["current_price"] else None,
            change_24h=float(r["change_24h"]) if r["change_24h"] else None,
            quote_volume_24h=float(r["quote_volume_24h"]) if r["quote_volume_24h"] else None,
        )
    for r in losers:
        upsert(
            r["symbol"],
            trigger="24h_loser",
            current_price=float(r["current_price"]) if r["current_price"] else None,
            change_24h=float(r["change_24h"]) if r["change_24h"] else None,
            quote_volume_24h=float(r["quote_volume_24h"]) if r["quote_volume_24h"] else None,
        )
    for r in fund_pos:
        upsert(
            r["symbol"],
            trigger="funding_pos_extreme",
            current_rate=float(r["current_rate"]) if r["current_rate"] is not None else None,
            rate_avg_7d=float(r["rate_avg_7d"]) if r["rate_avg_7d"] is not None else None,
        )
    for r in fund_neg:
        upsert(
            r["symbol"],
            trigger="funding_neg_extreme",
            current_rate=float(r["current_rate"]) if r["current_rate"] is not None else None,
            rate_avg_7d=float(r["rate_avg_7d"]) if r["rate_avg_7d"] is not None else None,
        )
    return uni


__all__ = [
    "EXCLUDE_BASES",
    "STABLECOINS",
    "MIN_QUOTE_VOLUME",
    "TOP_MOVER",
    "TOP_FUNDING",
    "_read_setting",
    "_base_of",
    "_is_excluded",
    "_merge_universe",
]
