"""开仓顾问 — 按 source 映射策略审核标准 + Big4/方向闸门文案."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from app.services.ai_tactical_explore_prompts import (
    CHASE_MIN_ROOM_BELOW_7D_HIGH_PCT,
    CHASE_RSI_MAX,
    PULLBACK_RSI_MAX,
)


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
            "须全部符合才 approve（与代码门槛一致）：\n"
            "1. **近24根1h** 整体上涨趋势（通道/高点抬高）。\n"
            "2. **近4~6根1h** 回落/回踩/阴线，且有支撑企稳（非单边下跌抄底）。\n"
            "3. **RSI 1h ≤68**；若 RSI>68 仅浅调 → reject（应归追涨或 skip）。\n"
            "4. 距 7d 高点过近（below_7d_high_pct > -2）且无深回踩 → reject。\n"
            "5. 方向必须 LONG；叙事主调是「连阳追高/无明显回调」→ reject（应属追涨）。"
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
            "须全部符合才 approve（与代码门槛一致）：\n"
            "1. **近24根1h** 持续上涨，**近4~6根** 仍延续、无明显深度回踩。\n"
            "2. **RSI 1h ≤68**；>68 一律 reject（超买延伸段，48h 实盘胜率极差）。\n"
            "3. **below_7d_high_pct ≤ -3**（距 7d 高至少约 3% 空间）；不足则 reject。\n"
            "4. 量能平平可 approve，但不得仅有 24h 涨幅叙事。\n"
            "5. 方向 LONG；主叙事是「回踩/支撑反弹」→ reject（应属回调做多）。\n"
            "6. 不得因 catalyst 措辞像追涨就 approve：必须核对 RSI 与 7d 距离数值。"
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
        title_zh="AI 主探索（事件/结构）",
        expected_side=None,
        rubric=(
            "【探索专属，勿用战术四策略标准】\n"
            "须有多周期 K 线 + 事件/结构 catalyst（非纯24h涨跌）。\n"
            "- LONG：下跌后反转/利好结构；SHORT：上涨后衰竭/利空结构。\n"
            "- 禁止用「回调做多/追涨/反弹空/杀跌」战术 checklist 替代本 rubric。\n"
            "- 结构不清晰、与 catalyst 矛盾、极端追涨杀跌 → reject。"
        ),
    ),
    "predict": OpenAdvisorStrategyProfile(
        key="predict",
        title_zh="AI 预测（4h 方向）",
        expected_side=None,
        rubric=(
            "【预测专属，勿用战术/探索混审】\n"
            "catalyst 须含 1h/15m 方向依据 + 置信逻辑；\n"
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
            "【通用兜底】仅审 catalyst/entry_reason 与方向、K 线是否一致；"
            "禁止套用追涨/回调/探索等其它策略的专属 checklist。"
            "结构不清晰或与 Big4/方向闸门冲突 → reject。"
        ),
    ),
}

_TACTICAL_PROFILE_KEYS = frozenset({"pullback", "rebound", "chase", "dump"})
_UPSTREAM_GATED_OPEN_PROFILES = _TACTICAL_PROFILE_KEYS | {"reversal"}


def should_skip_llm_for_tactical_open(
    profile: OpenAdvisorStrategyProfile,
    source: str,
    *,
    tactical_llm_enabled: bool = True,
) -> bool:
    """战术/反转上游已过 catalyst 门槛时，可跳过开仓顾问 LLM 复审."""
    if profile.key not in _UPSTREAM_GATED_OPEN_PROFILES:
        return False
    return not tactical_llm_enabled


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
        return False, "System disallows LONG (allow_long=0)"
    if s == "SHORT" and not allow_short:
        return False, "System disallows SHORT (allow_short=0)"
    return True, ""


def check_expected_side(profile: OpenAdvisorStrategyProfile, side: str) -> Tuple[bool, str]:
    if not profile.expected_side:
        return True, ""
    s = (side or "").upper()
    if s != profile.expected_side:
        title = _GPT_PROFILE_TITLE_EN.get(profile.key, profile.title_zh)
        return (
            False,
            f"Strategy [{title}] allows {profile.expected_side} only, got {s}",
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


def build_strategy_review_steps(profile: OpenAdvisorStrategyProfile) -> str:
    """按策略类型生成审核步骤（非全策略共用同一套条文）."""
    key = profile.key
    title = profile.title_zh
    lines = [
        "## 审核步骤（仅适用于本策略）",
        f"- 本笔仅按 **「{title}」**（profile=`{key}`）审核；"
        "**禁止**用其它策略（回调/追涨/探索/预测/S1 等）的标准替代下文 rubric。",
        "1. 方向闸门 + Big4（上节）— 冲突则 reject。",
        f"2. **仅执行上文「{title}」专属 rubric** — 与 K 线表、下方量化指标交叉验证。",
    ]
    if key in _TACTICAL_PROFILE_KEYS:
        lines.append(
            "3. **战术互斥**（仅四战术）：形态更符合其它战术名 → reject，即使 catalyst 措辞漂亮。"
        )
        lines.append(
            "4. catalyst 须与 RSI、below_7d_high_pct 一致；仅24h涨跌 / 单根1h → reject。"
        )
        lines.append("5. 完全符合**本战术**才 approve；存疑 reject。")
    elif key == "reversal":
        lines.append(
            "3. SHORT=真顶部反转、LONG=真底部反转；禁止「涨多了/跌多了」无结构。"
        )
        lines.append("4. 勿按回调/追涨/杀跌战术 checklist 审核反转单。")
        lines.append("5. 完全符合反转定义才 approve。")
    elif key == "explore":
        lines.append("3. 审事件/结构 catalyst，**勿**用四战术互斥 checklist。")
        lines.append("4. 红/黑天鹅逻辑与方向自洽才 approve。")
    elif key == "predict":
        lines.append("3. 审 4h 预测逻辑与多周期依据，**勿**用战术或探索 checklist。")
        lines.append("4. catalyst 与 proposed side 自洽才 approve。")
    elif key in ("s1", "s5", "s6", "s9"):
        lines.append(f"3. 仅审 **{title}** 定义（RSI/量能/超卖等），勿混用 AI 战术标准。")
        lines.append("4. 不符合该多策略定义 → reject。")
    else:
        lines.append("3. entry_reason/catalyst 与方向、K 线一致；含糊或与闸门冲突 → reject。")
        lines.append("4. 勿套用未列出的其它策略专属标准。")
    return "\n".join(lines)


def build_tech_metrics_block(profile: OpenAdvisorStrategyProfile, ctx: dict) -> str:
    """战术/探索类顾问可见的量化字段（便于按策略核对，非通用模糊描述）."""
    if profile.key not in _TACTICAL_PROFILE_KEYS | {"reversal", "explore", "predict"}:
        return ""
    rsi = ctx.get("rsi_14_1h")
    b7h = ctx.get("below_7d_high_pct")
    a7l = ctx.get("above_7d_low_pct")
    rsi_s = f"{rsi:.1f}" if rsi is not None else "N/A"
    b7h_s = f"{b7h:.2f}%" if b7h is not None else "N/A"
    a7l_s = f"{a7l:.2f}%" if a7l is not None else "N/A"
    extra = ""
    if profile.key == "chase":
        extra = (
            f"\n- 追涨硬线: RSI≤{CHASE_RSI_MAX}, below_7d_high≤-{CHASE_MIN_ROOM_BELOW_7D_HIGH_PCT:.0f}%"
        )
    elif profile.key == "pullback":
        extra = f"\n- 回调硬线: RSI≤{PULLBACK_RSI_MAX}, 深回踩时 below_7d_high 不宜 >-2%"
    return (
        "## 量化指标（须与 rubric 交叉验证）\n"
        f"- RSI(1h): {rsi_s}\n"
        f"- below_7d_high_pct: {b7h_s}\n"
        f"- above_7d_low_pct: {a7l_s}"
        f"{extra}"
    )


def precheck_open_advisor(
    profile: OpenAdvisorStrategyProfile,
    side: str,
    ctx: dict,
) -> Tuple[bool, str]:
    """代码层策略专属预检（LLM 之前），避免所有单用同一套模糊标准."""
    s = (side or "").upper()
    rsi = ctx.get("rsi_14_1h")
    b7h = ctx.get("below_7d_high_pct")

    if profile.key == "chase" and s == "LONG":
        if rsi is not None and float(rsi) > CHASE_RSI_MAX:
            return False, (
                f"[chase] RSI={rsi:.0f}>{CHASE_RSI_MAX}, precheck reject"
            )
        if b7h is not None and float(b7h) > -CHASE_MIN_ROOM_BELOW_7D_HIGH_PCT:
            return False, (
                f"[chase] below_7d_high={b7h:.1f}% insufficient room, precheck reject"
            )

    if profile.key == "pullback" and s == "LONG":
        if rsi is not None and float(rsi) > PULLBACK_RSI_MAX:
            return False, (
                f"[pullback] RSI={rsi:.0f}>{PULLBACK_RSI_MAX}, precheck reject"
            )
        if b7h is not None and float(b7h) > -2.0:
            return False, (
                f"[pullback] below_7d_high={b7h:.1f}% too close, no real dip, precheck reject"
            )

    if profile.key == "dump" and s == "SHORT":
        if rsi is not None and float(rsi) > 55:
            return False, f"[dump] RSI={rsi:.0f}>55, looks like rebound short not breakdown, precheck reject"

    if profile.key == "rebound" and s == "SHORT":
        if rsi is not None and float(rsi) < 40:
            return False, f"[rebound] RSI={rsi:.0f}<40 too low for rebound short, precheck reject"

    return True, ""


def build_open_advisor_prompt(
    *,
    profile: OpenAdvisorStrategyProfile,
    symbol: str,
    side: str,
    price: float,
    source: str,
    catalyst: str,
    leverage: int,
    sl_pct: Optional[float],
    tp_pct: Optional[float],
    hold_hours: Optional[float],
    ctx: dict,
    format_kline_table: Callable[[list], str],
) -> str:
    """English open-advisor prompt (Gemini / DeepSeek / GPT production)."""
    return build_gpt_open_advisor_prompt(
        profile=profile,
        symbol=symbol,
        side=side,
        price=price,
        source=source,
        catalyst=catalyst,
        leverage=leverage,
        sl_pct=sl_pct,
        tp_pct=tp_pct,
        hold_hours=hold_hours,
        ctx=ctx,
        format_kline_table=format_kline_table,
    )


_GPT_PROFILE_TITLE_EN: dict[str, str] = {
    "reversal": "Top-short / Bottom-long (reversal)",
    "pullback": "Pullback long",
    "rebound": "Rebound short",
    "chase": "Momentum chase long",
    "dump": "Breakdown short",
    "explore": "AI main explore (event/structure)",
    "predict": "AI predict (4h direction)",
    "s1": "S1 early long",
    "s5": "S5 large-cap oversold",
    "s6": "S6 small-cap volume spike",
    "s9": "S9 Gemini bottom reversal",
    "btc_momentum": "BTC momentum",
    "smart_trader": "Main strategy / smart_trader",
    "mean_reversion": "Bollinger mean reversion",
    "generic": "Other strategy",
}

_GPT_RUBRIC_EN: dict[str, str] = {
    "reversal": (
        "1h: use last 24 bars for trend, last 4-6 for local structure.\n"
        "- SHORT: true top reversal — prior stage high / 7d stall, multi-TF weakness; "
        "recent bars show failed breakout / upper wicks, not 'up a lot' without structure.\n"
        "- LONG: true bottom reversal — oversold base, lower wicks / bullish follow-through; "
        "not bear-flag continuation.\n"
        "- Catalyst must match side; 24h % alone is insufficient."
    ),
    "pullback": (
        "ALL required (aligned with code gates):\n"
        "1. Last 24x1h: overall uptrend (higher highs/channel).\n"
        "2. Last 4-6x1h: pullback/dip with support hold (not knife-catch in downtrend).\n"
        "3. RSI 1h <= 68; if RSI>68 with shallow dip → reject (chase or skip).\n"
        "4. below_7d_high_pct > -2 without deep pullback → reject.\n"
        "5. Side must LONG; narrative is pure momentum chase without dip → reject."
    ),
    "rebound": (
        "ALL required (SHORT only — **Rebound** = short a **price** bounce inside a downtrend, NOT a bullish market):\n"
        "1. Prior top then downtrend on 24x1h.\n"
        "2. Last 4-6x1h: bounce but weak volume (state divergence in reason).\n"
        "3. Near resistance / relative high; SHORT only; no fresh breakout high short.\n"
        "4. STRONG_BEARISH / bearish Big4 is **supportive** for this SHORT — never reject because macro is bearish."
    ),
    "chase": (
        "ALL required (aligned with code gates):\n"
        "1. Last 24x1h uptrend; last 4-6 still extend without deep pullback.\n"
        "2. RSI 1h <= 68; >68 → reject (overbought extension).\n"
        "3. below_7d_high_pct <= -3; insufficient room → reject.\n"
        "4. Do not approve on pretty catalyst text alone — verify RSI and 7d distance.\n"
        "5. LONG only; narrative is dip/support bounce → reject (pullback)."
    ),
    "dump": (
        "ALL required (SHORT only):\n"
        "1. 24x1h downtrend intact.\n"
        "2. Bounce lacks volume support or fails (describe last 4-6 bars); "
        "ongoing grind-down without a clean bounce is OK if last 4-6 show lower highs / weak relief.\n"
        "3. SHORT only; not bottom-fishing bounce.\n"
        "4. STRONG_BEARISH Big4 **aligns** with dump SHORT — bearish macro is NOT a reject reason."
    ),
    "explore": (
        "Explore-only rubric (not tactical four-way checklist).\n"
        "Multi-TF K-lines + event/structure catalyst (not 24h % only).\n"
        "Unclear structure or catalyst/side mismatch → reject."
    ),
    "predict": (
        "Predict-only rubric.\n"
        "Catalyst needs 1h/15m direction logic; severe Big4 conflict without coin-specific case → reject."
    ),
    "s1": "LONG only; early bull RSI+MA20 structure; no downtrend knife-catch.",
    "s5": "LONG only; large-cap oversold bounce with evidence; not mid-bear continuation.",
    "s6": "LONG only; volume leads price; hollow pump narrative → reject.",
    "s9": "LONG only; bottom reversal structure; no counter-trend catch in strong bear.",
    "btc_momentum": "Side must align with BTC short-term momentum unless catalyst is exceptional.",
    "smart_trader": "entry_reason/catalyst matches side and K-line regime; vague signal → reject.",
    "mean_reversion": "Range-edge mean reversion; strong trend breakout chase → reject.",
    "generic": (
        "Generic: catalyst/entry_reason consistent with side and K-lines; "
        "do not apply another strategy's checklist."
    ),
}


def _gpt_rubric_en(profile: OpenAdvisorStrategyProfile) -> str:
    return _GPT_RUBRIC_EN.get(profile.key, _GPT_RUBRIC_EN["generic"])


def build_gpt_big4_subjective_block(
    big4_signal: str,
    big4_strength: float,
    allow_long: bool,
    allow_short: bool,
    side: str,
    btc_6h: float = 0.0,
    eth_6h: float = 0.0,
    profile_key: str = "",
) -> str:
    s = (side or "").upper()
    key = (profile_key or "").strip().lower()
    upstream_gated = key in _TACTICAL_PROFILE_KEYS | {"reversal", "explore", "predict"}
    lines = [
        "## Big4 & direction gates (mandatory)",
        f"- User switches: allow_long={'yes' if allow_long else '**no**'} | "
        f"allow_short={'yes' if allow_short else '**no**'}",
        f"- Big4: {big4_signal} (strength {big4_strength:.0f}) | BTC 6h {btc_6h:+.2f}% | ETH 6h {eth_6h:+.2f}%",
    ]
    if s == "LONG" and not allow_long:
        lines.append("- **Hard rule**: longs disabled → reject.")
    if s == "SHORT" and not allow_short:
        lines.append("- **Hard rule**: shorts disabled → reject.")
    if allow_long and allow_short:
        if upstream_gated:
            lines.append(
                "- Both sides allowed. Upstream strategy already passed catalyst/code gates; "
                "Big4 is **macro context only**. **Do not reject solely** because Big4 is "
                "STRONG_BEARISH/STRONG_BULLISH if the strategy rubric below is satisfied."
            )
        else:
            lines.append(
                "- Both sides allowed: weigh Big4 vs proposed side; "
                "STRONG_BEARISH long / STRONG_BULLISH short needs exceptional structure else reject."
            )
    elif allow_long and not allow_short:
        lines.append(
            "- **Long-only mode**: any SHORT → reject; "
            "bearish Big4 long needs very strong reversal/pullback proof."
        )
    elif allow_short and not allow_long:
        lines.append(
            "- **Short-only mode**: any LONG → reject; "
            "bullish Big4 short needs clear top / failed rally."
        )
    else:
        lines.append("- Both long and short disabled → reject.")
    sig = (big4_signal or "").upper()
    if s == "SHORT" and "BEAR" in sig:
        lines.append(
            "- **Macro aligned**: SHORT + bearish/STRONG_BEARISH Big4 is supportive — "
            "do NOT treat bearish Big4 as a conflict for SHORT entries."
        )
    elif s == "LONG" and "BULL" in sig:
        lines.append(
            "- **Macro aligned**: LONG + bullish/STRONG_BULLISH Big4 is supportive — "
            "do NOT treat bullish Big4 as a conflict for LONG entries."
        )
    lines.append(
        "- Big4 cannot replace K-lines; reject only when rubric/K-lines fail, not macro label alone."
    )
    lines.append(
        "- Upstream passed catalyst/code gates; do not batch-reject on macro alone "
        "if the strategy rubric below is satisfied."
    )
    return "\n".join(lines)


def build_gpt_strategy_review_steps(profile: OpenAdvisorStrategyProfile) -> str:
    title = _GPT_PROFILE_TITLE_EN.get(profile.key, profile.title_zh)
    key = profile.key
    if key in _TACTICAL_PROFILE_KEYS:
        step1 = (
            "1. **Hard**: allow_long/allow_short switches. **Soft**: Big4 — never reject on macro alone; "
            "need tactical rubric failure."
        )
    elif key == "reversal":
        step1 = (
            "1. **Hard**: direction switches. Big4 vs side needs **coin-level** reversal proof in rubric, "
            "not macro-only reject."
        )
    elif key in ("explore", "predict"):
        step1 = (
            "1. **Hard**: direction switches. Big4 severe conflict **without** matching catalyst/K-lines → reject."
        )
    else:
        step1 = "1. Direction gates + Big4 — conflict → reject."
    lines = [
        "## Review steps (this strategy only)",
        f"- Audit **only** profile `{key}` ({title}); do not substitute another strategy's checklist.",
        step1,
        f"2. Apply the **{title}** rubric below against K-line tables and metrics.",
    ]
    if key in _TACTICAL_PROFILE_KEYS:
        lines.append(
            "3. **Tactical mutual exclusion**: pattern fits another tactical profile → reject."
        )
        lines.append(
            "4. Catalyst must match RSI / below_7d_high_pct; single 1h bar or 24h % only → reject."
        )
        lines.append("5. Approve only on full rubric fit; doubt → reject.")
    elif key == "reversal":
        lines.append("3. SHORT = true top reversal; LONG = true bottom reversal.")
        lines.append("4. Do not use tactical four-way checklist for reversal.")
    elif key == "explore":
        lines.append("3. Event/structure catalyst; no tactical four-way checklist.")
    elif key == "predict":
        lines.append("3. 4h predict logic; no tactical/explore checklist mix.")
    else:
        lines.append("3. entry_reason/catalyst vs side and K-lines; vague → reject.")
    return "\n".join(lines)


def build_gpt_tech_metrics_block(profile: OpenAdvisorStrategyProfile, ctx: dict) -> str:
    if profile.key not in _TACTICAL_PROFILE_KEYS | {"reversal", "explore", "predict"}:
        return ""
    rsi = ctx.get("rsi_14_1h")
    b7h = ctx.get("below_7d_high_pct")
    a7l = ctx.get("above_7d_low_pct")
    rsi_s = f"{rsi:.1f}" if rsi is not None else "N/A"
    b7h_s = f"{b7h:.2f}%" if b7h is not None else "N/A"
    a7l_s = f"{a7l:.2f}%" if a7l is not None else "N/A"
    extra = ""
    if profile.key == "chase":
        extra = (
            f"\n- Chase hard lines: RSI<={CHASE_RSI_MAX}, "
            f"below_7d_high<=-{CHASE_MIN_ROOM_BELOW_7D_HIGH_PCT:.0f}%"
        )
    elif profile.key == "pullback":
        extra = f"\n- Pullback hard lines: RSI<={PULLBACK_RSI_MAX}, deep dip vs 7d high"
    return (
        "## Quant metrics (cross-check rubric)\n"
        f"- RSI(1h): {rsi_s}\n"
        f"- below_7d_high_pct: {b7h_s}\n"
        f"- above_7d_low_pct: {a7l_s}"
        f"{extra}"
    )


_KLINE_1H_READING_EN = """
## 1h K-line reading
- **Never** decide from the latest **single** 1h candle.
- **Trend**: last **24** 1h bars (~24h).
- **Local structure**: last **4-6** 1h bars (pullback/bounce/streak).
"""


def build_gpt_open_advisor_prompt(
    *,
    profile: OpenAdvisorStrategyProfile,
    symbol: str,
    side: str,
    price: float,
    source: str,
    catalyst: str,
    leverage: int,
    sl_pct: Optional[float],
    tp_pct: Optional[float],
    hold_hours: Optional[float],
    ctx: dict,
    format_kline_table: Callable[[list], str],
) -> str:
    """English open-advisor prompt (all teachers)."""
    title_en = _GPT_PROFILE_TITLE_EN.get(profile.key, profile.title_zh)
    rubric_en = _gpt_rubric_en(profile)
    big4_block = build_gpt_big4_subjective_block(
        ctx.get("big4_signal", "NEUTRAL"),
        float(ctx.get("big4_strength") or 0),
        bool(ctx.get("allow_long", True)),
        bool(ctx.get("allow_short", True)),
        side,
        float(ctx.get("btc_6h_change") or 0),
        float(ctx.get("eth_6h_change") or 0),
        profile_key=profile.key,
    )
    klines_15m = format_kline_table(ctx.get("klines_15m", []))
    klines_1h = format_kline_table(ctx.get("klines_1h", []))
    narr_1h = (ctx.get("narrative_1h") or "").strip() or "(no cache; use tables)"
    narr_15m = (ctx.get("narrative_15m") or "").strip() or "(none)"
    tech_block = build_gpt_tech_metrics_block(profile, ctx)
    review_steps = build_gpt_strategy_review_steps(profile)
    sl_s = f"{sl_pct}%" if sl_pct is not None else "default"
    tp_s = f"{tp_pct}%" if tp_pct is not None else "default"
    hold_s = f"{hold_hours}h" if hold_hours is not None else "strategy default"
    return f"""You are a senior crypto futures risk reviewer. The system asks you to approve or reject a **paper** open **before** execution.

## Scope
- **Only** the rubric for: **{title_en}** (profile=`{profile.key}`, source=`{source}`).
- Do **not** apply another strategy's rules (e.g. do not judge a pullback trade as chase).

## Strategy rubric ({title_en})
{rubric_en}

{_KLINE_1H_READING_EN}

{tech_block}

{big4_block}

## Proposed open
  Symbol:     {symbol}
  Direction:  {side}
  Entry:      {price}
  Leverage:   {leverage}x
  SL/TP:      {sl_s} / {tp_s}
  Plan hold:  {hold_s}
  Explore catalyst (from prior LLM round): {(catalyst or '')[:500]}

## Market context
  candidate_pool 1h narrative:
{narr_1h}
  candidate_pool 15m narrative:
{narr_15m}

## Last 24x1h K-lines (oldest → newest)
{klines_1h}

## Last ~4h 15m K-lines
{klines_15m}

{review_steps}

Output ONLY JSON:
{{
  "decision": "approve" | "reject",
  "reason": "<=120 English words; cite strategy [{title_en}] and key pass/fail point>"
}}
"""
