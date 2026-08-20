"""REQ-BRAIN v2 Playbook 识别 + 信号打标 — docs/REQUIREMENTS_LOGIC_ZH.md §7.3.10–7.3.12"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.brain_config import (
    BRAIN_EXHAUSTION_UPPER_WICK_MIN,
    BRAIN_IMPULSE_1H_BREAK_PCT,
    BRAIN_IMPULSE_1H_VOL_REL,
    CRASH_ATR_MULT,
    CRASH_LOOKBACK_BARS,
    PLAYBOOK_SIDE,
)
from app.services.brain_wick import analyze_wicks, bar_wick_metrics


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _ema(closes: List[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = c * k + ema * (1 - k)
    return ema


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
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


def _atr(rows: List[Dict], period: int = 14) -> Optional[float]:
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


def _structure_hh_hl(closes: List[float], lookback: int = 24) -> Tuple[bool, bool]:
    """Rough HH/HL vs LH/LL on last lookback closes (split halves)."""
    if len(closes) < lookback:
        return False, False
    w = closes[-lookback:]
    mid = lookback // 2
    first, second = w[:mid], w[mid:]
    hh = max(second) > max(first) and min(second) > min(first)
    ll = max(second) < max(first) and min(second) < min(first)
    return hh, ll


def extract_features(
    rows_1h: List[Dict],
    rows_15m: List[Dict],
    *,
    big4: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """从 1H/15M K 线抽取特征与信号 tags。"""
    big4 = big4 or {}
    feats: Dict[str, Any] = {
        "signals": [],
        "ref_price": None,
        "rsi_1h": None,
        "rsi_15m": None,
        "ema20_1h": None,
        "ema60_1h": None,
        "ema20_15m": None,
        "crash_spike": False,
        "pump_spike": False,
        "h1_breakout_up": False,
        "h1_breakdown_down": False,
        "impulse_up": False,
        "impulse_down": False,
        "exhaustion_up": False,
        "vol_up": False,
        "vol_down": False,
        "vol_shrink_pullback": False,
        "hh_hl": False,
        "lh_ll": False,
        "stop_new_low": False,
        "stop_new_high": False,
        "stall_at_high": False,
        "long_lower_wick": False,
        "long_upper_wick": False,
        "ema_bull": False,
        "ema_bear": False,
        "break_support": False,
        "break_resistance": False,
        "false_break_down": False,
        "false_break_up": False,
        "near_7d_high": False,
        "near_7d_low": False,
        "wick_frequent": False,
        "h1_side": "FLAT",
        "m15_side": "FLAT",
        "big4_bias": big4.get("bias") or "FLAT",
        "big4_ok": bool(big4.get("big4_ok", True)),
    }
    signals: List[str] = []

    if len(rows_1h) < 60 or len(rows_15m) < 40:
        feats["signals"] = signals
        return feats

    c1 = [_f(r.get("close_price")) for r in rows_1h]
    c15 = [_f(r.get("close_price")) for r in rows_15m]
    h15 = [_f(r.get("high_price")) for r in rows_15m]
    v15 = [_f(r.get("volume")) for r in rows_15m]
    feats["ref_price"] = c15[-1]

    ema20 = _ema(c1, 20)
    ema60 = _ema(c1, 60)
    ema20_15 = _ema(c15, 20)
    feats["ema20_1h"] = ema20
    feats["ema60_1h"] = ema60
    feats["ema20_15m"] = ema20_15

    price = c1[-1]
    if ema20 and ema60:
        if price > ema20 > ema60:
            feats["ema_bull"] = True
            signals.append("ema_bull_align")
        elif price < ema20 < ema60:
            feats["ema_bear"] = True
            signals.append("ema_bear_align")
        # reclaim / reject vs ema20 on 15m
        if ema20_15 and len(c15) >= 6:
            if c15[-3] < ema20_15 <= c15[-1]:
                signals.append("ema_reclaim")
            if c15[-3] > ema20_15 >= c15[-1]:
                signals.append("ema_reject")

    hh, ll = _structure_hh_hl(c1, 48)
    feats["hh_hl"] = hh
    feats["lh_ll"] = ll
    if hh:
        signals.append("hh_hl")
    if ll:
        signals.append("lh_ll")

    # 15m higher low / lower high (last 16 bars)
    if len(c15) >= 16:
        a, b = c15[-16:-8], c15[-8:]
        if min(b) > min(a):
            signals.append("15m_higher_low")
        if max(b) < max(a):
            signals.append("15m_lower_high")
        # stop new low: last 4 lows not below prior 8 min
        if min(c15[-4:]) >= min(c15[-12:-4]) * 0.999:
            feats["stop_new_low"] = True
            signals.append("15m_stop_new_low")
        if len(h15) >= 16 and max(h15[-4:]) <= max(h15[-12:-4]) * 1.001:
            feats["stop_new_high"] = True
            signals.append("15m_stop_new_high")

    rsi1 = _rsi(c1, 14)
    rsi15 = _rsi(c15, 14)
    feats["rsi_1h"] = round(rsi1, 1) if rsi1 is not None else None
    feats["rsi_15m"] = round(rsi15, 1) if rsi15 is not None else None
    if rsi1 is not None:
        if 45 <= rsi1 <= 68:
            signals.append("rsi_1h_healthy_long")
        if 32 <= rsi1 <= 55:
            signals.append("rsi_1h_healthy_short")
        if rsi1 > 72:
            signals.append("rsi_extreme_high")
        if rsi1 < 28:
            signals.append("rsi_extreme_low")
    if rsi15 is not None and len(c15) >= 16:
        prev = _rsi(c15[:-3], 14) if len(c15) > 20 else None
        if prev is not None:
            if prev < 35 and rsi15 > prev + 3:
                signals.append("rsi_15m_turn_up")
            if prev > 65 and rsi15 < prev - 3:
                signals.append("rsi_15m_turn_down")

    # volume
    if len(v15) >= 24:
        avg = sum(v15[-24:]) / 24
        recent = sum(v15[-4:]) / 4
        earlier = sum(v15[-12:-4]) / 8 if len(v15) >= 12 else avg
        up_move = c15[-1] > c15[-5]
        down_move = c15[-1] < c15[-5]
        if up_move and recent >= avg * 1.1:
            feats["vol_up"] = True
            signals.append("volume_expand_up")
        if down_move and recent >= avg * 1.1:
            feats["vol_down"] = True
            signals.append("volume_expand_down")
        # pullback shrink: last 3 bars opposite to 1h trend with lower vol
        if recent < earlier * 0.85:
            feats["vol_shrink_pullback"] = True
            signals.append("volume_shrink_pullback")
        # divergence rough
        if min(c15[-8:]) <= min(c15[-24:-8]) and sum(v15[-8:]) < sum(v15[-24:-8]) * 0.7:
            signals.append("volume_diverge_bull")
        if max(c15[-8:]) >= max(c15[-24:-8]) and sum(v15[-8:]) < sum(v15[-24:-8]) * 0.7:
            signals.append("volume_diverge_bear")

    if len(c1) >= 24:
        v1 = [_f(r.get("volume")) for r in rows_1h]
        prev_hi_1h = max(c1[-21:-1])
        prev_lo_1h = min(c1[-21:-1])
        avg_v1 = sum(v1[-21:-1]) / 20 if len(v1) >= 21 else 0.0
        vol_rel_1h = (v1[-1] / avg_v1) if avg_v1 > 0 else 0.0
        if c1[-1] > prev_hi_1h * (1 + BRAIN_IMPULSE_1H_BREAK_PCT / 100.0):
            feats["h1_breakout_up"] = True
            signals.append("h1_breakout_up")
            if vol_rel_1h >= BRAIN_IMPULSE_1H_VOL_REL:
                feats["impulse_up"] = True
                signals.append("impulse_up")
        if c1[-1] < prev_lo_1h * (1 - BRAIN_IMPULSE_1H_BREAK_PCT / 100.0):
            feats["h1_breakdown_down"] = True
            signals.append("h1_breakdown_down")
            if vol_rel_1h >= BRAIN_IMPULSE_1H_VOL_REL:
                feats["impulse_down"] = True
                signals.append("impulse_down")

    # crash / pump vs ATR
    atr = _atr(rows_15m[-60:] if len(rows_15m) >= 60 else rows_15m, 14)
    n = CRASH_LOOKBACK_BARS
    if atr and atr > 0 and len(c15) > n:
        chg = c15[-1] - c15[-n]
        if chg <= -CRASH_ATR_MULT * atr:
            feats["crash_spike"] = True
            signals.append("crash_spike")
        if chg >= CRASH_ATR_MULT * atr:
            feats["pump_spike"] = True
            signals.append("pump_spike")

    # last bar wick
    last = rows_15m[-1]
    wm = bar_wick_metrics(
        _f(last.get("open_price")),
        _f(last.get("high_price")),
        _f(last.get("low_price")),
        _f(last.get("close_price")),
    )
    if wm.get("lower_is_wick"):
        feats["long_lower_wick"] = True
        signals.append("long_lower_wick")
    if wm.get("upper_is_wick"):
        feats["long_upper_wick"] = True
        signals.append("long_upper_wick")
    candle_range = max(_f(last.get("high_price")) - _f(last.get("low_price")), 1e-12)
    upper_wick_ratio = _f(wm.get("upper")) / candle_range
    if (
        feats.get("pump_spike")
        and upper_wick_ratio >= BRAIN_EXHAUSTION_UPPER_WICK_MIN
        and ("volume_diverge_bear" in signals or "rsi_15m_turn_down" in signals or feats.get("vol_down"))
    ):
        feats["exhaustion_up"] = True
        signals.append("exhaustion_up")

    wick = analyze_wicks(rows_15m[-min(672, len(rows_15m)):])
    feats["wick_frequent"] = bool(wick.get("frequent"))
    feats["wick"] = wick
    if feats["wick_frequent"]:
        signals.append("wick_frequent")

    # break / false break vs recent 48-bar range on 15m
    if len(c15) >= 50:
        rng_hi = max(c15[-50:-2])
        rng_lo = min(c15[-50:-2])
        if c15[-1] < rng_lo * 0.998:
            feats["break_support"] = True
            signals.append("break_support")
        if c15[-1] > rng_hi * 1.002:
            feats["break_resistance"] = True
            signals.append("break_resistance")
        # false: pierced then back
        if min(c15[-4:-1]) < rng_lo and c15[-1] > rng_lo:
            feats["false_break_down"] = True
            signals.append("false_break_down")
        if max(c15[-4:-1]) > rng_hi and c15[-1] < rng_hi:
            feats["false_break_up"] = True
            signals.append("false_break_up")

    # 7d high/low on 1h
    if len(c1) >= 24:
        hi7 = max(c1[-168:]) if len(c1) >= 168 else max(c1)
        lo7 = min(c1[-168:]) if len(c1) >= 168 else min(c1)
        if hi7 > 0 and (hi7 - price) / hi7 <= 0.02:
            feats["near_7d_high"] = True
            signals.append("near_7d_high")
        if lo7 > 0 and (price - lo7) / lo7 <= 0.02:
            feats["near_7d_low"] = True
            signals.append("near_7d_low")

    # 冲高滞涨：须在 7d 高点判定之后。靠近近期高点且不再延伸，再叠加上影/量价/RSI
    if len(h15) >= 12:
        prior_hi = max(h15[-12:-3])
        recent_hi = max(h15[-3:])
        close_near_high = c15[-1] >= prior_hi * 0.992
        no_extension = recent_hi <= prior_hi * 1.0015
        stall_aux = (
            feats.get("long_upper_wick")
            or "volume_diverge_bear" in signals
            or "rsi_15m_turn_down" in signals
            or feats.get("vol_shrink_pullback")
            or feats.get("near_7d_high")
            or feats.get("stop_new_high")
        )
        if close_near_high and no_extension and stall_aux:
            feats["stall_at_high"] = True
            signals.append("stall_at_high")
        tagged = max(h15[-8:])
        off_high = tagged > 0 and c15[-1] > 0 and (tagged - c15[-1]) / c15[-1] >= 0.0008
        still_near = tagged > 0 and c15[-1] > 0 and (tagged - c15[-1]) / c15[-1] <= 0.0085
        reject_close = bool(feats.get("long_upper_wick") or upper_wick_ratio >= 0.22)
        if (
            off_high
            and still_near
            and (
                feats.get("stall_at_high")
                or feats.get("exhaustion_up")
                or reject_close
                or "15m_stop_new_high" in signals
            )
        ):
            feats["top_callback"] = True
            signals.append("top_callback")
        if (
            not feats.get("exhaustion_up")
            and feats.get("long_upper_wick")
            and (feats.get("near_7d_high") or feats.get("pump_spike") or feats.get("stall_at_high"))
            and (
                "volume_diverge_bear" in signals
                or "rsi_15m_turn_down" in signals
                or feats.get("stop_new_high")
                or feats.get("vol_down")
            )
        ):
            feats["exhaustion_up"] = True
            signals.append("exhaustion_up")

    # coarse h1/m15 sides for arbitration
    if len(c1) >= 48:
        chg = (c1[-1] - c1[-48]) / c1[-48] * 100 if c1[-48] else 0
        if chg >= 1.2:
            feats["h1_side"] = "LONG"
        elif chg <= -1.2:
            feats["h1_side"] = "SHORT"
    if len(c15) >= 32:
        chg15 = (c15[-1] - c15[-32]) / c15[-32] * 100 if c15[-32] else 0
        if chg15 >= 0.6:
            feats["m15_side"] = "LONG"
        elif chg15 <= -0.6:
            feats["m15_side"] = "SHORT"

    bias = feats["big4_bias"]
    if not feats["big4_ok"]:
        signals.append("big4_weak")
    elif bias == "LONG":
        signals.append("big4_bull")
    elif bias == "SHORT":
        signals.append("big4_bear")

    # dedupe preserve order
    seen = set()
    out_sig = []
    for s in signals:
        if s not in seen:
            seen.add(s)
            out_sig.append(s)
    feats["signals"] = out_sig
    return feats


def _score_playbooks(feats: Dict[str, Any]) -> List[Tuple[str, float, bool]]:
    """返回 [(playbook, score, confirmed), ...] 按 score 降序。"""
    scored: List[Tuple[str, float, bool]] = []
    sig = set(feats.get("signals") or [])

    # A1 多头趋势回踩
    a1 = 0.0
    if feats.get("ema_bull"):
        a1 += 0.35
    if feats.get("hh_hl"):
        a1 += 0.25
    if "15m_higher_low" in sig:
        a1 += 0.2
    if feats.get("vol_shrink_pullback"):
        a1 += 0.15
    if "rsi_1h_healthy_long" in sig:
        a1 += 0.1
    if a1 >= 0.5:
        scored.append(("A1", a1, a1 >= 0.7))

    # A2 空头趋势反抽：下降结构中出现缩量反抽，并在前高/EMA 被拒绝
    a2 = 0.0
    if feats.get("ema_bear"):
        a2 += 0.30
    if feats.get("lh_ll"):
        a2 += 0.25
    if "15m_lower_high" in sig:
        a2 += 0.20
    if feats.get("vol_shrink_pullback"):
        a2 += 0.15
    if "ema_reject" in sig or feats.get("long_upper_wick"):
        a2 += 0.20
    if "rsi_1h_healthy_short" in sig:
        a2 += 0.10
    a2_bounce_reject = bool(
        ("15m_lower_high" in sig or feats.get("vol_shrink_pullback"))
        and ("ema_reject" in sig or feats.get("long_upper_wick"))
    )
    a2_structure = bool(feats.get("ema_bear") and (feats.get("lh_ll") or feats.get("h1_side") == "SHORT"))
    if a2 >= 0.55 and a2_structure and not (feats.get("hh_hl") and feats.get("vol_up")):
        scored.append(("A2", a2, bool(a2 >= 0.75 and a2_bounce_reject)))

    # B1 暴跌放量反弹
    b1 = 0.0
    if feats.get("crash_spike"):
        b1 += 0.35
    if feats.get("stop_new_low") or feats.get("long_lower_wick"):
        b1 += 0.25
    if feats.get("vol_up") or "ema_reclaim" in sig:
        b1 += 0.25
    if "rsi_15m_turn_up" in sig:
        b1 += 0.1
    if feats.get("near_7d_low"):
        b1 += 0.05
    # 禁止：无止跌且仍创新低叙事
    if b1 >= 0.55 and (feats.get("stop_new_low") or feats.get("long_lower_wick")):
        scored.append(("B1", b1, b1 >= 0.75 and bool(feats.get("vol_up"))))

    # B2 弱反抽失败：必须先有下跌与弱反抽，再跌破反抽起点才确认（禁止无反抽纯追跌）
    b2 = 0.0
    b2_downtrend = bool(feats.get("lh_ll") or feats.get("ema_bear") or feats.get("h1_side") == "SHORT")
    if b2_downtrend:
        b2 += 0.25
    weak_n = 0
    if feats.get("vol_shrink_pullback"):
        weak_n += 1
    if "15m_lower_high" in sig:
        weak_n += 1
    if "ema_reject" in sig:
        weak_n += 1
    if "rsi_15m_turn_down" in sig:
        weak_n += 1
    if feats.get("long_upper_wick"):
        weak_n += 1
    had_bounce = bool(
        feats.get("vol_shrink_pullback")
        or "15m_lower_high" in sig
        or feats.get("long_upper_wick")
    )
    bounce_failed = bool(feats.get("break_support") or "ema_reject" in sig)
    if weak_n >= 2:
        b2 += 0.35
    if bounce_failed:
        b2 += 0.25
    if (
        b2 >= 0.55
        and b2_downtrend
        and had_bounce
        and weak_n >= 2
        and not (feats.get("vol_up") and feats.get("hh_hl"))
        and not (feats.get("crash_spike") and not had_bounce)
    ):
        scored.append(("B2", b2, bounce_failed and weak_n >= 2))

    # B3 暴涨滞涨
    b3 = 0.0
    if feats.get("pump_spike"):
        b3 += 0.35
    if feats.get("exhaustion_up"):
        b3 += 0.25
    if feats.get("long_upper_wick") or "volume_diverge_bear" in sig:
        b3 += 0.3
    if "ema_reject" in sig or feats.get("false_break_up"):
        b3 += 0.25
    if feats.get("stall_at_high") or "15m_stop_new_high" in sig:
        b3 += 0.2
    if feats.get("top_callback") or "top_callback" in sig:
        b3 += 0.15
    if b3 >= 0.55:
        b3_confirmed = b3 >= 0.7 or bool(
            feats.get("stall_at_high")
            or feats.get("exhaustion_up")
            or feats.get("false_break_up")
            or feats.get("top_callback")
        )
        scored.append(("B3", b3, b3_confirmed))

    # B4 暴涨回踩有力
    b4 = 0.0
    if feats.get("pump_spike") or (feats.get("ema_bull") and feats.get("h1_side") == "LONG"):
        b4 += 0.25
    if feats.get("vol_shrink_pullback") and "15m_higher_low" in sig:
        b4 += 0.35
    if feats.get("vol_up") and "ema_reclaim" in sig:
        b4 += 0.3
    if b4 >= 0.55:
        scored.append(("B4", b4, bool(feats.get("vol_up"))))

    # C1 向下破位：刚破位且放量/急跌 → 当根确认、跟风做空（不等回抽）
    if feats.get("break_support") and (feats.get("vol_down") or feats.get("ema_bear") or feats.get("impulse_down") or feats.get("crash_spike")):
        c1_confirmed = bool(
            (feats.get("vol_down") or feats.get("impulse_down") or feats.get("crash_spike"))
            and not feats.get("false_break_down")
        )
        scored.append(("C1", 0.7 + (0.1 if feats.get("vol_down") or feats.get("crash_spike") else 0), c1_confirmed))

    # C2 假破吸筹
    if feats.get("false_break_down") and (feats.get("long_lower_wick") or "15m_higher_low" in sig):
        scored.append(("C2", 0.75, True))

    # C3 向上突破：识别在突破当根；BRAIN 仍等回踩，中线市价跟风
    if (feats.get("break_resistance") and feats.get("vol_up")) or feats.get("impulse_up"):
        c3 = 0.7
        if feats.get("impulse_up"):
            c3 += 0.2
        if feats.get("h1_breakout_up"):
            c3 += 0.1
        c3_confirmed = bool(
            feats.get("impulse_up")
            or feats.get("h1_breakout_up")
            or (feats.get("break_resistance") and feats.get("vol_up"))
            or feats.get("vol_shrink_pullback")
            or "15m_higher_low" in sig
            or feats.get("long_lower_wick")
            or "ema_reclaim" in sig
        )
        scored.append(("C3", min(1.0, c3), c3_confirmed))

    # C4 假突陷阱
    if feats.get("false_break_up") and (
        feats.get("long_upper_wick") or feats.get("vol_down") or feats.get("stall_at_high")
    ):
        scored.append(("C4", 0.75 + (0.1 if feats.get("exhaustion_up") else 0), True))

    scored.sort(key=lambda x: (x[2], x[1]), reverse=True)
    return scored


def classify_playbook(
    rows_1h: List[Dict],
    rows_15m: List[Dict],
    *,
    big4: Optional[Dict[str, Any]] = None,
    win_prob_long: Optional[float] = None,
    win_prob_short: Optional[float] = None,
) -> Dict[str, Any]:
    """
    识别唯一主 Playbook + signals。
    仲裁：确认优先 → 与 1H 冲突降权 → 分向胜率 → 仍冲突则 D2。
    """
    feats = extract_features(rows_1h, rows_15m, big4=big4)
    signals = list(feats.get("signals") or [])

    token_event = bool(feats.get("impulse_up") or feats.get("impulse_down") or feats.get("exhaustion_up"))
    strong_big4_weak_trend = bool(
        (
            feats.get("ema_bull")
            and feats.get("hh_hl")
            and feats.get("h1_side") == "LONG"
            and feats.get("m15_side") == "LONG"
        )
        or (
            feats.get("ema_bear")
            and feats.get("lh_ll")
            and feats.get("h1_side") == "SHORT"
            and feats.get("m15_side") == "SHORT"
        )
    )
    if not feats.get("big4_ok") and not (token_event or strong_big4_weak_trend):
        return {
            "playbook": "D1",
            "side": "FLAT",
            "signals": signals,
            "edge_score": 0.0,
            "confirmed": False,
            "candidates": [],
            "features": feats,
            "evidence_summary": "Big4疲软，不可交易",
            "ref_price": feats.get("ref_price"),
        }

    if len(rows_1h) < 60 or len(rows_15m) < 40:
        return {
            "playbook": "D1",
            "side": "FLAT",
            "signals": signals,
            "edge_score": 0.0,
            "confirmed": False,
            "candidates": [],
            "features": feats,
            "evidence_summary": "K线不足",
            "ref_price": feats.get("ref_price"),
        }

    scored = _score_playbooks(feats)
    # EMA 纠缠 / 无结构
    if not scored and not feats.get("ema_bull") and not feats.get("ema_bear"):
        return {
            "playbook": "D1",
            "side": "FLAT",
            "signals": signals,
            "edge_score": 0.0,
            "confirmed": False,
            "candidates": [],
            "features": feats,
            "evidence_summary": "震荡无边/EMA纠缠",
            "ref_price": feats.get("ref_price"),
        }

    if not scored:
        return {
            "playbook": "D1",
            "side": "FLAT",
            "signals": signals,
            "edge_score": 0.0,
            "confirmed": False,
            "candidates": [],
            "features": feats,
            "evidence_summary": "无匹配Playbook",
            "ref_price": feats.get("ref_price"),
        }

    h1 = feats.get("h1_side") or "FLAT"
    # 过滤与 1H 严重冲突的（冲击反弹 B1/B3 允许逆势短线）
    filtered = []
    for pb, sc, conf in scored:
        side = PLAYBOOK_SIDE.get(pb, "FLAT")
        if pb in ("B1", "B3", "C2", "C4"):
            filtered.append((pb, sc, conf))
            continue
        if h1 in ("LONG", "SHORT") and side in ("LONG", "SHORT") and side != h1:
            continue
        filtered.append((pb, sc, conf))

    if not filtered:
        return {
            "playbook": "D2",
            "side": "FLAT",
            "signals": signals,
            "edge_score": 0.0,
            "confirmed": False,
            "candidates": [{"playbook": p, "score": s, "confirmed": c} for p, s, c in scored[:5]],
            "features": feats,
            "evidence_summary": "场景与1H方向冲突",
            "ref_price": feats.get("ref_price"),
        }

    # 多空都强 → D2
    long_best = max((s for p, s, _ in filtered if PLAYBOOK_SIDE.get(p) == "LONG"), default=0)
    short_best = max((s for p, s, _ in filtered if PLAYBOOK_SIDE.get(p) == "SHORT"), default=0)
    if long_best >= 0.65 and short_best >= 0.65 and abs(long_best - short_best) < 0.1:
        return {
            "playbook": "D2",
            "side": "FLAT",
            "signals": signals,
            "edge_score": 0.0,
            "confirmed": False,
            "candidates": [{"playbook": p, "score": s, "confirmed": c} for p, s, c in filtered[:5]],
            "features": feats,
            "evidence_summary": "多空场景同时成立，冲突不开",
            "ref_price": feats.get("ref_price"),
        }

    # 用分向胜率微调排序
    def _key(item: Tuple[str, float, bool]) -> Tuple:
        pb, sc, conf = item
        side = PLAYBOOK_SIDE.get(pb, "FLAT")
        wp = win_prob_long if side == "LONG" else win_prob_short if side == "SHORT" else 0
        return (1 if conf else 0, sc + (0.05 if wp and wp >= 0.55 else 0), wp or 0)

    filtered.sort(key=_key, reverse=True)
    best_pb, best_sc, best_conf = filtered[0]
    side = PLAYBOOK_SIDE.get(best_pb, "FLAT")

    # edge_score = playbook score（0~1）
    evidence = (
        f"{best_pb} {side} score={best_sc:.2f} conf={best_conf} "
        f"h1={h1} m15={feats.get('m15_side')} "
        f"signals={','.join(signals[:12])}"
    )
    return {
        "playbook": best_pb,
        "side": side,
        "signals": signals,
        "edge_score": round(min(1.0, best_sc), 3),
        "confirmed": best_conf,
        "candidates": [{"playbook": p, "score": round(s, 3), "confirmed": c} for p, s, c in filtered[:5]],
        "features": feats,
        "evidence_summary": evidence[:900],
        "ref_price": feats.get("ref_price"),
        "rsi_1h": feats.get("rsi_1h"),
        "wick": feats.get("wick") or {},
        "limit_offset_pct": 0.5,
        "forbid_market": bool(feats.get("wick_frequent")),
    }
