"""中线做多/做空策略 v2 — 常量与 source 命名.

权威需求: docs/REQUIREMENTS_LOGIC_ZH.md §7.2
"""
from __future__ import annotations

from typing import Dict, Tuple

# 持仓 8 小时
MIDLINE_HOLD_HOURS = 8
MIDLINE_HOLD_MINUTES = MIDLINE_HOLD_HOURS * 60
# 向后兼容旧字段名（API 勿再当「天」用）
MIDLINE_HOLD_DAYS = MIDLINE_HOLD_HOURS / 24.0

MIDLINE_LEVERAGE = 5
MIDLINE_MARGIN_USD = 500.0

# 限价偏移默认：做多 = 市价 −1%，做空 = 市价 +1%
DEFAULT_MIDLINE_LIMIT_LONG_OFFSET_PCT = 1.0
DEFAULT_MIDLINE_LIMIT_SHORT_OFFSET_PCT = 1.0
DEFAULT_MIDLINE_INTERVAL_HOURS = 4

MIDLINE_LIMIT_LONG_OFFSET_PCT = DEFAULT_MIDLINE_LIMIT_LONG_OFFSET_PCT
MIDLINE_LIMIT_SHORT_OFFSET_PCT = DEFAULT_MIDLINE_LIMIT_SHORT_OFFSET_PCT
MIDLINE_LIMIT_OFFSET_PCT = MIDLINE_LIMIT_LONG_OFFSET_PCT
MIDLINE_INTERVAL_HOURS = DEFAULT_MIDLINE_INTERVAL_HOURS
MIDLINE_LIMIT_TIMEOUT_MINUTES = DEFAULT_MIDLINE_INTERVAL_HOURS * 60

MIDLINE_INTERVAL_HOURS_MIN = 1
MIDLINE_INTERVAL_HOURS_MAX = 48

MIDLINE_SETTING_INTERVAL_HOURS = "midline_interval_hours"
MIDLINE_SETTING_LIMIT_LONG_OFFSET_PCT = "midline_limit_long_offset_pct"
MIDLINE_SETTING_LIMIT_SHORT_OFFSET_PCT = "midline_limit_short_offset_pct"

MIDLINE_SL_PCT = 6.0
MIDLINE_TP_PCT = 3.0
MIDLINE_ACCOUNT_ID = 2

# v2 独立量化 source（不再挂教师名）
MIDLINE_SOURCES = frozenset({
    "midline_long",
    "midline_short",
})

# 旧四路：仍识别为中线（SmartExit 排除 / 硬 SLTP / 存量仓），但不再调度
LEGACY_MIDLINE_SOURCES = frozenset({
    "gemini_midline_long",
    "gemini_midline_short",
    "deepseek_midline_long",
    "deepseek_midline_short",
})

ALL_MIDLINE_SOURCES = MIDLINE_SOURCES | LEGACY_MIDLINE_SOURCES

# profile -> source
MIDLINE_PROFILE_SOURCE: Dict[str, str] = {
    "long": "midline_long",
    "short": "midline_short",
}

# 兼容旧 (teacher, profile) 调用 → 映射到 v2 source
MIDLINE_SOURCE_MAP: Dict[Tuple[str, str], str] = {
    ("gemini", "long"): "midline_long",
    ("gemini", "short"): "midline_short",
    ("deepseek", "long"): "midline_long",
    ("deepseek", "short"): "midline_short",
    ("", "long"): "midline_long",
    ("", "short"): "midline_short",
}

MIDLINE_KILL_SWITCH: Dict[str, str] = {
    "midline_long": "midline_long_enabled",
    "midline_short": "midline_short_enabled",
}


def is_midline_source(source: str) -> bool:
    """含 v2 + 旧四路（存量仓仍按中线规则管理）."""
    return (source or "").strip().lower() in ALL_MIDLINE_SOURCES


def is_active_midline_source(source: str) -> bool:
    """仅 v2 可新开仓的 source."""
    return (source or "").strip().lower() in MIDLINE_SOURCES


def _clamp_midline_interval_hours(hours: float) -> int:
    try:
        h = int(float(hours))
    except (TypeError, ValueError):
        h = DEFAULT_MIDLINE_INTERVAL_HOURS
    return max(MIDLINE_INTERVAL_HOURS_MIN, min(MIDLINE_INTERVAL_HOURS_MAX, h))


def _clamp_midline_limit_offset_pct(pct: float) -> float:
    from app.services.paper_limit_entry import _clamp_offset_pct
    try:
        return _clamp_offset_pct(float(pct), strategy_override=True)
    except (TypeError, ValueError):
        return DEFAULT_MIDLINE_LIMIT_LONG_OFFSET_PCT


def get_midline_interval_hours() -> int:
    """中线扫描执行周期（小时），读 system_settings.midline_interval_hours。"""
    from app.services.system_settings_loader import get_setting
    raw = get_setting(MIDLINE_SETTING_INTERVAL_HOURS, str(DEFAULT_MIDLINE_INTERVAL_HOURS))
    return _clamp_midline_interval_hours(raw)


def get_midline_limit_long_offset_pct() -> float:
    from app.services.system_settings_loader import get_setting
    raw = get_setting(
        MIDLINE_SETTING_LIMIT_LONG_OFFSET_PCT,
        str(DEFAULT_MIDLINE_LIMIT_LONG_OFFSET_PCT),
    )
    return _clamp_midline_limit_offset_pct(raw)


def get_midline_limit_short_offset_pct() -> float:
    from app.services.system_settings_loader import get_setting
    raw = get_setting(
        MIDLINE_SETTING_LIMIT_SHORT_OFFSET_PCT,
        str(DEFAULT_MIDLINE_LIMIT_SHORT_OFFSET_PCT),
    )
    return _clamp_midline_limit_offset_pct(raw)


def get_midline_limit_timeout_minutes() -> int:
    return get_midline_interval_hours() * 60


def get_midline_runtime_params() -> dict:
    long_pct = get_midline_limit_long_offset_pct()
    short_pct = get_midline_limit_short_offset_pct()
    interval = get_midline_interval_hours()
    return {
        "interval_hours": interval,
        "limit_long_offset_pct": long_pct,
        "limit_short_offset_pct": short_pct,
        "limit_offset_pct": long_pct,
        "limit_timeout_minutes": interval * 60,
        "hold_hours": MIDLINE_HOLD_HOURS,
        "sl_pct": MIDLINE_SL_PCT,
        "tp_pct": MIDLINE_TP_PCT,
    }


def get_midline_limit_offset_pct(side: str) -> float:
    return (
        get_midline_limit_long_offset_pct()
        if (side or "").upper() == "LONG"
        else get_midline_limit_short_offset_pct()
    )


def calc_midline_limit_price(side: str, ref_price: float) -> float:
    from app.services.paper_limit_entry import calc_paper_limit_price
    return calc_paper_limit_price(
        side,
        ref_price,
        limit_offset_pct=get_midline_limit_offset_pct(side),
    )


def midline_source_sql_not_in(column: str = "source") -> str:
    """SQL 片段：排除全部中线 source（含旧四路）."""
    quoted = ", ".join(f"'{s}'" for s in sorted(ALL_MIDLINE_SOURCES))
    return f"LOWER({column}) NOT IN ({quoted})"


def source_for(teacher: str, profile: str) -> str:
    """兼容旧 teacher/profile 调用；teacher 可忽略，仅看 profile."""
    profile_l = profile.strip().lower()
    if profile_l in MIDLINE_PROFILE_SOURCE:
        return MIDLINE_PROFILE_SOURCE[profile_l]
    key = ((teacher or "").strip().lower(), profile_l)
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


def profile_for_source(source: str) -> str:
    s = (source or "").strip().lower()
    if s.endswith("_short") or s == "midline_short":
        return "short"
    return "long"
