"""BRAIN 按币风控参数评估 — docs/REQUIREMENTS_LOGIC_ZH.md §7.3.16

开仓瞬间：Playbook + 15m ATR + 插针 → sl_pct / tp_pct / hold_hours（百分点）。
失败时返回 fallback（BRAIN_SL_PCT / TP / HOLD），并标记 risk_fallback。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.brain_config import (
    BRAIN_HOLD_HOURS,
    BRAIN_HOLD_MAX_HOURS,
    BRAIN_HOLD_MIN_HOURS,
    BRAIN_RR_MIN,
    BRAIN_SL_MAX_PCT,
    BRAIN_SL_MIN_PCT,
    BRAIN_SL_PCT,
    BRAIN_TP_MAX_PCT,
    BRAIN_TP_MIN_PCT,
    BRAIN_TP_PCT,
)


# Playbook 族：ATR 倍数 + 基准持仓小时
_PLAYBOOK_RISK = {
    "A": {"sl_atr": 2.2, "tp_atr": 3.5, "hold_h": 5.0},
    "B": {"sl_atr": 1.7, "tp_atr": 2.8, "hold_h": 2.5},
    "C": {"sl_atr": 1.9, "tp_atr": 3.0, "hold_h": 3.5},
    "_": {"sl_atr": 2.0, "tp_atr": 3.2, "hold_h": float(BRAIN_HOLD_HOURS)},
}


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def atr_pct_from_rows(rows_15m: List[Dict], period: int = 14) -> Optional[float]:
    """15m ATR 占最新收盘价的百分比（如 1.2 = 1.2%）。"""
    if not rows_15m or len(rows_15m) < period + 1:
        return None
    trs: List[float] = []
    for i in range(1, len(rows_15m)):
        h = _f(rows_15m[i].get("high_price"))
        l = _f(rows_15m[i].get("low_price"))
        pc = _f(rows_15m[i - 1].get("close_price"))
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    atr = sum(trs[-period:]) / period
    close = _f(rows_15m[-1].get("close_price"))
    if close <= 0 or atr <= 0:
        return None
    return (atr / close) * 100.0


def _family(playbook: str) -> str:
    p = (playbook or "").strip().upper()
    if p.startswith("A"):
        return "A"
    if p.startswith("B"):
        return "B"
    if p.startswith("C"):
        return "C"
    return "_"


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def evaluate_brain_risk_params(
    *,
    playbook: str,
    side: str,
    rows_15m: Optional[List[Dict]] = None,
    wick: Optional[Dict[str, Any]] = None,
    win_prob: Optional[float] = None,
    edge_score: Optional[float] = None,
    atr_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """返回 sl_pct / tp_pct / hold_hours / risk_meta（百分点单位）。"""
    fam = _family(playbook)
    cfg = _PLAYBOOK_RISK.get(fam) or _PLAYBOOK_RISK["_"]
    atr = atr_pct if atr_pct is not None else atr_pct_from_rows(rows_15m or [])
    wick = wick or {}
    fallback = False

    if atr is None or atr <= 0:
        fallback = True
        atr = 1.2  # 保守默认波幅%
        sl = float(BRAIN_SL_PCT)
        tp = float(BRAIN_TP_PCT)
        hold = float(BRAIN_HOLD_HOURS)
    else:
        sl = float(cfg["sl_atr"]) * atr
        tp = float(cfg["tp_atr"]) * atr
        hold = float(cfg["hold_h"])

    # 插针频繁：略放宽 SL，避免贴在平均影线内
    if wick.get("frequent"):
        sl *= 1.25

    # 胜率贴门 / 低 edge：收紧并缩短
    wp = _f(win_prob, 0.0)
    edge = _f(edge_score, 0.0)
    if 0 < wp < 0.58:
        sl *= 0.9
        hold *= 0.75
    if 0 < edge < 0.55:
        hold *= 0.85

    sl = _clamp(sl, BRAIN_SL_MIN_PCT, BRAIN_SL_MAX_PCT)
    tp = _clamp(tp, BRAIN_TP_MIN_PCT, BRAIN_TP_MAX_PCT)
    hold = _clamp(hold, BRAIN_HOLD_MIN_HOURS, BRAIN_HOLD_MAX_HOURS)

    # 风险回报不足：抬 TP 或缩 hold
    if sl > 0 and tp / sl < BRAIN_RR_MIN:
        tp = _clamp(sl * BRAIN_RR_MIN, BRAIN_TP_MIN_PCT, BRAIN_TP_MAX_PCT)
        if tp / sl < BRAIN_RR_MIN:
            hold = _clamp(hold * 0.7, BRAIN_HOLD_MIN_HOURS, BRAIN_HOLD_MAX_HOURS)

    # 锁利激活线：评估 TP 的 40%，夹在 1.2%~3%
    trail_activate = _clamp(tp * 0.40, 1.2, 3.0)
    trail_pullback = _clamp(max(0.6, trail_activate * 0.45), 0.6, 1.5)
    trail_min_keep = 0.25  # 价格%，约保本+费缓冲

    meta = {
        "playbook": (playbook or "").upper(),
        "family": fam,
        "side": (side or "").upper(),
        "atr_pct": round(atr, 4) if atr is not None else None,
        "wick_frequent": bool(wick.get("frequent")),
        "win_prob": wp or None,
        "edge_score": edge or None,
        "fallback": fallback,
        "trail_activate_pct": round(trail_activate, 3),
        "trail_pullback_pct": round(trail_pullback, 3),
        "trail_min_keep_pct": trail_min_keep,
    }
    return {
        "sl_pct": round(sl, 2),
        "tp_pct": round(tp, 2),
        "hold_hours": round(hold, 2),
        "risk_meta": meta,
        "risk_fallback": fallback,
    }
