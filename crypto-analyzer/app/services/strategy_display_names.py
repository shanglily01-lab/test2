"""统一 futures_positions.source → 展示名（复盘页策略胜率、持仓分析等）."""
from __future__ import annotations

_STATIC: dict[str, str] = {
    "s1_early_long": "S1 早期做多",
    "s5_large_oversold": "S5 大币超卖",
    "s6_vol_spike": "S6 小币量能异动",
    "s9_gemini_ai": "S9 Gemini 抄底反转",
    "smart_trader": "超级大脑",
    "smart_trader_sync": "超级大脑同步",
    "PREDICTOR": "预测神器",
    "BTC_MOMENTUM": "BTC 动量",
    "gemini_explore": "Gemini 探索",
    "gemini_predict": "Gemini 预测",
    "deepseek_explore": "DeepSeek 探索",
    "deepseek_predict": "DeepSeek 预测",
    "gemini_reversal": "Gemini 顶空底多",
    "deepseek_reversal": "DeepSeek 顶空底多",
    "binance_sync": "Binance 同步",
    "auto_signal": "自动信号",
    "signal": "手动信号",
}

_TACTICAL_TITLE: dict[str, str] = {
    "pullback": "回调做多",
    "rebound": "反弹做空",
    "chase": "追涨做多",
    "dump": "杀跌做空",
    "reversal": "顶空底多",
}

_STATIC_SHORT: dict[str, str] = {
    "s1_early_long": "S1",
    "s5_large_oversold": "S5",
    "s6_vol_spike": "S6",
    "s9_gemini_ai": "S9",
    "smart_trader": "超级大脑",
    "PREDICTOR": "预测",
    "BTC_MOMENTUM": "BTC",
    "gemini_explore": "G探索",
    "gemini_predict": "G预测",
    "deepseek_explore": "D探索",
    "deepseek_predict": "D预测",
    "gemini_reversal": "G顶空底多",
    "deepseek_reversal": "D顶空底多",
}


def _tactical_parts(source: str) -> tuple[str, str] | None:
    for prefix, teacher in (("gemini_", "Gemini"), ("deepseek_", "DeepSeek")):
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
        if key == "reversal":
            return f"{teacher} 顶空底多"
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
        tag = "G" if teacher == "Gemini" else "D"
        if key == "reversal":
            return f"{tag}顶空底多"
        title = _TACTICAL_TITLE.get(key)
        if title:
            return f"{tag}{title[:2]}"
    return source[:8] if len(source) > 8 else source
