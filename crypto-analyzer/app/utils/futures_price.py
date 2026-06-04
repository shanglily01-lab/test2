"""U 本位模拟开仓/展示用价格 — mark 优先 + 5m 合约 K 线交叉校验."""
from __future__ import annotations

from typing import Optional

from loguru import logger

from app.utils.futures_symbol import (
    futures_symbol_clean,
    futures_symbol_kline_keys,
    futures_symbol_rating_canonical,
)

# 与 5m 收盘偏离超过此比例时, 以 5m 为准 (避免脏 ticker / 非合约 K 线)
_MAX_DEVIATION_FROM_5M = 0.12


def kline5m_futures_close(conn, symbol: str) -> Optional[float]:
    """最新一根 binance_futures 5m 收盘价."""
    sym = futures_symbol_rating_canonical(symbol)
    try:
        with conn.cursor() as cur:
            for sym_key in futures_symbol_kline_keys(sym):
                cur.execute(
                    "SELECT close_price FROM kline_data "
                    "WHERE symbol=%s AND timeframe='5m' AND exchange='binance_futures' "
                    "ORDER BY open_time DESC LIMIT 1",
                    (sym_key,),
                )
                row = cur.fetchone()
                if row and row.get("close_price"):
                    return float(row["close_price"])
    except Exception:
        pass
    return None


def get_futures_trade_price(
    conn,
    symbol: str,
    max_age_seconds: int = 90,
    log_tag: str = "futures_price",
) -> Optional[float]:
    """
    开仓/浮盈计算用价: DataHub mark 优先; 若与 5m 合约收盘偏离过大则采用 5m。
    """
    sym = futures_symbol_rating_canonical(symbol)
    ref = kline5m_futures_close(conn, sym)

    live: Optional[float] = None
    try:
        from app.services.binance_data_hub import get_global_data_hub

        hub = get_global_data_hub()
        if hub is not None:
            p = hub.get_trade_price_sync(sym, max_age_seconds=max_age_seconds)
            if p is not None and p > 0:
                live = float(p)
    except Exception:
        pass

    if live is None or live <= 0:
        return ref

    if ref and ref > 0:
        dev = abs(live - ref) / ref
        if dev > _MAX_DEVIATION_FROM_5M:
            logger.warning(
                f"[{log_tag}] {sym} 取价偏离5m合约收盘 {dev * 100:.1f}%: "
                f"live={live} ref5m={ref}, 采用5m收盘"
            )
            return ref
    return live


def candidate_pool_row(pool: list, symbol: str) -> Optional[dict]:
    """按 clean symbol 匹配 candidate_pool 行 (兼容 BASE/USDT 与 BASEUSDT)."""
    want = futures_symbol_clean(symbol)
    for r in pool:
        if futures_symbol_clean(r.get("symbol") or "") == want:
            return r
    return None
