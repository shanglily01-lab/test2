"""Active advisor core facade.

Live DeepSeek / BRAIN / smart-exit paths import neutral names from here.
Implementation: position_advisor_impl.py (via advisor_core).
"""
from __future__ import annotations

from app.services.position_advisor_impl import (
    ADVISOR_PER_CALL_DELAY_S,
    HOLD_15M_BARS,
    HOLD_5M_BARS,
    HOLD_1H_BARS,
    HOLD_ADVISOR_JSON_SYSTEM_ZH,
    HOLD_CHECK_INTERVAL_S,
    HOLD_MIN_HOURS,
    HOLD_MIN_MINUTES,
    OPEN_ADVISOR_JSON_SYSTEM_ZH,
    PositionAdvisorCore as AdvisorPromptHelper,
    get_open_advisor as get_primary_open_advisor,
)

__all__ = [
    "ADVISOR_PER_CALL_DELAY_S",
    "HOLD_15M_BARS",
    "HOLD_5M_BARS",
    "HOLD_1H_BARS",
    "HOLD_ADVISOR_JSON_SYSTEM_ZH",
    "HOLD_CHECK_INTERVAL_S",
    "HOLD_MIN_HOURS",
    "HOLD_MIN_MINUTES",
    "OPEN_ADVISOR_JSON_SYSTEM_ZH",
    "AdvisorPromptHelper",
    "get_primary_open_advisor",
]
