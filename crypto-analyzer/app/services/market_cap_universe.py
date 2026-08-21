"""市值排名交易对池 — config.yaml 按市值序写入；BRAIN 扫前 300，破位扫前 100。

权威: docs/REQUIREMENTS_LOGIC_ZH.md §7.2 / §7.3.2
"""
from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Set

from loguru import logger

from app.services.securities_filter import is_security
from app.utils.futures_symbol import futures_symbol_clean, futures_symbol_rating_canonical

BRAIN_MARKET_CAP_LIMIT = 300
MIDLINE_MARKET_CAP_LIMIT = 100
_PLAIN_USDT = re.compile(r"^[A-Z0-9]{2,24}/USDT$")

_PREFIXES = ("1000000", "100000", "10000", "1000", "1M")


def load_config_yaml_symbols_ranked() -> List[str]:
    """按 config.yaml 书写顺序读取 U 本位交易对（写入时已按市值排序）。"""
    from app.services.midline_swing_scanner import load_config_yaml_symbols
    return list(load_config_yaml_symbols())


def _forbidden_clean(conn=None) -> Set[str]:
    try:
        from app.services.trading_gates import load_trading_forbidden_symbols
        banned = load_trading_forbidden_symbols(conn) or set()
    except Exception as e:
        logger.warning(f"[market_cap_universe] 读取禁止名单失败: {e}")
        banned = set()
    return {futures_symbol_clean(futures_symbol_rating_canonical(b)) for b in banned}


def load_ranked_usdt_symbols(limit: int, conn=None) -> List[str]:
    """config.yaml 市值序，去掉证券/L2+/锁定，截取前 limit 个。"""
    ranked = load_config_yaml_symbols_ranked()
    banned = _forbidden_clean(conn)
    out: List[str] = []
    seen: Set[str] = set()
    for sym in ranked:
        clean = futures_symbol_clean(sym)
        if (
            not clean
            or clean in seen
            or clean in banned
            or is_security(sym)
        ):
            continue
        seen.add(clean)
        out.append(sym)
        if len(out) >= max(1, int(limit)):
            break
    return out


def load_brain_universe(conn=None) -> List[str]:
    return load_ranked_usdt_symbols(BRAIN_MARKET_CAP_LIMIT, conn)


def load_midline_universe_from_cap(conn=None) -> List[str]:
    return load_ranked_usdt_symbols(MIDLINE_MARKET_CAP_LIMIT, conn)


def _ticker_aliases(base: str) -> List[str]:
    b = (base or "").upper().strip()
    aliases = [b]
    for p in _PREFIXES:
        if b.startswith(p) and len(b) > len(p):
            aliases.append(b[len(p):])
    return aliases


def match_coingecko_to_binance(
    cg_tickers: Sequence[str],
    binance_pairs: Iterable[str],
    *,
    limit: int = BRAIN_MARKET_CAP_LIMIT,
) -> List[str]:
    """按市值序把 CoinGecko ticker 映射到 Binance `BASE/USDT` 永续。"""
    pair_by_base = {}
    for pair in binance_pairs:
        canon = futures_symbol_rating_canonical(str(pair or ""))
        if not canon.endswith("/USDT") or is_security(canon) or not _PLAIN_USDT.fullmatch(canon):
            continue
        base = canon.split("/")[0]
        pair_by_base[base] = canon

    ticker_to_bases: dict[str, List[str]] = {}
    for base in pair_by_base:
        for alias in _ticker_aliases(base):
            ticker_to_bases.setdefault(alias, []).append(base)

    used_bases: Set[str] = set()
    out: List[str] = []
    for raw in cg_tickers:
        ticker = str(raw or "").upper().strip()
        if not ticker or ticker in {"USDT", "USD", "DAI"}:
            continue
        candidates = ticker_to_bases.get(ticker) or []
        chosen = None
        if ticker in pair_by_base and ticker not in used_bases:
            chosen = ticker
        else:
            for base in candidates:
                if base in used_bases:
                    continue
                if base == ticker or any(base.startswith(p) and base[len(p):] == ticker for p in _PREFIXES):
                    chosen = base
                    break
            if chosen is None:
                for base in candidates:
                    if base not in used_bases:
                        chosen = base
                        break
        if not chosen:
            continue
        used_bases.add(chosen)
        out.append(pair_by_base[chosen])
        if len(out) >= limit:
            break
    return out
