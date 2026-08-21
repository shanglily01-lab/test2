"""统一 source / entry_signal_type → 展示名（交易记录、复盘、持仓）。"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

_STATIC: dict[str, str] = {
    "smart_trader": "超级大脑",
    "smart_trader_sync": "超级大脑同步",
    "brain_swing": "超级大脑",
    "brain_long": "超级大脑做多",
    "brain_short": "超级大脑做空",
    "PREDICTOR": "预测神器",
    "BTC_MOMENTUM": "BTC 动量",
    "gemini_explore": "Gemini 探索",
    "gemini_predict": "Gemini 预测",
    "deepseek_explore": "DeepSeek 探索",
    "deepseek_predict": "DeepSeek 预测",
    "binance_sync": "Binance 同步",
    "auto_signal": "自动信号",
    "signal": "手动信号",
    "spot_brain": "现货·大脑A1",
    "spot_deepseek_explore": "现货·DeepSeek探索",
    "spot_deepseek_predict": "现货·DeepSeek预测",
    "spot_dca": "现货·已停用DCA",
    "midline_long": "破位做多",
    "midline_short": "破位做空",
    "manual_watchlist": "自选手动",
    "gemini_midline_long": "破位做多",
    "gemini_midline_short": "破位做空",
    "deepseek_midline_long": "破位做多",
    "deepseek_midline_short": "破位做空",
}

PLAYBOOK_SIGNAL_CN: dict[str, str] = {
    "A1": "A1 趋势回踩做多",
    "A2": "A2 弱反抽拒绝做空",
    "B2": "B2 反抽失败跟空",
    "B3": "B3 顶部回调做空",
    "B4": "B4 暴涨回踩做多",
    "C1": "C1 放量破位跟空",
    "C2": "C2 假跌破吸筹",
    "C3": "C3 放量突破跟多",
    "C4": "C4 假突破陷阱做空",
}

_BREAKOUT_SOURCES = {
    "midline_long",
    "midline_short",
    "gemini_midline_long",
    "gemini_midline_short",
    "deepseek_midline_long",
    "deepseek_midline_short",
}

_BRAIN_SOURCES = {"brain_swing", "brain_long", "brain_short"}

_TACTICAL_TITLE: dict[str, str] = {
    "pullback": "回调做多",
    "rebound": "反弹做空",
    "chase": "追涨做多",
    "dump": "杀跌做空",
}

_STATIC_SHORT: dict[str, str] = {
    "smart_trader": "超级大脑",
    "brain_swing": "大脑",
    "PREDICTOR": "预测",
    "BTC_MOMENTUM": "BTC",
    "gemini_explore": "G探索",
    "gemini_predict": "G预测",
    "deepseek_explore": "D探索",
    "deepseek_predict": "D预测",
    "midline_long": "破位多",
    "midline_short": "破位空",
    "manual_watchlist": "自选",
}

_PB_RE = re.compile(
    r"(?:brain_|breakout_|midline_|playbook[=:_]\s*|"
    r"\"(?:name|playbook)\"\s*:\s*\")([A-D]\d)",
    re.IGNORECASE,
)

_SIGNAL_TOKEN_CN: dict[str, str] = {
    "break_support": "跌破支撑",
    "break_resistance": "突破阻力",
    "volume_expand_down": "放量下跌",
    "volume_expand_up": "放量上涨",
    "crash_spike": "急跌",
    "pump_spike": "急涨",
    "exhaustion_up": "冲高滞涨",
    "stall_at_high": "高点滞涨",
    "top_callback": "顶部第一回调",
    "long_upper_wick": "长上影",
    "volume_diverge_bear": "量价背离",
    "false_break_up": "假突破",
    "false_break_down": "假跌破",
    "impulse_up": "向上冲击",
    "impulse_down": "向下冲击",
    "h1_breakout_up": "1h向上突破",
    "h1_breakdown_down": "1h向下破位",
    "15m_higher_low": "15m抬高低点",
    "15m_lower_high": "15m降低高点",
}


def _tactical_parts(source: str) -> tuple[str, str] | None:
    for prefix, teacher in (("gemini_", "Gemini"), ("deepseek_", "DeepSeek"), ("gpt_", "GPT")):
        if source.startswith(prefix):
            return teacher, source[len(prefix):]
    return None


def get_strategy_display_name(source: str | None) -> str:
    if not source:
        return "未知"
    if source in _STATIC:
        return _STATIC[source]
    parts = _tactical_parts(source)
    if parts:
        teacher, key = parts
        title = _TACTICAL_TITLE.get(key)
        if title:
            return f"{teacher} {title}"
    return source


def get_strategy_short_name(source: str | None) -> str:
    if not source:
        return "?"
    if source in _STATIC_SHORT:
        return _STATIC_SHORT[source]
    parts = _tactical_parts(source)
    if parts:
        teacher, key = parts
        tag = {"Gemini": "G", "DeepSeek": "D", "GPT": "G"}.get(teacher, teacher[:1])
        title = _TACTICAL_TITLE.get(key)
        if title:
            return f"{tag}{title[:2]}"
    return source[:8] if len(source) > 8 else source


def _blob_text(*parts: Any) -> str:
    chunks: list[str] = []
    for p in parts:
        if p is None or p == "":
            continue
        if isinstance(p, (dict, list)):
            try:
                chunks.append(json.dumps(p, ensure_ascii=False))
            except TypeError:
                chunks.append(str(p))
        else:
            chunks.append(str(p))
    return " ".join(chunks)


def extract_playbook_code(
    entry_signal_type: str | None = None,
    entry_reason: str | None = None,
    extra: Any = None,
) -> Optional[str]:
    blob = _blob_text(entry_signal_type, entry_reason, extra)
    if not blob:
        return None
    m = _PB_RE.search(blob)
    if m:
        return m.group(1).upper()
    return None


def _family(source: str | None, entry_signal_type: str | None, entry_reason: str | None) -> str:
    src = str(source or "").strip().lower()
    sig = str(entry_signal_type or "").strip().lower()
    reason = str(entry_reason or "")
    if src in _BRAIN_SOURCES or sig.startswith("brain_") or "[BRAIN]" in reason or reason.startswith("大脑·"):
        return "brain"
    if (
        src in _BREAKOUT_SOURCES
        or sig.startswith("breakout_")
        or sig.startswith("midline_")
        or "midline_v2" in reason
        or reason.startswith("破位·")
    ):
        return "breakout"
    return "other"


def _signal_hits_cn(entry_reason: str | None) -> str:
    text = str(entry_reason or "")
    hits: list[str] = []
    for token, cn in _SIGNAL_TOKEN_CN.items():
        if token in text and cn not in hits:
            hits.append(cn)
        if len(hits) >= 3:
            break
    return "、".join(hits)


def format_entry_signal_cn(
    *,
    source: str | None = None,
    entry_signal_type: str | None = None,
    entry_reason: str | None = None,
    signal_components: Any = None,
) -> str:
    """具体开仓信号中文，供交易记录 / 复盘 / 持仓使用。"""
    extra = _blob_text(signal_components)
    pb = extract_playbook_code(entry_signal_type, entry_reason, extra)
    family = _family(source, entry_signal_type, entry_reason)
    pb_cn = PLAYBOOK_SIGNAL_CN.get(pb or "")
    hits = _signal_hits_cn(_blob_text(entry_reason, extra))
    if pb_cn:
        prefix = "大脑·" if family == "brain" else "破位·" if family == "breakout" else ""
        label = f"{prefix}{pb_cn}"
        if hits:
            return f"{label}（{hits}）"
        return label
    if family == "breakout":
        src = str(source or "").lower()
        if "short" in src:
            return "破位做空"
        if "long" in src:
            return "破位做多"
        return "破位策略"
    if family == "brain":
        return "超级大脑"
    named = get_strategy_display_name(source)
    if named and named != source:
        return named
    sig = str(entry_signal_type or "").strip()
    if sig and sig.lower() not in {"unknown", "none", source or ""}:
        return sig
    reason = str(entry_reason or "").strip()
    if reason:
        return reason[:40]
    return named or "未知"


def format_entry_signal_code(
    *,
    source: str | None = None,
    entry_signal_type: str | None = None,
    entry_reason: str | None = None,
    signal_components: Any = None,
) -> str:
    pb = extract_playbook_code(entry_signal_type, entry_reason, signal_components)
    family = _family(source, entry_signal_type, entry_reason)
    if pb and family in {"brain", "breakout"}:
        return f"{family}_{pb}"
    if pb:
        return pb
    src = str(source or "").strip()
    sig = str(entry_signal_type or "").strip()
    return sig or src or "unknown"


def build_breakout_entry_reason(
    playbook: str,
    *,
    side: str = "",
    timing_status: str = "",
    signals: Optional[list[Any]] = None,
) -> str:
    pb = str(playbook or "").strip().upper()
    pb_cn = PLAYBOOK_SIGNAL_CN.get(pb, pb or "破位")
    hits = []
    for s in signals or []:
        cn = _SIGNAL_TOKEN_CN.get(str(s))
        if cn and cn not in hits:
            hits.append(cn)
        if len(hits) >= 3:
            break
    parts = [f"破位·{pb_cn}"]
    if timing_status:
        parts.append(str(timing_status))
    if hits:
        parts.append("、".join(hits))
    if side:
        parts.append(str(side).upper())
    return " | ".join(parts)[:200]


def build_brain_entry_reason(
    playbook: str,
    *,
    evidence_summary: str = "",
    signals: Optional[list[Any]] = None,
) -> str:
    pb = str(playbook or "").strip().upper()
    pb_cn = PLAYBOOK_SIGNAL_CN.get(pb, pb or "大脑")
    hits = []
    for s in signals or []:
        cn = _SIGNAL_TOKEN_CN.get(str(s))
        if cn and cn not in hits:
            hits.append(cn)
        if len(hits) >= 3:
            break
    parts = [f"大脑·{pb_cn}"]
    if evidence_summary:
        parts.append(str(evidence_summary)[:80])
    if hits:
        parts.append("、".join(hits))
    return " | ".join(parts)[:200]
