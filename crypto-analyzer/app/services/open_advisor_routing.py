"""开仓/持仓顾问路由 — 按订单 source 决定 Gemini / DeepSeek / 双审."""
from __future__ import annotations


def is_gemini_order_source(source: str) -> bool:
    s = (source or "").strip().lower()
    return s.startswith("gemini_")


def is_deepseek_order_source(source: str) -> bool:
    s = (source or "").strip().lower()
    return s.startswith("deepseek_")


def is_gpt_order_source(source: str) -> bool:
    s = (source or "").strip().lower()
    return s.startswith("gpt_")


def uses_gemini_open_advisor(source: str) -> bool:
    """gemini_* 与 gpt_* 均走 Gemini 开仓顾问（GPT 不再自审）。"""
    return is_gemini_order_source(source) or is_gpt_order_source(source)


def resolve_open_advisors(source: str) -> tuple[str, ...]:
    """
    gemini_* / gpt_* → 仅 Gemini 开仓顾问；
    deepseek_* → 仅 DeepSeek 顾问；
    其他策略 → Gemini + DeepSeek 双审。
    """
    if uses_gemini_open_advisor(source):
        return ("gemini",)
    if is_deepseek_order_source(source):
        return ("deepseek",)
    return ("gemini", "deepseek")


def should_use_gemini_hold_advisor(source: str) -> bool:
    """非 deepseek_* 模拟仓（含 gpt_*）由 Gemini 持仓顾问监管."""
    return not is_deepseek_order_source(source)


def should_use_deepseek_hold_advisor(source: str) -> bool:
    """deepseek_* 模拟仓由 DeepSeek 持仓顾问监管."""
    return is_deepseek_order_source(source)


def should_use_gpt_hold_advisor(source: str) -> bool:
    """已废弃：gpt_* 改由 Gemini 持仓顾问监管。"""
    return False
