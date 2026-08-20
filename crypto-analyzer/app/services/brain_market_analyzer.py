"""REQ-BRAIN 自有行情分析 — 1H 大方向 + 15M 结构 + Big4 闸门 + 插针。"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

_BIG4_BIAS_CACHE = {"ts": 0.0, "bias": "FLAT"}
_BIG4_BIAS_TTL_S = 30.0

from app.services.brain_config import (
    BARS_15M_DAY,
    BARS_15M_WICK_7D,
    BARS_1H_WEEK,
    BIG4_SYMBOLS,
    BIG4_WEAK_ABS_CHANGE_PCT,
    BIG4_WEAK_REL_VOLUME,
)
from app.services.brain_wick import analyze_wicks, limit_offset_pct_from_wicks
from app.utils.futures_symbol import futures_symbol_rating_canonical


def _fetch_klines(cur, symbol: str, timeframe: str, limit: int) -> List[Dict]:
    sym = futures_symbol_rating_canonical(symbol)
    # DB 可能存 BTC/USDT 或 BTCUSDT
    variants = [sym, sym.replace("/", ""), sym.replace("USDT", "/USDT") if "/" not in sym else sym]
    seen = set()
    for v in variants:
        if v in seen:
            continue
        seen.add(v)
        cur.execute(
            """
            SELECT open_time, open_price, high_price, low_price, close_price, volume
            FROM kline_data
            WHERE symbol=%s AND timeframe=%s AND exchange='binance_futures'
            ORDER BY open_time DESC LIMIT %s
            """,
            (v, timeframe, limit),
        )
        rows = cur.fetchall()
        if rows:
            return list(reversed(rows))
    return []


def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l <= 1e-12:
        return 100.0
    rs = avg_g / avg_l
    return 100.0 - (100.0 / (1.0 + rs))


def _trend_side(closes: List[float], lookback: int) -> Tuple[str, Dict[str, Any]]:
    """FLAT / LONG / SHORT from close vs lookback MA bias."""
    detail: Dict[str, Any] = {}
    if len(closes) < lookback:
        return "FLAT", {"reason": "insufficient_bars", "need": lookback, "got": len(closes)}
    c0, c1 = closes[-lookback], closes[-1]
    if c0 <= 0:
        return "FLAT", {"reason": "bad_price"}
    change_pct = (c1 - c0) / c0 * 100.0
    ma = sum(closes[-min(24, lookback):]) / min(24, lookback)
    detail["change_pct"] = round(change_pct, 3)
    detail["ma"] = round(ma, 8)
    detail["last"] = round(c1, 8)
    # 阈值：1h 周线用 ±1.2%；15m 日线用 ±0.6%
    thr = 1.2 if lookback >= 100 else 0.6
    detail["threshold_pct"] = thr
    if change_pct >= thr and c1 >= ma:
        return "LONG", detail
    if change_pct <= -thr and c1 <= ma:
        return "SHORT", detail
    detail["reason"] = "no_clear_trend"
    return "FLAT", detail


def evaluate_big4_gate(cur) -> Dict[str, Any]:
    """
    Big4 疲软：多数币近 6×1h |涨跌| 低 且 相对成交量低 → big4_ok=False。
    另给出宏观方向 bias: LONG/SHORT/FLAT。
    """
    per: List[Dict[str, Any]] = []
    weak_n = 0
    bull_n = 0
    bear_n = 0
    for raw in BIG4_SYMBOLS:
        rows = _fetch_klines(cur, raw, "1h", BARS_1H_WEEK)
        if len(rows) < 48:
            per.append({"symbol": raw, "ok": False, "reason": "insufficient_1h"})
            weak_n += 1
            continue
        closes = [float(r["close_price"]) for r in rows]
        vols = [float(r.get("volume") or 0) for r in rows]
        # 近 6h 动量
        c6 = closes[-7] if len(closes) >= 7 else closes[0]
        chg6 = (closes[-1] - c6) / c6 * 100.0 if c6 > 0 else 0.0
        # 相对成交量：近 6 根均量 / 近 168 根均量
        v_recent = sum(vols[-6:]) / 6 if len(vols) >= 6 else 0.0
        v_base = sum(vols) / len(vols) if vols else 1.0
        rel_vol = (v_recent / v_base) if v_base > 0 else 0.0
        mom_weak = abs(chg6) < BIG4_WEAK_ABS_CHANGE_PCT
        vol_weak = rel_vol < BIG4_WEAK_REL_VOLUME
        is_weak = mom_weak and vol_weak
        if is_weak:
            weak_n += 1
        if chg6 >= BIG4_WEAK_ABS_CHANGE_PCT:
            bull_n += 1
        elif chg6 <= -BIG4_WEAK_ABS_CHANGE_PCT:
            bear_n += 1
        per.append({
            "symbol": raw,
            "change_6h_pct": round(chg6, 3),
            "rel_volume": round(rel_vol, 3),
            "mom_weak": mom_weak,
            "vol_weak": vol_weak,
            "weak": is_weak,
        })

    # ≥3/4 疲软 → 宏观不可交易
    big4_ok = weak_n < 3
    if bull_n >= 3:
        bias = "LONG"
    elif bear_n >= 3:
        bias = "SHORT"
    else:
        bias = "FLAT"

    return {
        "big4_ok": big4_ok,
        "bias": bias,
        "weak_count": weak_n,
        "bull_count": bull_n,
        "bear_count": bear_n,
        "per_coin": per,
        "reason": "" if big4_ok else "big4_weak_low_momentum_low_volume",
    }


def cached_big4_bias(conn=None) -> str:
    """最近一轮 BRAIN 扫描的 Big4 方向；30s 缓存，失败时沿用上次或 FLAT。"""
    now = time.time()
    cached_ts = float(_BIG4_BIAS_CACHE.get("ts") or 0.0)
    cached_bias = str(_BIG4_BIAS_CACHE.get("bias") or "FLAT").upper() or "FLAT"
    if now - cached_ts < _BIG4_BIAS_TTL_S:
        return cached_bias

    own = False
    try:
        if conn is None:
            import pymysql
            from app.utils.config_loader import get_db_config

            conn = pymysql.connect(
                **get_db_config(),
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
            )
            own = True
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT big4_bias FROM brain_scan_rounds "
                "WHERE big4_bias IS NOT NULL AND big4_bias <> '' "
                "ORDER BY id DESC LIMIT 1"
            )
            row = cur.fetchone()
        finally:
            cur.close()
        bias = "FLAT"
        if row:
            if isinstance(row, dict):
                bias = str(row.get("big4_bias") or "FLAT")
            else:
                bias = str(row[0] or "FLAT")
        bias = bias.upper() or "FLAT"
        _BIG4_BIAS_CACHE["ts"] = now
        _BIG4_BIAS_CACHE["bias"] = bias
        return bias
    except Exception as exc:
        logger.debug(f"[big4] cached bias fallback {cached_bias}: {exc}")
        return cached_bias
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def analyze_symbol(cur, symbol: str, big4: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """单币分析 → side / rationale / wick / aligned / big4_ok。"""
    symbol = futures_symbol_rating_canonical(symbol)
    if big4 is None:
        big4 = evaluate_big4_gate(cur)

    out: Dict[str, Any] = {
        "symbol": symbol,
        "side": "FLAT",
        "big4_ok": bool(big4.get("big4_ok")),
        "aligned": False,
        "edge_score": 0.0,
        "rationale": "",
        "h1": {},
        "m15": {},
        "wick": {},
        "limit_offset_pct": 0.5,
        "forbid_market": False,
        "ref_price": None,
    }

    if not out["big4_ok"]:
        out["rationale"] = f"Big4疲软: {big4.get('reason')}"
        return out

    rows_1h = _fetch_klines(cur, symbol, "1h", BARS_1H_WEEK)
    rows_15m = _fetch_klines(cur, symbol, "15m", max(BARS_15M_DAY, BARS_15M_WICK_7D))
    if len(rows_1h) < 48 or len(rows_15m) < 32:
        out["rationale"] = "K线不足"
        return out

    closes_1h = [float(r["close_price"]) for r in rows_1h]
    closes_15 = [float(r["close_price"]) for r in rows_15m]
    side_1h, d1 = _trend_side(closes_1h, min(BARS_1H_WEEK, len(closes_1h)))
    side_15, d15 = _trend_side(closes_15[-BARS_15M_DAY:], min(BARS_15M_DAY, len(closes_15)))
    out["h1"] = {"side": side_1h, **d1}
    out["m15"] = {"side": side_15, **d15}
    out["ref_price"] = float(closes_15[-1])
    rsi = _rsi(closes_1h, 14)
    out["rsi_1h"] = round(rsi, 1) if rsi is not None else None

    # 插针：近 7 日 15m
    wick_bars = rows_15m[-BARS_15M_WICK_7D:] if len(rows_15m) >= BARS_15M_WICK_7D else rows_15m
    wick = analyze_wicks(wick_bars)
    out["wick"] = wick
    out["forbid_market"] = bool(wick.get("frequent"))
    out["limit_offset_pct"] = 0.5

    bias = big4.get("bias") or "FLAT"
    # 对齐：1H 定调，15M 同向确认；且与 Big4 bias 一致
    token_side = "FLAT"
    if side_1h == side_15 and side_1h in ("LONG", "SHORT"):
        token_side = side_1h
    elif side_1h in ("LONG", "SHORT") and side_15 == "FLAT":
        # 大方向明确、15m 中性：仍给方向但 edge 较低
        token_side = side_1h

    aligned = token_side != "FLAT" and bias == token_side
    out["aligned"] = aligned

    if not aligned:
        out["side"] = "FLAT"
        out["rationale"] = (
            f"未对齐 big4={bias} h1={side_1h} m15={side_15}"
        )
        return out

    out["side"] = token_side
    if wick.get("frequent"):
        out["limit_offset_pct"] = limit_offset_pct_from_wicks(token_side, wick)
    # edge：趋势幅度 + RSI 顺向
    chg = abs(float(d1.get("change_pct") or 0))
    edge = min(1.0, chg / 5.0)
    if rsi is not None:
        if token_side == "LONG" and 40 <= rsi <= 68:
            edge = min(1.0, edge + 0.15)
        elif token_side == "SHORT" and 32 <= rsi <= 60:
            edge = min(1.0, edge + 0.15)
        else:
            edge *= 0.7
    out["edge_score"] = round(edge, 3)
    out["rationale"] = (
        f"{token_side} h1Δ{d1.get('change_pct')}% m15={side_15} "
        f"rsi={out['rsi_1h']} wick_freq={wick.get('wick_ratio')} "
        f"offset={out['limit_offset_pct']}%"
    )
    return out
