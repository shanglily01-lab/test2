"""Midline/breakout programmatic sell points.

Old ai-trail-tp only armed after a 3% peak — the same level as hard TP — so
+1–2.9% winners were held to 8h and often given back. This module locks earlier,
cuts profit-to-loss, and shortens breakout holds.

v4.5.18: Big4 LONG 时多单改用更宽 trail，并关掉/放宽 no_follow，避免主升浪被 90 分钟
-1.2% 回踩闷杀。
"""
from __future__ import annotations

from typing import Optional, Sequence

# Price percent (not ROI). 5x * 1.2% ≈ 6% margin ROI.
MIDLINE_TRAIL_ACTIVATE_PCT = 1.20
MIDLINE_TRAIL_PULLBACK_PCT = 0.45
MIDLINE_TRAIL_MIN_KEEP_PCT = 0.25

MIDLINE_BULL_TRAIL_ACTIVATE_PCT = 2.50
MIDLINE_BULL_TRAIL_PULLBACK_PCT = 0.80
MIDLINE_BULL_TRAIL_MIN_KEEP_PCT = 0.50

MIDLINE_RALLY_TRAIL_ACTIVATE_PCT = 3.00
MIDLINE_RALLY_TRAIL_PULLBACK_PCT = 1.10
MIDLINE_RALLY_TRAIL_MIN_KEEP_PCT = 0.80

MIDLINE_GIVEBACK_PEAK_PCT = 1.00
MIDLINE_GIVEBACK_NOW_PCT = 0.05
MIDLINE_GIVEBACK_MIN_AGE_MIN = 25
MIDLINE_RALLY_GIVEBACK_PEAK_PCT = 2.00
MIDLINE_RALLY_GIVEBACK_NOW_PCT = 0.35
MIDLINE_RALLY_GIVEBACK_MIN_AGE_MIN = 35

MIDLINE_NO_FOLLOW_AGE_MIN = 90
MIDLINE_NO_FOLLOW_MAX_PEAK_PCT = 0.80
MIDLINE_NO_FOLLOW_LOSS_PCT = -1.20
# 多单非明确多头趋势时仍可早砍，但必须更晚更深，避免趋势回踩被当失败。
MIDLINE_NO_FOLLOW_LONG_AGE_MIN = 180
MIDLINE_NO_FOLLOW_LONG_MAX_PEAK_PCT = 1.00
MIDLINE_NO_FOLLOW_LONG_LOSS_PCT = -2.50

MIDLINE_HOLD_HOURS_BY_PLAYBOOK = {
    "A1": 6.0,
    "A2": 6.0,
    "B2": 4.0,
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


def is_bull_long_hold(side: str | None, market_bias: str | None) -> bool:
    return str(side or "").upper() == "LONG" and str(market_bias or "").upper() == "LONG"


def _signal_set(signals: Optional[Sequence[str]]) -> set[str]:
    return {str(s) for s in (signals or [])}


def _is_rally_runner(playbook: str | None = None, signals: Optional[Sequence[str]] = None) -> bool:
    pb = str(playbook or "").strip().upper()
    sig = _signal_set(signals)
    if pb == "C3":
        return True
    bullish_impulse = bool(sig & {"volume_expand_up", "pump_spike", "impulse_up", "h1_breakout_up"})
    bearish_exhaustion = bool(sig & {"exhaustion_up", "false_break_up", "rsi_15m_turn_down", "long_upper_wick"})
    return bullish_impulse and not bearish_exhaustion


def _trail_profile(
    playbook: str | None = None,
    signals: Optional[Sequence[str]] = None,
    *,
    side: str | None = None,
    market_bias: str | None = None,
) -> tuple[float, float, float, str]:
    if _is_rally_runner(playbook, signals):
        return (
            MIDLINE_RALLY_TRAIL_ACTIVATE_PCT,
            MIDLINE_RALLY_TRAIL_PULLBACK_PCT,
            MIDLINE_RALLY_TRAIL_MIN_KEEP_PCT,
            "rally",
        )
    if is_bull_long_hold(side, market_bias):
        return (
            MIDLINE_BULL_TRAIL_ACTIVATE_PCT,
            MIDLINE_BULL_TRAIL_PULLBACK_PCT,
            MIDLINE_BULL_TRAIL_MIN_KEEP_PCT,
            "bull",
        )
    return (
        MIDLINE_TRAIL_ACTIVATE_PCT,
        MIDLINE_TRAIL_PULLBACK_PCT,
        MIDLINE_TRAIL_MIN_KEEP_PCT,
        "normal",
    )


def check_midline_trail_lock(
    pnl_pct: float,
    peak_pct: float,
    *,
    playbook: str | None = None,
    signals: Optional[Sequence[str]] = None,
    side: str | None = None,
    market_bias: str | None = None,
) -> Optional[str]:
    """Price fractions: 0.012 = 1.2%."""
    act_pct, pull_pct, keep_pct, profile = _trail_profile(
        playbook, signals, side=side, market_bias=market_bias,
    )
    act = act_pct / 100.0
    pull = pull_pct / 100.0
    keep = keep_pct / 100.0
    if peak_pct < act:
        return None
    drawdown = peak_pct - pnl_pct
    if drawdown < pull:
        return None
    if pnl_pct < keep:
        return (
            f"midline_trail_lock:{profile}(peak={peak_pct * 100:.2f}%, "
            f"dd={drawdown * 100:.2f}%, now={pnl_pct * 100:.2f}%, below_keep)"
        )
    return (
        f"midline_trail_lock:{profile}(peak={peak_pct * 100:.2f}%, "
        f"dd={drawdown * 100:.2f}%, now={pnl_pct * 100:.2f}%)"
    )


def check_midline_giveback(
    pnl_pct: float,
    peak_pct: float,
    age_s: float,
    *,
    playbook: str | None = None,
    signals: Optional[Sequence[str]] = None,
    side: str | None = None,
    market_bias: str | None = None,
) -> Optional[str]:
    """Had a real peak, now flat/red — do not wait for 8h or hard SL."""
    use_wide = _is_rally_runner(playbook, signals) or is_bull_long_hold(side, market_bias)
    if use_wide:
        peak_line = MIDLINE_RALLY_GIVEBACK_PEAK_PCT / 100.0
        now_line = MIDLINE_RALLY_GIVEBACK_NOW_PCT / 100.0
        min_age_s = MIDLINE_RALLY_GIVEBACK_MIN_AGE_MIN * 60
        profile = "rally" if _is_rally_runner(playbook, signals) else "bull"
    else:
        peak_line = MIDLINE_GIVEBACK_PEAK_PCT / 100.0
        now_line = MIDLINE_GIVEBACK_NOW_PCT / 100.0
        min_age_s = MIDLINE_GIVEBACK_MIN_AGE_MIN * 60
        profile = "normal"
    if age_s < min_age_s:
        return None
    if peak_pct >= peak_line and pnl_pct <= now_line:
        return (
            f"midline_giveback:{profile}(peak={peak_pct * 100:.2f}%, "
            f"now={pnl_pct * 100:.2f}%, age={age_s / 60:.0f}m)"
        )
    return None


def check_midline_no_follow(
    pnl_pct: float,
    peak_pct: float,
    age_s: float,
    *,
    playbook: str | None = None,
    signals: Optional[Sequence[str]] = None,
    side: str | None = None,
    market_bias: str | None = None,
) -> Optional[str]:
    if is_bull_long_hold(side, market_bias) or _is_rally_runner(playbook, signals):
        return None
    age_min = MIDLINE_NO_FOLLOW_AGE_MIN
    peak_max = MIDLINE_NO_FOLLOW_MAX_PEAK_PCT
    loss_line = MIDLINE_NO_FOLLOW_LOSS_PCT
    if str(side or "").upper() == "LONG":
        age_min = MIDLINE_NO_FOLLOW_LONG_AGE_MIN
        peak_max = MIDLINE_NO_FOLLOW_LONG_MAX_PEAK_PCT
        loss_line = MIDLINE_NO_FOLLOW_LONG_LOSS_PCT
    if age_s < age_min * 60:
        return None
    if peak_pct <= peak_max / 100.0 and pnl_pct <= loss_line / 100.0:
        return (
            f"midline_no_follow(age={age_s / 60:.0f}m, "
            f"peak={peak_pct * 100:.2f}%, pnl={pnl_pct * 100:.2f}%)"
        )
    return None


def check_midline_hold_exits(
    pnl_pct: float,
    peak_pct: float,
    age_s: float,
    *,
    playbook: str | None = None,
    signals: Optional[Sequence[str]] = None,
    side: str | None = None,
    market_bias: str | None = None,
) -> Optional[str]:
    kw = dict(playbook=playbook, signals=signals, side=side, market_bias=market_bias)
    return (
        check_midline_giveback(pnl_pct, peak_pct, age_s, **kw)
        or check_midline_trail_lock(pnl_pct, peak_pct, **kw)
        or check_midline_no_follow(pnl_pct, peak_pct, age_s, **kw)
    )


def midline_expiry_should_extend(
    pnl_pct: float,
    peak_pct: float,
    *,
    playbook: str | None = None,
    signals: Optional[Sequence[str]] = None,
    side: str | None = None,
    market_bias: str | None = None,
) -> bool:
    """At planned close: keep only if still working and not giving back."""
    _, pull_pct, _, _ = _trail_profile(
        playbook, signals, side=side, market_bias=market_bias,
    )
    if pnl_pct < MIDLINE_HOLD_EXTEND_MIN_PNL_PCT / 100.0:
        return False
    if peak_pct - pnl_pct >= pull_pct / 100.0:
        return False
    return True
