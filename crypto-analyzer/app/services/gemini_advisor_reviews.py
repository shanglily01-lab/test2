"""Gemini 顾问审核记录 — 兼容壳（写入层见 advisor_review_store）."""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.advisor_review_store import (
    GEMINI_ADVISOR_REVIEWS_TABLE,
    log_advisor_review_row,
)


def log_advisor_review(
    review_type: str,
    decision: str,
    symbol: str,
    *,
    position_side: Optional[str] = None,
    source: Optional[str] = None,
    position_id: Optional[int] = None,
    entry_price: Optional[float] = None,
    leverage: Optional[int] = None,
    hold_hours: Optional[float] = None,
    roi_pct: Optional[float] = None,
    reason: Optional[str] = None,
    catalyst: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    prompt_text: Optional[str] = None,
    input_json: Optional[Dict[str, Any]] = None,
    raw_response: Optional[str] = None,
    system_prompt: Optional[str] = None,
    conn=None,
) -> Optional[int]:
    """写入 gemini_advisor_reviews（遗留 Gemini 顾问路径）."""
    return log_advisor_review_row(
        GEMINI_ADVISOR_REVIEWS_TABLE,
        review_type,
        decision,
        symbol,
        log_tag="顾问记录/Gemini",
        position_side=position_side,
        source=source,
        position_id=position_id,
        entry_price=entry_price,
        leverage=leverage,
        hold_hours=hold_hours,
        roi_pct=roi_pct,
        reason=reason,
        catalyst=catalyst,
        extra=extra,
        prompt_text=prompt_text,
        input_json=input_json,
        raw_response=raw_response,
        system_prompt=system_prompt,
        conn=conn,
    )


__all__ = ["log_advisor_review"]
