"""开仓/持仓顾问路由.

Gemini 探索/预测已下线：开仓/持仓顾问统一走 DeepSeek。
中线 v2 仍由 hold_advisor_query SQL 排除，不进持仓顾问。
"""


GEMINI_PRIMARY_ORDER_SOURCES = {"gemini_explore", "gemini_predict"}


def is_gemini_order_source(source: str) -> bool:
    s = (source or "").strip().lower()
    return s.startswith("gemini_")


def is_deepseek_order_source(source: str) -> bool:
    s = (source or "").strip().lower()
    return s.startswith("deepseek_")


def uses_gemini_open_advisor(source: str) -> bool:
    """Gemini 开仓顾问已下线；恒 False。"""
    return False


def resolve_open_advisors(source: str) -> tuple[str, ...]:
    """按订单 source 选择开仓顾问（统一 DeepSeek）。"""
    return ("deepseek",)


def should_use_gemini_hold_advisor(source: str) -> bool:
    """Gemini 持仓顾问已下线；恒 False。"""
    return False


def should_use_deepseek_hold_advisor(source: str) -> bool:
    """DeepSeek 持仓顾问监管全部非中线模拟仓（含历史 gemini_*）。"""
    return True
