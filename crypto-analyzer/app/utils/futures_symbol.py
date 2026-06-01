"""U 本位合约 symbol 格式 — K 线/缓存多为 BASE/USDT，部分持仓为 BASEUSDT."""
from __future__ import annotations

from typing import Dict, List


def futures_symbol_clean(symbol: str) -> str:
    return (symbol or "").replace("/", "").replace("%2F", "").upper()


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
