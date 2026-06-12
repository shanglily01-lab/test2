"""Big4 宏观信号 — 探索/预测 prompt 与 global_context 共用逻辑.

Big4 仅作参考，非极端行情下要求多空独立评估，避免 LLM 整池单边。
"""
from __future__ import annotations

from typing import Optional


def market_regime_from_btc_change(btc_change_24h: Optional[float]) -> str:
    btc_abs = abs(float(btc_change_24h or 0))
    if btc_abs > 8:
        return "极端波动 (BTC 24h > 8%) — 注意止损保护"
    if btc_abs > 4:
        return "强趋势 (BTC 24h > 4%) — 顺趋势交易"
    if btc_abs > 1.5:
        return "温和趋势 (BTC 24h 1.5-4%) — 正常交易环境"
    return "低波动盘整 (BTC 24h < 1.5%) — 适合小周期震荡策略"


def big4_trading_hint(
    big4_signal: str,
    market_regime: str,
    btc_change_24h: Optional[float] = None,
) -> str:
    big4 = (big4_signal or "NEUTRAL").upper()
    regime = market_regime or market_regime_from_btc_change(btc_change_24h)
    btc_abs = abs(float(btc_change_24h or 0))
    extreme = btc_abs > 8 or "极端" in regime
    strong = big4 in ("STRONG_BEARISH", "STRONG_BULLISH")

    if extreme or strong:
        return (
            f"Big4={big4}；偏极端/强趋势：可减少逆势仓，但仍须逐币独立 catalyst，"
            "禁止整池单边；summary_zh 勿写「严格禁止多头/空头」。"
        )
    return (
        f"Big4={big4}；{regime}：非极端行情，必须多空独立评估。"
        "不得因 Big4 看跌就只给 bearish/只做空，或因看涨就只给 bullish/只做多。"
        "summary_zh 客观描述，禁止「严格禁止多头/空头」类措辞。"
    )


def enrich_global_context(ctx: dict) -> dict:
    """补全 market_regime 与 big4_trading_hint."""
    if not ctx.get("market_regime"):
        ctx["market_regime"] = market_regime_from_btc_change(ctx.get("btc_change_24h"))
    ctx["big4_trading_hint"] = big4_trading_hint(
        ctx.get("big4_signal", "NEUTRAL"),
        ctx.get("market_regime", ""),
        ctx.get("btc_change_24h"),
    )
    return ctx


def big4_conflict_risk_note(big4_signal: str, side: str) -> str:
    return f"Big4={big4_signal} 与{side}方向不一致(已评估个股信号)"


BIG4_PROMPT_BLOCK_EXPLORE = """
# Big4 使用规则 (必读 global_context 内 big4_trading_hint)
- Big4 是 BTC/ETH/BNB/SOL 综合趋势，**仅作背景**，不是开仓禁令。
- **非极端行情** (BTC 24h |涨跌| <8%、低波动/盘整): 每个 symbol **独立** 看多或看空；允许与 Big4 反向的 bullish/bearish (confidence≥0.65 须在 risk_note 写明与 Big4 冲突)。
- **仅极端或 STRONG_BEARISH/STRONG_BULLISH + 大盘同向大幅波动时**: 可**略减**逆势单比例，仍禁止整池只空或只多。
- summary_zh: 描述环境即可，**禁止**「严格禁止多头/空头」「只做空/只做多」等单边指令。
"""

BIG4_PROMPT_BLOCK_EXPLORE_EN = BIG4_PROMPT_BLOCK_EXPLORE

BIG4_PROMPT_BLOCK_PREDICT = """
# Big4 使用规则 (必读 global_context 内 big4_trading_hint)
- Big4 **仅作宏观参考**，不是「严禁做多/做空」的禁令。
- **非极端行情**: 每个 symbol 独立给出 bullish/bearish/skip；允许与 Big4 反向 (confidence≥0.65 须在 risk_note 说明冲突)。
- **禁止 Big4 单边偏见**: 不得因 Big4=BEARISH 就把多数标的标 bearish；低波动盘整时应多空均衡筛选。
- summary_zh 客观描述环境，**禁止**「严格禁止多头/空头」「只做空/只做多」。
"""

BIG4_PROMPT_BLOCK_PREDICT_EN = BIG4_PROMPT_BLOCK_PREDICT

CONFIDENCE_ROW_BIG4_OK = (
    "个股 catalyst 充分 (与 Big4 冲突须在 risk_note 说明)"
)
