"""Midline/breakout programmatic sell points.

Old ai-trail-tp only armed after a 3% peak — the same level as hard TP — so
+1–2.9% winners were held to 8h and often given back. This module locks earlier,
cuts profit-to-loss, and shortens breakout holds.
"""
from __future__ import annotations

from typing import Optional

# Price percent (not ROI). 5x * 1.2% ≈ 6% margin ROI.
MIDLINE_TRAIL_ACTIVATE_PCT = 1.20
MIDLINE_TRAIL_PULLBACK_PCT = 0.45
MIDLINE_TRAIL_MIN_KEEP_PCT = 0.25

MIDLINE_GIVEBACK_PEAK_PCT = 1.00
MIDLINE_GIVEBACK_NOW_PCT = 0.05
MIDLINE_GIVEBACK_MIN_AGE_MIN = 25

MIDLINE_NO_FOLLOW_AGE_MIN = 90
MIDLINE_NO_FOLLOW_MAX_PEAK_PCT = 0.80
MIDLINE_NO_FOLLOW_LOSS_PCT = -1.20

MIDLINE_HOLD_HOURS_BY_PLAYBOOK = {
    "A1": 6.0,
    "A2": 6.0,
    "B3": 4.0,
    "C1": 4.0,
    "C3": 4.0,
    "C4": 4.0,
}
MIDLINE_HOLD_HOURS_DEFAULT = 6.0
MIDLINE_HOLD_EXTEND_HOURS = 2.0
MIDLINE_HOLD_EXTEND_MIN_PNL_PCT = 0.80


def midline_hold_hours(playbook: str | None) -> float:
    pb = str(playbook or "").strip().upper()
    return float(MIDLINE_HOLD_HOURS_BY_PLAYBOOK.get(pb, MIDLINE_HOLD_HOURS_DEFAULT))


def check_midline_trail_lock(pnl_pct: float, peak_pct: float) -> Optional[str]:
    """Price fractions: 0.012 = 1.2%."""
    act = MIDLINE_TRAIL_ACTIVATE_PCT / 100.0
    pull = MIDLINE_TRAIL_PULLBACK_PCT / 100.0
    keep = MIDLINE_TRAIL_MIN_KEEP_PCT / 100.0
    if peak_pct < act:
        return None
    drawdown = peak_pct - pnl_pct
    if drawdown < pull:
        return None
    if pnl_pct < keep:
        return (
            f"midline_trail_lock(peak={peak_pct * 100:.2f}%, "
            f"dd={drawdown * 100:.2f}%, now={pnl_pct * 100:.2f}%, below_keep)"
        )
    return (
        f"midline_trail_lock(peak={peak_pct * 100:.2f}%, "
        f"dd={drawdown * 100:.2f}%, now={pnl_pct * 100:.2f}%)"
    )


def check_midline_giveback(pnl_pct: float, peak_pct: float, age_s: float) -> Optional[str]:
    """Had a real peak, now flat/red — do not wait for 8h or hard SL."""
    if age_s < MIDLINE_GIVEBACK_MIN_AGE_MIN * 60:
        return None
    peak_line = MIDLINE_GIVEBACK_PEAK_PCT / 100.0
    now_line = MIDLINE_GIVEBACK_NOW_PCT / 100.0
    if peak_pct >= peak_line and pnl_pct <= now_line:
        return (
            f"midline_giveback(peak={peak_pct * 100:.2f}%, "
            f"now={pnl_pct * 100:.2f}%, age={age_s / 60:.0f}m)"
        )
    return None


def check_midline_no_follow(pnl_pct: float, peak_pct: float, age_s: float) -> Optional[str]:
    if age_s < MIDLINE_NO_FOLLOW_AGE_MIN * 60:
        return None
    if peak_pct <= MIDLINE_NO_FOLLOW_MAX_PEAK_PCT / 100.0 and pnl_pct <= MIDLINE_NO_FOLLOW_LOSS_PCT / 100.0:
        return (
            f"midline_no_follow(age={age_s / 60:.0f}m, "
            f"peak={peak_pct * 100:.2f}%, pnl={pnl_pct * 100:.2f}%)"
        )
    return None


def check_midline_hold_exits(
    pnl_pct: float,
    peak_pct: float,
    age_s: float,
) -> Optional[str]:
    return (
        check_midline_giveback(pnl_pct, peak_pct, age_s)
        or check_midline_trail_lock(pnl_pct, peak_pct)
        or check_midline_no_follow(pnl_pct, peak_pct, age_s)
    )


def midline_expiry_should_extend(pnl_pct: float, peak_pct: float) -> bool:
    """At planned close: keep only if still working and not giving back."""
    if pnl_pct < MIDLINE_HOLD_EXTEND_MIN_PNL_PCT / 100.0:
        return False
    if peak_pct - pnl_pct >= MIDLINE_TRAIL_PULLBACK_PCT / 100.0:
        return False
    return True
