"""U 本位模拟开仓/展示用价格 — 统一走 DataHub，禁止旁路读 kline_data。"""
from __future__ import annotations

from typing import Optional

from loguru import logger

from app.utils.futures_symbol import (
    futures_symbol_clean,
    futures_symbol_rating_canonical,
)


def _rest_futures_last_price(symbol: str) -> Optional[float]:
    """Hub 不可达时 REST 最新成交价（与 ticker/price 一致，非 mark）。"""
    sym_clean = futures_symbol_clean(symbol)
    if not sym_clean:
        return None
    try:
        import requests

        r = requests.get(
            "https://fapi.binance.com/fapi/v1/ticker/price",
            params={"symbol": sym_clean},
            timeout=5,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        p = data.get("price")
        if p is not None and float(p) > 0:
            return float(p)
    except Exception as e:
        logger.debug(f"[futures_price] {symbol} REST last 失败: {e}")
    return None


def _kline_close_fallback(conn, symbol: str) -> Optional[float]:
    """与 /api/futures/prices/batch 第二步一致：5m→15m→1h K 线收盘。"""
    from app.utils.futures_symbol import futures_symbol_kline_keys

    sym = futures_symbol_rating_canonical(symbol)
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            for key in futures_symbol_kline_keys(sym):
                for tf in ("5m", "15m", "1h", "1m"):
                    cur.execute(
                        """
                        SELECT close_price FROM kline_data
                        WHERE symbol=%s AND timeframe=%s
                        ORDER BY open_time DESC LIMIT 1
                        """,
                        (key, tf),
                    )
                    row = cur.fetchone()
                    if row and row.get("close_price") and float(row["close_price"]) > 0:
                        return float(row["close_price"])
    except Exception as e:
        logger.debug(f"[limit_trigger] {sym} kline fallback: {e}")
    return None


def get_futures_limit_trigger_price(
    conn,
    symbol: str,
    max_age_seconds: int = 30,
    log_tag: str = "limit_trigger",
) -> Optional[float]:
    """
    限价单触价判断价 — 与 Web 限价列表 /prices/batch 一致（ticker 最新价），
    不用 mark price，避免 UI 显示「可成交」但执行器不认。
    """
    sym = futures_symbol_rating_canonical(symbol)
    clean = futures_symbol_clean(sym)

    try:
        from app.services.binance_data_hub import get_global_data_hub

        hub = get_global_data_hub()
        if hub is not None:
            tmap = hub.get_full_ticker_map(market="futures")
            if clean and clean in tmap:
                p = float(tmap[clean])
                if p > 0:
                    return p
            px = hub.get_price_sync(
                sym, max_age_seconds=max_age_seconds, allow_rest_fallback=True,
            )
            if px is not None and float(px) > 0:
                return float(px)
    except Exception as e:
        logger.debug(f"[{log_tag}] {sym} Hub ticker 取价异常: {e}")

    live = _kline_close_fallback(conn, sym)
    if live and live > 0:
        return live

    live = _rest_futures_last_price(sym)
    if live and live > 0:
        return live

    logger.warning(f"[{log_tag}] {sym} ticker/kline/REST 均无有效触价")
    return None


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
    开仓/浮盈用价：mark 优先（WS → premiumIndex → ticker → REST mark）。
    限价触价判断请用 get_futures_limit_trigger_price（ticker 与 UI 一致）。
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
