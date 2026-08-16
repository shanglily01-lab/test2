"""Breakout opportunity scanner: Top50 universe, multi-period context, 15m break levels.

midline_* sources and tables are retained only for historical order/position/API compatibility.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from app.services.securities_filter import is_security
from app.utils.futures_symbol import futures_symbol_clean, futures_symbol_rating_canonical

MIDLINE_TOP50_LIMIT = 50
MIDLINE_BIG_SYMBOLS = ("BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT")
PLAIN_USDT_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,24}/USDT$")
BREAKOUT_LOOKBACK_15M = 32
BREAKOUT_RECENT_BARS = 4
BREAKDOWN_SUPPORT_BUFFER = 0.0015
BREAKOUT_RESIST_BUFFER = 0.0015
BREAKOUT_VOL_RATIO_MIN = 1.12
BREAKDOWN_1H_DROP_MIN_PCT = 0.45
BREAKOUT_1H_RISE_MIN_PCT = 0.45
BREAKDOWN_4H_DROP_MIN_PCT = 0.80
BREAKOUT_4H_RISE_MIN_PCT = 0.80
PHASE_RANGE_MIN_PCT = 0.35
PHASE_RANGE_MAX_PCT = 4.80
PHASE_BREAK_MAX_4H_MOVE_PCT = 4.50
PHASE_MAJOR_TREND_SCORE_MIN = 2.20
PHASE_MAJOR_TREND_STRONG_SCORE = 3.20
PHASE_OPPOSITE_SCORE_MAX = 2.50
PHASE_STRONG_BREAK_PCT = 0.35
FUTURE_4H_OPPORTUNITY_SCORE_MIN = 0.50
BREAKOUT_ACTION_SCORE_MIN = 82.0
TREND_WINDOWS = (
    ("cycle", "cycle", 120, "1d"),
    ("m3", "last_3_months", 90, "1d"),
    ("m1", "last_1_month", 30, "1d"),
    ("d7", "last_7_days", 7, "1d"),
    ("d1", "last_24h", 96, "15m"),
)
FUTURE_4H_LABEL = "next_4h"


def _is_plain_usdt_symbol(symbol: str) -> bool:
    """Only Binance-style crypto USDT pairs, e.g. BTC/USDT or 1000PEPE/USDT."""
    return bool(PLAIN_USDT_SYMBOL_RE.fullmatch(futures_symbol_rating_canonical(symbol or "")))


def load_config_yaml_symbols() -> List[str]:
    """Read USDT pairs from config.yaml and normalize to BTCUSDT format."""
    try:
        import yaml
    except ImportError:
        logger.error("[breakout scanner] PyYAML is missing")
        return []

    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    if not config_path.exists():
        logger.error(f"[breakout scanner] config not found: {config_path}")
        return []

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    raw = config.get("symbols") or []
    out: List[str] = []
    seen = set()
    for s in raw:
        if not isinstance(s, str):
            continue
        s = s.strip()
        if not s.endswith("/USDT"):
            continue
        binance = s.replace("/", "")
        canon = futures_symbol_rating_canonical(binance)
        clean = futures_symbol_clean(canon)
        if not clean or clean in seen or not _is_plain_usdt_symbol(canon) or is_security(canon):
            continue
        seen.add(clean)
        out.append(canon)
    return out


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


def _ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _window_direction(closes: List[float], highs: List[float], lows: List[float]) -> Dict[str, Any]:
    if len(closes) < 2:
        return {"side": "FLAT", "score": 0.0, "reason": "insufficient"}
    first, last = closes[0], closes[-1]
    if first <= 0:
        return {"side": "FLAT", "score": 0.0, "reason": "bad_price"}
    change = (last - first) / first * 100.0
    hi, lo = max(highs), min(lows)
    pos = 0.5 if hi <= lo else (last - lo) / (hi - lo)
    ema20 = _ema(closes, min(20, len(closes)))
    side = "FLAT"
    if change >= 3.0 and pos >= 0.45 and (ema20 is None or last >= ema20):
        side = "LONG"
    elif change <= -3.0 and pos <= 0.55 and (ema20 is None or last <= ema20):
        side = "SHORT"
    elif change >= 1.2 and pos >= 0.55:
        side = "LONG"
    elif change <= -1.2 and pos <= 0.45:
        side = "SHORT"
    score = min(1.0, abs(change) / 12.0 + abs(pos - 0.5))
    return {
        "side": side,
        "score": round(score, 3),
        "change_pct": round(change, 2),
        "range_pos": round(pos, 3),
        "last": round(last, 8),
        "ema20": round(ema20, 8) if ema20 else None,
    }


def _future_4h_direction(
    closes_15m: List[float],
    highs_15m: List[float],
    lows_15m: List[float],
    vols_15m: List[float],
) -> Dict[str, Any]:
    """Estimate next 4h directional bias from the latest 15m structure."""
    if len(closes_15m) < 32:
        return {"label": FUTURE_4H_LABEL, "side": "FLAT", "score": 0.0, "reason": "insufficient_15m"}

    last = closes_15m[-1]
    prev_4h = closes_15m[-17]
    prev_8h = closes_15m[-33] if len(closes_15m) >= 33 else closes_15m[0]
    if prev_4h <= 0 or prev_8h <= 0:
        return {"label": FUTURE_4H_LABEL, "side": "FLAT", "score": 0.0, "reason": "bad_price"}

    change_4h = (last - prev_4h) / prev_4h * 100.0
    change_8h = (last - prev_8h) / prev_8h * 100.0
    ema_fast = _ema(closes_15m[-32:], 8)
    ema_slow = _ema(closes_15m[-32:], 21)
    prev_hi = max(highs_15m[-32:-4])
    prev_lo = min(lows_15m[-32:-4])
    span = max(prev_hi - prev_lo, 0.0)
    range_pos = 0.5 if span <= 0 else (last - prev_lo) / span
    vol_recent = sum(vols_15m[-4:]) / 4 if len(vols_15m) >= 4 else 0.0
    vol_prior = sum(vols_15m[-16:-4]) / 12 if len(vols_15m) >= 16 else vol_recent
    vol_ratio = vol_recent / vol_prior if vol_prior > 0 else 1.0

    long_score = 0.0
    short_score = 0.0
    if ema_fast is not None and ema_slow is not None:
        if last >= ema_fast >= ema_slow:
            long_score += 1.2
        if last <= ema_fast <= ema_slow:
            short_score += 1.2
    if change_4h > 0.35:
        long_score += min(1.4, change_4h / 1.5)
    elif change_4h < -0.35:
        short_score += min(1.4, abs(change_4h) / 1.5)
    if change_8h > 0.6:
        long_score += 0.8
    elif change_8h < -0.6:
        short_score += 0.8
    if last > prev_hi * 1.001:
        long_score += 1.0
    if last < prev_lo * 0.999:
        short_score += 1.0
    if range_pos >= 0.68:
        long_score += 0.4
    elif range_pos <= 0.32:
        short_score += 0.4
    if vol_ratio >= 1.15:
        if change_4h > 0:
            long_score += 0.3
        elif change_4h < 0:
            short_score += 0.3

    side = "FLAT"
    diff = long_score - short_score
    if diff >= 0.9:
        side = "LONG"
    elif diff <= -0.9:
        side = "SHORT"
    score = min(1.0, abs(diff) / 3.2)
    return {
        "label": FUTURE_4H_LABEL,
        "side": side,
        "score": round(score, 3),
        "change_4h_pct": round(change_4h, 2),
        "change_8h_pct": round(change_8h, 2),
        "range_pos": round(range_pos, 3),
        "vol_ratio": round(vol_ratio, 3),
        "ema_fast": round(ema_fast, 8) if ema_fast else None,
        "ema_slow": round(ema_slow, 8) if ema_slow else None,
    }


def _breakout_action_opportunity(
    *,
    side: str,
    playbook: str,
    edge: float,
    confirmed: bool,
    signals: set,
    features: Dict[str, Any],
    future_4h: Dict[str, Any],
    big4_bias: str,
    global_name: str,
    entry_15m: Dict[str, Any],
) -> Dict[str, Any]:
    """Return the actionable 4h breakout decision; score is only evidence, not the gate."""
    side_u = (side or "").upper()
    playbook_u = (playbook or "").upper()
    future_side = str(future_4h.get("side") or "FLAT").upper()
    future_score = float(future_4h.get("score") or 0.0)
    token_aligned = features.get("h1_side") == side_u and features.get("m15_side") == side_u
    entry_fresh = bool(entry_15m.get("fresh_breakout"))

    detail: Dict[str, Any] = {
        "should_open": False,
        "reason": None,
        "mode": "shadow_only",
        "action_score": 0.0,
        "future_side": future_side,
        "future_score": round(future_score, 3),
        "entry_15m": entry_15m,
    }

    if playbook_u not in {"A1", "A2", "C1", "C3"}:
        detail["reason"] = "not_breakout_playbook"
        return detail
    if not confirmed:
        detail["reason"] = "unconfirmed_playbook"
        return detail
    if future_side != side_u or future_score < FUTURE_4H_OPPORTUNITY_SCORE_MIN:
        detail["reason"] = "future_4h_not_actionable"
        return detail

    if side_u == "SHORT":
        evidence = {
            "break_support": "break_support" in signals,
            "volume_expand_down": "volume_expand_down" in signals,
            "crash_spike": "crash_spike" in signals,
            "ema_reject": "ema_reject" in signals,
            "lower_high": "15m_lower_high" in signals,
            "ema_bear": bool(features.get("ema_bear")),
            "token_aligned": token_aligned,
            "entry_fresh": entry_fresh,
        }
        if playbook_u == "C1":
            structure_ok = evidence["break_support"] or entry_fresh
            force_ok = evidence["volume_expand_down"] or evidence["crash_spike"] or evidence["token_aligned"]
        else:
            structure_ok = evidence["ema_bear"] and (evidence["lower_high"] or evidence["ema_reject"])
            force_ok = evidence["volume_expand_down"] or evidence["token_aligned"] or bool(features.get("vol_shrink_pullback"))
    else:
        evidence = {
            "break_resistance": "break_resistance" in signals,
            "volume_expand_up": "volume_expand_up" in signals,
            "pump_spike": "pump_spike" in signals,
            "h1_breakout_up": "h1_breakout_up" in signals,
            "impulse_up": "impulse_up" in signals,
            "ema_reclaim": "ema_reclaim" in signals,
            "higher_low": "15m_higher_low" in signals,
            "ema_bull": bool(features.get("ema_bull")),
            "hh_hl": bool(features.get("hh_hl")),
            "volume_shrink_pullback": bool(features.get("vol_shrink_pullback")),
            "token_aligned": token_aligned,
            "entry_fresh": entry_fresh,
        }
        if playbook_u == "A1":
            structure_ok = (
                (evidence["ema_bull"] and (evidence["hh_hl"] or evidence["higher_low"]))
                or entry_fresh
                or token_aligned
            )
            force_ok = (
                (evidence["token_aligned"] and (evidence["volume_expand_up"] or evidence["volume_shrink_pullback"]))
                or edge >= 0.90
                or evidence["pump_spike"]
            )
        else:
            structure_ok = evidence["break_resistance"] or evidence["h1_breakout_up"] or evidence["impulse_up"] or entry_fresh
            force_ok = evidence["volume_expand_up"] or evidence["pump_spike"] or evidence["impulse_up"] or evidence["token_aligned"]

    detail["evidence"] = evidence
    if not structure_ok:
        detail["reason"] = "breakout_structure_missing"
        return detail
    if not force_ok:
        detail["reason"] = "breakout_force_missing"
        return detail
    if big4_bias in {"LONG", "SHORT"} and big4_bias != side_u and not token_aligned:
        detail["reason"] = "big4_opposes_without_token_alignment"
        return detail

    action_score = edge * 100.0 + future_score * 20.0
    action_score += sum(1 for ok in evidence.values() if ok) * 4.0
    if big4_bias == side_u:
        action_score += 6.0
    if global_name in {"BEAR_TREND", "CRASH_DOWN", "DAILY_BEAR_PROBE"} and side_u == "SHORT":
        action_score += 5.0
    if global_name in {"BULL_TREND", "BULL_RECOVERY"} and side_u == "LONG":
        action_score += 5.0

    detail["action_score"] = round(min(100.0, action_score), 1)
    if action_score < BREAKOUT_ACTION_SCORE_MIN:
        detail["reason"] = "action_score_below_threshold"
        return detail

    detail["should_open"] = True
    detail["reason"] = "high_confidence_4h_breakout_opportunity"
    detail["mode"] = "open_limit_order"
    return detail


def _fetch_window(cur, symbol: str, key: str) -> Tuple[List[float], List[float], List[float], List[float]]:
    _, _, limit, timeframe = next(w for w in TREND_WINDOWS if w[0] == key)
    rows = _fetch_klines(cur, symbol, timeframe, limit)
    return _bar_floats(rows) if rows else ([], [], [], [])


def load_midline_universe(conn) -> List[str]:
    """Top50 liquid crypto universe. Market-cap rank can replace this query later."""
    symbols: List[str] = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT symbol
                FROM price_stats_24h
                WHERE symbol LIKE '%%/USDT'
                  AND quote_volume_24h IS NOT NULL
                  AND quote_volume_24h > 0
                ORDER BY quote_volume_24h DESC
                LIMIT 120
                """
            )
            for row in cur.fetchall() or []:
                raw = row.get("symbol") if isinstance(row, dict) else row[0]
                canon = futures_symbol_rating_canonical(str(raw or ""))
                if canon and _is_plain_usdt_symbol(canon) and not is_security(canon):
                    symbols.append(canon)
    except Exception as e:
        logger.warning(f"[breakout scanner] failed to read liquid Top50, fallback to config pool: {e}")
        symbols = load_config_yaml_symbols()

    try:
        from app.services.trading_gates import load_trading_forbidden_symbols
        banned = load_trading_forbidden_symbols(conn) or set()
    except Exception as e:
        logger.warning(f"[breakout scanner] failed to read forbidden symbols: {e}")
        banned = set()
    banned_clean = {futures_symbol_clean(futures_symbol_rating_canonical(b)) for b in banned}

    filtered: List[str] = []
    seen = set()
    for sym in symbols:
        clean = futures_symbol_clean(sym)
        if (
            not clean
            or clean in seen
            or clean in banned_clean
            or not _is_plain_usdt_symbol(sym)
            or is_security(sym)
        ):
            continue
        seen.add(clean)
        filtered.append(sym)
        if len(filtered) >= MIDLINE_TOP50_LIMIT:
            break
    return filtered


def evaluate_global_trend_dimensions(cur) -> Dict[str, Any]:
    dims = []
    votes = {"LONG": 0.0, "SHORT": 0.0, "FLAT": 0.0}
    for key, label, _, _ in TREND_WINDOWS:
        coins = []
        for sym in MIDLINE_BIG_SYMBOLS:
            c, h, l, _ = _fetch_window(cur, sym, key)
            if not c:
                continue
            d = _window_direction(c, h, l)
            coins.append({"symbol": futures_symbol_rating_canonical(sym), **d})
            votes[d["side"]] += max(0.1, float(d.get("score") or 0))
        long_n = sum(1 for x in coins if x["side"] == "LONG")
        short_n = sum(1 for x in coins if x["side"] == "SHORT")
        side = "SHORT" if short_n >= 3 else "LONG" if long_n >= 3 else "FLAT"
        dims.append({"key": key, "label": label, "side": side, "coins": coins})

    future_coins = []
    for sym in MIDLINE_BIG_SYMBOLS:
        c, h, l, v = _fetch_window(cur, sym, "d1")
        if not c:
            continue
        d = _future_4h_direction(c, h, l, v)
        future_coins.append({"symbol": futures_symbol_rating_canonical(sym), **d})
        votes[d["side"]] += max(0.1, float(d.get("score") or 0))
    future_long_n = sum(1 for x in future_coins if x["side"] == "LONG")
    future_short_n = sum(1 for x in future_coins if x["side"] == "SHORT")
    future_side = "SHORT" if future_short_n >= 3 else "LONG" if future_long_n >= 3 else "FLAT"
    dims.append({"key": "future_4h", "label": FUTURE_4H_LABEL, "side": future_side, "coins": future_coins})

    bias = "SHORT" if votes["SHORT"] > votes["LONG"] * 1.15 else "LONG" if votes["LONG"] > votes["SHORT"] * 1.15 else "FLAT"
    return {"bias": bias, "dimensions": dims, "votes": votes}


def _major_trend_context(
    dims: Dict[str, Any],
    global_bias: str,
    side: str,
) -> Tuple[bool, Dict[str, Any]]:
    opposite = "SHORT" if side == "LONG" else "LONG"
    key_sides = {k: (dims.get(k) or {}).get("side", "FLAT") for k in ("cycle", "m3", "m1", "d7", "d1")}
    weights = {"cycle": 0.8, "m3": 1.4, "m1": 1.4, "d7": 1.1, "d1": 0.9}
    trend_score = sum(weights[k] for k, v in key_sides.items() if v == side)
    opposite_score = sum(weights[k] for k, v in key_sides.items() if v == opposite)
    flat_score = sum(weights[k] for k, v in key_sides.items() if v == "FLAT")
    if global_bias == side:
        trend_score += 0.8
    elif global_bias == opposite:
        opposite_score += 0.8

    core_trend_ok = key_sides["m3"] == side or key_sides["m1"] == side
    short_cycle_pullback = key_sides["d1"] == opposite and key_sides["m1"] == side and key_sides["d7"] != opposite
    strong_context = trend_score >= PHASE_MAJOR_TREND_STRONG_SCORE
    detail = {
        "layer": "major_trend_context",
        "side": side,
        "global_bias": global_bias,
        "trend_score": round(trend_score, 2),
        "opposite_score": round(opposite_score, 2),
        "flat_score": round(flat_score, 2),
        "core_trend_ok": core_trend_ok,
        "short_cycle_pullback": short_cycle_pullback,
        "dimension_sides": key_sides,
    }

    if not core_trend_ok:
        detail["reason"] = "major_core_trend_missing"
        return False, detail
    if trend_score < PHASE_MAJOR_TREND_SCORE_MIN:
        detail["reason"] = "major_trend_not_aligned"
        return False, detail
    if opposite_score > PHASE_OPPOSITE_SCORE_MAX and not strong_context:
        detail["reason"] = "major_trend_too_mixed"
        return False, detail
    if key_sides["cycle"] == opposite and key_sides["m3"] != side:
        detail["reason"] = "cycle_trend_opposes_phase"
        return False, detail
    if key_sides["d1"] == opposite and not short_cycle_pullback and not strong_context:
        detail["reason"] = "latest_day_opposes_without_pullback_context"
        return False, detail
    if global_bias == opposite and not strong_context:
        detail["reason"] = "global_trend_opposes_phase"
        return False, detail

    detail["passed"] = True
    return True, detail


def _entry_15m_opportunity(
    closes_15m: List[float],
    highs_15m: List[float],
    lows_15m: List[float],
    vols_15m: List[float],
    side: str,
) -> Tuple[bool, Dict[str, Any]]:
    detail: Dict[str, Any] = {"layer": "entry_15m_opportunity"}
    if len(closes_15m) < BREAKOUT_LOOKBACK_15M:
        detail["reason"] = "insufficient_15m"
        return False, detail
    last = closes_15m[-1]
    prev_hi = max(highs_15m[-BREAKOUT_LOOKBACK_15M:-BREAKOUT_RECENT_BARS])
    prev_lo = min(lows_15m[-BREAKOUT_LOOKBACK_15M:-BREAKOUT_RECENT_BARS])
    recent_hi = max(highs_15m[-BREAKOUT_RECENT_BARS:])
    recent_lo = min(lows_15m[-BREAKOUT_RECENT_BARS:])
    vol_recent = sum(vols_15m[-BREAKOUT_RECENT_BARS:]) / BREAKOUT_RECENT_BARS
    vol_prior = sum(vols_15m[-16:-4]) / 12 if len(vols_15m) >= 16 else vol_recent
    vol_ratio = vol_recent / vol_prior if vol_prior > 0 else 1.0
    prev_1h = closes_15m[-5]
    prev_4h = closes_15m[-17]
    change_1h = (last - prev_1h) / prev_1h * 100.0 if prev_1h > 0 else 0.0
    change_4h = (last - prev_4h) / prev_4h * 100.0 if prev_4h > 0 else 0.0
    break_down_pct = (prev_lo - last) / prev_lo * 100.0 if prev_lo > 0 else 0.0
    break_up_pct = (last - prev_hi) / prev_hi * 100.0 if prev_hi > 0 else 0.0
    phase_range_pct = (prev_hi - prev_lo) / last * 100.0 if last > 0 else 0.0
    phase_range_ok = PHASE_RANGE_MIN_PCT <= phase_range_pct <= PHASE_RANGE_MAX_PCT
    detail.update({
        "prev_hi": round(prev_hi, 8),
        "prev_lo": round(prev_lo, 8),
        "recent_hi": round(recent_hi, 8),
        "recent_lo": round(recent_lo, 8),
        "vol_ratio": round(vol_ratio, 3),
        "change_1h_pct": round(change_1h, 2),
        "change_4h_pct": round(change_4h, 2),
        "phase_range_pct": round(phase_range_pct, 2),
    })
    if side == "SHORT":
        breakdown = last < prev_lo * (1.0 - BREAKDOWN_SUPPORT_BUFFER)
        momentum_ok = change_1h <= -BREAKDOWN_1H_DROP_MIN_PCT or change_4h <= -BREAKDOWN_4H_DROP_MIN_PCT
        volume_ok = vol_ratio >= BREAKOUT_VOL_RATIO_MIN
        strong_break = break_down_pct >= PHASE_STRONG_BREAK_PCT
        not_overextended = abs(change_4h) <= PHASE_BREAK_MAX_4H_MOVE_PCT or break_down_pct >= PHASE_STRONG_BREAK_PCT * 2.0
        fresh_breakdown = (
            breakdown
            and phase_range_ok
            and not_overextended
            and momentum_ok
            and (volume_ok or strong_break)
        )
        detail["setup"] = "fresh_breakdown_short" if fresh_breakdown else "breakdown_short" if breakdown else "none"
        if breakdown:
            detail["break_pct"] = round(break_down_pct, 3)
        detail["momentum_ok"] = momentum_ok
        detail["volume_ok"] = volume_ok
        detail["strong_break"] = strong_break
        detail["not_overextended"] = not_overextended
        detail["fresh_breakout"] = fresh_breakdown
        ok = fresh_breakdown
    else:
        breakout = last > prev_hi * (1.0 + BREAKOUT_RESIST_BUFFER)
        momentum_ok = change_1h >= BREAKOUT_1H_RISE_MIN_PCT or change_4h >= BREAKOUT_4H_RISE_MIN_PCT
        volume_ok = vol_ratio >= BREAKOUT_VOL_RATIO_MIN
        strong_break = break_up_pct >= PHASE_STRONG_BREAK_PCT
        not_overextended = abs(change_4h) <= PHASE_BREAK_MAX_4H_MOVE_PCT or break_up_pct >= PHASE_STRONG_BREAK_PCT * 2.0
        fresh_breakout = (
            breakout
            and phase_range_ok
            and not_overextended
            and momentum_ok
            and (volume_ok or strong_break)
        )
        detail["setup"] = "fresh_breakout_long" if fresh_breakout else "breakout_long" if breakout else "none"
        if breakout:
            detail["break_pct"] = round(break_up_pct, 3)
        detail["momentum_ok"] = momentum_ok
        detail["volume_ok"] = volume_ok
        detail["strong_break"] = strong_break
        detail["not_overextended"] = not_overextended
        detail["fresh_breakout"] = fresh_breakout
        ok = fresh_breakout
    if not ok:
        if detail.get("setup") in ("breakdown_short", "breakout_long"):
            detail["reason"] = (
                "phase_range_not_clean"
                if not phase_range_ok else
                "breakout_overextended_4h"
                if not not_overextended else
                "breakout_without_volume_or_momentum"
            )
        else:
            detail["reason"] = "no_15m_setup"
        return False, detail
    detail["passed"] = True
    return True, detail


def evaluate_symbol_multiperiod(
    cur,
    symbol: str,
    profile: str,
    global_trend: Optional[Dict[str, Any]] = None,
    big4: Optional[Dict[str, Any]] = None,
    global_regime: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    profile_l = profile.strip().lower()
    side = "LONG" if profile_l == "long" else "SHORT"
    out: Dict[str, Any] = {"symbol": symbol, "side": side, "passed": False, "reason": None, "score": 0.0, "ref_price": None}

    from app.services.brain_playbook import classify_playbook

    rows_1h = _fetch_klines(cur, symbol, "1h", 168)
    rows_15m = _fetch_klines(cur, symbol, "15m", 672)
    if len(rows_1h) < 60:
        out["reason"] = "insufficient_1h"
        return out
    if len(rows_15m) < 50:
        out["reason"] = "insufficient_15m"
        return out

    dims: Dict[str, Any] = {}
    c15 = h15 = l15 = v15 = []
    for key, label, _, _ in TREND_WINDOWS:
        c, h, l, v = _fetch_window(cur, symbol, key)
        if not c:
            out["reason"] = f"insufficient_{key}"
            return out
        dims[key] = {"label": label, **_window_direction(c, h, l)}
        if key == "d1":
            c15, h15, l15, v15 = c, h, l, v
            out["ref_price"] = c15[-1]

    global_bias = (global_trend or {}).get("bias") or "FLAT"
    future_4h = _future_4h_direction(c15, h15, l15, v15)

    pb = classify_playbook(rows_1h, rows_15m, big4=big4 or {})
    playbook = str(pb.get("playbook") or "D1")
    pb_side = str(pb.get("side") or "FLAT").upper()
    signals = set(pb.get("signals") or [])
    features = pb.get("features") or {}
    edge = float(pb.get("edge_score") or 0.0)
    confirmed = bool(pb.get("confirmed"))
    global_name = str((global_regime or {}).get("global_regime") or "GLOBAL_UNKNOWN")
    big4_bias = str((big4 or {}).get("bias") or "FLAT").upper()
    big4_ok = bool((big4 or {}).get("big4_ok", True))

    allowed_playbooks = {"LONG": {"A1", "C3"}, "SHORT": {"A2", "C1"}}[side]
    if pb_side != side:
        out.update({"reason": f"playbook_side_{pb_side}", "trend": dims, "future_4h": future_4h, "playbook": pb})
        return out
    if playbook not in allowed_playbooks:
        out.update({"reason": f"playbook_{playbook}", "trend": dims, "future_4h": future_4h, "playbook": pb})
        return out
    has_break_signal = bool(signals & {"break_support", "break_resistance", "ema_reject", "ema_reclaim"})
    has_volume_signal = bool(signals & {"volume_expand_down", "volume_expand_up", "crash_spike", "pump_spike"})
    strong_token_side = features.get("h1_side") == side and features.get("m15_side") == side
    trend_playbook = playbook in {"A1", "A2"}
    breakout_playbook = playbook in {"C1", "C3"}
    weak_big4_long_override = (
        not big4_ok
        and side == "LONG"
        and playbook in {"A1", "C3"}
        and confirmed
        and strong_token_side
        and str(future_4h.get("side") or "FLAT").upper() == "LONG"
        and float(future_4h.get("score") or 0.0) >= FUTURE_4H_OPPORTUNITY_SCORE_MIN
        and edge >= (0.80 if playbook == "A1" else 0.85)
    )
    weak_big4_short_override = (
        not big4_ok
        and side == "SHORT"
        and playbook == "C1"
        and confirmed
        and strong_token_side
        and str(future_4h.get("side") or "FLAT").upper() == "SHORT"
        and float(future_4h.get("score") or 0.0) >= FUTURE_4H_OPPORTUNITY_SCORE_MIN
        and edge >= 0.80
        and bool(signals & {"break_support", "volume_expand_down", "crash_spike"})
    )
    if not big4_ok and not (weak_big4_long_override or weak_big4_short_override):
        out.update({"reason": "big4_weak", "trend": dims, "future_4h": future_4h, "playbook": pb})
        return out

    min_edge = 0.68 if breakout_playbook else 0.72
    if side == "SHORT" and playbook == "C1":
        min_edge = 0.70
    if edge < min_edge:
        out.update({"reason": "low_edge", "trend": dims, "future_4h": future_4h, "playbook": pb})
        return out
    if not confirmed:
        out.update({"reason": "unconfirmed_playbook", "trend": dims, "future_4h": future_4h, "playbook": pb})
        return out
    if not (has_break_signal or has_volume_signal or strong_token_side):
        out.update({"reason": "weak_phase_evidence", "trend": dims, "future_4h": future_4h, "playbook": pb})
        return out

    if global_name == "DAILY_BEAR_PROBE" and side == "LONG" and not weak_big4_long_override:
        out.update({"reason": "daily_bear_blocks_long", "trend": dims, "future_4h": future_4h, "playbook": pb})
        return out
    if global_name == "RELIEF_BOUNCE" and side == "SHORT" and not (playbook == "C1" and "break_support" in signals and "crash_spike" in signals):
        out.update({"reason": "relief_bounce_blocks_fresh_short", "trend": dims, "future_4h": future_4h, "playbook": pb})
        return out
    if big4_bias in ("LONG", "SHORT") and big4_bias != side and not strong_token_side:
        out.update({"reason": "big4_bias_opposes_symbol", "trend": dims, "future_4h": future_4h, "playbook": pb})
        return out

    entry_ok, entry_15m = _entry_15m_opportunity(c15, h15, l15, v15, side)

    setup = {
        "A1": "brain_A1_trend_continuation_long",
        "A2": "brain_A2_failed_rebound_short",
        "C1": "brain_C1_breakdown_short",
        "C3": "brain_C3_breakout_long",
    }.get(playbook, playbook)
    score = edge * 100.0
    if confirmed:
        score += 6
    if breakout_playbook:
        score += 5
    if has_break_signal:
        score += 4
    if has_volume_signal:
        score += 3
    if strong_token_side:
        score += 5
    if big4_bias == side:
        score += 4
    if global_name in ("DAILY_BEAR_PROBE", "BULL_RECOVERY") and (
        (side == "SHORT" and global_name == "DAILY_BEAR_PROBE")
        or (side == "LONG" and global_name == "BULL_RECOVERY")
    ):
        score += 4

    action_opportunity = _breakout_action_opportunity(
        side=side,
        playbook=playbook,
        edge=edge,
        confirmed=confirmed,
        signals=signals,
        features=features,
        future_4h=future_4h,
        big4_bias=big4_bias,
        global_name=global_name,
        entry_15m=entry_15m,
    )
    should_open = bool(action_opportunity.get("should_open"))
    display_score = max(score, float(action_opportunity.get("action_score") or 0.0))

    out.update({
        "passed": should_open,
        "reason": None if should_open else (action_opportunity.get("reason") or "no_high_confidence_4h_breakout_opportunity"),
        "score": round(min(100.0, display_score), 1),
        "trend": dims,
        "future_4h": future_4h,
        "playbook": pb,
        "signal_detail": {
            "strategy": "brain_playbook_phase_break",
            "intent": "open_on_high_confidence_4h_breakout",
            "global_trend": global_trend,
            "global_regime": global_regime,
            "trend_dimensions": dims,
            "future_4h": future_4h,
            "action_opportunity": action_opportunity,
            "playbook": {
                "name": playbook,
                "side": pb_side,
                "edge_score": edge,
                "confirmed": confirmed,
                "signals": list(pb.get("signals") or []),
                "candidates": pb.get("candidates") or [],
                "evidence_summary": pb.get("evidence_summary"),
                "features": features,
            },
            "entry": {
                "setup": setup,
                "playbook": playbook,
                "edge_score": edge,
                "confirmed": confirmed,
                "h1_side": features.get("h1_side"),
                "m15_side": features.get("m15_side"),
                "rsi_1h": features.get("rsi_1h"),
                "rsi_15m": features.get("rsi_15m"),
                "has_break_signal": has_break_signal,
                "has_volume_signal": has_volume_signal,
                "strong_token_side": strong_token_side,
                "entry_15m_passed": entry_ok,
                "big4_bias": big4_bias,
                "global_regime": global_name,
            },
            "setup": setup,
        },
    })
    return out


def scan_universe(
    conn,
    profile: str,
    *,
    include_rejects: bool = False,
) -> Tuple[List[Dict[str, Any]], int]:
    symbols = load_midline_universe(conn)
    universe_size = len(symbols)
    results: List[Dict[str, Any]] = []
    profile_l = profile.strip().lower()

    with conn.cursor() as cur:
        global_trend = evaluate_global_trend_dimensions(cur)
        try:
            from app.services.brain_market_analyzer import evaluate_big4_gate
            from app.services.brain_market_regime import evaluate_global_daily_regime
            big4 = evaluate_big4_gate(cur)
            global_regime = evaluate_global_daily_regime(cur)
        except Exception as e:
            logger.warning(f"[breakout opportunity] failed to read BRAIN market context, fallback to local trend: {e}")
            big4 = {"big4_ok": True, "bias": (global_trend or {}).get("bias") or "FLAT"}
            global_regime = {"global_regime": "GLOBAL_UNKNOWN", "reason": "fallback_global_trend"}
        for symbol in symbols:
            try:
                ev = evaluate_symbol_multiperiod(
                    cur,
                    symbol,
                    profile_l,
                    global_trend=global_trend,
                    big4=big4,
                    global_regime=global_regime,
                )
                if ev["passed"]:
                    results.append({
                        "symbol": ev["symbol"],
                        "side": ev["side"],
                        "score": float(ev["score"]),
                        "signal_detail": ev.get("signal_detail") or {},
                        "ref_price": ev.get("ref_price"),
                        "passed": True,
                        "reason": None,
                    })
                elif include_rejects:
                    results.append({
                        "symbol": ev["symbol"],
                        "side": ev["side"],
                        "score": float(ev.get("score") or 0),
                        "signal_detail": {
                            "global_trend": global_trend,
                            "trend_dimensions": ev.get("trend") or {},
                            "future_4h": ev.get("future_4h") or {},
                            "entry": ev.get("entry") or {},
                            "reason": ev.get("reason"),
                        },
                        "ref_price": ev.get("ref_price"),
                        "passed": False,
                        "reason": ev.get("reason"),
                    })
            except Exception as e:
                logger.debug(f"[breakout scanner] skip {symbol}: {e}")
                if include_rejects:
                    results.append({
                        "symbol": symbol,
                        "side": "LONG" if profile_l == "long" else "SHORT",
                        "score": 0.0,
                        "signal_detail": {"error": str(e)},
                        "ref_price": None,
                        "passed": False,
                        "reason": "eval_error",
                    })
    results.sort(key=lambda x: (0 if x.get("passed") else 1, -float(x.get("score") or 0)))
    return results, universe_size


def signal_detail_json(detail: Dict[str, Any]) -> str:
    return json.dumps(detail, ensure_ascii=False, default=str)
