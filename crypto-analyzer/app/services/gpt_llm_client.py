"""GPT OpenAI 调用 — 与 Gemini 对齐: 中文约束 + 低温, 避免主观发挥."""
from __future__ import annotations

from typing import Any, Optional

from app.services.ai_explore_prompt import EXPLORE_LLM_MAX_OUTPUT_TOKENS
from app.services.gpt_config import GPT_MODEL, GPT_TIMEOUT_S

# Gemini 无单独 system; GPT 用极短中文 system 强化客观性, 不覆盖 user 内策略全文
GPT_LLM_TEMPERATURE = 0.1

GPT_JSON_SYSTEM_EN = """You are a quantitative crypto futures analysis engine. The user message is the sole source of truth.

Hard requirements (aligned with Gemini/DeepSeek explore):
1. catalyst / data_signal must cite kline_narrative (1h: 24-bar trend + last 4-6 bars) and tech (RSI value if present; or EMA/7d/bar counts).
2. Do not use 24h change, funding rate, or Big4 alone as primary reason; no multi-bar structure → skip / no entry.
3. Do not invent prices or indicators; must match universe rows.
4. summary_zh: 1-3 sentences in Chinese describing technical backdrop only.
5. Output one valid JSON object only, no markdown fences.
6. If prefiltered universe has symbols meeting hard lines, verdicts must not be empty — pick 1-3 best entries; no macro-only table skip."""

GPT_JSON_SYSTEM_ZH = """你是量化交易分析引擎。用户消息中的策略定义与 universe 数据为唯一依据。

硬性要求（与 Gemini 探索一致）:
1. catalyst / data_signal 必须引用 universe 里的 kline_narrative（1h 近24根整体 + 近4~6根结构）与 tech（有 rsi_14_1h 则 catalyst 须写出 RSI 数值；或 EMA/7d/阳阴根数）。
2. 禁止仅用 24h 涨跌幅、资金费率、Big4/大盘一句话作为主因；无多周期 K 线结构则 skip / 不给 entry。
3. 不得编造数据中不存在的价位、涨跌幅或指标；涨跌幅须与 kline_narrative / tech 一致。
4. summary_zh 只描述技术面氛围, 不写空泛多空偏好。
5. 仅输出一个合法 JSON 对象, 无 markdown 代码块, 字段名与用户消息约定一致。
6. 若 user 提供的 universe 经预筛后仍有币种且 tech/叙事满足策略硬线，verdicts 不得为空；须输出 1~3 个最符合的 entry，勿因宏观/Big4 悲观整表 skip。"""


def _completion_token_param(max_tokens: int) -> dict:
    if GPT_MODEL.startswith("gpt-5"):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


def gpt_chat_json(
    client: Any,
    *,
    user_prompt: str,
    max_tokens: int = EXPLORE_LLM_MAX_OUTPUT_TOKENS,
    timeout: Optional[int] = None,
    system_prompt: Optional[str] = None,
) -> str:
    """OpenAI Chat Completions, JSON mode, 统一温度与 system."""
    params = {
        "model": GPT_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt or GPT_JSON_SYSTEM_ZH},
            {"role": "user", "content": user_prompt},
        ],
        "timeout": timeout or GPT_TIMEOUT_S,
        "response_format": {"type": "json_object"},
    }
    if not GPT_MODEL.startswith("gpt-5"):
        params["temperature"] = GPT_LLM_TEMPERATURE
    params.update(_completion_token_param(max_tokens))
    resp = client.chat.completions.create(**params)
    return (resp.choices[0].message.content or "").strip()
