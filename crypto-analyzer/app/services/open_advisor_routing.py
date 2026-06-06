"""开仓/持仓顾问路由 — 全部由 DeepSeek 统一监管."""


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
    """已废弃: 全部走 DeepSeek 开仓顾问."""
    return False


def resolve_open_advisors(source: str) -> tuple[str, ...]:
    """全部策略均由 DeepSeek 开仓顾问统一监管."""
    return ("deepseek",)


def should_use_gemini_hold_advisor(source: str) -> bool:
    """已废弃: 全部由 DeepSeek 持仓顾问监管."""
    return False


def should_use_deepseek_hold_advisor(source: str) -> bool:
    """全部模拟仓由 DeepSeek 持仓顾问统一监管."""
    return True


def should_use_gpt_hold_advisor(source: str) -> bool:
    """已废弃: 全部由 DeepSeek 持仓顾问监管."""
    return False
