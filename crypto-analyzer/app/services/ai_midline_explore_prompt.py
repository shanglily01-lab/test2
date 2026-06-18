"""中线做多/做空 — Gemini / DeepSeek 共用 LLM prompt（L0/L1 池，固定方向）."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from app.services.ai_big4_prompt import BIG4_PROMPT_BLOCK_EXPLORE
from app.services.midline_swing_config import (
    MIDLINE_CONFIDENCE_THRESHOLD,
    MIDLINE_HOLD_DAYS,
    MIDLINE_LEVERAGE,
    MIDLINE_LIMIT_OFFSET_PCT,
    MIDLINE_LIMIT_TIMEOUT_MINUTES,
    MIDLINE_MARGIN_USD,
    MIDLINE_SL_PCT,
    MIDLINE_TP_PCT,
)

MIDLINE_LLM_MAX_SYMBOLS = 50
MIDLINE_LLM_MAX_OUTPUT_TOKENS = 8192

MIDLINE_LONG_PROMPT = """
你是加密货币 U 本位合约 **中线做多** 分析师。任务：从 L0 白名单 + L1 黑名单1级标的池中，
结合 **24 根 1D + 60 根 1H** K 线叙事，找出未来约 **{hold_days} 天** 内具备反弹/趋势延续潜力的 LONG 机会。

## 交易参数（系统强制执行，勿在 JSON 中改写）
- 方向：**仅做多 LONG**（本任务禁止输出 bearish / SHORT）
- 保证金 {margin_usd}U · 杠杆 {leverage}x · 限价偏移 ±{limit_pct}% · 挂单超时 {limit_timeout}min
- 止损 {sl_pct} · 止盈 {tp_pct} · 计划持仓 {hold_days} 天

{llm_universe_note}

## 全局背景
{global_context_json}

## 本策略历史表现（近30天 closed）
{historical_stats_json}

{BIG4_BLOCK}

## 候选标的（含 kline_narrative.1d / 1h）
{universe_json}

## 输出要求
1. 只输出合法 JSON（无 Markdown）。
2. 仅对列表内 symbol 输出 verdict；**category 必须为 bullish**；confidence 0~1。
3. catalyst 须引用 **1D 趋势 + 1H 结构/量能/RSI**，禁止仅用 24h 涨跌幅。
4. 无合格标的时 verdicts 可为空数组，summary_zh 说明原因。
5. confidence 校准：结构清晰可交易 ≥ {conf_threshold}；模糊则 skip 或不输出。

JSON 格式:
{{
  "summary_zh": "本轮综述",
  "verdicts": [
    {{
      "symbol": "BTCUSDT",
      "category": "bullish",
      "confidence": 0.68,
      "catalyst": "1D… + 1H…",
      "data_signal": "量化摘要",
      "risk_note": "风险"
    }}
  ]
}}
"""

MIDLINE_SHORT_PROMPT = """
你是加密货币 U 本位合约 **中线做空** 分析师。任务：从 L0 白名单 + L1 黑名单1级标的池中，
结合 **24 根 1D + 60 根 1H** K 线叙事，找出未来约 **{hold_days} 天** 内具备回调/趋势衰竭潜力的 SHORT 机会。

## 交易参数（系统强制执行）
- 方向：**仅做空 SHORT**（本任务禁止输出 bullish / LONG）
- 保证金 {margin_usd}U · 杠杆 {leverage}x · 限价偏移 ±{limit_pct}% · 挂单超时 {limit_timeout}min
- 止损 {sl_pct} · 止盈 {tp_pct} · 计划持仓 {hold_days} 天

{llm_universe_note}

## 全局背景
{global_context_json}

## 本策略历史表现
{historical_stats_json}

{BIG4_BLOCK}

## 候选标的
{universe_json}

## 输出要求
1. 只输出合法 JSON。
2. 仅对列表内 symbol 输出 verdict；**category 必须为 bearish**。
3. catalyst 须引用 1D + 1H 技术面；禁止仅用 24h 涨跌幅。
4. confidence ≥ {conf_threshold} 才值得开仓。

JSON 格式同探索策略（summary_zh + verdicts[]，category=bearish）。
"""


def _format_pct(pct: float) -> str:
    if pct == int(pct):
        return f"{int(pct)}%"
    return f"{pct:.1f}%".rstrip("0").rstrip(".")


def build_midline_prompt(
    profile: str,
    universe: Dict[str, Any],
    global_ctx: dict,
    historical_stats: dict,
    *,
    meta: Dict[str, Any],
) -> str:
    """profile: long | short"""
    p = profile.strip().lower()
    template = MIDLINE_LONG_PROMPT if p == "long" else MIDLINE_SHORT_PROMPT
    universe_list = list(universe.values())
    compact = {"ensure_ascii": False, "separators": (",", ":"), "default": str}
    return template.format(
        hold_days=MIDLINE_HOLD_DAYS,
        margin_usd=int(MIDLINE_MARGIN_USD),
        leverage=MIDLINE_LEVERAGE,
        limit_pct=_format_pct(MIDLINE_LIMIT_OFFSET_PCT),
        limit_timeout=MIDLINE_LIMIT_TIMEOUT_MINUTES,
        sl_pct=_format_pct(MIDLINE_SL_PCT),
        tp_pct=_format_pct(MIDLINE_TP_PCT),
        conf_threshold=MIDLINE_CONFIDENCE_THRESHOLD,
        llm_universe_note=(
            f"本列表为全池 {meta.get('universe_total', len(universe_list))} 个 L0/L1 标的中，"
            f"按量化预评分取 TOP {meta.get('llm_symbol_count', len(universe_list))}；"
            f"仅对这些 symbol 输出 verdict。"
        ),
        global_context_json=json.dumps(global_ctx, **compact),
        historical_stats_json=json.dumps(historical_stats, **compact),
        BIG4_BLOCK=BIG4_PROMPT_BLOCK_EXPLORE,
        universe_json=json.dumps(universe_list, **compact),
    )


def expected_category(profile: str) -> str:
    return "bullish" if profile.strip().lower() == "long" else "bearish"
