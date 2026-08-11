"""BRAIN market regime classifier and scenario gates.

The regime layer is intentionally conservative. It decides which playbooks are
eligible before win-rate, edge, cooldown, and account gates run.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Set, Tuple


BULL_TREND = "BULL_TREND"
BEAR_TREND = "BEAR_TREND"
CRASH_DOWN = "CRASH_DOWN"
PANIC_REBOUND = "PANIC_REBOUND"
RANGE_CHOP = "RANGE_CHOP"
LOW_VOL_NO_TRADE = "LOW_VOL_NO_TRADE"
TOKEN_DIVERGENCE = "TOKEN_DIVERGENCE"
TRANSITION = "TRANSITION"


@dataclass(frozen=True)
class RegimeDecision:
    regime: str
    reason: str
    execution_mode: str
    margin_multiplier: float = 1.0


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
) -> RegimeDecision:
    """Apply approved scenario allow-list for BRAIN openings."""
    side_u = (side or "").upper()
    pb = str(playbook or "")
    sig = _signals(playbook_row)
    edge = float(playbook_row.get("edge_score") or 0)
    confirmed = bool(playbook_row.get("confirmed"))
    regime, why = classify_brain_regime(big4, playbook_row)

    if regime == LOW_VOL_NO_TRADE:
        return RegimeDecision(regime, f"regime_low_vol_no_trade:{why}", "shadow_only", 0.0)

    if regime == BULL_TREND:
        if side_u == "LONG" and pb == "A1":
            return RegimeDecision(regime, "regime_bull_allows_A1", "pullback_limit", 1.0)
        return RegimeDecision(regime, f"regime_bull_blocks_{side_u}_{pb}", "shadow_only", 0.0)

    if regime == BEAR_TREND:
        if side_u == "SHORT" and pb == "A2":
            return RegimeDecision(regime, "regime_bear_allows_A2", "pullback_limit", 0.50)
        if side_u == "SHORT" and pb == "C1":
            return RegimeDecision(regime, "regime_bear_allows_C1_probe", "breakout_confirm_limit", 0.35)
        return RegimeDecision(regime, f"regime_bear_blocks_{side_u}_{pb}", "shadow_only", 0.0)

    if regime == CRASH_DOWN:
        if (
            side_u == "SHORT"
            and pb in {"A2", "C1"}
            and confirmed
            and edge >= 0.80
            and _strong_token_side(playbook_row, "SHORT")
            and bool(sig & {"crash_spike", "break_support", "volume_expand_down"})
        ):
            mult = 0.35 if pb == "A2" else 0.25
            mode = "crash_probe_limit" if pb == "C1" else "pullback_limit"
            return RegimeDecision(regime, f"regime_crash_allows_{pb}_probe", mode, mult)
        return RegimeDecision(regime, f"regime_crash_blocks_{side_u}_{pb}", "shadow_only", 0.0)

    if regime == PANIC_REBOUND:
        return RegimeDecision(regime, f"regime_panic_rebound_shadow_{side_u}_{pb}", "shadow_only", 0.0)

    if regime == RANGE_CHOP:
        if side_u in ("LONG", "SHORT") and pb in {"A1", "A2"} and confirmed and edge >= 0.90:
            return RegimeDecision(regime, f"regime_range_allows_high_edge_{pb}", "pullback_limit", 0.35)
        return RegimeDecision(regime, f"regime_range_blocks_{side_u}_{pb}", "shadow_only", 0.0)

    if regime == TOKEN_DIVERGENCE:
        if side_u == "LONG" and pb == "A1" and confirmed and edge >= 0.80:
            return RegimeDecision(regime, "regime_token_divergence_allows_A1", "pullback_limit", 0.50)
        if side_u == "SHORT" and pb == "A2" and confirmed and edge >= 0.80:
            return RegimeDecision(regime, "regime_token_divergence_allows_A2", "pullback_limit", 0.50)
        if (
            side_u == "SHORT"
            and pb == "C1"
            and confirmed
            and edge >= 0.80
            and bool(sig & {"crash_spike", "break_support", "volume_expand_down"})
        ):
            return RegimeDecision(regime, "regime_token_divergence_allows_C1_probe", "breakout_confirm_limit", 0.35)
        return RegimeDecision(regime, f"regime_token_divergence_blocks_{side_u}_{pb}", "shadow_only", 0.0)

    if regime == TRANSITION:
        # Transition forbids old-direction inertia. Only strong, confirmed token divergence may pass.
        if side_u in ("LONG", "SHORT") and pb in {"A1", "A2"} and confirmed and edge >= 0.95 and _strong_token_side(playbook_row, side_u):
            return RegimeDecision(regime, f"regime_transition_allows_strong_{side_u}_{pb}", "pullback_limit", 0.25)
        return RegimeDecision(regime, f"regime_transition_blocks_{side_u}_{pb}", "shadow_only", 0.0)

    return RegimeDecision(regime, f"regime_unknown_blocks_{side_u}_{pb}", "shadow_only", 0.0)
