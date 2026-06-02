"""开仓顾问路由 — 按订单 source 决定 Gemini / DeepSeek / 双审."""
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


def resolve_open_advisors(source: str) -> tuple[str, ...]:
    """
    Gemini 订单 → 仅 Gemini 顾问；
    DeepSeek 订单 → 仅 DeepSeek 顾问；
    其他策略 → Gemini + DeepSeek 均须通过。
    """
    if is_gemini_order_source(source):
        return ("gemini",)
    if is_deepseek_order_source(source):
        return ("deepseek",)
    if is_gpt_order_source(source):
        return ("gpt",)
    return ("gemini", "deepseek")


def should_use_gemini_hold_advisor(source: str) -> bool:
    """非 deepseek_* 模拟仓由 Gemini 持仓顾问监管."""
    return not is_deepseek_order_source(source) and not is_gpt_order_source(source)


def should_use_deepseek_hold_advisor(source: str) -> bool:
    """deepseek_* 模拟仓由 DeepSeek 持仓顾问监管."""
    return is_deepseek_order_source(source)


def should_use_gpt_hold_advisor(source: str) -> bool:
    """gpt_* 模拟仓由 GPT 持仓顾问监管."""
    return is_gpt_order_source(source)
