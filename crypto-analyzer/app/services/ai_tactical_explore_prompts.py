"""战术探索 prompt — 回调做多 / 反弹做空 / 追涨做多 / 杀跌做空 (Gemini/DeepSeek 共用)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from app.services.ai_big4_prompt import BIG4_PROMPT_BLOCK_EXPLORE
from app.services.ai_explore_prompt import (
    explore_catalyst_technical_ok,
    parse_explore_llm_json,
    prepare_universe_for_llm,
)

TACTICAL_CONFIDENCE_THRESHOLD = 0.5

_JSON_OUTPUT = """
## 输出 JSON (仅此结构，无 markdown)
{{
  "summary_zh": "本轮机会概况，1-3 句中文",
  "verdicts": [
    {{
      "symbol": "BTCUSDT",
      "category": "entry|skip",
      "confidence": 0.0,
      "catalyst": "多周期 K 线 + RSI/EMA 量化描述",
      "data_signal": "可选",
      "risk_note": "可选"
    }}
  ]
}}
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
        "3. 仅对下方 universe 中的 symbol 输出 verdict。\n"
        "4. category 仅 `entry` 或 `skip`；confidence≥0.5 且结构清晰才用 entry。\n"
        "5. 宁缺毋滥。\n\n"
        f"本列表为全池 {meta['universe_total']} 个中按 |24h涨跌| 取 TOP {meta['llm_symbol_count']}。\n\n"
        f"## Big4 / 宏观 (仅参考)\n{BIG4_PROMPT_BLOCK_EXPLORE}\n\n"
        f"## 全局市场\n{json.dumps(global_ctx, **compact)}\n\n"
        f"## 本策略历史 (30d closed)\n{json.dumps(historical_stats, **compact)}\n\n"
        f"## universe\n{json.dumps(universe_list, **compact)}\n"
        f"{_JSON_OUTPUT}"
    )
    return prompt, meta


def parse_tactical_llm_json(text: str, tag: str) -> Tuple[Optional[dict], Optional[str]]:
    return parse_explore_llm_json(text, tag)


def tactical_category_to_side(
    definition: TacticalStrategyDef,
    category: str,
    confidence: float,
) -> Optional[str]:
    cat = (category or "").lower().strip()
    if confidence < TACTICAL_CONFIDENCE_THRESHOLD:
        return None
    if cat in ("entry", "signal", "bullish", "bearish", "top_reversal", "bottom_reversal"):
        return definition.fixed_side
    return None


def tactical_catalyst_ok(
    definition: TacticalStrategyDef,
    catalyst: str,
    data_signal: str = "",
    sym_data: Optional[dict] = None,
) -> Tuple[bool, str]:
    ok, reason = explore_catalyst_technical_ok(catalyst, data_signal, sym_data)
    if not ok:
        return ok, reason
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
    if not any(x in text for x in (
        "反弹", "受阻", "阻力", "上影", "回落", "滞涨", "ma", "ema",
        "rebound", "resistance", "reject", "upper shadow", "wick", "stall",
    )):
        return False, "反弹做空须写明受阻/阻力/上影/滞涨等结构"
    return True, ""


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
    if not any(x in text for x in (
        "跌破", "放量", "连阴", "下杀", "新低", "动能", "趋势",
        "breakdown", "break down", "volume", "downtrend", "selloff", "new low",
    )):
        return False, "杀跌做空须写明跌破/放量/连阴/趋势延续等"
    return True, ""


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
        "典型：1h/15m 上影 + RSI 从超买区回落 + 缩量反弹后放量阴线。"
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
