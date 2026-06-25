"""中线做多/做空策略 — 常量与 source 命名."""
from __future__ import annotations

from typing import Dict, Tuple

MIDLINE_HOLD_DAYS = 15
MIDLINE_HOLD_MINUTES = MIDLINE_HOLD_DAYS * 24 * 60
MIDLINE_LEVERAGE = 5
MIDLINE_MARGIN_USD = 500.0
# 限价偏移默认：做多 = 市价 −3%，做空 = 市价 +3%（可通过 system_settings 调整）
DEFAULT_MIDLINE_LIMIT_LONG_OFFSET_PCT = 3.0
DEFAULT_MIDLINE_LIMIT_SHORT_OFFSET_PCT = 3.0
DEFAULT_MIDLINE_INTERVAL_HOURS = 6
# 向后兼容：模块级常量 = 默认值
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
MIDLINE_TP_PCT = 20.0
MIDLINE_MIN_SIGNAL_SCORE = 55.0
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
    """做多限价偏移百分点（市价 −N%）。"""
    from app.services.system_settings_loader import get_setting
    raw = get_setting(
        MIDLINE_SETTING_LIMIT_LONG_OFFSET_PCT,
        str(DEFAULT_MIDLINE_LIMIT_LONG_OFFSET_PCT),
    )
    return _clamp_midline_limit_offset_pct(raw)


def get_midline_limit_short_offset_pct() -> float:
    """做空限价偏移百分点（市价 +N%）。"""
    from app.services.system_settings_loader import get_setting
    raw = get_setting(
        MIDLINE_SETTING_LIMIT_SHORT_OFFSET_PCT,
        str(DEFAULT_MIDLINE_LIMIT_SHORT_OFFSET_PCT),
    )
    return _clamp_midline_limit_offset_pct(raw)


def get_midline_limit_timeout_minutes() -> int:
    """限价单超时（分钟），与执行周期一致。"""
    return get_midline_interval_hours() * 60


def get_midline_runtime_params() -> dict:
    """Web/API 展示用运行时参数。"""
    long_pct = get_midline_limit_long_offset_pct()
    short_pct = get_midline_limit_short_offset_pct()
    interval = get_midline_interval_hours()
    return {
        "interval_hours": interval,
        "limit_long_offset_pct": long_pct,
        "limit_short_offset_pct": short_pct,
        "limit_offset_pct": long_pct,
        "limit_timeout_minutes": interval * 60,
    }


def get_midline_limit_offset_pct(side: str) -> float:
    """中线限价偏移：LONG −N% / SHORT +N%（可配置）。"""
    return (
        get_midline_limit_long_offset_pct()
        if (side or "").upper() == "LONG"
        else get_midline_limit_short_offset_pct()
    )


def calc_midline_limit_price(side: str, ref_price: float) -> float:
    """按中线可配置偏移规则计算限价."""
    from app.services.paper_limit_entry import calc_paper_limit_price
    return calc_paper_limit_price(
        side,
        ref_price,
        limit_offset_pct=get_midline_limit_offset_pct(side),
    )


def midline_source_sql_not_in(column: str = "source") -> str:
    """SQL 片段：排除四路中线 source（供 SmartExit / 健康检查等）."""
    quoted = ", ".join(f"'{s}'" for s in sorted(MIDLINE_SOURCES))
    return f"LOWER({column}) NOT IN ({quoted})"


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
