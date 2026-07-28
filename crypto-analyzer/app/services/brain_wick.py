"""REQ-BRAIN 插针统计 — 影线 > 实体×2；近 7 日频次与平均幅度。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from app.services.brain_config import WICK_BODY_RATIO, WICK_FREQUENT_RATIO


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def bar_wick_metrics(open_p: float, high: float, low: float, close: float) -> Dict[str, float]:
    """单根 K：上/下影与实体。"""
    body = abs(close - open_p)
    upper = high - max(open_p, close)
    lower = min(open_p, close) - low
    if upper < 0:
        upper = 0.0
    if lower < 0:
        lower = 0.0
    return {
        "body": body,
        "upper": upper,
        "lower": lower,
        "upper_is_wick": body > 0 and upper > body * WICK_BODY_RATIO,
        "lower_is_wick": body > 0 and lower > body * WICK_BODY_RATIO,
    }


def analyze_wicks(
    bars: Sequence[Dict[str, Any]],
    *,
    frequent_ratio: float = WICK_FREQUENT_RATIO,
) -> Dict[str, Any]:
    """
    bars: 需含 open_price/high_price/low_price/close_price（或 open/high/low/close）。
    返回上下影频次、平均影线幅度(相对收盘%)、是否频繁。
    """
    upper_n = 0
    lower_n = 0
    upper_pcts: List[float] = []
    lower_pcts: List[float] = []
    n = 0

    for b in bars:
        o = _f(b.get("open_price", b.get("open")))
        h = _f(b.get("high_price", b.get("high")))
        l = _f(b.get("low_price", b.get("low")))
        c = _f(b.get("close_price", b.get("close")))
        if c <= 0 or h <= 0 or l <= 0:
            continue
        n += 1
        m = bar_wick_metrics(o, h, l, c)
        if m["upper_is_wick"]:
            upper_n += 1
            upper_pcts.append(m["upper"] / c * 100.0)
        if m["lower_is_wick"]:
            lower_n += 1
            lower_pcts.append(m["lower"] / c * 100.0)

    total_wicks = upper_n + lower_n
    ratio = (total_wicks / n) if n else 0.0
    avg_upper = sum(upper_pcts) / len(upper_pcts) if upper_pcts else 0.0
    avg_lower = sum(lower_pcts) / len(lower_pcts) if lower_pcts else 0.0

    return {
        "bars": n,
        "upper_wick_n": upper_n,
        "lower_wick_n": lower_n,
        "wick_n": total_wicks,
        "wick_ratio": round(ratio, 4),
        "avg_upper_wick_pct": round(avg_upper, 3),
        "avg_lower_wick_pct": round(avg_lower, 3),
        "frequent": bool(n >= 48 and ratio >= frequent_ratio),
        "wick_body_ratio_rule": WICK_BODY_RATIO,
    }


def limit_offset_pct_from_wicks(
    side: str,
    wick: Dict[str, Any],
    *,
    fallback_pct: float = 0.5,
    min_pct: float = 0.1,
    max_pct: float = 3.0,
) -> float:
    """频繁插针时：LONG 用平均下影%，SHORT 用平均上影%。"""
    s = (side or "").upper()
    if s == "LONG":
        raw = float(wick.get("avg_lower_wick_pct") or 0)
    else:
        raw = float(wick.get("avg_upper_wick_pct") or 0)
    if raw <= 0:
        raw = fallback_pct
    return max(min_pct, min(max_pct, raw))
