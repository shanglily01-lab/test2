"""模拟开仓前 Gemini 顾问闸门 — 所有 account_id=2 开仓路径应调用."""
from __future__ import annotations

from typing import Optional, Tuple

from loguru import logger


def gate_simulated_open(
    symbol: str,
    side: str,
    price: float,
    source: str,
    catalyst: str = "",
    leverage: int = 5,
    sl_pct: Optional[float] = None,
    tp_pct: Optional[float] = None,
    hold_hours: Optional[float] = None,
    conn=None,
) -> Tuple[bool, str]:
    """
    开仓前审核。返回 (允许开仓, 原因).
    顾问关闭 / API 异常时放行 (降级), 仅明确 reject 时拦截。
    """
    try:
        from app.services.gemini_position_advisor import get_open_advisor

        advisor = get_open_advisor()
        return advisor.review_open(
            symbol=symbol,
            side=side,
            price=price,
            source=source,
            catalyst=catalyst,
            leverage=leverage,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
            hold_hours=hold_hours,
            conn=conn,
        )
    except Exception as e:
        logger.warning(f"[开仓顾问] {symbol} 审核异常, 放行: {e}")
        return True, "advisor_error_allow"
