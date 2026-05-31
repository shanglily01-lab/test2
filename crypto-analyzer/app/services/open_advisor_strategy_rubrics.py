"""开仓顾问 — 按 source 映射策略审核标准 + Big4/方向闸门文案."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class OpenAdvisorStrategyProfile:
    key: str
    title_zh: str
    expected_side: Optional[str]  # LONG | SHORT | None=双向
    rubric: str


def _match_source(source: str) -> str:
    s = (source or "").strip().lower()
    if not s:
        return "generic"
    if "reversal" in s:
        return "reversal"
    if "pullback" in s:
        return "pullback"
    if "rebound" in s:
        return "rebound"
    if "chase" in s:
        return "chase"
    if "dump" in s:
        return "dump"
    if "predict" in s or s == "predictor":
        return "predict"
    if "explore" in s:
        return "explore"
    if s.startswith("s1_") or "early_long" in s:
        return "s1"
    if s.startswith("s5_") or "large_oversold" in s or "oversold" in s:
        return "s5"
    if s.startswith("s6_") or "vol_spike" in s:
        return "s6"
    if s.startswith("s9_") or "gemini" in s and "strategy" in s:
        return "s9"
    if "btc_momentum" in s or s == "btc_momentum":
        return "btc_momentum"
    if s in ("smart_trader", "smart_trader_sync") or "signal_confirm" in s or "trend_follow" in s:
        return "smart_trader"
    if "bollinger" in s or "mean_reversion" in s:
        return "mean_reversion"
    if "pullback" in s or "kline_pullback" in s:
        return "pullback"
    return "generic"


_PROFILES: dict[str, OpenAdvisorStrategyProfile] = {
    "reversal": OpenAdvisorStrategyProfile(
        key="reversal",
        title_zh="顶空底多（反转）",
        expected_side=None,
        rubric=(
            "审核要点（1h：整体用近24根判断趋势，近期用近4~6根）：\n"
            "- **SHORT**：须为真·**顶部反转**——此前阶段顶/7d 高位滞涨，多周期转弱，"
            "近4~6根有反弹或上影但**无力创新高**；禁止「涨多了」无结构做空。\n"
            "- **LONG**：须为真·**底部反转**——此前阶段底/超卖止跌，近4~6根下影/连阳确认；"
            "禁止单边下跌中继抄底。\n"
            "- catalyst 须与方向一致；仅24h涨跌幅不足以 approve。"
        ),
    ),
    "pullback": OpenAdvisorStrategyProfile(
        key="pullback",
        title_zh="回调做多",
        expected_side="LONG",
        rubric=(
            "须全部符合才 approve：\n"
            "1. **近24根1h** 整体上涨趋势（通道/高点抬高）。\n"
            "2. **近4~6根1h** 回落/回踩/阴线，且有支撑企稳（非单边下跌抄底）。\n"
            "3. 方向必须 LONG；与「追涨」「杀跌」叙事混淆则 reject。"
        ),
    ),
    "rebound": OpenAdvisorStrategyProfile(
        key="rebound",
        title_zh="反弹做空",
        expected_side="SHORT",
        rubric=(
            "须全部符合才 approve：\n"
            "1. 曾见顶后进入**下降趋势**（近24根1h）。\n"
            "2. **近4~6根1h** 有反弹，但**量能不支持**（缩量/背离须写明）。\n"
            "3. 处于**相对高点/阻力**；方向 SHORT。禁止强突破新高追空。"
        ),
    ),
    "chase": OpenAdvisorStrategyProfile(
        key="chase",
        title_zh="追涨做多",
        expected_side="LONG",
        rubric=(
            "须全部符合才 approve：\n"
            "1. **近24根1h** 持续上涨，**近4~6根** 仍延续、无明显深度回踩。\n"
            "2. **不要求放量**；量能平平但结构未破可 approve。\n"
            "3. 方向 LONG；若 catalyst 主叙事是「大幅回踩支撑」则 reject（应属回调做多）。"
        ),
    ),
    "dump": OpenAdvisorStrategyProfile(
        key="dump",
        title_zh="杀跌做空",
        expected_side="SHORT",
        rubric=(
            "须全部符合才 approve：\n"
            "1. **近24根1h** 下跌趋势未扭转。\n"
            "2. 反弹**无量价支持**或反弹失败（近4~6根描述清楚）。\n"
            "3. 方向 SHORT；非底部博反弹。"
        ),
    ),
    "explore": OpenAdvisorStrategyProfile(
        key="explore",
        title_zh="Gemini/DeepSeek 探索（红/黑天鹅）",
        expected_side=None,
        rubric=(
            "探索仓：须有多周期 K 线 + 事件/结构 catalyst（非纯24h涨跌）。\n"
            "- LONG：下跌后反转/利好结构；SHORT：上涨后衰竭/利空结构。\n"
            "- 结构不清晰、与 catalyst 矛盾、极端追涨杀跌 → reject。"
        ),
    ),
    "predict": OpenAdvisorStrategyProfile(
        key="predict",
        title_zh="AI 预测（4h 方向）",
        expected_side=None,
        rubric=(
            "预测单：catalyst 须含 1h/15m 方向依据 + 置信逻辑；\n"
            "与 Big4 严重冲突且个股 catalyst 不足 → reject；"
            "非极端行情下允许与 Big4 反向但须在 catalyst 中自洽。"
        ),
    ),
    "s1": OpenAdvisorStrategyProfile(
        key="s1",
        title_zh="S1 早期做多",
        expected_side="LONG",
        rubric=(
            "仅 LONG。须 RSI+MA20 类早期多头结构；"
            "非下跌趋势抄底、非顶部追高。"
        ),
    ),
    "s5": OpenAdvisorStrategyProfile(
        key="s5",
        title_zh="S5 大币超卖反弹",
        expected_side="LONG",
        rubric=(
            "仅 LONG；大币(BTC/ETH等)超卖反弹；"
            "须有超卖+止跌证据，非下跌中继。"
        ),
    ),
    "s6": OpenAdvisorStrategyProfile(
        key="s6",
        title_zh="S6 小币量能异动",
        expected_side="LONG",
        rubric=(
            "仅 LONG；量能先行+价格跟进；"
            "无量拉升或叙事仅「涨得多」→ reject。"
        ),
    ),
    "s9": OpenAdvisorStrategyProfile(
        key="s9",
        title_zh="S9 Gemini 抄底反转",
        expected_side="LONG",
        rubric=(
            "仅 LONG；AI 抄底反转须接近底部结构（超卖/下影/止跌）；"
            "禁止趋势空头中的逆势摸底。"
        ),
    ),
    "btc_momentum": OpenAdvisorStrategyProfile(
        key="btc_momentum",
        title_zh="BTC 动量",
        expected_side=None,
        rubric=(
            "BTC 动量：方向须与 BTC 短周期动量一致；"
            "逆势且无独立强 catalyst → reject。"
        ),
    ),
    "smart_trader": OpenAdvisorStrategyProfile(
        key="smart_trader",
        title_zh="主策略/smart_trader",
        expected_side=None,
        rubric=(
            "主策略：entry_reason/catalyst 须与 proposed side 一致；"
            "震荡/趋势模式与 K 线匹配；信号含糊 → reject。"
        ),
    ),
    "mean_reversion": OpenAdvisorStrategyProfile(
        key="mean_reversion",
        title_zh="布林均值回归",
        expected_side=None,
        rubric=(
            "震荡市均值回归：须在震荡区间边缘；"
            "强趋势突破追单 → reject。"
        ),
    ),
    "generic": OpenAdvisorStrategyProfile(
        key="generic",
        title_zh="其他策略",
        expected_side=None,
        rubric=(
            "通用：catalyst/entry_reason 与方向、K 线一致；"
            "结构不清晰或与 Big4/方向闸门冲突 → reject。"
        ),
    ),
}


def resolve_strategy_profile(source: str) -> OpenAdvisorStrategyProfile:
    key = _match_source(source)
    return _PROFILES.get(key, _PROFILES["generic"])


def check_direction_gates(
    side: str,
    allow_long: bool,
    allow_short: bool,
) -> Tuple[bool, str]:
    s = (side or "").upper()
    if s == "LONG" and not allow_long:
        return False, "系统设置禁止做多(allow_long=0)"
    if s == "SHORT" and not allow_short:
        return False, "系统设置禁止做空(allow_short=0)"
    return True, ""


def check_expected_side(profile: OpenAdvisorStrategyProfile, side: str) -> Tuple[bool, str]:
    if not profile.expected_side:
        return True, ""
    s = (side or "").upper()
    if s != profile.expected_side:
        return (
            False,
            f"策略「{profile.title_zh}」仅允许{profile.expected_side}，实际为{s}",
        )
    return True, ""


def build_big4_subjective_block(
    big4_signal: str,
    big4_strength: float,
    allow_long: bool,
    allow_short: bool,
    side: str,
    btc_6h: float = 0.0,
    eth_6h: float = 0.0,
) -> str:
    """开仓顾问：结合用户方向开关 + Big4 的主观审核指引（不改 system_settings）。"""
    s = (side or "").upper()
    lines = [
        "## Big4 与方向闸门（主观审核，你必须遵守）",
        f"- 用户开关: allow_long={'是' if allow_long else '**否**'} | "
        f"allow_short={'是' if allow_short else '**否**'}",
        f"- Big4 信号: {big4_signal} (强度 {big4_strength:.0f}) | BTC 6h {btc_6h:+.2f}% | ETH 6h {eth_6h:+.2f}%",
    ]
    if s == "LONG" and not allow_long:
        lines.append("- **硬规则**: 禁止做多 → 必须 reject。")
    if s == "SHORT" and not allow_short:
        lines.append("- **硬规则**: 禁止做空 → 必须 reject。")
    if allow_long and allow_short:
        lines.append(
            "- 多空均允许时：结合 Big4 **主观**判断逆势风险；"
            "STRONG_BEARISH 下做多、STRONG_BULLISH 下做空须 catalyst 极强否则 reject。"
        )
    elif allow_long and not allow_short:
        lines.append(
            "- 当前**仅允许多头**：SHORT 一律 reject；"
            "Big4 偏空时做多须结构极强（反转底/回调支撑明确）。"
        )
    elif allow_short and not allow_long:
        lines.append(
            "- 当前**仅允许空头**：LONG 一律 reject；"
            "Big4 偏多时做空须结构极强（见顶/反弹衰竭明确）。"
        )
    else:
        lines.append("- 多空均关闭：一律 reject。")
    lines.append(
        "- Big4 不能替代 K 线；但与用户方向开关冲突且无充分个股结构 → reject。"
    )
    return "\n".join(lines)


_KLINE_1H_READING = """
## 1h K 线读法
- **禁止**仅凭最近 **1 根** 1h K 线下结论。
- **整体趋势**：近 **24 根 1h**（约24小时）。
- **近期结构**：近 **4~6 根 1h**（回调/反弹/连阳连阴）。
"""
