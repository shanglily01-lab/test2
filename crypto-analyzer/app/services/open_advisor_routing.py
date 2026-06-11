"""开仓/持仓顾问路由."""


GEMINI_PRIMARY_ORDER_SOURCES = {"gemini_explore", "gemini_predict"}


def is_gemini_order_source(source: str) -> bool:
    s = (source or "").strip().lower()
    return s.startswith("gemini_")


def is_deepseek_order_source(source: str) -> bool:
    s = (source or "").strip().lower()
    return s.startswith("deepseek_")


def uses_gemini_open_advisor(source: str) -> bool:
    """Gemini 主探索/预测订单由 Gemini 开仓顾问审核."""
    return (source or "").strip().lower() in GEMINI_PRIMARY_ORDER_SOURCES


def resolve_open_advisors(source: str) -> tuple[str, ...]:
    """按订单 source 选择开仓顾问."""
    if uses_gemini_open_advisor(source):
        return ("gemini",)
    return ("deepseek",)


def should_use_gemini_hold_advisor(source: str) -> bool:
    """Gemini 主探索/预测持仓由 Gemini 持仓顾问监管."""
    return (source or "").strip().lower() in GEMINI_PRIMARY_ORDER_SOURCES


def should_use_deepseek_hold_advisor(source: str) -> bool:
    """其余模拟仓（含 gpt_*）由 DeepSeek 持仓顾问监管."""
    return not should_use_gemini_hold_advisor(source)
