"""顶空底多探索 — 共用 prompt (Gemini / DeepSeek).

策略: 顶部反转做空 (top_reversal → SHORT), 底部反转做多 (bottom_reversal → LONG).
仅模拟仓; 技术面 catalyst 门槛与方向探索共用多周期 K 线校验.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from app.services.ai_big4_prompt import BIG4_PROMPT_BLOCK_EXPLORE
from app.services.ai_explore_prompt import (
    EXPLORE_LLM_MAX_OUTPUT_TOKENS,
    EXPLORE_LLM_MAX_SYMBOLS,
    explore_catalyst_technical_ok,
    parse_explore_llm_json,
    prepare_universe_for_llm,
)

REVERSAL_CONFIDENCE_THRESHOLD = 0.5

REVERSAL_PROMPT_TEMPLATE = """你是加密货币 U 本位合约的**反转交易**分析师。任务：在候选池中找出「顶部做空、底部做多」机会。

## 策略定义
- **top_reversal**（顶部反转）→ 后续应 **SHORT**：多周期滞涨、上影线、RSI 超买区、假突破后回落、连阴确认等。
- **bottom_reversal**（底部反转）→ 后续应 **LONG**：多周期止跌、下影线、RSI 超卖区、假跌破后反弹、连阳确认等。
- **skip**：无清晰反转结构，或仅因 24h 涨跌幅/资金费率主观判断。

## 硬性规则
1. 每个 verdict 的 catalyst 必须写明 **至少两个周期** (1h/15m/1d) 的 K 线形态 + **量化技术位** (RSI 数值、EMA 偏离、7d 高低距离、阳阴根数等)。
2. 禁止仅用「涨多了做空 / 跌多了做多」；须有多周期结构证据。
3. 全池仅对下方 universe 列表中的 symbol 输出 verdict；其余忽略。
4. confidence 0~1；仅当结构清晰时给 ≥0.5。
5. 宁缺毋滥：无把握一律 skip。

{llm_universe_note}

## Big4 / 宏观 (仅参考，不可替代 K 线)
{big4_block}

## 全局市场
{global_context_json}

## 本策略历史表现 (closed 模拟单)
{historical_stats_json}

## 候选 universe (JSON 数组)
{universe_json}

## 输出 JSON (仅此结构，无 markdown)
{{
  "summary_zh": "本轮反转机会概况，1-3 句中文",
  "verdicts": [
    {{
      "symbol": "BTCUSDT",
      "category": "top_reversal|bottom_reversal|skip",
      "confidence": 0.0,
      "catalyst": "多周期 K 线 + RSI/EMA 量化描述",
      "data_signal": "可选补充",
      "risk_note": "可选"
    }}
  ]
}}
"""


def build_reversal_explore_prompt(
    universe: dict,
    global_ctx: dict,
    historical_stats: dict,
) -> Tuple[str, Dict[str, Any]]:
    universe_list, meta = prepare_universe_for_llm(universe)
    compact = {"ensure_ascii": False, "separators": (",", ":"), "default": str}
    prompt = REVERSAL_PROMPT_TEMPLATE.format(
        big4_block=BIG4_PROMPT_BLOCK_EXPLORE,
        global_context_json=json.dumps(global_ctx, **compact),
        universe_json=json.dumps(universe_list, **compact),
        historical_stats_json=json.dumps(historical_stats, **compact),
        llm_universe_note=(
            f"本列表为全池 {meta['universe_total']} 个中按 |24h涨跌| 取 TOP {meta['llm_symbol_count']}，"
            f"仅对这些 symbol 输出 verdict."
        ),
    )
    return prompt, meta


def parse_reversal_llm_json(text: str, tag: str = "Reversal") -> Tuple[Optional[dict], Optional[str]]:
    return parse_explore_llm_json(text, tag)


def reversal_category_to_side(category: str, confidence: float) -> Optional[str]:
    """top_reversal → SHORT, bottom_reversal → LONG."""
    cat = (category or "").lower().strip()
    if confidence < REVERSAL_CONFIDENCE_THRESHOLD:
        return None
    if cat == "top_reversal":
        return "SHORT"
    if cat == "bottom_reversal":
        return "LONG"
    return None


def reversal_catalyst_technical_ok(
    category: str,
    catalyst: str,
    data_signal: str = "",
    sym_data: Optional[dict] = None,
) -> Tuple[bool, str]:
    """多周期 K 线门槛 + 反转方向与 RSI 粗校验."""
    ok, reason = explore_catalyst_technical_ok(catalyst, data_signal, sym_data)
    if not ok:
        return ok, reason

    cat = (category or "").lower().strip()
    text = f"{catalyst or ''} {data_signal or ''}".lower()
    tech = (sym_data or {}).get("tech") or {}
    rsi = tech.get("rsi_14_1h")

    if cat == "top_reversal":
        if rsi is not None and float(rsi) < 55:
            if "超买" not in text and "overbought" not in text and "rsi" not in text:
                return False, f"顶部做空需 RSI 超买区或 catalyst 写明超买 (当前 1h RSI={rsi})"
        weak_top = ("涨多了", "涨幅过大", "已经涨", "24h涨") and not any(
            x in text for x in ("上影", "滞涨", "假突破", "连阴", "回落", "阻力")
        )
        if weak_top:
            return False, "顶部做空不能仅因涨幅大，须写明滞涨/上影/假突破等结构"

    if cat == "bottom_reversal":
        if rsi is not None and float(rsi) > 45:
            if "超卖" not in text and "oversold" not in text and "rsi" not in text:
                return False, f"底部做多需 RSI 超卖区或 catalyst 写明超卖 (当前 1h RSI={rsi})"
        weak_bot = ("跌多了", "跌幅过大", "已经跌", "24h跌") and not any(
            x in text for x in ("下影", "止跌", "假跌破", "连阳", "反弹", "支撑")
        )
        if weak_bot:
            return False, "底部做多不能仅因跌幅大，须写明止跌/下影/假跌破等结构"

    return True, ""
