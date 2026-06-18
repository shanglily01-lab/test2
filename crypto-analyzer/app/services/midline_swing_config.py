"""中线做多/做空策略 — 常量与 source 命名."""
from __future__ import annotations

from typing import Dict, Tuple

MIDLINE_HOLD_DAYS = 15
MIDLINE_HOLD_MINUTES = MIDLINE_HOLD_DAYS * 24 * 60
MIDLINE_LEVERAGE = 5
MIDLINE_MARGIN_USD = 500.0
MIDLINE_LIMIT_OFFSET_PCT = 3.0
MIDLINE_LIMIT_TIMEOUT_MINUTES = 120
MIDLINE_SL_PCT = 6.0
MIDLINE_TP_PCT = 20.0
MIDLINE_INTERVAL_HOURS = 6
MIDLINE_MIN_SIGNAL_SCORE = 55.0
MIDLINE_CONFIDENCE_THRESHOLD = 0.60
MIDLINE_LLM_MAX_SYMBOLS = 50
MIDLINE_ACCOUNT_ID = 2

MIDLINE_SOURCES = frozenset({
    "gemini_midline_long",
    "gemini_midline_short",
    "deepseek_midline_long",
    "deepseek_midline_short",
})

# (teacher, profile) -> source
MIDLINE_SOURCE_MAP: Dict[Tuple[str, str], str] = {
    ("gemini", "long"): "gemini_midline_long",
    ("gemini", "short"): "gemini_midline_short",
    ("deepseek", "long"): "deepseek_midline_long",
    ("deepseek", "short"): "deepseek_midline_short",
}

# source -> system_settings kill switch key
MIDLINE_KILL_SWITCH: Dict[str, str] = {
    "gemini_midline_long": "gemini_midline_long_enabled",
    "gemini_midline_short": "gemini_midline_short_enabled",
    "deepseek_midline_long": "deepseek_midline_long_enabled",
    "deepseek_midline_short": "deepseek_midline_short_enabled",
}


def is_midline_source(source: str) -> bool:
    return (source or "").strip().lower() in MIDLINE_SOURCES


def source_for(teacher: str, profile: str) -> str:
    key = (teacher.strip().lower(), profile.strip().lower())
    if key not in MIDLINE_SOURCE_MAP:
        raise ValueError(f"unknown midline teacher/profile: {teacher}/{profile}")
    return MIDLINE_SOURCE_MAP[key]


def profile_side(profile: str) -> str:
    p = profile.strip().lower()
    if p == "long":
        return "LONG"
    if p == "short":
        return "SHORT"
    raise ValueError(f"unknown profile: {profile}")
