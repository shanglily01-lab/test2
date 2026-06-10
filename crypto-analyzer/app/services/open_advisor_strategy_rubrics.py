"""开仓顾问 — 按 source 映射策略审核标准 + Big4/方向闸门文案."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple

CHASE_RSI_MAX = 68
CHASE_RSI_MIN = 54
DUMP_RSI_MAX = 55
PULLBACK_RSI_MAX = 68
PULLBACK_MAX_BELOW_7D_HIGH_PCT = -2.0
CHASE_MIN_ROOM_BELOW_7D_HIGH_PCT = 3.0
REBOUND_NEAR_7D_HIGH_PCT = -12.0
REBOUND_RSI_MIN = 40
REBOUND_RSI_MAX = 65


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
    "pullback": OpenAdvisorStrategyProfile(
        key="pullback",
        title_zh="回调做多",
        expected_side="LONG",
        rubric=(
            "须全部符合才 approve（与代码门槛一致）：\n"
            "1. **近24根1h** 整体上涨趋势（通道/高点抬高）。\n"
            "2. **近4~6根1h** 回落/回踩/阴线，且有支撑企稳（非单边下跌抄底）。\n"
            "3. **RSI 1h ≤68**；若 RSI>68 仅浅调 → reject（应归追涨或 skip）。\n"
            f"4. below_7d_high_pct > {PULLBACK_MAX_BELOW_7D_HIGH_PCT:.0f}%（距 7d 高过近、回踩不深）→ reject。\n"
            "5. 方向必须 LONG；叙事主调是「连阳追高/无明显回调」→ reject（应属追涨）。"
        ),
    ),
    "rebound": OpenAdvisorStrategyProfile(
        key="rebound",
        title_zh="反弹做空",
        expected_side="SHORT",
        rubric=(
            "须全部符合才 approve（与战术探索代码一致）：\n"
            "1. **近24根1h** 下降通道（不要求写「曾见顶」）。\n"
            "2. **近4~6根1h** 有反弹，且**量能不支持**（缩量/乏力/背离须写明）。\n"
            f"3. **相对高点**：阻力/前高/上影，或 below_7d_high_pct > {REBOUND_NEAR_7D_HIGH_PCT:.0f}%。\n"
            f"4. RSI 1h **{REBOUND_RSI_MIN}~{REBOUND_RSI_MAX}**；方向 SHORT。禁止强突破新高追空。"
        ),
    ),
    "chase": OpenAdvisorStrategyProfile(
        key="chase",
        title_zh="追涨做多",
        expected_side="LONG",
        rubric=(
            "须全部符合才 approve（与代码门槛一致）：\n"
            "1. **近24根1h** 持续上涨，**近4~6根** 仍延续、无明显深度回踩。\n"
            f"2. **RSI 1h {CHASE_RSI_MIN}~{CHASE_RSI_MAX}**；<{CHASE_RSI_MIN} 或 >{CHASE_RSI_MAX} → reject。\n"
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
            "须全部符合才 approve（与战术探索代码一致）：\n"
            "1. **近24根1h** 下跌趋势未扭转。\n"
            "2. 反弹**无量/乏力/不支持**或几乎无反弹（近4~6根描述清楚）。\n"
            f"3. RSI 1h ≤{DUMP_RSI_MAX}；主叙事非「缩量反弹至前高」（那是反弹做空）。\n"
            "4. 方向 SHORT；非底部博反弹。"
        ),
    ),
    "explore": OpenAdvisorStrategyProfile(
        key="explore",
        title_zh="AI 主探索（事件/结构）",
        expected_side=None,
        rubric=(
            "【探索专属，与探索 prompt 一致；勿用已移除战术标准】\n"
            "上游已通过 catalyst 技术面门槛；你做**矛盾复核**，勿用单一 RSI 否决整笔。\n"
            "- **允许趋势延续**（与探索 A 类一致）：catalyst 写清 1h 整体+近4~6根与方向同向、"
            "可有 15m 共振时，下跌趋势中 SHORT、上涨趋势中 LONG → 可 approve。\n"
            "- 亦允许：LONG=下跌后反转/利好结构；SHORT=上涨后衰竭/利空结构。\n"
            "- **禁止**仅因「RSI 偏低不能做空」「RSI 偏高不能做多」「距7d 低点近」"
            "单独 reject；须判断 catalyst 描述的是延续还是逆势摸顶/抄底。\n"
            "- **必须 reject**：catalyst 与 proposed side 明显矛盾；仅 24h 涨跌幅/费率无 K 线；"
            "结构叙述空泛；与用户方向闸门或 Big4 硬冲突（见上节）。"
        ),
    ),
    "predict": OpenAdvisorStrategyProfile(
        key="predict",
        title_zh="AI 预测（2h 方向）",
        expected_side=None,
        rubric=(
            "【预测专属，与预测 prompt 一致；勿用战术/探索混审】\n"
            "上游已通过 catalyst 门槛；勿用单一 RSI 否决。\n"
            "catalyst 须含 1h/15m 方向依据；允许趋势延续与反转两类，只要与 side 自洽。\n"
            "与 Big4 严重冲突且个股 catalyst 不足 → reject；"
            "非极端行情下允许与 Big4 反向但须在 catalyst 中自洽。"
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
_UPSTREAM_GATED_OPEN_PROFILES = _TACTICAL_PROFILE_KEYS | {"explore", "predict"}


def should_skip_llm_for_tactical_open(
    profile: OpenAdvisorStrategyProfile,
    source: str,
    *,
    tactical_llm_enabled: bool = True,
    explore_predict_llm_enabled: bool = False,
) -> bool:
    """上游已过 catalyst 门槛时，可跳过开仓顾问 LLM 复审（避免与探索/预测 prompt 双标全拒）."""
    if profile.key not in _UPSTREAM_GATED_OPEN_PROFILES:
        return False
    if profile.key in ("explore", "predict"):
        return not explore_predict_llm_enabled
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
        return False, "系统禁止做多 (allow_long=0)"
    if s == "SHORT" and not allow_short:
        return False, "系统禁止做空 (allow_short=0)"
    return True, ""


def check_expected_side(profile: OpenAdvisorStrategyProfile, side: str) -> Tuple[bool, str]:
    if not profile.expected_side:
        return True, ""
    s = (side or "").upper()
    if s != profile.expected_side:
        return (
            False,
            f"策略「{profile.title_zh}」仅允许 {profile.expected_side}，当前为 {s}",
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
        "**禁止**用其它策略（回调/追涨/探索/预测等）的标准替代下文 rubric。",
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
        lines.append(
            "3. 以探索 catalyst 为主：延续/反转均可；**勿**用四战术互斥 checklist。"
        )
        lines.append(
            "4. 仅当 catalyst 与 K 线/方向矛盾、或纯涨跌幅追价 → reject；"
            "存疑且 catalyst 已写清多周期结构时 → approve。"
        )
    elif key == "predict":
        lines.append("3. 审 2h 预测逻辑与多周期依据，**勿**用战术 checklist。")
        lines.append(
            "4. catalyst 与 side 自洽即可 approve；勿因 RSI 高低单独否决。"
        )
    else:
        lines.append("3. entry_reason/catalyst 与方向、K 线一致；含糊或与闸门冲突 → reject。")
        lines.append("4. 勿套用未列出的其它策略专属标准。")
    return "\n".join(lines)


def build_tech_metrics_block(profile: OpenAdvisorStrategyProfile, ctx: dict) -> str:
    """战术/探索类顾问可见的量化字段（便于按策略核对，非通用模糊描述）."""
    if profile.key not in _TACTICAL_PROFILE_KEYS | {"explore", "predict"}:
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
            f"\n- 追涨硬线: RSI {CHASE_RSI_MIN}~{CHASE_RSI_MAX}, "
            f"below_7d_high≤-{CHASE_MIN_ROOM_BELOW_7D_HIGH_PCT:.0f}%"
        )
    elif profile.key == "pullback":
        extra = (
            f"\n- 回调硬线: RSI≤{PULLBACK_RSI_MAX}, "
            f"below_7d_high≤{PULLBACK_MAX_BELOW_7D_HIGH_PCT:.0f}%"
        )
    elif profile.key == "rebound":
        extra = (
            f"\n- 反弹空硬线: RSI {REBOUND_RSI_MIN}~{REBOUND_RSI_MAX}, "
            f"below_7d_high>{REBOUND_NEAR_7D_HIGH_PCT:.0f}%"
        )
    elif profile.key == "dump":
        extra = f"\n- 杀跌硬线: RSI≤{DUMP_RSI_MAX}"
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
                f"[追涨] RSI={rsi:.0f}>{CHASE_RSI_MAX}，预检驳回"
            )
        if rsi is not None and float(rsi) < CHASE_RSI_MIN:
            return False, (
                f"[追涨] RSI={rsi:.0f}<{CHASE_RSI_MIN}，预检驳回"
            )
        if b7h is not None and float(b7h) > -CHASE_MIN_ROOM_BELOW_7D_HIGH_PCT:
            return False, (
                f"[追涨] below_7d_high={b7h:.1f}% 距高点空间不足，预检驳回"
            )

    if profile.key == "pullback" and s == "LONG":
        if rsi is not None and float(rsi) > PULLBACK_RSI_MAX:
            return False, (
                f"[回调] RSI={rsi:.0f}>{PULLBACK_RSI_MAX}，预检驳回"
            )
        if b7h is not None and float(b7h) > PULLBACK_MAX_BELOW_7D_HIGH_PCT:
            return False, (
                f"[回调] below_7d_high={b7h:.1f}% 距高点过近，非有效回踩，预检驳回"
            )

    if profile.key == "dump" and s == "SHORT":
        if rsi is not None and float(rsi) > DUMP_RSI_MAX:
            return False, (
                f"[杀跌] RSI={rsi:.0f}>{DUMP_RSI_MAX}，更像反弹做空而非破位，预检驳回"
            )

    if profile.key == "rebound" and s == "SHORT":
        if rsi is not None and float(rsi) < REBOUND_RSI_MIN:
            return False, (
                f"[反弹做空] RSI={rsi:.0f}<{REBOUND_RSI_MIN} 过低，预检驳回"
            )
        if rsi is not None and float(rsi) > REBOUND_RSI_MAX:
            return False, (
                f"[反弹做空] RSI={rsi:.0f}>{REBOUND_RSI_MAX} 过高，预检驳回"
            )
        if b7h is not None and float(b7h) <= REBOUND_NEAR_7D_HIGH_PCT:
            return False, (
                f"[反弹做空] below_7d_high={b7h:.1f}% 离7d高点过远，预检驳回"
            )

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
    """Chinese open-advisor prompt (Gemini / DeepSeek / GPT production)."""
    big4_block = build_big4_subjective_block(
        ctx.get("big4_signal", "NEUTRAL"),
        float(ctx.get("big4_strength") or 0),
        bool(ctx.get("allow_long", True)),
        bool(ctx.get("allow_short", True)),
        side,
        float(ctx.get("btc_6h_change") or 0),
        float(ctx.get("eth_6h_change") or 0),
    )
    klines_15m = format_kline_table(ctx.get("klines_15m", []))
    klines_1h = format_kline_table(ctx.get("klines_1h", []))
    narr_1h = (ctx.get("narrative_1h") or "").strip() or "(缓存暂无，以上表为准)"
    narr_15m = (ctx.get("narrative_15m") or "").strip() or "(无)"
    tech_block = build_tech_metrics_block(profile, ctx)
    review_steps = build_strategy_review_steps(profile)
    sl_s = f"{sl_pct}%" if sl_pct is not None else "默认"
    tp_s = f"{tp_pct}%" if tp_pct is not None else "默认"
    hold_s = f"{hold_hours}h" if hold_hours is not None else "策略默认"
    return f"""你是超级交易大师。系统在**开模拟仓之前**请你审核是否允许开仓。

## 重要
- 本笔 **唯一** 审核标准：下方「{profile.title_zh}」专属 rubric（profile={profile.key}）。
- **禁止**用其它策略的标准审本单（例如用「追涨」标准审「回调」单）。

## 本笔策略
  策略名:     {profile.title_zh}
  profile:    {profile.key}
  source:     {source}
  固定方向:   {profile.expected_side or '按信号 LONG/SHORT'}

### 「{profile.title_zh}」专属审核标准（仅此一节有效）
{profile.rubric}

{_KLINE_1H_READING}

{tech_block}

{big4_block}

## 拟开仓
  Symbol:     {symbol}
  Direction:  {side}
  Entry:      {price}
  Leverage:   {leverage}x
  SL/TP:      {sl_s} / {tp_s}
  Plan hold:  {hold_s}
  Catalyst:   {(catalyst or '')[:500]}

## 市场数据
  candidate_pool 1h 叙事:
{narr_1h}
  candidate_pool 15m 叙事:
{narr_15m}

## 近 24 根 1h K 线 (oldest → newest)
{klines_1h}

## 近 4h 15m K 线
{klines_15m}

{review_steps}

Output ONLY JSON:
{{
  "decision": "approve" | "reject",
  "reason": "<50字中文，必须写明策略名「{profile.title_zh}」通过/驳回要点>"
}}
"""


_GPT_PROFILE_TITLE_EN: dict[str, str] = {
    "pullback": "Pullback long",
    "rebound": "Rebound short",
    "chase": "Momentum chase long",
    "dump": "Breakdown short",
    "explore": "AI main explore (event/structure)",
    "predict": "AI predict (2h direction)",
    "btc_momentum": "BTC momentum",
    "smart_trader": "Main strategy / smart_trader",
    "mean_reversion": "Bollinger mean reversion",
    "generic": "Other strategy",
}

_GPT_RUBRIC_EN: dict[str, str] = {
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
        "Explore-only (aligned with explore worker prompt). Upstream catalyst gate already passed.\n"
        "Approve trend continuation when catalyst cites 1h+recent bars aligned with side "
        "(SHORT in downtrend / LONG in uptrend is OK).\n"
        "Do NOT reject solely for low/high RSI or proximity to 7d low/high.\n"
        "Reject only: catalyst contradicts side, 24h%-only narrative, vague structure, or hard gate conflict."
    ),
    "predict": (
        "Predict-only. Upstream catalyst gate passed.\n"
        "Catalyst needs 1h/15m logic; continuation or reversal OK if self-consistent.\n"
        "Do not reject on RSI alone; severe Big4 conflict without coin case → reject."
    ),
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
    upstream_gated = key in _TACTICAL_PROFILE_KEYS | {"explore", "predict"}
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
    elif key == "explore":
        lines.append("3. Event/structure catalyst; no tactical four-way checklist.")
    elif key == "predict":
        lines.append("3. 2h predict logic; no tactical/explore checklist mix.")
    else:
        lines.append("3. entry_reason/catalyst vs side and K-lines; vague → reject.")
    return "\n".join(lines)


def build_gpt_tech_metrics_block(profile: OpenAdvisorStrategyProfile, ctx: dict) -> str:
    if profile.key not in _TACTICAL_PROFILE_KEYS | {"explore", "predict"}:
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


