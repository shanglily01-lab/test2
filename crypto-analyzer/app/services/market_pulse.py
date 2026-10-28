"""市值前 100 的规则行情：情绪、当前走势、后续推演。不调用 LLM，不负责开仓。"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pymysql
from loguru import logger

from app.services.midline_swing_scanner import load_config_yaml_symbols
from app.services.securities_filter import is_security
from app.utils.config_loader import get_db_config
from app.utils.futures_symbol import futures_symbol_rating_canonical

PULSE_LIMIT = 100
_CACHE_TTL_S = 45.0
_cache: Dict[str, Any] = {"ts": 0.0, "payload": None}


def _f(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _rsi(closes: Sequence[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(len(closes) - period, len(closes)):
        delta = closes[i] - closes[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss <= 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _return_pct(closes: Sequence[float], bars: int) -> Optional[float]:
    if len(closes) < bars + 1:
        return None
    start = closes[-(bars + 1)]
    end = closes[-1]
    if start <= 0:
        return None
    return (end - start) / start * 100.0


def _as_ms(value: Any) -> Optional[float]:
    if value is None:
        return None
    if hasattr(value, "timestamp"):
        return float(value.timestamp()) * 1000.0
    num = _f(value)
    if num is None:
        return None
    if num < 1e12:
        return num * 1000.0
    return num


def _closed_bars(rows: Sequence[Dict[str, Any]], interval_ms: int, now_ms: float) -> List[Dict[str, float]]:
    out: List[Tuple[float, Dict[str, float]]] = []
    for row in rows:
        opened = _as_ms(row.get("open_time"))
        open_px = _f(row.get("open_price") if row.get("open_price") is not None else row.get("open"))
        close_px = _f(row.get("close_price") if row.get("close_price") is not None else row.get("close"))
        if opened is None or open_px is None or close_px is None:
            continue
        if open_px <= 0 or close_px <= 0:
            continue
        if opened + interval_ms > now_ms:
            continue
        out.append((opened, {"open": open_px, "close": close_px}))
    out.sort(key=lambda item: item[0])
    return [bar for _, bar in out]


def classify_pulse(
    bars_1h: Sequence[Dict[str, float]],
    bars_4h: Sequence[Dict[str, float]],
    bars_15m: Sequence[Dict[str, float]],
    change_24h: Optional[float],
    funding_rate: Optional[float],
) -> Dict[str, Any]:
    """用已收盘 K 线做判断。后续方向是规则推演，不是成交保证。"""
    c1 = [b["close"] for b in bars_1h]
    c4 = [b["close"] for b in bars_4h]
    c15 = [b["close"] for b in bars_15m]
    ret_1h = _return_pct(c1, 8)
    ret_4h = _return_pct(c4, 6)
    ret_15 = _return_pct(c15, 8)
    rsi = _rsi(c1, 14)
    last3 = list(bars_1h[-3:])
    red3 = len(last3) == 3 and all(b["close"] < b["open"] for b in last3)
    green3 = len(last3) == 3 and all(b["close"] > b["open"] for b in last3)

    chg = change_24h
    hot = (chg is not None and chg >= 3) or (rsi is not None and rsi >= 70)
    cold = (chg is not None and chg <= -3) or (rsi is not None and rsi <= 30)
    if hot and not cold:
        sentiment = "偏热"
    elif cold and not hot:
        sentiment = "偏冷"
    elif hot and cold:
        sentiment = "分歧"
    else:
        sentiment = "中性"
    if funding_rate is not None and funding_rate >= 0.0005:
        sentiment_note = "资金费率偏高，多头拥挤"
    elif funding_rate is not None and funding_rate <= -0.0003:
        sentiment_note = "资金费率偏低，空头拥挤"
    else:
        sentiment_note = "资金费率中性"

    up_1 = ret_1h is not None and ret_1h >= 1.2
    dn_1 = ret_1h is not None and ret_1h <= -1.2
    up_4 = ret_4h is not None and ret_4h >= 1.5
    dn_4 = ret_4h is not None and ret_4h <= -1.5
    if up_4:
        trend = "上升"
    elif dn_4 and not up_1:
        trend = "下跌"
    elif up_1:
        trend = "上升"
    elif dn_1:
        trend = "下跌"
    else:
        trend = "震荡"

    if ret_1h is None and ret_4h is None:
        outlook = "数据不足"
        outlook_note = "K 线不够，先不推演"
    elif trend == "上升" and (red3 or dn_1 or (rsi is not None and rsi >= 70 and not green3)):
        outlook = "冲高回落"
        outlook_note = "大方向仍强，但 1h 回落、连续收阴或 RSI 过热"
    elif trend == "上升":
        outlook = "趋势延续"
        outlook_note = "4h/1h 仍顺" + ("，RSI 已高" if rsi is not None and rsi >= 70 else "")
    elif trend == "下跌" and green3:
        outlook = "跌后反抽"
        outlook_note = "大方向仍弱，最近 3 根 1h 转阳"
    elif trend == "下跌":
        outlook = "延续下行"
        outlook_note = "4h/1h 仍弱，反抽还没形成"
    elif ret_15 is not None and ret_15 >= 1.0:
        outlook = "短线转强"
        outlook_note = "中周期震荡，15m 最近 8 根在抬"
    elif ret_15 is not None and ret_15 <= -1.0:
        outlook = "短线转弱"
        outlook_note = "中周期震荡，15m 最近 8 根在压"
    else:
        outlook = "区间震荡"
        outlook_note = "多周期没有同向趋势"

    return {
        "sentiment": sentiment,
        "sentiment_note": sentiment_note,
        "trend": trend,
        "outlook": outlook,
        "outlook_note": outlook_note,
        "rsi_1h": None if rsi is None else round(rsi, 1),
        "ret_1h_8": None if ret_1h is None else round(ret_1h, 2),
        "ret_4h_6": None if ret_4h is None else round(ret_4h, 2),
        "ret_15m_8": None if ret_15 is None else round(ret_15, 2),
    }


def _top_symbols() -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in load_config_yaml_symbols():
        canon = futures_symbol_rating_canonical(raw)
        if not canon or is_security(canon) or canon in seen:
            continue
        seen.add(canon)
        out.append(canon)
        if len(out) >= PULSE_LIMIT:
            break
    return out


def _fetch_tf(cur, symbols: Sequence[str], timeframe: str, keep: int) -> Dict[str, List[Dict[str, Any]]]:
    if not symbols:
        return {}
    placeholders = ",".join(["%s"] * len(symbols))
    cur.execute(
        f"""
        SELECT symbol, open_time, open_price, close_price
        FROM (
            SELECT symbol, open_time, open_price, close_price,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY open_time DESC) AS rn
            FROM kline_data
            WHERE timeframe=%s
              AND exchange='binance_futures'
              AND symbol IN ({placeholders})
        ) t
        WHERE rn <= %s
        """,
        (timeframe, *symbols, int(keep)),
    )
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in cur.fetchall() or []:
        grouped.setdefault(row["symbol"], []).append(row)
    return grouped


def build_market_pulse(force: bool = False) -> Dict[str, Any]:
    now = time.time()
    cached = _cache.get("payload")
    if not force and cached and now - float(_cache.get("ts") or 0) < _CACHE_TTL_S:
        return cached

    symbols = _top_symbols()
    now_ms = now * 1000.0
    rows_1h: Dict[str, List[Dict[str, Any]]] = {}
    rows_4h: Dict[str, List[Dict[str, Any]]] = {}
    rows_15: Dict[str, List[Dict[str, Any]]] = {}
    stats: Dict[str, Dict[str, Any]] = {}
    funding: Dict[str, float] = {}
    cfg = dict(get_db_config())
    cfg.setdefault("read_timeout", 40)
    conn = pymysql.connect(**cfg, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
    try:
        cur = conn.cursor()
        rows_1h = _fetch_tf(cur, symbols, "1h", 30)
        rows_4h = _fetch_tf(cur, symbols, "4h", 16)
        rows_15 = _fetch_tf(cur, symbols, "15m", 20)
        if symbols:
            placeholders = ",".join(["%s"] * len(symbols))
            cur.execute(
                f"""
                SELECT symbol, current_price, change_24h, quote_volume_24h
                FROM price_stats_24h
                WHERE symbol IN ({placeholders})
                """,
                tuple(symbols),
            )
            for row in cur.fetchall() or []:
                stats[row["symbol"]] = row
            try:
                cur.execute(
                    f"""
                    SELECT f.symbol, f.funding_rate
                    FROM funding_rate_data f
                    JOIN (
                        SELECT symbol, MAX(funding_time) AS funding_time
                        FROM funding_rate_data
                        WHERE symbol IN ({placeholders})
                        GROUP BY symbol
                    ) latest
                      ON latest.symbol = f.symbol AND latest.funding_time = f.funding_time
                    """,
                    tuple(symbols),
                )
                for row in cur.fetchall() or []:
                    rate = _f(row.get("funding_rate"))
                    if rate is not None:
                        funding[row["symbol"]] = rate
            except Exception as exc:
                logger.debug(f"[market pulse] funding skipped: {exc}")
    finally:
        conn.close()

    items = []
    for rank, symbol in enumerate(symbols, start=1):
        b1 = _closed_bars(rows_1h.get(symbol) or [], 3_600_000, now_ms)
        b4 = _closed_bars(rows_4h.get(symbol) or [], 14_400_000, now_ms)
        b15 = _closed_bars(rows_15.get(symbol) or [], 900_000, now_ms)
        st = stats.get(symbol) or {}
        chg = _f(st.get("change_24h"))
        judged = classify_pulse(b1, b4, b15, chg, funding.get(symbol))
        items.append({
            "rank": rank,
            "symbol": symbol,
            "base": symbol.split("/")[0],
            "price": _f(st.get("current_price")),
            "change_24h": None if chg is None else round(chg, 2),
            "quote_volume_24h": _f(st.get("quote_volume_24h")),
            "funding_rate": funding.get(symbol),
            **judged,
        })

    def _count(key: str, value: str) -> int:
        return sum(1 for item in items if item.get(key) == value)

    payload = {
        "ok": True,
        "universe": "市值前100",
        "count": len(items),
        "updated_at": int(now),
        "note": "情绪看 24h 涨跌、1h RSI 和资金费率。当前走势看 4h 与 1h。后续方向是这些指标的规则推演，不是预测保证。",
        "summary": {
            "trend_up": _count("trend", "上升"),
            "trend_down": _count("trend", "下跌"),
            "trend_range": _count("trend", "震荡"),
            "hot": _count("sentiment", "偏热"),
            "cold": _count("sentiment", "偏冷"),
            "continue_up": _count("outlook", "趋势延续"),
            "pullback": _count("outlook", "冲高回落"),
            "continue_down": _count("outlook", "延续下行"),
        },
        "items": items,
    }
    _cache["ts"] = now
    _cache["payload"] = payload
    return payload
