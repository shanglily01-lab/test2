"""战术探索 prompt — 回调做多 / 反弹做空 / 追涨做多 / 杀跌做空 (Gemini/DeepSeek 共用)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger

from app.services.ai_big4_prompt import BIG4_PROMPT_BLOCK_EXPLORE
from app.services.ai_explore_prompt import (
    EXPLORE_LLM_MAX_SYMBOLS,
    _KBAR_COUNT_RE,
    explore_catalyst_technical_ok,
    normalize_explore_llm_payload,
    parse_explore_llm_json,
)
from app.services.ai_reversal_explore_prompt import _catalyst_pct_sane

TACTICAL_CONFIDENCE_THRESHOLD = 0.55

_KLINE_1H_RULES = """
## 1h K 线读法（必读，禁止只看 1 根）
- **整体趋势**：用 universe 中 **最近 24 根 1h**（约 24 小时）判断上涨/下跌/通道；与 `kline_narrative.1h` 的「整体 N 根趋势」一致。
- **近期结构**：用 **最近 4~6 根 1h** 描述回调、反弹、连阳/连阴、回踩支撑（catalyst 须写「最近 4~6 根」或「近 6 根 1h」等，**不得**只写「最近 1 根 1h」）。
- 15m/1d 仍作辅助；主判据以 1h 的 24 根 + 近 4~6 根为准。
"""

_JSON_OUTPUT = """
## 输出 JSON (仅此结构，无 markdown)
{{
  "summary_zh": "本轮机会概况，1-3 句中文；无机会时说明原因",
  "verdicts": [
    {{
      "symbol": "XXXUSDT",
      "category": "entry",
      "confidence": 0.68,
      "catalyst": "1h/15m 多周期 K 线 + RSI/EMA/7d/阳阴根数量化描述",
      "data_signal": "可选",
      "risk_note": "可选"
    }}
  ]
}}
无符合策略的标的时 verdicts 用空数组 []，不要逐条输出 skip。
"""


@dataclass(frozen=True)
class TacticalStrategyDef:
    key: str
    title_zh: str
    fixed_side: str  # LONG | SHORT
    prompt_body: str
    extra_catalyst_check: Optional[Callable[[str, str, Optional[dict]], Tuple[bool, str]]] = None


def _narrative_text(sym_data: Optional[dict]) -> str:
    if not isinstance(sym_data, dict):
        return ""
    kn = sym_data.get("kline_narrative") or {}
    return " ".join(str(v) for v in kn.values())


def _tech(sym_data: Optional[dict]) -> dict:
    return (sym_data or {}).get("tech") or {} if isinstance(sym_data, dict) else {}


def _rsi_1h(sym_data: Optional[dict]) -> Optional[float]:
    raw = _tech(sym_data).get("rsi_14_1h")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _tactical_universe_score(defn: TacticalStrategyDef, item: dict) -> float:
    """LONG 策略偏强势币，SHORT 策略偏弱势币（非 |24h| 动量盲选）."""
    tech = item.get("tech") or {}
    rsi = tech.get("rsi_14_1h")
    try:
        rsi_f = float(rsi) if rsi is not None else 50.0
    except (TypeError, ValueError):
        rsi_f = 50.0
    chg = float(item.get("change_24h") or 0)
    b7h = tech.get("below_7d_high_pct")
    a7l = tech.get("above_7d_low_pct")
    score = 0.0
    if defn.fixed_side == "LONG":
        score = rsi_f + chg * 0.25
        if defn.key == "pullback" and b7h is not None:
            try:
                score += max(0.0, -float(b7h) * 0.3)
            except (TypeError, ValueError):
                pass
        return score
    score = (100.0 - rsi_f) - chg * 0.25
    if defn.key == "rebound" and b7h is not None:
        try:
            score += max(0.0, -float(b7h) * 0.4)
        except (TypeError, ValueError):
            pass
    if defn.key == "dump" and a7l is not None:
        try:
            score += max(0.0, (25.0 - float(a7l)) * 0.2)
        except (TypeError, ValueError):
            pass
    return score


def prepare_tactical_universe_for_llm(
    definition: TacticalStrategyDef,
    universe: dict,
    max_symbols: int = EXPLORE_LLM_MAX_SYMBOLS,
) -> Tuple[List[dict], Dict[str, Any]]:
    items = sorted(
        universe.values(),
        key=lambda x: _tactical_universe_score(definition, x),
        reverse=True,
    )
    for item in items:
        item.pop("k_1d_ohlc", None)
        item.pop("k_1h_ohlc", None)
        item.pop("k_15m_ohlc", None)
    selected = items[:max_symbols]
    meta = {
        "universe_total": len(items),
        "llm_symbol_count": len(selected),
        "llm_symbols_truncated": max(0, len(items) - len(selected)),
        "selection": f"tactical_{definition.key}",
    }
    return selected, meta


def _build_prompt(definition: TacticalStrategyDef, universe: dict, global_ctx: dict, historical_stats: dict):
    universe_list, meta = prepare_tactical_universe_for_llm(definition, universe)
    compact = {"ensure_ascii": False, "separators": (",", ":"), "default": str}
    side_note = "做多" if definition.fixed_side == "LONG" else "做空"
    prompt = (
        f"你是加密货币 U 本位合约的**{definition.title_zh}**分析师。"
        f"仅寻找符合本策略的 **{side_note}** 机会。\n\n"
        f"{definition.prompt_body}\n\n"
        f"{_KLINE_1H_RULES}\n"
        "## 硬性规则\n"
        "1. catalyst 须写明 1h **24 根整体趋势** + **近 4~6 根结构**，并辅以 15m/1d + 量化技术位 (RSI/EMA/7d/阳阴根数)。\n"
        "2. 禁止仅用 24h 涨跌幅或资金费率做主因。\n"
        "3. **只输出 category=entry 的标的**（最多 5 个）；不符合的不写入 verdicts。\n"
        "4. entry 时 confidence 必填 0.55–0.85；结构不清晰勿勉强 entry。\n"
        "5. 无机会时 verdicts=[]，在 summary_zh 说明。\n"
        "6. K 线涨跌幅须合理（单根通常 <20%），与 universe 中 kline_narrative / tech 一致。\n\n"
        f"本列表为全池 {meta['universe_total']} 个中按本策略方向相关性取 TOP "
        f"{meta['llm_symbol_count']}（非单纯 |24h涨跌| 排序）。\n\n"
        f"## Big4 / 宏观 (仅参考)\n{BIG4_PROMPT_BLOCK_EXPLORE}\n\n"
        f"## 全局市场\n{json.dumps(global_ctx, **compact)}\n\n"
        f"## 本策略历史 (30d closed)\n{json.dumps(historical_stats, **compact)}\n\n"
        f"## universe\n{json.dumps(universe_list, **compact)}\n"
        f"{_JSON_OUTPUT}"
    )
    return prompt, meta


def _coerce_verdict_fields(v: dict) -> dict:
    out = dict(v)
    for key in ("catalyst", "data_signal", "risk_note", "category"):
        val = out.get(key)
        if isinstance(val, list):
            out[key] = " ".join(str(x) for x in val if x is not None)
        elif val is not None and not isinstance(val, str):
            out[key] = str(val)
    sym = out.get("symbol")
    if sym is not None:
        out["symbol"] = str(sym).upper().replace("/", "")
    return out


def parse_tactical_llm_json(
    text: str,
    tag: str,
    fixed_side: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    parsed, err = parse_explore_llm_json(text, tag)
    if parsed is None:
        return parsed, err
    parsed = normalize_explore_llm_payload(parsed) or parsed
    flat: List[dict] = []
    for v in parsed.get("verdicts") or []:
        if isinstance(v, dict):
            flat.append(_coerce_verdict_fields(v))
        elif isinstance(v, list):
            for sub in v:
                if isinstance(sub, dict):
                    flat.append(_coerce_verdict_fields(sub))
    parsed["verdicts"] = flat
    for v in parsed["verdicts"]:
        v["category"] = normalize_tactical_category(v, fixed_side)
        v["confidence"] = parse_tactical_confidence(v)
    return parsed, err


def parse_tactical_confidence(verdict: dict) -> float:
    for key in ("confidence", "conf", "score", "prob", "probability"):
        raw = verdict.get(key)
        if raw is None:
            continue
        try:
            c = float(raw)
        except (TypeError, ValueError):
            continue
        if c > 1.0 and c <= 100.0:
            c /= 100.0
        return max(0.0, min(1.0, c))
    return 0.0


def normalize_tactical_category(verdict: dict, fixed_side: Optional[str] = None) -> str:
    for key in ("category", "cat", "action", "verdict"):
        raw = verdict.get(key)
        if not raw:
            continue
        cat = str(raw).lower().strip()
        if cat in ("skip", "none", "hold", "wait", "neutral", "跳过"):
            return "skip"
        if cat in ("entry", "signal", "open", "trade", "入场", "开仓"):
            return "entry"
        if cat in ("long", "buy", "做多", "bullish", "bottom_reversal") and (
            fixed_side in (None, "LONG")
        ):
            return "entry"
        if cat in ("short", "sell", "做空", "bearish", "top_reversal") and (
            fixed_side in (None, "SHORT")
        ):
            return "entry"
        if cat in ("bullish", "bearish", "top_reversal", "bottom_reversal"):
            return "entry"
        return cat
    return "skip"


def tactical_category_to_side(
    definition: TacticalStrategyDef,
    category: str,
    confidence: float,
) -> Optional[str]:
    cat = normalize_tactical_category({"category": category}, definition.fixed_side)
    if confidence < TACTICAL_CONFIDENCE_THRESHOLD:
        return None
    if cat == "entry":
        return definition.fixed_side
    if cat == "skip":
        return None
    if cat in ("signal", "bullish", "bearish", "top_reversal", "bottom_reversal"):
        return definition.fixed_side
    if cat == "long" and definition.fixed_side == "LONG":
        return "LONG"
    if cat == "short" and definition.fixed_side == "SHORT":
        return "SHORT"
    return None


def tactical_catalyst_ok(
    definition: TacticalStrategyDef,
    catalyst: str,
    data_signal: str = "",
    sym_data: Optional[dict] = None,
    confidence: float = 0.0,
) -> Tuple[bool, str]:
    ok, reason = explore_catalyst_technical_ok(catalyst, data_signal, sym_data)
    if not ok:
        return ok, reason
    sane, sane_reason = _catalyst_pct_sane(catalyst, data_signal)
    if not sane:
        return False, sane_reason
    if confidence >= 0.70:
        return True, ""
    if definition.extra_catalyst_check:
        return definition.extra_catalyst_check(catalyst, data_signal, sym_data)
    return True, ""


def _has_any(text: str, words: tuple) -> bool:
    low = text.lower()
    return any(w in low for w in words)


def _catalyst_uses_multi_1h_bars(catalyst: str, data_signal: str) -> bool:
    """拒绝仅依据单根 1h K 线的 catalyst."""
    text = f"{catalyst} {data_signal}".lower()
    if _has_any(text, (
        "4~6", "4-6", "近4", "近5", "近6", "最近4", "最近5", "最近6",
        "4根", "5根", "6根", "四根", "五根", "六根", "连续2", "连续3", "连续4",
        "连续 2", "连续 3", "连续 4", "两根", "三根", "四根", "连阴", "连阳",
        "24根", "24 根", "整体24", "近24", "近 24",
        "last 4", "last 5", "last 6", "4-6 bar", "recent 4", "recent 5", "recent 6",
        "24 bar", "24h bars",
    )):
        return True
    if re.search(r"连续\s*\d+\s*根", text):
        return True
    if re.search(r"最近\s*\d+\s*根", text) and not re.search(r"最近\s*1\s*根", text):
        return True
    if _has_any(text, ("24根", "整体", "通道", "趋势延续")) and _has_any(
        text, ("回调", "回踩", "反弹", "连阴", "连阳", "回落", "上行", "下行"),
    ):
        return True
    if re.search(r"最近\s*1\s*根\s*1h", text) or re.search(r"单根\s*1h", text):
        return False
    return False


def _pullback_extra(catalyst: str, data_signal: str, sym_data: Optional[dict]) -> Tuple[bool, str]:
    text = f"{catalyst} {data_signal}"
    low = text.lower()
    narr = _narrative_text(sym_data).lower()
    rsi = _rsi_1h(sym_data)

    if not _catalyst_uses_multi_1h_bars(catalyst, data_signal):
        return False, "须基于 1h 近 4~6 根结构 + 24 根整体趋势描述，禁止只看 1 根 1h"

    if not _has_any(low, (
        "24根", "24 根", "整体", "24小时", "24 hour", "近24",
    )) and "整体" not in narr:
        return False, "回调做多须写明 1h 近 24 根整体处于上涨趋势"

    if not _has_any(low, (
        "回调", "回踩", "回落", "下跌", "阴线", "下影", "探底",
        "pullback", "retrace", "dip", "pull back",
    )):
        return False, "回调做多须写明近 4~6 根 1h 回落/回踩/阴线等"

    if not _has_any(low, (
        "支撑", "企稳", "止跌", "下影", "前低", "ema", "ma", "箱体下沿",
        "support", "hold", "bounce",
    )):
        return False, "回调做多须写明支撑/企稳/下影/均线支撑"

    uptrend_ok = _has_any(
        f"{low} {narr}",
        ("上涨", "上升趋势", "上行", "偏多", "连阳", "高点抬高", "上升通道", "uptrend", "bullish"),
    )
    if not uptrend_ok:
        return False, "回调做多须先确认 1h/多周期处于上涨趋势（非单边下跌中抄底）"

    if _has_any(narr, ("偏空", "强势下降", "连阴", "下行趋势")) and not _has_any(
        narr, ("偏多", "上升", "上攻", "连阳")
    ):
        return False, "kline_narrative 1h/1d 仍偏空，不符合回调做多前提"

    if rsi is not None and rsi > 75:
        return False, f"1h RSI={rsi} 过高，更像追涨而非健康回调"

    return True, ""


def _rebound_extra(catalyst: str, data_signal: str, sym_data: Optional[dict]) -> Tuple[bool, str]:
    text = f"{catalyst} {data_signal}"
    low = text.lower()
    narr = _narrative_text(sym_data).lower()
    tech = _tech(sym_data)
    rsi = _rsi_1h(sym_data)

    if not _catalyst_uses_multi_1h_bars(catalyst, data_signal):
        return False, "须基于 1h 近 4~6 根结构 + 24 根整体趋势描述，禁止只看 1 根 1h"

    if not _has_any(low, ("反弹", "反抽", "回升", "rebound", "bounce", "relief")):
        return False, "反弹做空须写明近 4~6 根 1h 存在反弹"

    if not _has_any(low, (
        "见顶", "高点", "下降", "下跌", "下行", "偏空", "连阴", "跌破",
        "downtrend", "bearish", "top", "lower high",
    )):
        return False, "反弹做空须写明此前见顶/已进入下降趋势"

    vol_weak = _has_any(low, (
        "量能", "缩量", "萎缩", "背离", "不支持", "乏力", "无量",
        "volume", "weak", "diverg", "no follow",
    ))
    if not vol_weak:
        return False, "反弹做空须写明反弹量能不足/缩量/量价不支持"

    at_high = _has_any(low, (
        "相对高", "阻力", "前高", "上影", "滞涨", "遇阻", "7d高", "接近高",
        "resistance", "relative high", "reject",
    ))
    try:
        b7h = tech.get("below_7d_high_pct")
        if b7h is not None and float(b7h) > -12:
            at_high = True
    except (TypeError, ValueError):
        pass
    if not at_high:
        return False, "反弹做空须处于相对高点/阻力区（非下跌中继低位）"

    if rsi is not None and rsi < 40:
        return False, f"1h RSI={rsi} 偏低，更像杀跌追空而非反弹空"

    if _has_any(narr, ("强势上升", "偏多", "突破", "连阳")) and not _has_any(
        narr, ("偏空", "下降", "回落", "连阴")
    ):
        return False, "叙事仍偏强势上行，不符合反弹做空"

    return True, ""


def _chase_extra(catalyst: str, data_signal: str, sym_data: Optional[dict]) -> Tuple[bool, str]:
    text = f"{catalyst} {data_signal}"
    low = text.lower()
    narr = _narrative_text(sym_data).lower()
    rsi = _rsi_1h(sym_data)

    if not _catalyst_uses_multi_1h_bars(catalyst, data_signal):
        return False, "须基于 1h 近 4~6 根结构 + 24 根整体趋势描述，禁止只看 1 根 1h"

    if not _has_any(low, (
        "上涨", "上行", "趋势", "延续", "连阳", "新高", "通道", "多头",
        "uptrend", "rally", "continuation", "higher high",
    )):
        return False, "追涨做多须写明持续上涨/趋势延续"

    if _has_any(low, (
        "深度回调", "大幅回踩", "跌破支撑", "见顶", "滞涨做空", "逆势",
    )):
        return False, "追涨做多不应以深度回调/见顶为主叙事"

    if _has_any(low, ("放量", "volume surge", "巨量")) and _has_any(
        low, ("缩量", "量能萎缩", "量能未放大", "量能没有放大", "量能平平")
    ):
        pass  # 明确「量能未放大仍可追」时允许
    # 不要求必须放量 — 用户策略允许量能未放大

    no_pullback = _has_any(low, (
        "无明显回调", "无回调", "未见回调", "未回调", "直线", "持续上行",
        "no pullback", "without pullback",
    )) or not _has_any(low, ("深度回踩", "大幅回调", "回调到位", "支撑反弹"))
    if not no_pullback and _has_any(low, ("回调", "回踩", "探底", "支撑反弹")):
        return False, "叙事以回调做多为主，应交给回调策略而非追涨"

    if rsi is not None and rsi < 48:
        return False, f"1h RSI={rsi} 偏弱，不宜追涨"

    if _has_any(narr, ("偏空", "下降", "连阴", "下杀")) and not _has_any(narr, ("偏多", "上升", "上攻")):
        return False, "kline 叙事偏空，不符合追涨"

    return True, ""


def _dump_extra(catalyst: str, data_signal: str, sym_data: Optional[dict]) -> Tuple[bool, str]:
    text = f"{catalyst} {data_signal}"
    low = text.lower()
    narr = _narrative_text(sym_data).lower()
    rsi = _rsi_1h(sym_data)

    if not _catalyst_uses_multi_1h_bars(catalyst, data_signal):
        return False, "须基于 1h 近 4~6 根结构 + 24 根整体趋势描述，禁止只看 1 根 1h"

    if not _has_any(low, (
        "下跌", "下行", "下降", "趋势", "延续", "连阴", "新低", "跌破", "偏空",
        "downtrend", "breakdown", "lower low", "selloff",
    )):
        return False, "杀跌做空须写明下跌趋势延续"

    no_bounce_support = _has_any(low, (
        "无量反弹", "量能不支持", "量价不支持", "反弹乏力", "缩量反弹",
        "无明显反弹", "无反弹", "反弹无力", "weak bounce", "no bounce",
    )) or not _has_any(low, ("强势反弹", "放量反弹", "反弹突破"))
    if not no_bounce_support and _has_any(low, ("反弹", "回升", "反抽")):
        return False, "杀跌做空须说明反弹缺乏量能/量价支持（或趋势未改无像样反弹）"

    if _has_any(low, ("见顶回调", "支撑", "抄底", "做多")):
        return False, "叙事偏做多/抄底，不符合杀跌追空"

    if rsi is not None and rsi > 55:
        return False, f"1h RSI={rsi} 偏高，不宜杀跌追空"

    if _has_any(narr, ("偏多", "强势上升", "突破", "连阳")) and not _has_any(
        narr, ("偏空", "下降", "下杀", "连阴")
    ):
        return False, "kline 叙事仍偏强，不符合杀跌追空"

    return True, ""


PULLBACK_LONG = TacticalStrategyDef(
    key="pullback",
    title_zh="回调做多",
    fixed_side="LONG",
    prompt_body=(
        "## 策略定义（必须全部满足才 entry）\n"
        "1. **大前提**：**近 24 根 1h** 整体处于**上涨趋势**（高点抬高/通道上行；见 kline_narrative 整体形态）。\n"
        "2. **近 4~6 根 1h**：出现**回落/回调**（多根阴线、回踩、短线下跌），而非仅凭 1 根阴线。\n"
        "3. **支撑有效**：回踩至支撑（EMA/前低/箱体下沿/下影线企稳）并有止跌迹象。\n"
        "4. **方向**：仅 **做多**；禁止在单边下跌或无趋势时「跌多了抄底」。\n"
        "典型 catalyst 示例：「1h 近24根上升通道；近5根连续阴线回踩 EMA20；"
        "15m 下影企稳；RSI 1h 从 58 回落至 48」。"
    ),
    extra_catalyst_check=_pullback_extra,
)

REBOUND_SHORT = TacticalStrategyDef(
    key="rebound",
    title_zh="反弹做空",
    fixed_side="SHORT",
    prompt_body=(
        "## 策略定义（必须全部满足才 entry）\n"
        "1. **曾见顶**：此前出现相对**最高价/阶段顶**，之后进入**下降趋势**（1h/1d 结构转弱）。\n"
        "2. **近 4~6 根 1h**：出现**反弹**（多根阳线/反抽/触及均线或前高），非单根异动。\n"
        "3. **量能不支持反弹**：反弹缩量、量价背离或量能未跟上（须写明）。\n"
        "4. **位置**：当前处于**相对高点/阻力区**（距 7d 高点较近或上影受阻）。\n"
        "5. **方向**：仅 **做空**；禁止在强趋势突破新高时做空。\n"
        "典型：「1h 近24根下降通道；近4根缩量反弹碰前高；"
        "15m 上影；RSI 1h 52 反弹无力」。"
    ),
    extra_catalyst_check=_rebound_extra,
)

CHASE_LONG = TacticalStrategyDef(
    key="chase",
    title_zh="追涨做多",
    fixed_side="LONG",
    prompt_body=(
        "## 策略定义（必须全部满足才 entry）\n"
        "1. **持续上涨**：**近 24 根 1h** 趋势向上，**近 4~6 根** 仍延续上行、**无明显深度回调**。\n"
        "2. **量能**：**不要求放量**；即使量能未放大、量能平平，只要趋势延续仍可 entry。\n"
        "3. **禁止**：把「大幅回踩支撑」当追涨；那是回调做多。\n"
        "4. **方向**：仅 **做多**；须有趋势延续证据（连阳、通道、高点抬升等）。\n"
        "典型：「1h 近24根偏多；近6根连阳无像样回调；量能未放大；RSI 1h 62」。"
    ),
    extra_catalyst_check=_chase_extra,
)

DUMP_SHORT = TacticalStrategyDef(
    key="dump",
    title_zh="杀跌做空",
    fixed_side="SHORT",
    prompt_body=(
        "## 策略定义（必须全部满足才 entry）\n"
        "1. **下跌趋势**：**近 24 根 1h** 处于**下降通道**，趋势**未扭转**。\n"
        "2. **反弹无力**：无明显量价支持的反弹，或反弹缩量、反弹失败（须写明）。\n"
        "3. **追空**：顺势**做空**延续下跌，非在底部博反弹。\n"
        "4. **量能**：不要求杀跌必须放量；关键是**没有**像样的放量反弹。\n"
        "典型：「1h 近24根连阴下行；近5根反弹无量；15m 反抽失败；RSI 1h 38」。"
    ),
    extra_catalyst_check=_dump_extra,
)

TACTICAL_STRATEGIES: Dict[str, TacticalStrategyDef] = {
    "pullback": PULLBACK_LONG,
    "rebound": REBOUND_SHORT,
    "chase": CHASE_LONG,
    "dump": DUMP_SHORT,
}


def build_strategy_prompt(
    strategy_key: str,
    universe: dict,
    global_ctx: dict,
    historical_stats: dict,
) -> Tuple[str, Dict[str, Any]]:
    defn = TACTICAL_STRATEGIES[strategy_key]
    return _build_prompt(defn, universe, global_ctx, historical_stats)
