"""战术 / 顶空底多 — 送模前量化预筛 + 筛选记录（RSI、价格、成交量、K 线叙事）."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from app.services.ai_explore_prompt import EXPLORE_LLM_MAX_SYMBOLS

# 与 ai_tactical_explore_prompts 门槛对齐（改 prompt 常量时同步此处 import）
from app.services.ai_tactical_explore_prompts import (
    CHASE_MIN_ROOM_BELOW_7D_HIGH_PCT,
    CHASE_RSI_MAX,
    CHASE_RSI_MIN,
    DUMP_RSI_MAX,
    PULLBACK_MAX_BELOW_7D_HIGH_PCT,
    PULLBACK_RSI_MAX,
    REBOUND_NEAR_7D_HIGH_PCT,
    REBOUND_RSI_MAX,
    REBOUND_RSI_MIN,
)
REVERSAL_TOP_RSI_MIN = 58
REVERSAL_BOTTOM_RSI_MAX = 42
REVERSAL_NEAR_7D_HIGH_PCT = -10.0
REVERSAL_NEAR_7D_LOW_PCT = 18.0


@dataclass
class ExploreScreenRecord:
    symbol: str
    stage: str  # llm_pool | dropped
    screen_side: Optional[str] = None
    score: Optional[float] = None
    rsi_1h: Optional[float] = None
    below_7d_high_pct: Optional[float] = None
    above_7d_low_pct: Optional[float] = None
    volume_note: Optional[str] = None
    reason: Optional[str] = None
    sent_to_llm: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _has_any(text: str, words: tuple) -> bool:
    t = (text or "").lower()
    return any(w in t for w in words)


def _tech(item: dict) -> dict:
    return (item or {}).get("tech") or {}


def _narrative_text(item: dict) -> str:
    kn = (item or {}).get("kline_narrative") or {}
    if not isinstance(kn, dict):
        return ""
    return " ".join(str(v) for v in kn.values())


def _rsi_1h(item: dict) -> Optional[float]:
    raw = _tech(item).get("rsi_14_1h")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _below_7d_high_pct(item: dict) -> Optional[float]:
    t = _tech(item)
    raw = t.get("below_7d_high_pct")
    if raw is None:
        raw = (item or {}).get("below_7d_high_pct")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _above_7d_low_pct(item: dict) -> Optional[float]:
    t = _tech(item)
    raw = t.get("above_7d_low_pct")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _quote_volume_24h(item: dict) -> Optional[float]:
    raw = (item or {}).get("quote_volume_24h")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _volume_note(item: dict) -> str:
    qv = _quote_volume_24h(item)
    narr = _narrative_text(item).lower()
    parts: List[str] = []
    if qv is not None and qv >= 0:
        if qv >= 50_000_000:
            parts.append("24h额高")
        elif qv >= 10_000_000:
            parts.append("24h额中")
        else:
            parts.append("24h额一般")
    if _has_any(narr, ("放量", "量能放大", "volume spike", "spike")):
        parts.append("叙事放量")
    if _has_any(narr, ("缩量", "量能萎缩", "缩量反弹")):
        parts.append("叙事缩量")
    return " ".join(parts)[:120] if parts else ""


def _reversal_top_score(item: dict) -> float:
    rsi = _rsi_1h(item)
    b7h = _below_7d_high_pct(item)
    narr = _narrative_text(item).lower()
    score = 0.0
    if rsi is not None and rsi >= REVERSAL_TOP_RSI_MIN:
        score += (rsi - 50) * 1.5
    if b7h is not None and b7h >= REVERSAL_NEAR_7D_HIGH_PCT:
        score += max(0.0, 10.0 + float(b7h))
    if _has_any(narr, ("滞涨", "上影", "假突破", "遇阻", "超买", "overbought")):
        score += 12.0
    if _has_any(narr, ("偏多", "上升", "连阳")) and rsi is not None and rsi >= 55:
        score += 5.0
    if _has_any(_volume_note(item), ("放量",)):
        score += 4.0
    return score


def _reversal_bottom_score(item: dict) -> float:
    rsi = _rsi_1h(item)
    a7l = _above_7d_low_pct(item)
    narr = _narrative_text(item).lower()
    score = 0.0
    if rsi is not None and rsi <= REVERSAL_BOTTOM_RSI_MAX:
        score += (50 - rsi) * 1.5
    if a7l is not None and a7l <= REVERSAL_NEAR_7D_LOW_PCT:
        score += max(0.0, REVERSAL_NEAR_7D_LOW_PCT - float(a7l) + 5)
    if _has_any(narr, ("止跌", "下影", "假跌破", "超卖", "oversold", "企稳")):
        score += 12.0
    if _has_any(narr, ("偏空", "连阴", "下行")) and rsi is not None and rsi <= 45:
        score += 5.0
    return score


def _reversal_screen_one(item: dict) -> Tuple[Optional[str], float, Optional[str]]:
    """返回 (side_tag, score, drop_reason). side_tag: top|bottom|None."""
    sym = str(item.get("symbol") or "")
    top_s = _reversal_top_score(item)
    bot_s = _reversal_bottom_score(item)
    if top_s < 8.0 and bot_s < 8.0:
        return None, max(top_s, bot_s), "无顶/底反转特征(RSI/7d/叙事)"
    if top_s >= bot_s:
        return "top", top_s, None
    return "bottom", bot_s, None


def screen_reversal_universe(
    universe: dict,
    *,
    max_symbols: int = EXPLORE_LLM_MAX_SYMBOLS,
) -> Tuple[List[dict], List[ExploreScreenRecord], Dict[str, Any]]:
    pool = list(universe.values())
    records: List[ExploreScreenRecord] = []
    candidates: List[Tuple[float, dict, str]] = []

    for item in pool:
        sym = str(item.get("symbol") or "").upper().replace("/", "")
        if not sym:
            continue
        side_tag, score, drop_reason = _reversal_screen_one(item)
        base = ExploreScreenRecord(
            symbol=sym,
            stage="dropped" if side_tag is None else "llm_pool",
            screen_side=side_tag,
            score=round(score, 2) if score is not None else None,
            rsi_1h=_rsi_1h(item),
            below_7d_high_pct=_below_7d_high_pct(item),
            above_7d_low_pct=_above_7d_low_pct(item),
            volume_note=_volume_note(item) or None,
            reason=drop_reason,
            sent_to_llm=False,
        )
        if side_tag is None:
            records.append(base)
            continue
        candidates.append((score, item, side_tag))
        records.append(base)

    candidates.sort(key=lambda x: x[0], reverse=True)
    selected_items: List[dict] = []
    selected_syms: set = set()
    for _score, item, _tag in candidates[:max_symbols]:
        sym = str(item.get("symbol") or "").upper().replace("/", "")
        item = dict(item)
        item.pop("k_1d_ohlc", None)
        item.pop("k_1h_ohlc", None)
        item.pop("k_15m_ohlc", None)
        selected_items.append(item)
        selected_syms.add(sym)

    for rec in records:
        if rec.stage == "llm_pool" and rec.symbol in selected_syms:
            rec.sent_to_llm = True

    meta = {
        "universe_total": len(pool),
        "llm_symbol_count": len(selected_items),
        "llm_symbols_truncated": max(0, len(candidates) - len(selected_items)),
        "precheck_dropped": len(pool) - len(candidates),
        "selection": "reversal_screen_rsi_price_volume",
        "screen_top_count": sum(1 for _, _, t in candidates if t == "top"),
        "screen_bottom_count": sum(1 for _, _, t in candidates if t == "bottom"),
        "screen_records": [r.to_dict() for r in records],
    }
    if not selected_items:
        logger.info(
            f"[ReversalScreen] 预筛后无候选: 全池 {len(pool)}, "
            f"顶/底特征 {len(candidates)}"
        )
    return selected_items, records, meta


def _tactical_pullback_fail(item: dict) -> Optional[str]:
    rsi = _rsi_1h(item)
    narr = _narrative_text(item).lower()
    b7h = _below_7d_high_pct(item)
    if rsi is not None and rsi > PULLBACK_RSI_MAX:
        return f"RSI={rsi:.0f}>{PULLBACK_RSI_MAX}"
    if _has_any(narr, ("偏空", "强势下降", "连阴", "下行趋势")) and not _has_any(
        narr, ("偏多", "上升", "上攻", "连阳", "上升通道", "回踩")
    ):
        return "1h叙事偏空无回调结构"
    if b7h is not None and b7h > PULLBACK_MAX_BELOW_7D_HIGH_PCT:
        return f"below_7d_high={b7h:.1f}%过近"
    if not _has_any(narr, ("回调", "回踩", "阴线", "dip", "pullback", "上升", "偏多", "连阳")):
        return "无回调/上升结构叙事"
    return None


def _tactical_rebound_fail(item: dict) -> Optional[str]:
    rsi = _rsi_1h(item)
    narr = _narrative_text(item).lower()
    b7h = _below_7d_high_pct(item)
    if rsi is not None and rsi < REBOUND_RSI_MIN:
        return f"RSI={rsi:.0f}<{REBOUND_RSI_MIN}"
    if rsi is not None and rsi > REBOUND_RSI_MAX:
        return f"RSI={rsi:.0f}>{REBOUND_RSI_MAX}"
    if _has_any(narr, ("偏多", "上升", "上攻", "连阳")) and not _has_any(
        narr, ("偏空", "回落", "下降", "连阴", "反弹", "rebound")
    ):
        return "1h叙事仍偏多"
    if b7h is not None and b7h <= REBOUND_NEAR_7D_HIGH_PCT:
        return f"below_7d_high={b7h:.1f}%离7d高点过远"
    if not _has_any(narr, ("偏空", "回落", "下降", "连阴", "反弹", "rebound", "缩量")):
        return "无下跌反弹结构叙事"
    return None


def _tactical_chase_fail(item: dict) -> Optional[str]:
    rsi = _rsi_1h(item)
    narr = _narrative_text(item).lower()
    b7h = _below_7d_high_pct(item)
    if rsi is not None and rsi > CHASE_RSI_MAX:
        return f"RSI={rsi:.0f}>{CHASE_RSI_MAX}"
    if rsi is not None and rsi < CHASE_RSI_MIN:
        return f"RSI={rsi:.0f}<{CHASE_RSI_MIN}"
    if b7h is not None and b7h > -CHASE_MIN_ROOM_BELOW_7D_HIGH_PCT:
        return f"below_7d_high={b7h:.1f}%空间不足"
    if _has_any(narr, ("偏空", "连阴", "下行", "下跌")) and not _has_any(
        narr, ("连阳", "上涨", "上攻", "持续", "趋势延续")
    ):
        return "1h叙事偏空"
    if not _has_any(narr, ("连阳", "上涨", "上攻", "持续", "趋势", "偏多")):
        return "无连续上涨叙事"
    return None


def _tactical_dump_fail(item: dict) -> Optional[str]:
    rsi = _rsi_1h(item)
    narr = _narrative_text(item).lower()
    a7l = _above_7d_low_pct(item)
    if rsi is not None and rsi > DUMP_RSI_MAX:
        return f"RSI={rsi:.0f}>{DUMP_RSI_MAX}"
    if _has_any(narr, ("偏多", "上升", "上攻", "连阳")) and not _has_any(
        narr, ("偏空", "连阴", "下行", "下跌", "杀跌")
    ):
        return "1h叙事仍偏强"
    if not _has_any(narr, ("偏空", "连阴", "下行", "下跌", "下降", "杀跌", "反弹无力")):
        return "无连续下跌叙事"
    if a7l is not None and a7l <= 5.0:
        return f"above_7d_low={a7l:.1f}%近底"
    return None


_TACTICAL_FAIL_FN = {
    "pullback": _tactical_pullback_fail,
    "rebound": _tactical_rebound_fail,
    "chase": _tactical_chase_fail,
    "dump": _tactical_dump_fail,
}


def _tactical_score(strategy_key: str, item: dict) -> float:
    rsi = _rsi_1h(item) or 50.0
    chg = float(item.get("change_24h") or 0)
    b7h = _below_7d_high_pct(item)
    a7l = _above_7d_low_pct(item)
    narr = _narrative_text(item).lower()

    if strategy_key == "pullback":
        score = 100.0 - abs(rsi - 52.0) + chg * 0.15
        if b7h is not None:
            score += max(0.0, min(25.0, -float(b7h) * 0.35))
        if _has_any(narr, ("回调", "回踩", "阴线")):
            score += 8.0
        return score
    if strategy_key == "rebound":
        score = (100.0 - rsi) - chg * 0.25
        if b7h is not None:
            score += max(0.0, -float(b7h) * 0.4)
        if _has_any(narr, ("缩量反弹", "反弹", "上影")):
            score += 8.0
        return score
    if strategy_key == "chase":
        if rsi > CHASE_RSI_MAX:
            return -500.0 - rsi
        score = rsi + chg * 0.3
        if b7h is not None:
            bf = float(b7h)
            if bf > -CHASE_MIN_ROOM_BELOW_7D_HIGH_PCT:
                score -= 30.0
            else:
                score += min(15.0, max(0.0, (-bf - CHASE_MIN_ROOM_BELOW_7D_HIGH_PCT) * 0.5))
        if _has_any(narr, ("连阳", "持续上涨", "趋势延续")):
            score += 10.0
        return score
    if strategy_key == "dump":
        score = (100.0 - rsi) - chg * 0.25
        if a7l is not None:
            score += max(0.0, (25.0 - float(a7l)) * 0.2)
        if _has_any(narr, ("连阴", "下行", "反弹无力")):
            score += 10.0
        return score
    return 0.0


def screen_tactical_universe(
    strategy_key: str,
    universe: dict,
    *,
    max_symbols: int = EXPLORE_LLM_MAX_SYMBOLS,
    allow_empty: bool = False,
) -> Tuple[List[dict], List[ExploreScreenRecord], Dict[str, Any]]:
    fail_fn = _TACTICAL_FAIL_FN.get(strategy_key)
    if fail_fn is None:
        raise ValueError(f"unknown strategy_key={strategy_key}")

    pool = list(universe.values())
    records: List[ExploreScreenRecord] = []
    eligible: List[Tuple[float, dict]] = []

    for item in pool:
        sym = str(item.get("symbol") or "").upper().replace("/", "")
        if not sym:
            continue
        reason = fail_fn(item)
        score = _tactical_score(strategy_key, item)
        rec = ExploreScreenRecord(
            symbol=sym,
            stage="dropped" if reason else "llm_pool",
            screen_side=strategy_key,
            score=round(score, 2),
            rsi_1h=_rsi_1h(item),
            below_7d_high_pct=_below_7d_high_pct(item),
            above_7d_low_pct=_above_7d_low_pct(item),
            volume_note=_volume_note(item) or None,
            reason=reason,
            sent_to_llm=False,
        )
        records.append(rec)
        if reason:
            continue
        eligible.append((score, item))

    eligible.sort(key=lambda x: x[0], reverse=True)
    selected: List[dict] = []
    selected_syms: set = set()
    for _score, item in eligible[:max_symbols]:
        sym = str(item.get("symbol") or "").upper().replace("/", "")
        clean = dict(item)
        clean.pop("k_1d_ohlc", None)
        clean.pop("k_1h_ohlc", None)
        clean.pop("k_15m_ohlc", None)
        selected.append(clean)
        selected_syms.add(sym)

    for rec in records:
        if rec.stage == "llm_pool" and rec.symbol in selected_syms:
            rec.sent_to_llm = True

    if not selected and not allow_empty:
        logger.warning(
            f"[TacticalScreen/{strategy_key}] 预筛无候选 (全池 {len(pool)}), 本轮不送模"
        )

    meta = {
        "universe_total": len(pool),
        "llm_symbol_count": len(selected),
        "llm_symbols_truncated": max(0, len(eligible) - len(selected)),
        "precheck_dropped": sum(1 for r in records if r.stage == "dropped"),
        "selection": f"tactical_screen_{strategy_key}",
        "screen_records": [r.to_dict() for r in records],
    }
    return selected, records, meta


def screen_tactical_group(
    strategy_keys: Tuple[str, ...],
    universe: dict,
    *,
    max_symbols_per_strategy: int = EXPLORE_LLM_MAX_SYMBOLS,
) -> Tuple[Dict[str, List[dict]], List[ExploreScreenRecord], Dict[str, Any]]:
    """合并战术组预筛：如 pullback+rebound / chase+dump."""
    by_strategy: Dict[str, List[dict]] = {}
    all_records: List[ExploreScreenRecord] = []
    group_meta: Dict[str, Any] = {"strategies": {}}

    for sk in strategy_keys:
        selected, records, meta = screen_tactical_universe(
            sk, universe, max_symbols=max_symbols_per_strategy,
        )
        by_strategy[sk] = selected
        all_records.extend(records)
        group_meta["strategies"][sk] = {
            "llm_symbol_count": meta["llm_symbol_count"],
            "precheck_dropped": meta["precheck_dropped"],
        }

    group_meta["screen_records"] = [r.to_dict() for r in all_records]
    group_meta["universe_total"] = len(universe)
    return by_strategy, all_records, group_meta
