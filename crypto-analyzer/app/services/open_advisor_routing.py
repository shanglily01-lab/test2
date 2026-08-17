"""开仓/持仓顾问路由.

Gemini 探索/预测已下线：开仓/持仓顾问统一走 DeepSeek。
中线 v2 跳过开仓顾问，但进入持仓顾问做盈利保护复核。
BRAIN 跳过开仓顾问，但进入 DeepSeek 持仓顾问做 thesis 复核。
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
    """DeepSeek 持仓顾问：监管模拟仓（含 BRAIN 与中线 v2；历史 gemini_* 亦覆盖）。"""
    return True
