"""中线做多/做空 — L0/L1 标的池 + 24×1D / 60×1H 量化扫描与 LLM universe 构建."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from app.services.midline_swing_config import (
    MIDLINE_LLM_MAX_SYMBOLS,
    MIDLINE_MIN_SIGNAL_SCORE,
)
from app.services.securities_filter import is_security
from app.utils.futures_symbol import futures_symbol_clean, futures_symbol_rating_canonical


def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if not closes or len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _rsi_series(closes: List[float], period: int = 14) -> List[float]:
    out: List[float] = []
    for end in range(period + 1, len(closes) + 1):
        v = _rsi(closes[:end], period)
        if v is not None:
            out.append(v)
    return out


def load_l0_l1_symbols(conn) -> List[str]:
    """仅 L0 白名单 + L1 黑名单1级；未评级排除。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, rating_level FROM trading_symbol_rating
            WHERE rating_level IN (0, 1)
            ORDER BY rating_level ASC, symbol ASC
            """
        )
        rows = cur.fetchall()
    symbols = []
    ratings: Dict[str, int] = {}
    seen = set()
    for row in rows:
        sym = futures_symbol_rating_canonical(
            row["symbol"] if isinstance(row, dict) else row[0]
        )
        rl = int(row.get("rating_level") if isinstance(row, dict) else row[1])
        clean = futures_symbol_clean(sym)
        if clean and clean not in seen and not is_security(sym):
            seen.add(clean)
            symbols.append(sym)
            ratings[sym] = rl
    return symbols, ratings


def _fetch_klines(cur, symbol: str, timeframe: str, limit: int) -> List[Dict]:
    cur.execute(
        """
        SELECT open_time, open_price, high_price, low_price, close_price, volume
        FROM kline_data
        WHERE symbol=%s AND timeframe=%s AND exchange='binance_futures'
        ORDER BY open_time DESC LIMIT %s
        """,
        (symbol, timeframe, limit),
    )
    return list(reversed(cur.fetchall()))


def _bar_floats(rows: List[Dict]) -> Tuple[List[float], List[float], List[float], List[float]]:
    closes, highs, lows, vols = [], [], [], []
    for r in rows:
        closes.append(float(r["close_price"]))
        highs.append(float(r["high_price"]))
        lows.append(float(r["low_price"]))
        vols.append(float(r.get("volume") or 0))
    return closes, highs, lows, vols


def _score_long(closes_1d, lows_1d, closes_1h, highs_1h, lows_1h, vols_1h) -> Tuple[float, Dict[str, Any]]:
    detail: Dict[str, Any] = {}
    score = 0.0
    n1d = len(closes_1d)
    n1h = len(closes_1h)
    if n1d < 20 or n1h < 30:
        return 0.0, {"error": "insufficient_klines"}

    low_20d = min(lows_1d[-20:])
    last_close_1d = closes_1d[-1]
    dist_low_pct = (last_close_1d - low_20d) / low_20d * 100 if low_20d > 0 else 999
    detail["dist_from_20d_low_pct"] = round(dist_low_pct, 2)
    if dist_low_pct <= 8:
        score += 25
        detail["near_20d_low"] = True

    recent_5_low = min(lows_1d[-5:])
    prior_low = min(lows_1d[-20:-5]) if n1d >= 20 else min(lows_1d[:-5])
    stabilized = recent_5_low >= prior_low * 0.995
    detail["stabilized"] = stabilized
    if stabilized:
        score += 15

    rsi_vals = _rsi_series(closes_1h, 14)
    rsi_now = rsi_vals[-1] if rsi_vals else None
    rsi_min_recent = min(rsi_vals[-20:]) if len(rsi_vals) >= 20 else (min(rsi_vals) if rsi_vals else None)
    detail["rsi_1h"] = round(rsi_now, 1) if rsi_now is not None else None
    detail["rsi_1h_min_20"] = round(rsi_min_recent, 1) if rsi_min_recent is not None else None
    if (
        rsi_now is not None
        and rsi_min_recent is not None
        and rsi_min_recent < 38
        and 38 <= rsi_now <= 55
    ):
        score += 20
        detail["rsi_recovery"] = True

    if n1h >= 10:
        hl = lows_1h[-10:]
        higher_lows = sum(1 for i in range(1, len(hl)) if hl[i] >= hl[i - 1] * 0.998)
        detail["higher_lows_10h"] = higher_lows
        if higher_lows >= 6:
            score += 15

    if n1h >= 13:
        vol_recent = sum(vols_1h[-3:]) / 3
        vol_prior = sum(vols_1h[-13:-3]) / 10
        bull_bars = sum(1 for i in range(-3, 0) if closes_1h[i] >= closes_1h[i - 1])
        detail["vol_recent_avg"] = round(vol_recent, 4)
        detail["vol_prior_avg"] = round(vol_prior, 4)
        detail["bull_bars_3h"] = bull_bars
        if vol_prior > 0 and vol_recent > vol_prior * 1.2 and bull_bars >= 2:
            score += 25
            detail["volume_breakout"] = True

    detail["score"] = round(score, 1)
    return score, detail


def _score_short(closes_1d, highs_1d, closes_1h, highs_1h, lows_1h, vols_1h) -> Tuple[float, Dict[str, Any]]:
    detail: Dict[str, Any] = {}
    score = 0.0
    n1d = len(closes_1d)
    n1h = len(closes_1h)
    if n1d < 20 or n1h < 30:
        return 0.0, {"error": "insufficient_klines"}

    high_20d = max(highs_1d[-20:])
    last_close_1d = closes_1d[-1]
    dist_high_pct = (high_20d - last_close_1d) / high_20d * 100 if high_20d > 0 else 999
    detail["dist_from_20d_high_pct"] = round(dist_high_pct, 2)
    if dist_high_pct <= 5:
        score += 25
        detail["near_20d_high"] = True

    rsi_vals = _rsi_series(closes_1h, 14)
    rsi_now = rsi_vals[-1] if rsi_vals else None
    rsi_peak_5 = max(rsi_vals[-5:]) if len(rsi_vals) >= 5 else rsi_now
    detail["rsi_1h"] = round(rsi_now, 1) if rsi_now is not None else None
    detail["rsi_1h_peak_5"] = round(rsi_peak_5, 1) if rsi_peak_5 is not None else None
    if (
        rsi_now is not None
        and rsi_peak_5 is not None
        and rsi_peak_5 > 68
        and (rsi_peak_5 - rsi_now) >= 5
    ):
        score += 25
        detail["rsi_exhaustion"] = True

    if n1h >= 10:
        hh = highs_1h[-10:]
        weakening = sum(1 for i in range(1, len(hh)) if hh[i] <= hh[i - 1] * 1.002)
        detail["weakening_highs_10h"] = weakening
        if weakening >= 6:
            score += 15

    if n1h >= 5:
        last_vol = vols_1h[-1]
        avg_vol_10 = sum(vols_1h[-11:-1]) / 10 if n1h >= 11 else sum(vols_1h[:-1]) / max(n1h - 1, 1)
        bearish = closes_1h[-1] < closes_1h[-2]
        upper_wick = (
            (highs_1h[-1] - max(closes_1h[-1], closes_1h[-2]))
            / highs_1h[-1]
            * 100
            if highs_1h[-1] > 0
            else 0
        )
        detail["last_vol"] = round(last_vol, 4)
        detail["avg_vol_10"] = round(avg_vol_10, 4)
        detail["bearish_bar"] = bearish
        detail["upper_wick_pct"] = round(upper_wick, 2)
        vol_climax = avg_vol_10 > 0 and last_vol > avg_vol_10 * 1.5
        if vol_climax and (bearish or upper_wick > 1.5):
            score += 35
            detail["volume_climax_bearish"] = True

    detail["score"] = round(score, 1)
    return score, detail


def scan_universe(
    conn,
    profile: str,
    min_score: float = MIDLINE_MIN_SIGNAL_SCORE,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    扫描 L0/L1 池，返回按 score 降序的信号列表。
    profile: 'long' | 'short'
    """
    symbols, _ = load_l0_l1_symbols(conn)
    universe_size = len(symbols)
    signals: List[Dict[str, Any]] = []
    profile_l = profile.strip().lower()

    with conn.cursor() as cur:
        for symbol in symbols:
            try:
                rows_1d = _fetch_klines(cur, symbol, "1d", 24)
                rows_1h = _fetch_klines(cur, symbol, "1h", 60)
                if len(rows_1d) < 20 or len(rows_1h) < 30:
                    continue
                c1d, h1d, l1d, _ = _bar_floats(rows_1d)
                c1h, h1h, l1h, v1h = _bar_floats(rows_1h)
                if profile_l == "long":
                    score, detail = _score_long(c1d, l1d, c1h, h1h, l1h, v1h)
                    side = "LONG"
                elif profile_l == "short":
                    score, detail = _score_short(c1d, h1d, c1h, h1h, l1h, v1h)
                    side = "SHORT"
                else:
                    raise ValueError(f"unknown profile {profile}")
                if score < min_score:
                    continue
                signals.append({
                    "symbol": symbol,
                    "side": side,
                    "score": score,
                    "signal_detail": detail,
                    "ref_price": c1h[-1],
                })
            except Exception as e:
                logger.debug(f"[中线扫描] {symbol} 跳过: {e}")
                continue

    signals.sort(key=lambda x: x["score"], reverse=True)
    return signals, universe_size


def build_midline_universe(
    conn,
    profile: str,
    max_symbols: int = MIDLINE_LLM_MAX_SYMBOLS,
) -> Tuple[Dict[str, Any], Dict[str, Any], int]:
    """
    构建送 LLM 的 universe：L0/L1 全池量化预评分后取 TOP N，附 1D/1H K 线叙事。
  返回 (universe_dict, meta, universe_total).
    """
    from app.services.data_cache_service import _make_kline_narrative

    symbols, ratings = load_l0_l1_symbols(conn)
    universe_total = len(symbols)
    ranked: List[Dict[str, Any]] = []
    profile_l = profile.strip().lower()

    with conn.cursor() as cur:
        for symbol in symbols:
            try:
                rows_1d = _fetch_klines(cur, symbol, "1d", 24)
                rows_1h = _fetch_klines(cur, symbol, "1h", 60)
                if len(rows_1d) < 20 or len(rows_1h) < 30:
                    continue
                c1d, h1d, l1d, _ = _bar_floats(rows_1d)
                c1h, h1h, l1h, v1h = _bar_floats(rows_1h)
                if profile_l == "long":
                    score, detail = _score_long(c1d, l1d, c1h, h1h, l1h, v1h)
                elif profile_l == "short":
                    score, detail = _score_short(c1d, h1d, c1h, h1h, l1h, v1h)
                else:
                    raise ValueError(f"unknown profile {profile}")

                rsi_vals = _rsi_series(c1h, 14)
                rsi_now = rsi_vals[-1] if rsi_vals else None
                price = float(c1h[-1])
                rl = ratings.get(symbol, 1)

                sym_data = {
                    "symbol": symbol,
                    "current_price": price,
                    "rating_level": rl,
                    "quant_score": round(score, 1),
                    "quant_detail": detail,
                    "triggers": [f"L{rl}评级", f"量化分{score:.0f}"],
                    "kline_narrative": {
                        "1d": _make_kline_narrative(rows_1d, "1d"),
                        "1h": _make_kline_narrative(rows_1h, "1h"),
                    },
                    "tech": {
                        "rsi_1h": round(rsi_now, 1) if rsi_now is not None else None,
                    },
                }
                ranked.append({"symbol": symbol, "score": score, "sym_data": sym_data})
            except Exception as e:
                logger.debug(f"[中线universe] {symbol} 跳过: {e}")

    ranked.sort(key=lambda x: x["score"], reverse=True)
    selected = ranked[:max_symbols]
    universe = {item["symbol"]: item["sym_data"] for item in selected}
    meta = {
        "universe_total": universe_total,
        "llm_symbol_count": len(selected),
        "llm_symbols_truncated": universe_total > len(selected),
        "profile": profile_l,
    }
    if meta["llm_symbols_truncated"]:
        logger.info(
            f"[中线universe] LLM 候选截断: 全池 {universe_total} → 送模 {len(selected)}"
        )
    return universe, meta, universe_total


def signal_detail_json(detail: Dict[str, Any]) -> str:
    return json.dumps(detail, ensure_ascii=False)
