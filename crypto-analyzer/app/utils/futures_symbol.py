"""U 本位合约 symbol 格式 — K 线/缓存多为 BASE/USDT，部分持仓为 BASEUSDT."""
from __future__ import annotations

from typing import Dict, List, Set, Optional


def futures_symbol_clean(symbol: str) -> str:
    return (symbol or "").replace("/", "").replace("%2F", "").upper()


def futures_symbol_rating_canonical(symbol: str) -> str:
    """trading_symbol_rating 唯一存储格式: BASE/USDT 或 BASE/USD."""
    s = (symbol or "").strip()
    if not s:
        return s
    if "/" in s:
        base, quote = s.split("/", 1)
        return f"{base.upper()}/{quote.upper()}"
    clean = futures_symbol_clean(s)
    if clean.endswith("USDT") and len(clean) > 4:
        return f"{clean[:-4]}/USDT"
    if clean.endswith("USD") and len(clean) > 3:
        return f"{clean[:-3]}/USD"
    return clean


def futures_symbol_rating_variants(symbol: str) -> List[str]:
    """同一交易对在库中可能出现的写法（合并/删除重复行用）。"""
    out: List[str] = []
    seen: Set[str] = set()

    def _add(x: str) -> None:
        if x and x not in seen:
            seen.add(x)
            out.append(x)

    _add(futures_symbol_rating_canonical(symbol))
    raw = (symbol or "").strip()
    if raw:
        _add(raw)
        _add(raw.upper())
    clean = futures_symbol_clean(symbol)
    _add(clean)
    for k in futures_symbol_kline_keys(symbol):
        _add(k)
    return out


def sql_rating_symbol_clean(column: str = "symbol") -> str:
    """SQL 表达式: 将 symbol 列规范为无斜杠大写，用于跨格式匹配。"""
    return f"(REPLACE(UPPER({column}), '/', '') COLLATE utf8mb4_unicode_ci)"


def sql_rating_l3_clean_subquery(min_level: int = 3) -> str:
    """返回 L>=min_level 的 clean symbol 子查询。"""
    return (
        f"SELECT {sql_rating_symbol_clean('symbol')} FROM trading_symbol_rating "
        f"WHERE rating_level >= {int(min_level)}"
    )


def resolve_futures_universe_item(universe: dict, symbol: str) -> Optional[dict]:
    """LLM/持仓常为 BTCUSDT，universe 键多为 BTC/USDT."""
    if not symbol or not universe:
        return None
    canon = futures_symbol_rating_canonical(symbol)
    if canon in universe:
        return universe[canon]
    clean = futures_symbol_clean(symbol)
    for key, item in universe.items():
        if futures_symbol_clean(key) == clean:
            return item
    return None


def futures_symbol_kline_keys(symbol: str) -> List[str]:
    """kline_data 查询候选（按优先级）。"""
    s = (symbol or "").strip()
    if not s:
        return []
    out: List[str] = []
    seen = set()

    def _add(x: str) -> None:
        if x and x not in seen:
            seen.add(x)
            out.append(x)

    _add(s)
    su = s.upper()
    if "/" not in s and su.endswith("USDT") and len(su) > 4:
        _add(f"{su[:-4]}/USDT")
    elif "/" in s:
        _add(su.replace("/", ""))
    return out


def futures_symbol_groups(symbols: List[str]) -> Dict[str, List[str]]:
    """clean symbol -> 所有原始写法（避免 batch 取价互相覆盖）。"""
    groups: Dict[str, List[str]] = {}
    for raw in symbols:
        if not raw:
            continue
        clean = futures_symbol_clean(raw)
        if not clean:
            continue
        bucket = groups.setdefault(clean, [])
        if raw not in bucket:
            bucket.append(raw)
    return groups
