"""U 本位模拟开仓/展示用价格 — 统一走 DataHub，禁止旁路读 kline_data。"""
from __future__ import annotations

from typing import Optional

from loguru import logger

from app.utils.futures_symbol import (
    futures_symbol_clean,
    futures_symbol_rating_canonical,
)


def _rest_futures_mark_price(symbol: str) -> Optional[float]:
    """Hub 不可达时的紧急 REST mark（scheduler 进程无 9020 时）。"""
    sym_clean = futures_symbol_clean(symbol)
    if not sym_clean:
        return None
    try:
        import requests

        r = requests.get(
            "https://fapi.binance.com/fapi/v1/premiumIndex",
            params={"symbol": sym_clean},
            timeout=5,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        mp = data.get("markPrice")
        if mp is not None and float(mp) > 0:
            return float(mp)
    except Exception as e:
        logger.debug(f"[futures_price] {symbol} REST mark 失败: {e}")
    return None


def get_futures_trade_price(
    conn,
    symbol: str,
    max_age_seconds: int = 90,
    log_tag: str = "futures_price",
    require_fresh: bool = False,
) -> Optional[float]:
    """
    开仓/浮盈/限价执行用价：仅 DataHub（WS → mark 缓存 → ticker → REST），
    不读 kline_data。conn 保留以兼容调用方签名。
    """
    del conn  # 定价不查库，统一 Hub
    sym = futures_symbol_rating_canonical(symbol)

    live: Optional[float] = None
    try:
        from app.services.binance_data_hub import get_global_data_hub

        hub = get_global_data_hub()
        if hub is not None:
            p = hub.get_trade_price_sync(
                sym,
                max_age_seconds=max_age_seconds,
                allow_rest_fallback=True,
                allow_db_fallback=False,
            )
            if p is not None and p > 0:
                live = float(p)
    except Exception as e:
        logger.debug(f"[{log_tag}] {sym} Hub 取价异常: {e}")

    if live is None or live <= 0:
        live = _rest_futures_mark_price(sym)

    if live is None or live <= 0:
        logger.warning(f"[{log_tag}] {sym} Hub/REST 均无有效市价")
        return None
    return live


def candidate_pool_row(pool: list, symbol: str) -> Optional[dict]:
    """按 clean symbol 匹配 candidate_pool 行 (兼容 BASE/USDT 与 BASEUSDT)."""
    want = futures_symbol_clean(symbol)
    for r in pool:
        if futures_symbol_clean(r.get("symbol") or "") == want:
            return r
    return None
