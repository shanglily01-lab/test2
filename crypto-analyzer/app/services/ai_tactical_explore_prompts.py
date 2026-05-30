"""战术探索 prompt — 回调做多 / 反弹做空 / 追涨做多 / 杀跌做空 (Gemini/DeepSeek 共用)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.ai_big4_prompt import BIG4_PROMPT_BLOCK_EXPLORE
from app.services.ai_explore_prompt import (
    _KBAR_COUNT_RE,
    explore_catalyst_technical_ok,
    normalize_explore_llm_payload,
    parse_explore_llm_json,
    prepare_universe_for_llm,
)

TACTICAL_CONFIDENCE_THRESHOLD = 0.5

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


def _build_prompt(definition: TacticalStrategyDef, universe: dict, global_ctx: dict, historical_stats: dict):
    universe_list, meta = prepare_universe_for_llm(universe)
    compact = {"ensure_ascii": False, "separators": (",", ":"), "default": str}
    prompt = (
        f"你是加密货币 U 本位合约的**{definition.title_zh}**分析师。仅寻找符合本策略的 **{definition.fixed_side}** 机会。\n\n"
        f"{definition.prompt_body}\n\n"
        "## 硬性规则\n"
        "1. catalyst 须写明至少两个周期 (1h/15m/1d) 的 K 线形态 + 量化技术位 (RSI/EMA/7d/阳阴根数)。\n"
        "2. 禁止仅用 24h 涨跌幅或资金费率做主因。\n"
        "3. **只输出 category=entry 的标的**（最多 5 个）；不符合的不写入 verdicts。\n"
        "4. entry 时 confidence 必填 0.50–0.85（一般 0.55–0.65，结构清晰 0.65–0.75）；勿填 0。\n"
        "5. 无机会时 verdicts=[]，在 summary_zh 说明；结构基本符合即可 entry，不必等完美形态。\n\n"
        f"本列表为全池 {meta['universe_total']} 个中按 |24h涨跌| 取 TOP {meta['llm_symbol_count']}。\n\n"
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
    # 置信度≥0.65 且已通过多周期/量化校验 → 不再卡策略关键词 (LLM 措辞不一)
    if confidence >= 0.65:
        return True, ""
    if definition.extra_catalyst_check:
        return definition.extra_catalyst_check(catalyst, data_signal, sym_data)
    return True, ""


# ── 四策略定义 ──

def _pullback_extra(catalyst: str, data_signal: str, sym_data: Optional[dict]) -> Tuple[bool, str]:
    text = f"{catalyst} {data_signal}".lower()
    if not any(x in text for x in (
        "回调", "回踩", "支撑", "止跌", "下影", "反弹", "ma", "ema",
        "pullback", "retrace", "support", "bounce", "lower shadow", "wick",
    )):
        return False, "回调做多须写明回踩/支撑/下影/止跌等结构"
    return True, ""


def _rebound_extra(catalyst: str, data_signal: str, sym_data: Optional[dict]) -> Tuple[bool, str]:
    text = f"{catalyst} {data_signal}".lower()
    if "无反弹" in text or "没有反弹" in text:
        return False, "反弹做空须写明受阻/阻力/上影/滞涨等结构"
    if any(x in text for x in (
        "受阻", "阻力", "上影", "回落", "滞涨", "遇阻", "冲高回落", "反弹失败",
        "ma", "ema", "rebound", "resistance", "reject", "upper shadow", "wick", "stall",
    )):
        return True, ""
    if "反弹" in text and any(x in text for x in ("空", "跌", "阴", "下", "回落", "偏空", "下降")):
        return True, ""
    if _KBAR_COUNT_RE.search(text):
        return True, ""
    return False, "反弹做空须写明受阻/阻力/上影/滞涨等结构"


def _chase_extra(catalyst: str, data_signal: str, sym_data: Optional[dict]) -> Tuple[bool, str]:
    text = f"{catalyst} {data_signal}".lower()
    if not any(x in text for x in (
        "突破", "放量", "连阳", "趋势", "上攻", "新高", "动能",
        "breakout", "volume", "momentum", "uptrend", "rally", "surge",
    )):
        return False, "追涨做多须写明突破/放量/连阳/趋势延续等"
    return True, ""


def _dump_extra(catalyst: str, data_signal: str, sym_data: Optional[dict]) -> Tuple[bool, str]:
    text = f"{catalyst} {data_signal}".lower()
    if any(x in text for x in (
        "跌破", "放量", "连阴", "下杀", "新低", "动能", "趋势", "下降", "下行", "偏空", "通道",
        "breakdown", "break down", "volume", "downtrend", "selloff", "new low",
    )):
        return True, ""
    if _KBAR_COUNT_RE.search(text):
        return True, ""
    return False, "杀跌做空须写明跌破/放量/连阴/趋势延续等"


PULLBACK_LONG = TacticalStrategyDef(
    key="pullback",
    title_zh="回调做多",
    fixed_side="LONG",
    prompt_body=(
        "## 策略\n"
        "上升趋势或震荡偏强中，价格回踩关键支撑 (EMA/前低/箱体下沿) 后企稳，**做多**。\n"
        "典型：1h/15m 下影 + RSI 从超卖区回升 + 缩量回踩后放量阳线。"
    ),
    extra_catalyst_check=_pullback_extra,
)

REBOUND_SHORT = TacticalStrategyDef(
    key="rebound",
    title_zh="反弹做空",
    fixed_side="SHORT",
    prompt_body=(
        "## 策略\n"
        "下降趋势或震荡偏弱中，价格反弹至阻力 (EMA/前高/箱体上沿) 后受阻，**做空**。\n"
        "典型：1h/15m 上影 + RSI 从超买区回落 + 缩量反弹后放量阴线。\n"
        "1h/1d 偏空时，15m 小反弹碰 EMA/前高即受阻也可 entry (不必等大反弹)。"
    ),
    extra_catalyst_check=_rebound_extra,
)

CHASE_LONG = TacticalStrategyDef(
    key="chase",
    title_zh="追涨做多",
    fixed_side="LONG",
    prompt_body=(
        "## 策略\n"
        "多周期共振上行，突破关键阻力后趋势延续，**顺势做多** (非盲目追高)。\n"
        "须有：突破确认 + 量能配合 + 1h/15m 结构未破坏。"
    ),
    extra_catalyst_check=_chase_extra,
)

DUMP_SHORT = TacticalStrategyDef(
    key="dump",
    title_zh="杀跌做空",
    fixed_side="SHORT",
    prompt_body=(
        "## 策略\n"
        "多周期共振下行，跌破关键支撑后趋势延续，**顺势做空**。\n"
        "须有：跌破确认 + 量能配合 + 1h/15m 结构未破坏。"
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
