"""BRAIN market regime classifier and scenario gates.

The regime layer is intentionally conservative. It decides which playbooks are
eligible before win-rate, edge, cooldown, and account gates run.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple


BULL_TREND = "BULL_TREND"
BEAR_TREND = "BEAR_TREND"
CRASH_DOWN = "CRASH_DOWN"
PANIC_REBOUND = "PANIC_REBOUND"
RANGE_CHOP = "RANGE_CHOP"
LOW_VOL_NO_TRADE = "LOW_VOL_NO_TRADE"
TOKEN_DIVERGENCE = "TOKEN_DIVERGENCE"
TRANSITION = "TRANSITION"

GLOBAL_DAILY_BEAR_PROBE = "DAILY_BEAR_PROBE"
GLOBAL_RELIEF_BOUNCE = "RELIEF_BOUNCE"
GLOBAL_RANGE_DISTRIBUTION = "RANGE_DISTRIBUTION"
GLOBAL_RANGE_ACCUMULATION = "RANGE_ACCUMULATION"
GLOBAL_BULL_RECOVERY = "BULL_RECOVERY"
GLOBAL_UNKNOWN = "GLOBAL_UNKNOWN"


@dataclass(frozen=True)
class RegimeDecision:
    regime: str
    reason: str
    execution_mode: str
    margin_multiplier: float = 1.0


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _pct(a: float, b: float) -> Optional[float]:
    if a <= 0:
        return None
    return (b - a) / a * 100.0


def _daily_stats(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if len(rows) < 60:
        return None
    closes = [_f(r.get("close_price")) for r in rows if _f(r.get("close_price")) > 0]
    highs = [_f(r.get("high_price")) for r in rows if _f(r.get("high_price")) > 0]
    lows = [_f(r.get("low_price")) for r in rows if _f(r.get("low_price")) > 0]
    if len(closes) < 60 or not highs or not lows:
        return None
    window = min(90, len(closes), len(highs), len(lows))
    last = closes[-1]
    hi = max(highs[-window:])
    lo = min(lows[-window:])
    pos = 0.5 if hi <= lo else (last - lo) / (hi - lo)
    ema20 = _ema(closes, 20)
    ema60 = _ema(closes, 60)
    return {
        "last": last,
        "range_pos_90d": round(max(0.0, min(1.0, pos)), 4),
        "change_7d_pct": _pct(closes[-8], last) if len(closes) >= 8 else None,
        "change_30d_pct": _pct(closes[-31], last) if len(closes) >= 31 else None,
        "change_90d_pct": _pct(closes[-91], last) if len(closes) >= 91 else None,
        "ema20": ema20,
        "ema60": ema60,
        "ema_bear": bool(ema20 and ema60 and last < ema20 < ema60),
        "ema_bull": bool(ema20 and ema60 and last > ema20 > ema60),
    }


def classify_global_daily_regime_from_rows(
    btc_rows: List[Dict[str, Any]],
    eth_rows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Classify the top-level daily regime before short-cycle BRAIN gates.

    This layer captures the broad cycle bias: in a daily bear/probe phase, the
    system should primarily sell failed rebounds and heavily discount ordinary
    long continuation signals.
    """
    btc = _daily_stats(btc_rows)
    eth = _daily_stats(eth_rows or [])
    if not btc:
        return {"global_regime": GLOBAL_UNKNOWN, "reason": "insufficient_btc_1d"}

    pos = float(btc.get("range_pos_90d") or 0.5)
    chg30 = float(btc.get("change_30d_pct") or 0.0)
    chg90 = btc.get("change_90d_pct")
    chg90_f = float(chg90 or 0.0)
    chg7 = float(btc.get("change_7d_pct") or 0.0)
    eth_confirms_bear = bool(
        eth
        and (float(eth.get("range_pos_90d") or 0.5) <= 0.50)
        and (eth.get("ema_bear") or float(eth.get("change_30d_pct") or 0.0) <= -3.0)
    )

    if pos <= 0.40 and chg7 >= 4.0 and chg30 <= 0.0:
        regime = GLOBAL_RELIEF_BOUNCE
        reason = "btc_low_range_short_relief_bounce"
    elif pos <= 0.45 and (btc.get("ema_bear") or chg30 <= -5.0 or chg90_f <= -8.0 or eth_confirms_bear):
        regime = GLOBAL_DAILY_BEAR_PROBE
        reason = "btc_near_90d_low_daily_bear_probe"
    elif btc.get("ema_bull") and pos >= 0.55 and chg30 >= 5.0:
        regime = GLOBAL_BULL_RECOVERY
        reason = "btc_daily_bull_recovery"
    elif pos >= 0.65 and chg30 <= 3.0:
        regime = GLOBAL_RANGE_DISTRIBUTION
        reason = "btc_upper_range_stalling"
    elif pos <= 0.35 and abs(chg30) <= 8.0:
        regime = GLOBAL_RANGE_ACCUMULATION
        reason = "btc_lower_range_chop"
    else:
        regime = GLOBAL_UNKNOWN
        reason = "btc_daily_mixed"

    return {
        "global_regime": regime,
        "reason": reason,
        "btc": btc,
        "eth": eth,
    }


def evaluate_global_daily_regime(cur) -> Dict[str, Any]:
    from app.services.brain_market_analyzer import _fetch_klines

    btc_rows = _fetch_klines(cur, "BTCUSDT", "1d", 120)
    eth_rows = _fetch_klines(cur, "ETHUSDT", "1d", 120)
    return classify_global_daily_regime_from_rows(btc_rows, eth_rows)


def _signals(playbook_row: Dict[str, Any]) -> Set[str]:
    return set(playbook_row.get("signals") or [])


def _features(playbook_row: Dict[str, Any]) -> Dict[str, Any]:
    return playbook_row.get("features") or {}


def _strong_token_side(playbook_row: Dict[str, Any], side: str) -> bool:
    feat = _features(playbook_row)
    side_u = (side or "").upper()
    if side_u not in ("LONG", "SHORT"):
        return False
    return feat.get("h1_side") == side_u and feat.get("m15_side") == side_u


def _token_crash(playbook_row: Dict[str, Any]) -> bool:
    sig = _signals(playbook_row)
    return bool("crash_spike" in sig and ("volume_expand_down" in sig or "break_support" in sig))


def _token_impulse_long(playbook_row: Dict[str, Any]) -> bool:
    sig = _signals(playbook_row)
    return bool(
        "impulse_up" in sig
        or ("h1_breakout_up" in sig and ("volume_expand_up" in sig or "break_resistance" in sig))
        or ("break_resistance" in sig and "volume_expand_up" in sig)
    )


def _token_failed_bounce_short(playbook_row: Dict[str, Any]) -> bool:
    sig = _signals(playbook_row)
    feats = playbook_row.get("features") or {}
    downtrend = bool(
        feats.get("ema_bear")
        or "ema_bear_align" in sig
        or "lh_ll" in sig
        or feats.get("lh_ll")
        or feats.get("h1_side") == "SHORT"
    )
    bounce = bool(
        "15m_lower_high" in sig
        or feats.get("vol_shrink_pullback")
        or "volume_shrink_pullback" in sig
        or feats.get("long_upper_wick")
        or "long_upper_wick" in sig
    )
    reject = bool(
        "ema_reject" in sig
        or "long_upper_wick" in sig
        or feats.get("long_upper_wick")
        or "15m_lower_high" in sig
        or "break_support" in sig
        or feats.get("break_support")
    )
    return bool(downtrend and bounce and reject and not (feats.get("hh_hl") and feats.get("vol_up")))


def _token_exhaustion_short(playbook_row: Dict[str, Any]) -> bool:
    sig = _signals(playbook_row)
    return bool(
        "exhaustion_up" in sig
        or "stall_at_high" in sig
        or ("false_break_up" in sig and ("long_upper_wick" in sig or "volume_expand_down" in sig))
        or ("pump_spike" in sig and ("long_upper_wick" in sig or "volume_diverge_bear" in sig))
    )


def _panic_rebound(playbook_row: Dict[str, Any]) -> bool:
    sig = _signals(playbook_row)
    return bool("crash_spike" in sig and ("long_lower_wick" in sig or "15m_stop_new_low" in sig))


def classify_brain_regime(
    big4: Dict[str, Any],
    playbook_row: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """Return a per-symbol regime using Big4 plus token features."""
    playbook_row = playbook_row or {}
    big4_ok = bool(big4.get("big4_ok"))
    if not big4_ok:
        side = str(playbook_row.get("side") or "FLAT").upper()
        if side == "LONG" and _token_impulse_long(playbook_row):
            return TOKEN_DIVERGENCE, f"big4_weak_token_long_impulse:{big4.get('reason') or ''}"
        if side == "SHORT" and _token_exhaustion_short(playbook_row):
            return TOKEN_DIVERGENCE, f"big4_weak_token_short_exhaustion:{big4.get('reason') or ''}"
        return LOW_VOL_NO_TRADE, str(big4.get("reason") or "big4_weak")

    bias = str(big4.get("bias") or "FLAT").upper()
    bull_n = int(big4.get("bull_count") or 0)
    bear_n = int(big4.get("bear_count") or 0)

    if _panic_rebound(playbook_row):
        return PANIC_REBOUND, "token_panic_rebound"

    if _token_crash(playbook_row):
        return CRASH_DOWN, "token_crash_down"

    if bias == "LONG":
        return BULL_TREND, "big4_long"
    if bias == "SHORT":
        return BEAR_TREND, "big4_short"

    side = str(playbook_row.get("side") or "FLAT").upper()
    if bias == "FLAT" and side in ("LONG", "SHORT") and _strong_token_side(playbook_row, side):
        return TOKEN_DIVERGENCE, f"big4_flat_token_{side.lower()}"

    # Mixed 2-vs-2 or 2-vs-rest Big4 votes are treated as transition, not neutral safety.
    if bias == "FLAT" and max(bull_n, bear_n) >= 2:
        return TRANSITION, f"big4_transition_bull{bull_n}_bear{bear_n}"

    return RANGE_CHOP, "big4_flat_range"


def brain_open_regime_decision(
    *,
    big4: Dict[str, Any],
    playbook_row: Dict[str, Any],
    side: str,
    playbook: str,
    global_regime: Optional[Dict[str, Any]] = None,
) -> RegimeDecision:
    """Apply approved scenario allow-list for BRAIN openings."""
    side_u = (side or "").upper()
    pb = str(playbook or "")
    sig = _signals(playbook_row)
    edge = float(playbook_row.get("edge_score") or 0)
    confirmed = bool(playbook_row.get("confirmed"))
    regime, why = classify_brain_regime(big4, playbook_row)
    global_regime = global_regime or {}
    global_name = str(global_regime.get("global_regime") or GLOBAL_UNKNOWN)

    if global_name == GLOBAL_DAILY_BEAR_PROBE:
        if side_u == "LONG":
            if pb == "C3" and confirmed and edge >= 0.75 and _token_impulse_long(playbook_row):
                return RegimeDecision(
                    regime,
                    f"global_daily_bear_probe_allows_token_impulse_C3:{why}",
                    "pullback_limit",
                    0.25,
                )
            return RegimeDecision(
                regime,
                f"global_daily_bear_probe_blocks_long:{regime}:{pb}",
                "shadow_only",
                0.0,
            )
        if (
            side_u == "SHORT"
            and pb == "C1"
            and confirmed
            and edge >= 0.80
            and bool(sig & {"break_support", "volume_expand_down", "crash_spike"})
        ):
            return RegimeDecision(
                regime,
                f"global_daily_bear_probe_allows_breakdown_C1:{why}",
                "crash_probe_limit",
                0.50,
            )
        if side_u == "SHORT" and pb == "A2" and confirmed and edge >= 0.80 and _token_failed_bounce_short(playbook_row):
            return RegimeDecision(
                regime,
                f"global_daily_bear_probe_allows_A2:{why}",
                "pullback_limit",
                0.45,
            )
        if side_u == "SHORT" and pb == "B2" and confirmed and edge >= 0.80 and _token_failed_bounce_short(playbook_row):
            return RegimeDecision(
                regime,
                f"global_daily_bear_probe_allows_B2:{why}",
                "crash_probe_limit",
                0.30,
            )
        if side_u == "SHORT" and pb in {"B3", "C4"} and confirmed and edge >= 0.75 and _token_exhaustion_short(playbook_row):
            return RegimeDecision(
                regime,
                f"global_daily_bear_probe_allows_exhaustion_{pb}:{why}",
                "breakout_confirm_limit",
                0.25,
            )
        return RegimeDecision(
            regime,
            f"global_daily_bear_probe_blocks_{side_u}_{pb}",
            "shadow_only",
            0.0,
        )

    if global_name == GLOBAL_RELIEF_BOUNCE:
        if side_u == "SHORT" and pb == "A2" and confirmed and edge >= 0.80 and _token_failed_bounce_short(playbook_row):
            return RegimeDecision(
                regime,
                "global_relief_bounce_allows_A2_reject",
                "pullback_limit",
                0.45,
            )
        if side_u == "SHORT" and pb == "B2" and confirmed and "break_support" in sig and _token_failed_bounce_short(playbook_row):
            return RegimeDecision(
                regime,
                "global_relief_bounce_allows_B2_fail",
                "crash_probe_limit",
                0.30,
            )
        if side_u == "SHORT" and pb in {"C1"}:
            return RegimeDecision(
                regime,
                f"global_relief_bounce_blocks_fresh_short:{regime}:{pb}",
                "shadow_only",
                0.0,
            )
        if side_u == "LONG" and pb == "A1" and confirmed and edge >= 0.95 and _strong_token_side(playbook_row, "LONG"):
            return RegimeDecision(
                regime,
                "global_relief_bounce_allows_only_strong_A1",
                "pullback_limit",
                0.25,
            )

    if regime == LOW_VOL_NO_TRADE:
        return RegimeDecision(regime, f"regime_low_vol_no_trade:{why}", "shadow_only", 0.0)

    if regime == BULL_TREND:
        if side_u == "LONG" and pb == "A1":
            return RegimeDecision(regime, "regime_bull_allows_A1", "pullback_limit", 1.0)
        if side_u == "LONG" and pb == "C3" and confirmed and _token_impulse_long(playbook_row):
            return RegimeDecision(regime, "regime_bull_allows_C3_impulse", "pullback_limit", 0.50)
        if side_u == "SHORT" and pb in {"B3", "C4"} and confirmed and edge >= 0.85 and _token_exhaustion_short(playbook_row):
            return RegimeDecision(regime, f"regime_bull_allows_exhaustion_{pb}", "breakout_confirm_limit", 0.20)
        return RegimeDecision(regime, f"regime_bull_blocks_{side_u}_{pb}", "shadow_only", 0.0)

    if regime == BEAR_TREND:
        if side_u == "SHORT" and pb == "C1":
            return RegimeDecision(regime, "regime_bear_allows_C1_follow", "crash_probe_limit", 0.35)
        if side_u == "SHORT" and pb == "A2" and confirmed and _token_failed_bounce_short(playbook_row):
            return RegimeDecision(regime, "regime_bear_allows_A2", "pullback_limit", 0.45)
        if side_u == "SHORT" and pb == "B2" and confirmed and _token_failed_bounce_short(playbook_row):
            return RegimeDecision(regime, "regime_bear_allows_B2", "crash_probe_limit", 0.30)
        if side_u == "SHORT" and pb in {"B3", "C4"} and confirmed and _token_exhaustion_short(playbook_row):
            return RegimeDecision(regime, f"regime_bear_allows_exhaustion_{pb}", "breakout_confirm_limit", 0.35)
        return RegimeDecision(regime, f"regime_bear_blocks_{side_u}_{pb}", "shadow_only", 0.0)

    if regime == CRASH_DOWN:
        if (
            side_u == "SHORT"
            and pb == "C1"
            and confirmed
            and edge >= 0.80
            and _strong_token_side(playbook_row, "SHORT")
            and bool(sig & {"crash_spike", "break_support", "volume_expand_down"})
        ):
            return RegimeDecision(regime, "regime_crash_allows_C1_follow", "crash_probe_limit", 0.25)
        if (
            side_u == "SHORT"
            and pb == "B2"
            and confirmed
            and edge >= 0.80
            and _token_failed_bounce_short(playbook_row)
        ):
            return RegimeDecision(regime, "regime_crash_allows_B2_fail", "crash_probe_limit", 0.25)
        return RegimeDecision(regime, f"regime_crash_blocks_{side_u}_{pb}", "shadow_only", 0.0)

    if regime == PANIC_REBOUND:
        return RegimeDecision(regime, f"regime_panic_rebound_shadow_{side_u}_{pb}", "shadow_only", 0.0)

    if regime == RANGE_CHOP:
        if side_u == "LONG" and pb == "A1" and confirmed and edge >= 0.90:
            return RegimeDecision(regime, f"regime_range_allows_high_edge_{pb}", "pullback_limit", 0.35)
        if side_u == "LONG" and pb == "C3" and confirmed and edge >= 0.75 and _token_impulse_long(playbook_row):
            return RegimeDecision(regime, "regime_range_allows_token_impulse_C3", "pullback_limit", 0.35)
        if side_u == "SHORT" and pb in {"B3", "C4"} and confirmed and edge >= 0.80 and _token_exhaustion_short(playbook_row):
            return RegimeDecision(regime, f"regime_range_allows_exhaustion_{pb}", "breakout_confirm_limit", 0.25)
        if side_u == "SHORT" and pb == "A2" and confirmed and edge >= 0.90 and _token_failed_bounce_short(playbook_row):
            return RegimeDecision(regime, "regime_range_allows_high_edge_A2", "pullback_limit", 0.30)
        return RegimeDecision(regime, f"regime_range_blocks_{side_u}_{pb}", "shadow_only", 0.0)

    if regime == TOKEN_DIVERGENCE:
        if side_u == "LONG" and pb == "A1" and confirmed and edge >= 0.80:
            return RegimeDecision(regime, "regime_token_divergence_allows_A1", "pullback_limit", 0.50)
        if side_u == "LONG" and pb == "C3" and confirmed and edge >= 0.75 and _token_impulse_long(playbook_row):
            return RegimeDecision(regime, "regime_token_divergence_allows_C3_impulse", "pullback_limit", 0.35)
        if (
            side_u == "SHORT"
            and pb == "C1"
            and confirmed
            and edge >= 0.80
            and bool(sig & {"crash_spike", "break_support", "volume_expand_down"})
        ):
            return RegimeDecision(regime, "regime_token_divergence_allows_C1_follow", "crash_probe_limit", 0.35)
        if (
            side_u == "SHORT"
            and pb == "A2"
            and confirmed
            and edge >= 0.80
            and _token_failed_bounce_short(playbook_row)
        ):
            return RegimeDecision(regime, "regime_token_divergence_allows_A2", "pullback_limit", 0.40)
        if (
            side_u == "SHORT"
            and pb == "B2"
            and confirmed
            and edge >= 0.80
            and _token_failed_bounce_short(playbook_row)
        ):
            return RegimeDecision(regime, "regime_token_divergence_allows_B2", "crash_probe_limit", 0.30)
        if side_u == "SHORT" and pb in {"B3", "C4"} and confirmed and edge >= 0.75 and _token_exhaustion_short(playbook_row):
            return RegimeDecision(regime, f"regime_token_divergence_allows_exhaustion_{pb}", "breakout_confirm_limit", 0.25)
        return RegimeDecision(regime, f"regime_token_divergence_blocks_{side_u}_{pb}", "shadow_only", 0.0)

    if regime == TRANSITION:
        # Transition forbids old-direction inertia. Only strong A1 may pass.
        if side_u == "LONG" and pb == "A1" and confirmed and edge >= 0.95 and _strong_token_side(playbook_row, side_u):
            return RegimeDecision(regime, f"regime_transition_allows_strong_{side_u}_{pb}", "pullback_limit", 0.25)
        return RegimeDecision(regime, f"regime_transition_blocks_{side_u}_{pb}", "shadow_only", 0.0)

    return RegimeDecision(regime, f"regime_unknown_blocks_{side_u}_{pb}", "shadow_only", 0.0)
