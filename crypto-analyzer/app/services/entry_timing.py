"""Entry timing — buy the pullback, sell the high, follow a fresh break.

LONG A1: wait for the 15m pullback into EMA20 / 38–50% retrace.
LONG C3: BRAIN waits for the pullback; midline follows a fresh breakout (market).
SHORT exhaustion (B3/C4): sell the first callback off a tagged high — do not wait for EMA.
SHORT follow (C1/B2): follow a fresh breakdown immediately — do not wait for a bounce.
SHORT bounce (A2): sell the 15m bounce into EMA / prior high in a downtrend.

Used by BRAIN and midline/breakout. Direction can already be right; this module
only answers "is this the buy/sell point, and where to rest the limit".
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

PULLBACK_LONG_PLAYBOOKS = frozenset({"A1", "B4", "C3"})
PULLBACK_SHORT_PLAYBOOKS = frozenset({"A2"})
EXHAUSTION_SHORT_PLAYBOOKS = frozenset({"B3", "C4"})
FOLLOW_BREAKDOWN_PLAYBOOKS = frozenset({"C1", "B2"})

ENTRY_OFFSET_MIN_PCT = 0.20
ENTRY_OFFSET_MAX_PCT = 1.80
ENTRY_READY_OFFSET_PCT = 0.35
EXHAUSTION_OFFSET_MAX_PCT = 0.55
FOLLOW_OFFSET_MAX_PCT = 0.30
EXTENDED_RANGE_POS = 0.82
EXTENDED_EMA_DIST_PCT = 0.80
ZONE_ATR_PAD = 0.35
INVALIDATION_BUFFER = 0.0015
STALL_HIT_MIN = 2
NEAR_HIGH_MAX_DIST_PCT = 0.85
MISSED_HIGH_DIST_PCT = 1.35
# First callback must actually leave the tagged high — 0.08% is noise, not fade.
TOP_CALLBACK_MIN_OFF_PCT = 0.28
REJECT_STALL_HITS = frozenset({"wick", "reject_close", "false_break", "rsi", "reject", "callback"})
# LONG: still hugging the local 15m high → wait. 0.40% is a pause, not a pullback to EMA.
LONG_STILL_AT_HIGH_PCT = 0.80
LONG_A1_NEAR_EMA_MAX_RANGE_POS = 0.55
LONG_A1_NEAR_EMA_MAX_DIST_PCT = 0.15
# C3 follow: measure extension vs the high *before* the last 2h, so the break
# level does not ratchet with the pump (Aug 20 FIL/DOGE re-chase).
C3_PRE_BREAK_EXCLUDE_BARS = 8
C3_MISSED_BREAK_PCT = 1.40
C3_BLOWOFF_SIGNALS = frozenset({"rsi_extreme_high", "near_7d_high"})
C3_STALL_SIGNALS = frozenset({"stall_at_high", "top_callback", "long_upper_wick"})
A1_STALL_SIGNALS = frozenset({"15m_lower_high", "top_callback", "stall_at_high", "near_7d_high"})


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _closes(rows: List[Dict[str, Any]]) -> List[float]:
    return [_f(r.get("close_price")) for r in rows]


def _highs(rows: List[Dict[str, Any]]) -> List[float]:
    return [_f(r.get("high_price")) for r in rows]


def _lows(rows: List[Dict[str, Any]]) -> List[float]:
    return [_f(r.get("low_price")) for r in rows]


def _vols(rows: List[Dict[str, Any]]) -> List[float]:
    return [_f(r.get("volume")) for r in rows]


def _ema(closes: List[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = c * k + ema * (1 - k)
    return ema


def _atr(rows: List[Dict[str, Any]], period: int = 14) -> Optional[float]:
    if len(rows) < period + 1:
        return None
    trs = []
    for i in range(1, len(rows)):
        h = _f(rows[i].get("high_price"))
        l = _f(rows[i].get("low_price"))
        pc = _f(rows[i - 1].get("close_price"))
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss <= 1e-12:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


@dataclass
class EntryTiming:
    ready: bool
    status: str
    reason: str
    limit_offset_pct: float
    limit_price: Optional[float]
    zone_low: Optional[float]
    zone_high: Optional[float]
    ema20: Optional[float]
    break_level: Optional[float]
    extended: bool
    bounce_ok: bool
    mode: str = "pullback"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _empty(status: str, reason: str, *, offset: float = ENTRY_READY_OFFSET_PCT, mode: str = "pullback") -> EntryTiming:
    return EntryTiming(
        ready=False,
        status=status,
        reason=reason,
        limit_offset_pct=offset,
        limit_price=None,
        zone_low=None,
        zone_high=None,
        ema20=None,
        break_level=None,
        extended=False,
        bounce_ok=False,
        mode=mode,
    )


def _signals_of(playbook_row: Optional[Dict[str, Any]]) -> set:
    row = playbook_row or {}
    feats = row.get("features") or {}
    raw = list(row.get("signals") or feats.get("signals") or [])
    return {str(s) for s in raw}


def _upper_wick_ratio(row: Dict[str, Any]) -> float:
    o = _f(row.get("open_price"))
    h = _f(row.get("high_price"))
    l = _f(row.get("low_price"))
    c = _f(row.get("close_price"))
    span = max(h - l, 1e-12)
    body_top = max(o, c)
    return max(h - body_top, 0.0) / span


def _close_loc_in_bar(row: Dict[str, Any]) -> float:
    h = _f(row.get("high_price"))
    l = _f(row.get("low_price"))
    c = _f(row.get("close_price"))
    span = max(h - l, 1e-12)
    return (c - l) / span


def collect_stall_hits(
    rows_15m: List[Dict[str, Any]],
    *,
    signals: Optional[Sequence[str]] = None,
) -> Tuple[List[str], bool]:
    """Independent evidence that the high is failing (need >=2 to sell the spike)."""
    sig = {str(s) for s in (signals or [])}
    hits: List[str] = []
    if len(rows_15m) < 8:
        return hits, False

    h = _highs(rows_15m)
    v = _vols(rows_15m)
    c = _closes(rows_15m)
    last = rows_15m[-1]
    wick_ratio = _upper_wick_ratio(last)
    close_loc = _close_loc_in_bar(last)
    tagged_high = max(h[-8:])
    last_close = c[-1]
    off_high_pct = (tagged_high - last_close) / last_close * 100.0 if last_close > 0 else 0.0

    if (
        "long_upper_wick" in sig
        or "exhaustion_up" in sig
        or wick_ratio >= 0.35
    ):
        hits.append("wick")
    if "volume_diverge_bear" in sig or (
        len(v) >= 8
        and max(h[-8:]) >= max(h[-24:-8] if len(h) >= 24 else h[-8:]) * 0.999
        and sum(v[-3:]) < sum(v[-8:-3]) * 0.85
    ):
        hits.append("vol_diverge")
    rsi_now = _rsi(c, 14)
    rsi_prev = _rsi(c[:-3], 14) if len(c) > 20 else None
    # RSI still extreme-high is strength, not fade. Only a turn-down counts.
    rsi_turn = bool(
        "rsi_15m_turn_down" in sig
        or (
            rsi_prev is not None
            and rsi_now is not None
            and rsi_prev >= 65.0
            and rsi_now <= rsi_prev - 3.0
        )
    )
    if rsi_turn:
        hits.append("rsi")
    if "false_break_up" in sig:
        hits.append("false_break")
    if "15m_lower_high" in sig or "ema_reject" in sig:
        hits.append("reject")
    if "stall_at_high" in sig or "15m_stop_new_high" in sig:
        hits.append("no_new_high")
    elif len(h) >= 8 and max(h[-3:]) <= max(h[-8:-3]) * 1.0015:
        hits.append("no_new_high")
    if close_loc <= 0.45 and wick_ratio >= 0.28:
        hits.append("reject_close")
    if (
        "top_callback" in sig
        or (
            TOP_CALLBACK_MIN_OFF_PCT <= off_high_pct <= NEAR_HIGH_MAX_DIST_PCT
            and close_loc <= 0.50
            and wick_ratio >= 0.28
        )
    ):
        hits.append("callback")

    # Still printing / holding a new high → not exhausted, even if volume cooled.
    prior_hi = max(h[-8:-1]) if len(h) >= 9 else max(h[:-1])
    last_vol = v[-1] if v else 0.0
    avg_vol = (sum(v[-6:-1]) / 5.0) if len(v) >= 6 else last_vol
    printing_high = h[-1] > prior_hi * 1.001
    held_high = close_loc >= 0.62 and wick_ratio < 0.32
    accelerating = bool(
        printing_high
        and held_high
        and (
            last_vol >= avg_vol * 1.05
            or close_loc >= 0.70
        )
    )
    return list(dict.fromkeys(hits)), accelerating


def _exhaustion_short_entry(
    rows_15m: List[Dict[str, Any]],
    *,
    playbook_row: Optional[Dict[str, Any]],
    price: float,
    ema20: Optional[float],
) -> EntryTiming:
    """Sell the stall at the high: limit slightly above last poke, not down at EMA."""
    h = _highs(rows_15m)
    l = _lows(rows_15m)
    sig = _signals_of(playbook_row)
    hits, accelerating = collect_stall_hits(rows_15m, signals=sig)
    recent_high = max(h[-8:])
    impulse_lo = min(l[-8:])
    dist_pct = (recent_high - price) / price * 100.0 if price > 0 else 99.0
    at_highs = dist_pct <= NEAR_HIGH_MAX_DIST_PCT or (
        ema20 is not None and price >= ema20 * 1.004 and dist_pct <= 1.10
    )
    missed = dist_pct >= MISSED_HIGH_DIST_PCT and not at_highs
    zone_high = recent_high
    zone_low = max(recent_high * (1.0 - NEAR_HIGH_MAX_DIST_PCT / 100.0), impulse_lo)
    target = min(recent_high, price * (1.0 + EXHAUSTION_OFFSET_MAX_PCT / 100.0))
    target = max(target, price * (1.0 + ENTRY_OFFSET_MIN_PCT / 100.0))
    offset = _clamp((target - price) / price * 100.0, ENTRY_OFFSET_MIN_PCT, EXHAUSTION_OFFSET_MAX_PCT)
    stall_ok = (
        len(hits) >= STALL_HIT_MIN
        and bool(set(hits) & REJECT_STALL_HITS)
    )
    hit_label = "+".join(hits) if hits else "none"
    last = rows_15m[-1]
    last_close_loc = _close_loc_in_bar(last)
    last_wick = _upper_wick_ratio(last)
    last_red = _f(last.get("close_price")) < _f(last.get("open_price"))
    last_is_reject = last_close_loc <= 0.45 and (last_wick >= 0.28 or last_red)
    left_the_high = TOP_CALLBACK_MIN_OFF_PCT <= dist_pct <= NEAR_HIGH_MAX_DIST_PCT
    callback_ok = left_the_high and (
        "false_break" in hits
        or last_is_reject
        or ("callback" in hits and last_close_loc <= 0.50)
    )

    if price > recent_high * (1.0 + INVALIDATION_BUFFER) and accelerating:
        return EntryTiming(
            ready=False, status="invalidated", reason="new_high_with_volume",
            limit_offset_pct=offset, limit_price=None,
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=recent_high, extended=True, bounce_ok=False, mode="exhaustion",
        )
    if missed:
        return EntryTiming(
            ready=False, status="missed_high",
            reason=f"already_off_high_{dist_pct:.2f}pct",
            limit_offset_pct=offset, limit_price=round(target, 8),
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=recent_high, extended=False, bounce_ok=stall_ok, mode="exhaustion",
        )
    if accelerating:
        return EntryTiming(
            ready=False, status="wait_stall",
            reason="still_extending_wait_reject",
            limit_offset_pct=offset, limit_price=round(target, 8),
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=recent_high, extended=True, bounce_ok=False, mode="exhaustion",
        )
    if at_highs and stall_ok and callback_ok:
        return EntryTiming(
            ready=True, status="exhaustion_ready",
            reason=f"top_callback:{hit_label}",
            limit_offset_pct=offset, limit_price=round(target, 8),
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=recent_high, extended=True, bounce_ok=True, mode="exhaustion",
        )
    if at_highs and stall_ok:
        return EntryTiming(
            ready=False, status="wait_stall",
            reason=f"at_high_wait_first_callback:{hit_label}",
            limit_offset_pct=offset, limit_price=round(target, 8),
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=recent_high, extended=True, bounce_ok=False, mode="exhaustion",
        )
    if at_highs:
        return EntryTiming(
            ready=False, status="wait_stall",
            reason=f"at_high_need_more_stall:{hit_label}",
            limit_offset_pct=offset, limit_price=round(target, 8),
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=recent_high, extended=True, bounce_ok=False, mode="exhaustion",
        )
    return EntryTiming(
        ready=False, status="wait_stall",
        reason=f"not_at_high:{hit_label}",
        limit_offset_pct=offset, limit_price=round(target, 8),
        zone_low=zone_low, zone_high=zone_high, ema20=ema20,
        break_level=recent_high, extended=False, bounce_ok=stall_ok, mode="exhaustion",
    )


def _follow_breakdown_entry(
    rows_15m: List[Dict[str, Any]],
    *,
    playbook_row: Optional[Dict[str, Any]],
    price: float,
    ema20: Optional[float],
    look_lo: float,
) -> EntryTiming:
    """Follow a fresh breakdown: sell near the break, do not wait for a bounce to EMA."""
    c = _closes(rows_15m)
    l = _lows(rows_15m)
    h = _highs(rows_15m)
    v = _vols(rows_15m)
    sig = _signals_of(playbook_row)
    recent_lo = min(l[-8:])
    recent_hi = max(h[-8:])
    swing_lo = min(l[-16:-2]) if len(l) >= 18 else (look_lo if look_lo > 0 else recent_lo)
    dist_from_low = (price - recent_lo) / price * 100.0 if price > 0 else 99.0
    broke = bool(
        "break_support" in sig
        or "impulse_down" in sig
        or "crash_spike" in sig
        or "h1_breakdown_down" in sig
        or (look_lo > 0 and price < look_lo * 0.998)
        or (swing_lo > 0 and c[-1] < swing_lo * 0.998)
    )
    force = bool(
        "volume_expand_down" in sig
        or "crash_spike" in sig
        or "impulse_down" in sig
        or (len(v) >= 8 and sum(v[-3:]) > sum(v[-8:-3]) * 1.05 and c[-1] < c[-4])
    )
    at_lows = dist_from_low <= 1.20 or (look_lo > 0 and price <= look_lo * 1.003)
    recovering = bool(
        "false_break_down" in sig
        or (look_lo > 0 and c[-1] > look_lo * 1.004 and len(c) >= 3 and c[-1] > c[-3])
    )
    last_down = len(c) >= 2 and c[-1] <= c[-2]
    target = price * (1.0 + ENTRY_OFFSET_MIN_PCT / 100.0)
    offset = _clamp((target - price) / price * 100.0, ENTRY_OFFSET_MIN_PCT, FOLLOW_OFFSET_MAX_PCT)
    zone_high = look_lo if look_lo > 0 else recent_hi
    zone_low = recent_lo

    if recovering:
        return EntryTiming(
            ready=False, status="invalidated", reason="breakdown_reclaimed",
            limit_offset_pct=offset, limit_price=None,
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=look_lo, extended=False, bounce_ok=False, mode="follow",
        )
    if not broke:
        return EntryTiming(
            ready=False, status="wait_break", reason="support_not_broken",
            limit_offset_pct=offset, limit_price=round(target, 8),
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=look_lo, extended=False, bounce_ok=False, mode="follow",
        )
    if at_lows and (force or last_down):
        return EntryTiming(
            ready=True, status="breakdown_ready",
            reason="follow_fresh_breakdown",
            limit_offset_pct=offset, limit_price=round(target, 8),
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=look_lo if look_lo > 0 else swing_lo, extended=True, bounce_ok=True, mode="follow",
        )
    if broke and force and dist_from_low <= 1.20:
        return EntryTiming(
            ready=True, status="breakdown_ready",
            reason="follow_volume_breakdown",
            limit_offset_pct=offset, limit_price=round(target, 8),
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=look_lo if look_lo > 0 else swing_lo, extended=True, bounce_ok=True, mode="follow",
        )
    if dist_from_low >= 1.40:
        return EntryTiming(
            ready=False, status="missed_break",
            reason=f"already_off_low_{dist_from_low:.2f}pct",
            limit_offset_pct=offset, limit_price=round(target, 8),
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=look_lo, extended=False, bounce_ok=False, mode="follow",
        )
    return EntryTiming(
        ready=False, status="wait_follow",
        reason="broke_wait_continuation",
        limit_offset_pct=offset, limit_price=round(target, 8),
        zone_low=zone_low, zone_high=zone_high, ema20=ema20,
        break_level=look_lo, extended=False, bounce_ok=False, mode="follow",
    )


def _pre_impulse_high(h: List[float], exclude: int = C3_PRE_BREAK_EXCLUDE_BARS) -> float:
    """Resistance from before the current 15m impulse; does not ride the pump."""
    if not h:
        return 0.0
    if len(h) > exclude + 2:
        return max(h[:-exclude])
    if len(h) > 3:
        return max(h[:-2])
    return max(h)


def _follow_breakout_long_entry(
    rows_15m: List[Dict[str, Any]],
    *,
    playbook_row: Optional[Dict[str, Any]],
    price: float,
    ema20: Optional[float],
    look_hi: float,
) -> EntryTiming:
    """Midline C3: follow a fresh upside break with a marketable fill, do not wait for EMA."""
    c = _closes(rows_15m)
    h = _highs(rows_15m)
    v = _vols(rows_15m)
    sig = _signals_of(playbook_row)
    recent_hi = max(h[-8:])
    break_level = _pre_impulse_high(h)
    if break_level <= 0 and look_hi > 0:
        break_level = look_hi
    dist_from_break = (price - break_level) / price * 100.0 if price > 0 and break_level > 0 else 0.0
    broke = bool(
        "break_resistance" in sig
        or "impulse_up" in sig
        or "h1_breakout_up" in sig
        or "pump_spike" in sig
        or (break_level > 0 and price > break_level * 1.002)
        or (len(c) >= 2 and c[-1] > break_level * 1.002)
    )
    force = bool(
        "volume_expand_up" in sig
        or "impulse_up" in sig
        or "pump_spike" in sig
        or (len(v) >= 8 and sum(v[-3:]) > sum(v[-8:-3]) * 1.05 and c[-1] > c[-4])
    )
    fake = bool(
        "false_break_up" in sig
        or (break_level > 0 and c[-1] < break_level * 0.997 and len(c) >= 3 and max(h[-3:]) > break_level)
    )
    target = price
    offset = 0.0
    zone_low = break_level
    zone_high = recent_hi

    if fake:
        return EntryTiming(
            ready=False, status="invalidated", reason="breakout_reclaimed",
            limit_offset_pct=ENTRY_OFFSET_MIN_PCT, limit_price=None,
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=break_level, extended=False, bounce_ok=False, mode="follow",
        )
    if C3_BLOWOFF_SIGNALS <= sig:
        return EntryTiming(
            ready=False, status="chase_blowoff",
            reason="rsi_extreme_near_7d_high",
            limit_offset_pct=ENTRY_OFFSET_MIN_PCT, limit_price=round(price, 8),
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=break_level, extended=True, bounce_ok=False, mode="follow",
        )
    if "rsi_extreme_high" in sig and (sig & C3_STALL_SIGNALS):
        return EntryTiming(
            ready=False, status="chase_blowoff",
            reason="extreme_rsi_stalling_at_high",
            limit_offset_pct=ENTRY_OFFSET_MIN_PCT, limit_price=round(price, 8),
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=break_level, extended=True, bounce_ok=False, mode="follow",
        )
    if not broke:
        return EntryTiming(
            ready=False, status="wait_break", reason="resistance_not_broken",
            limit_offset_pct=ENTRY_OFFSET_MIN_PCT, limit_price=round(price, 8),
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=break_level, extended=False, bounce_ok=False, mode="follow",
        )
    if dist_from_break >= C3_MISSED_BREAK_PCT:
        return EntryTiming(
            ready=False, status="missed_break",
            reason=f"already_extended_{dist_from_break:.2f}pct",
            limit_offset_pct=ENTRY_OFFSET_MIN_PCT, limit_price=round(price, 8),
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=break_level, extended=True, bounce_ok=False, mode="follow",
        )
    if force or (len(c) >= 2 and c[-1] >= c[-2]):
        return EntryTiming(
            ready=True, status="breakout_ready",
            reason="follow_fresh_breakout",
            limit_offset_pct=offset, limit_price=round(target, 8),
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=break_level, extended=False, bounce_ok=True, mode="follow",
        )
    return EntryTiming(
        ready=False, status="wait_follow",
        reason="broke_wait_continuation",
        limit_offset_pct=ENTRY_OFFSET_MIN_PCT, limit_price=round(price, 8),
        zone_low=zone_low, zone_high=zone_high, ema20=ema20,
        break_level=break_level, extended=False, bounce_ok=False, mode="follow",
    )


def compute_pullback_entry(
    side: str,
    playbook: str,
    rows_15m: List[Dict[str, Any]],
    *,
    playbook_row: Optional[Dict[str, Any]] = None,
    ref_price: Optional[float] = None,
    follow_breakout: bool = False,
) -> EntryTiming:
    """Decide whether the current 15m bar is a pullback buy, top callback sell, or break follow."""
    side_u = (side or "").upper()
    pb = (playbook or "").strip().upper()
    if side_u not in ("LONG", "SHORT"):
        return _empty("no_zone", "flat_side")
    if len(rows_15m) < 24:
        return _empty("no_zone", "insufficient_15m")

    c = _closes(rows_15m)
    h = _highs(rows_15m)
    l = _lows(rows_15m)
    v = _vols(rows_15m)
    price = _f(ref_price) if ref_price else c[-1]
    if price <= 0:
        return _empty("no_zone", "no_price")

    ema20 = _ema(c, 20)
    atr = _atr(rows_15m[-60:] if len(rows_15m) >= 60 else rows_15m, 14) or (price * 0.004)
    look_hi = max(h[-48:-2]) if len(h) >= 50 else max(h[:-1])
    look_lo = min(l[-48:-2]) if len(l) >= 50 else min(l[:-1])
    impulse_hi = max(h[-8:])
    impulse_lo = min(l[-8:])
    impulse_range = max(impulse_hi - impulse_lo, price * 0.002)
    sig = _signals_of(playbook_row)

    if side_u == "LONG" and pb == "C3" and follow_breakout:
        return _follow_breakout_long_entry(
            rows_15m, playbook_row=playbook_row, price=price, ema20=ema20, look_hi=look_hi,
        )

    if side_u == "LONG":
        break_level = look_hi
        fib38 = impulse_hi - impulse_range * 0.382
        fib50 = impulse_hi - impulse_range * 0.50
        ema_hi = (ema20 * 1.002) if ema20 else fib38
        ema_lo = (ema20 - atr * ZONE_ATR_PAD) if ema20 else fib50
        zone_high = min(ema_hi, fib38)
        zone_low = max(ema_lo, fib50)
        if zone_low > zone_high:
            mid = ema20 if ema20 else (fib38 + fib50) / 2.0
            zone_low, zone_high = mid - atr * 0.25, mid + atr * 0.15
        invalidation = min(impulse_lo, (ema20 - atr) if ema20 else impulse_lo)
        range_span = max(max(h[-8:]) - min(l[-8:]), 1e-12)
        range_pos = (price - min(l[-8:])) / range_span
        ema_dist_pct = ((price - ema20) / ema20 * 100.0) if ema20 else 0.0
        dist_from_high_pct = ((impulse_hi - price) / price * 100.0) if price > 0 else 0.0
        still_at_high = dist_from_high_pct <= LONG_STILL_AT_HIGH_PCT
        extended = (
            range_pos >= EXTENDED_RANGE_POS
            or ema_dist_pct >= EXTENDED_EMA_DIST_PCT
            or price > zone_high * 1.003
            or still_at_high
        )
        if "impulse_up" in sig or "h1_breakout_up" in sig or "pump_spike" in sig:
            extended = extended or range_pos >= 0.72
        in_zone = zone_low <= price <= zone_high * 1.002
        pulled_back = len(c) >= 5 and c[-1] < max(h[-5:-1]) * 0.998
        vol_shrink = "volume_shrink_pullback" in sig or (
            len(v) >= 8 and sum(v[-3:]) < sum(v[-8:-3]) * 0.85
        )
        left_high = (not still_at_high) and pulled_back
        bounce_ok = left_high and bool(
            "long_lower_wick" in sig
            or "rsi_15m_turn_up" in sig
            or "ema_reclaim" in sig
            or vol_shrink
        )
        if price < invalidation:
            return EntryTiming(
                ready=False, status="invalidated", reason="broke_pullback_invalidation",
                limit_offset_pct=ENTRY_READY_OFFSET_PCT, limit_price=None,
                zone_low=zone_low, zone_high=zone_high, ema20=ema20,
                break_level=break_level, extended=extended, bounce_ok=False,
            )
        target = _clamp(
            ema20 if ema20 else (zone_low + zone_high) / 2.0,
            zone_low,
            zone_high,
        )
        if target >= price:
            target = price * (1.0 - ENTRY_READY_OFFSET_PCT / 100.0)
        offset = _clamp((price - target) / price * 100.0, ENTRY_OFFSET_MIN_PCT, ENTRY_OFFSET_MAX_PCT)
        if still_at_high:
            return EntryTiming(
                ready=False, status="wait_pullback",
                reason="still_at_high_wait_pullback",
                limit_offset_pct=offset, limit_price=round(target, 8),
                zone_low=zone_low, zone_high=zone_high, ema20=ema20,
                break_level=break_level, extended=True, bounce_ok=bounce_ok,
            )
        if (
            pb in {"A1", "B4"}
            and "15m_stop_new_high" in sig
            and bool(sig & A1_STALL_SIGNALS)
        ):
            return EntryTiming(
                ready=False, status="wait_pullback",
                reason="stall_high_not_pullback",
                limit_offset_pct=offset, limit_price=round(target, 8),
                zone_low=zone_low, zone_high=zone_high, ema20=ema20,
                break_level=break_level, extended=True, bounce_ok=False,
            )
        if extended:
            return EntryTiming(
                ready=False, status="wait_pullback",
                reason="extended_wait_15m_pullback",
                limit_offset_pct=offset, limit_price=round(target, 8),
                zone_low=zone_low, zone_high=zone_high, ema20=ema20,
                break_level=break_level, extended=True, bounce_ok=bounce_ok,
            )
        if in_zone and bounce_ok:
            return EntryTiming(
                ready=True, status="pullback_ready",
                reason="pullback_zone_bounce",
                limit_offset_pct=min(offset, ENTRY_READY_OFFSET_PCT + 0.15),
                limit_price=round(target, 8),
                zone_low=zone_low, zone_high=zone_high, ema20=ema20,
                break_level=break_level, extended=False, bounce_ok=True,
            )
        if in_zone:
            return EntryTiming(
                ready=False, status="wait_bounce",
                reason="in_zone_wait_15m_bounce",
                limit_offset_pct=min(offset, 0.55),
                limit_price=round(target, 8),
                zone_low=zone_low, zone_high=zone_high, ema20=ema20,
                break_level=break_level, extended=False, bounce_ok=False,
            )
        if (
            pb in {"A1", "B4"}
            and bounce_ok
            and ema20 is not None
            and ema_dist_pct <= LONG_A1_NEAR_EMA_MAX_DIST_PCT
            and range_pos <= LONG_A1_NEAR_EMA_MAX_RANGE_POS
            and dist_from_high_pct >= LONG_STILL_AT_HIGH_PCT
        ):
            return EntryTiming(
                ready=True, status="pullback_ready",
                reason="trend_pullback_near_ema",
                limit_offset_pct=min(offset, 0.55),
                limit_price=round(target, 8),
                zone_low=zone_low, zone_high=zone_high, ema20=ema20,
                break_level=break_level, extended=False, bounce_ok=True,
            )
        return EntryTiming(
            ready=False, status="wait_pullback",
            reason="not_in_pullback_zone",
            limit_offset_pct=offset, limit_price=round(target, 8),
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=break_level, extended=extended, bounce_ok=bounce_ok,
        )

    if pb in EXHAUSTION_SHORT_PLAYBOOKS:
        return _exhaustion_short_entry(
            rows_15m, playbook_row=playbook_row, price=price, ema20=ema20,
        )
    if pb in FOLLOW_BREAKDOWN_PLAYBOOKS:
        return _follow_breakdown_entry(
            rows_15m, playbook_row=playbook_row, price=price, ema20=ema20,
            look_lo=look_lo,
        )

    break_level = look_lo
    fib38 = impulse_lo + impulse_range * 0.382
    fib50 = impulse_lo + impulse_range * 0.50
    ema_lo = (ema20 * 0.998) if ema20 else fib38
    ema_hi = (ema20 + atr * ZONE_ATR_PAD) if ema20 else fib50
    zone_low = max(ema_lo, fib38)
    zone_high = min(ema_hi, fib50)
    if zone_low > zone_high:
        mid = ema20 if ema20 else (fib38 + fib50) / 2.0
        zone_low, zone_high = mid - atr * 0.15, mid + atr * 0.25
    invalidation = max(impulse_hi, (ema20 + atr) if ema20 else impulse_hi)
    range_span = max(max(h[-8:]) - min(l[-8:]), 1e-12)
    range_pos = (max(h[-8:]) - price) / range_span
    ema_dist_pct = ((ema20 - price) / ema20 * 100.0) if ema20 else 0.0
    extended = (
        range_pos >= EXTENDED_RANGE_POS
        or ema_dist_pct >= EXTENDED_EMA_DIST_PCT
        or price < zone_low * 0.997
    )
    if "impulse_down" in sig or "crash_spike" in sig or "h1_breakdown_down" in sig:
        extended = extended or range_pos >= 0.72
    in_zone = zone_low * 0.998 <= price <= zone_high
    bounce_ok = bool(
        "15m_lower_high" in sig
        or "volume_shrink_pullback" in sig
        or "long_upper_wick" in sig
        or "rsi_15m_turn_down" in sig
        or "ema_reject" in sig
        or "exhaustion_up" in sig
        or "false_break_up" in sig
    )
    if not bounce_ok and len(c) >= 5:
        bounced = c[-1] > min(l[-5:-1]) * 1.002
        vol_shrink = len(v) >= 8 and sum(v[-3:]) < sum(v[-8:-3]) * 0.85
        bounce_ok = bounced and vol_shrink
    if price > invalidation:
        return EntryTiming(
            ready=False, status="invalidated", reason="broke_retest_invalidation",
            limit_offset_pct=ENTRY_READY_OFFSET_PCT, limit_price=None,
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=break_level, extended=extended, bounce_ok=False,
        )
    target = _clamp(
        ema20 if ema20 else (zone_low + zone_high) / 2.0,
        zone_low,
        zone_high,
    )
    if target <= price:
        target = price * (1.0 + ENTRY_READY_OFFSET_PCT / 100.0)
    offset = _clamp((target - price) / price * 100.0, ENTRY_OFFSET_MIN_PCT, ENTRY_OFFSET_MAX_PCT)
    if extended:
        return EntryTiming(
            ready=False, status="wait_pullback",
            reason="extended_wait_15m_retest",
            limit_offset_pct=offset, limit_price=round(target, 8),
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=break_level, extended=True, bounce_ok=bounce_ok,
        )
    if in_zone and bounce_ok:
        return EntryTiming(
            ready=True, status="pullback_ready",
            reason="retest_zone_reject",
            limit_offset_pct=min(offset, ENTRY_READY_OFFSET_PCT + 0.15),
            limit_price=round(target, 8),
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=break_level, extended=False, bounce_ok=True,
        )
    if in_zone:
        return EntryTiming(
            ready=False, status="wait_bounce",
            reason="in_zone_wait_15m_reject",
            limit_offset_pct=min(offset, 0.55),
            limit_price=round(target, 8),
            zone_low=zone_low, zone_high=zone_high, ema20=ema20,
            break_level=break_level, extended=False, bounce_ok=False,
        )
    return EntryTiming(
        ready=False, status="wait_pullback",
        reason="not_in_retest_zone",
        limit_offset_pct=offset, limit_price=round(target, 8),
        zone_low=zone_low, zone_high=zone_high, ema20=ema20,
        break_level=break_level, extended=extended, bounce_ok=bounce_ok,
    )
