"""BRAIN 程序化移动锁利 / 无跟进早砍 — 非 DeepSeek、非旧 ai-trail 常量路径。"""
from __future__ import annotations

from typing import Optional, Tuple

from app.services.brain_config import (
    BRAIN_SOFT_NO_FOLLOW_ENABLED,
    BRAIN_SOFT_NO_FOLLOW_LOSS_PCT,
    BRAIN_SOFT_NO_FOLLOW_MAX_PEAK_PCT,
    BRAIN_SOFT_NO_FOLLOW_MIN_AGE,
    BRAIN_TRAIL_ACTIVATE_MAX_PCT,
    BRAIN_TRAIL_ACTIVATE_MIN_PCT,
    BRAIN_TRAIL_ACTIVATE_PCT,
    BRAIN_TRAIL_ENABLED,
    BRAIN_TRAIL_MIN_KEEP_PCT,
    BRAIN_TRAIL_PULLBACK_PCT,
)


def trail_levels_from_sl_tp(sl_pct: float, tp_pct: float) -> Tuple[float, float, float]:
    """由本笔 SL/TP（百分点）推激活/回撤/保本线（百分点）。"""
    try:
        sl_f = max(0.0, float(sl_pct))
        tp_f = max(0.0, float(tp_pct))
    except (TypeError, ValueError):
        return (
            float(BRAIN_TRAIL_ACTIVATE_PCT),
            float(BRAIN_TRAIL_PULLBACK_PCT),
            float(BRAIN_TRAIL_MIN_KEEP_PCT),
        )
    if sl_f <= 0 and tp_f <= 0:
        return (
            float(BRAIN_TRAIL_ACTIVATE_PCT),
            float(BRAIN_TRAIL_PULLBACK_PCT),
            float(BRAIN_TRAIL_MIN_KEEP_PCT),
        )
    raw = tp_f * 0.40 if tp_f > 0 else sl_f * 0.50
    if sl_f > 0 and tp_f > 0:
        raw = min(tp_f * 0.40, sl_f * 0.50)
    elif sl_f > 0:
        raw = sl_f * 0.50
    act = max(BRAIN_TRAIL_ACTIVATE_MIN_PCT, min(BRAIN_TRAIL_ACTIVATE_MAX_PCT, raw))
    pull = max(0.5, min(1.0, max(0.5, act * 0.45)))
    return act, pull, float(BRAIN_TRAIL_MIN_KEEP_PCT)


def check_brain_trail_lock(
    pnl_pct: float,
    peak_pct: float,
    *,
    activate_pct: float | None = None,
    pullback_pct: float | None = None,
    min_keep_pct: float | None = None,
) -> Optional[str]:
    """价格维度小数（0.015=1.5%）。激活后从峰值回撤且仍≥保本缓冲 → 锁利平。"""
    if not BRAIN_TRAIL_ENABLED:
        return None
    act = float(activate_pct if activate_pct is not None else BRAIN_TRAIL_ACTIVATE_PCT) / 100.0
    pull = float(pullback_pct if pullback_pct is not None else BRAIN_TRAIL_PULLBACK_PCT) / 100.0
    keep = float(min_keep_pct if min_keep_pct is not None else BRAIN_TRAIL_MIN_KEEP_PCT) / 100.0
    if peak_pct < act:
        return None
    drawdown = peak_pct - pnl_pct
    if drawdown < pull:
        return None
    if pnl_pct < keep:
        # 已激活但吐到保本下：仍平，避免继续扛到硬 SL（赚过又亏光）
        return (
            f"brain_trail_lock(peak={peak_pct * 100:.2f}%, "
            f"dd={drawdown * 100:.2f}%, now={pnl_pct * 100:.2f}%, below_keep)"
        )
    return (
        f"brain_trail_lock(peak={peak_pct * 100:.2f}%, "
        f"dd={drawdown * 100:.2f}%, now={pnl_pct * 100:.2f}%)"
    )


def check_brain_soft_no_follow(
    pnl_pct: float,
    peak_pct: float,
    age_s: float,
) -> Optional[str]:
    """持仓一段时间无跟进且浮亏 → 早砍（价格%用 config 百分点换算）。"""
    if not BRAIN_SOFT_NO_FOLLOW_ENABLED:
        return None
    if age_s < BRAIN_SOFT_NO_FOLLOW_MIN_AGE * 60:
        return None
    max_peak = BRAIN_SOFT_NO_FOLLOW_MAX_PEAK_PCT / 100.0
    loss_line = BRAIN_SOFT_NO_FOLLOW_LOSS_PCT / 100.0
    if peak_pct <= max_peak and pnl_pct <= loss_line:
        return (
            f"brain_soft_no_follow(age={age_s / 60:.0f}m, "
            f"peak={peak_pct * 100:.2f}%, pnl={pnl_pct * 100:.2f}%)"
        )
    return None
