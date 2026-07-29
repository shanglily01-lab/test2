"""Legacy import shim for position advisor implementation.

Implementation lives in position_advisor_impl.py. Prefer importing via
advisor_core for active DeepSeek / BRAIN / smart-exit paths.
"""
from __future__ import annotations

from app.services.position_advisor_impl import (  # noqa: F401
    ADVISOR_PER_CALL_DELAY_S,
    GEMINI_PER_CALL_DELAY_S,
    GEMINI_TIMEOUT_MS,
    HOLD_15M_BARS,
    HOLD_1H_BARS,
    HOLD_5M_BARS,
    HOLD_ADVISOR_JSON_SYSTEM_ZH,
    HOLD_CHECK_INTERVAL_S,
    HOLD_LOSS_MILD_ROI,
    HOLD_LOSS_MODERATE_ROI,
    HOLD_LOSS_SEVERE_ROI,
    HOLD_LOSS_STRICT_ROI,
    HOLD_MIN_HOURS,
    HOLD_MIN_MINUTES,
    HOLD_NO_FOLLOW_PEAK_ROI,
    HOLD_NO_FOLLOW_SELL_ROI,
    HOLD_PEAK_ROI_GIVEBACK_SELL,
    HOLD_PROFIT_SELL_ROI,
    HOLD_PROFIT_TEMPER_ROI,
    HOLD_RISK_REASON_TAGS,
    OPEN_ADVISOR_JSON_SYSTEM_ZH,
    GeminiPositionAdvisor,
    PositionAdvisorCore,
    get_open_advisor,
)

__all__ = [
    "ADVISOR_PER_CALL_DELAY_S",
    "GEMINI_PER_CALL_DELAY_S",
    "GEMINI_TIMEOUT_MS",
    "HOLD_15M_BARS",
    "HOLD_1H_BARS",
    "HOLD_5M_BARS",
    "HOLD_ADVISOR_JSON_SYSTEM_ZH",
    "HOLD_CHECK_INTERVAL_S",
    "HOLD_LOSS_MILD_ROI",
    "HOLD_LOSS_MODERATE_ROI",
    "HOLD_LOSS_SEVERE_ROI",
    "HOLD_LOSS_STRICT_ROI",
    "HOLD_MIN_HOURS",
    "HOLD_MIN_MINUTES",
    "HOLD_NO_FOLLOW_PEAK_ROI",
    "HOLD_NO_FOLLOW_SELL_ROI",
    "HOLD_PEAK_ROI_GIVEBACK_SELL",
    "HOLD_PROFIT_SELL_ROI",
    "HOLD_PROFIT_TEMPER_ROI",
    "HOLD_RISK_REASON_TAGS",
    "OPEN_ADVISOR_JSON_SYSTEM_ZH",
    "GeminiPositionAdvisor",
    "PositionAdvisorCore",
    "get_open_advisor",
]
