"""开仓顾问 — 按 source 映射策略审核标准 + Big4/方向闸门文案."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

OPEN_KLINE_15M_BARS = 16  # 开仓顾问：近 16 根 15m = 4h（核心判据）
OPEN_KLINE_15M_RECENT_BARS = 6  # 近 6 根 15m 局部结构
OPEN_KLINE_1H_WINDOW_BARS = 4  # 近 4 根 1h = 同 4h 窗口（1h≈4×15m，背景交叉验证）
OPEN_KLINE_1H_RECENT_BARS = 6  # 1h 近 6 根明细（战术/更长背景）

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
    if s in ("smart_trader", "smart_trader_sync", "btc_momentum", "BTC_MOMENTUM") or "signal_confirm" in s or "trend_follow" in s:
        return "smart_trader"
    if "bollinger" in s or "mean_reversion" in s:
        return "generic"
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
            "【探索专属；4h 窗口；**15m 量价 + 价格趋势定方向**】\n"
            "上游已通过 catalyst 门槛；复核 **15m 价格趋势是否与 proposed side 一致**。\n"
            "**核对顺序**：① 15m 价格趋势（偏多/偏空）② 量价是否配合 ③ 近4~6根结构 ④ RSI(1h)辅证\n"
            "- **approve**：LONG 时 15m 趋势偏多/抬高且量价不背离；SHORT 时趋势偏空/走低且量价不背离。\n"
            "- **reject**：catalyst 方向与 side 矛盾；15m 表显示反向 K 线占优；仅 RSI/24h 无量价趋势；"
            "缩量假突破/放量反向。\n"
            "- 近 4 根 1h 仅交叉验证；**不得**用 1h/RSI 替代 15m 趋势定方向。"
        ),
    ),
    "predict": OpenAdvisorStrategyProfile(
        key="predict",
        title_zh="AI 预测（4h 方向）",
        expected_side=None,
        rubric=(
            "【预测专属；4h 窗口；**15m 量价 + 价格趋势定方向**】\n"
            "上游已通过 catalyst 门槛；核对 **15m 价格趋势**是否仍支持 proposed side。\n"
            "**核对顺序**：① 15m 趋势方向 ② 量价配合 ③ 近结构 ④ RSI(1h)辅证\n"
            "- **approve**：15m 趋势与 side 一致，量能能解释方向，无量价背离。\n"
            "- **reject**：15m 趋势与 side 矛盾；仅 RSI/24h 无量价趋势；15m 表反向占优。\n"
            "- 1h 保留作同窗口对照；**不得**用 1h/RSI 替代 15m 趋势定方向。"
        ),
    ),
    "smart_trader": OpenAdvisorStrategyProfile(
        key="smart_trader",
        title_zh="主策略/smart_trader",
        expected_side=None,
        rubric=(
            "主策略：须综合 1h/15m 形态、RSI、成交量与 entry_reason；"
            "方向与 K 线多维一致才 approve；震荡/趋势模式不匹配或信号含糊 → reject。"
        ),
    ),
    "generic": OpenAdvisorStrategyProfile(
        key="generic",
        title_zh="其他策略",
        expected_side=None,
        rubric=(
            "【通用兜底】须综合审查 **1h 形态、15m 形态、RSI、成交量、入场价** 与 catalyst 是否一致；"
            "禁止套用追涨/回调/探索等其它策略专属 checklist。\n"
            "1h/15m 形态不支持 proposed side，或仅 narrative 无量价印证 → reject。"
            "结构不清晰或与 Big4/方向闸门冲突 → reject。"
        ),
    ),
}

_TACTICAL_PROFILE_KEYS = frozenset({"pullback", "rebound", "chase", "dump"})
_UPSTREAM_GATED_OPEN_PROFILES = _TACTICAL_PROFILE_KEYS | {"explore", "predict"}
_UPSTREAM_CATALYST_VALIDATED_SOURCES = frozenset({
    "gemini_explore", "gemini_predict",
    "deepseek_explore", "deepseek_predict",
    "gpt_explore", "gpt_predict",
})


def should_skip_upstream_catalyst_precheck(source: str, profile_key: str) -> bool:
    """探索/预测 worker 已用 catalyst+data_signal 做过技术门，开仓顾问勿重复弱校验."""
    if profile_key not in ("explore", "predict"):
        return False
    return (source or "").strip().lower() in _UPSTREAM_CATALYST_VALIDATED_SOURCES


def should_skip_llm_for_tactical_open(
    profile: OpenAdvisorStrategyProfile,
    source: str,
    *,
    tactical_llm_enabled: bool = True,
    explore_predict_llm_enabled: bool = True,
) -> bool:
    """上游已过 catalyst 门槛时，可跳过开仓顾问 LLM 复审（system_settings 可关）."""
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


def _recent_klines(klines: list, n: int) -> list:
    if not klines or n <= 0:
        return klines or []
    return klines[-n:]


def _score_klines_for_side(klines: list, side: str) -> dict:
    """客观统计 K 线对开仓方向的支持/反向根数（供 prompt 综合审查）."""
    empty = {
        "for": 0, "against": 0, "last3": "", "trail_against": 0,
        "summary": "数据不足",
    }
    if not klines:
        return empty
    dirs: List[str] = []
    for_count = 0
    against_count = 0
    for k in klines:
        o, c = float(k["o"]), float(k["c"])
        if c > o:
            d = "G"
        elif c < o:
            d = "R"
        else:
            d = "D"
        dirs.append(d)
        if side == "LONG":
            if d == "G":
                for_count += 1
            elif d == "R":
                against_count += 1
        else:
            if d == "R":
                for_count += 1
            elif d == "G":
                against_count += 1
    want = "G" if side == "LONG" else "R"
    trail_against = 0
    for d in reversed(dirs):
        if d == "D":
            continue
        if d != want:
            trail_against += 1
        else:
            break
    last3 = "".join(dirs[-3:])
    summary = (
        f"顺向={for_count} 反向={against_count} "
        f"末3根={last3} 连反向={trail_against}"
    )
    return {
        "for": for_count,
        "against": against_count,
        "last3": last3,
        "trail_against": trail_against,
        "summary": summary,
    }


def _volume_summary(klines: list, n: int = 6) -> str:
    """近 n 根 vs 前 n 根均量对比（缩量/放量/持平）."""
    if not klines:
        return "数据不足"
    recent = _recent_klines(klines, n)
    if len(klines) >= n * 2:
        prior = klines[-(n * 2):-n]
    elif len(klines) > n:
        prior = klines[:-n]
    else:
        prior = []
    r_avg = sum(float(k.get("v", 0) or 0) for k in recent) / len(recent)
    p_avg = (
        sum(float(k.get("v", 0) or 0) for k in prior) / len(prior)
        if prior else r_avg
    )
    last_v = float(recent[-1].get("v", 0) or 0)
    if r_avg > p_avg * 1.1:
        trend = "放量"
    elif r_avg < p_avg * 0.9:
        trend = "缩量"
    else:
        trend = "持平"
    return f"近{n}均量={r_avg:.0f} 前段均量={p_avg:.0f} 最新={last_v:.0f} ({trend})"


_KLINE_COMPREHENSIVE_READING_EXPLORE_PREDICT = """
## K 线综合读法（探索/预测：**15m 量价 + 价格趋势定方向**）
- **周期**：16 根 15m = 4 根 1h = 4h 窗口（1 根 1h ≈ 4 根 15m）。
- **方向怎么定**：先看 **15m 价格变化趋势**（偏多/偏空/抬高/走低），再结合量价是否配合；RSI(1h)/24h 不能单独定方向。
- **核心判据**：15m 表 + narrative.15m（趋势 + 近4~6根 + 量能）。
- **1h 保留**：narrative.1h（24根背景）+ 近 **4 根 1h 表**（同窗口对照）；**不得**用 1h 替代 15m approve/reject。
- **矛盾处理**：15m 趋势与 side 矛盾 → reject；15m 自洽、近4根1h 逆势 → 可 approve 但 reason 须点明。
"""

_OPEN_ADVISOR_SELF_CHECK = """
## 6/5 高盈利版本保留的自检
- 删除 `24h涨跌幅`、资金费率、Big4、triggers 后，开仓理由仍能独立成立吗？不能 → reject。
- catalyst 须有 **15m 价格趋势 + 量价**；RSI(1h) 仅辅证，**不能**单独定方向。
- 允许趋势延续，也允许结构反转；但必须说清是「延续 / 回踩突破 / 反转确认」哪一类。
- reason 不得写空话；必须落到 **15m 趋势/形态**、量能、策略名三点。
- **禁止**主要依据 1h 表或 RSI 单独 approve/reject（1h 滞后，易误判）。
"""

_KLINE_COMPREHENSIVE_READING_TACTICAL = """
## K 线综合读法（战术策略：**15m 定入场与4h窗口**，1h 仅背景）
- **禁止**仅凭单根 K 线、仅 24h 涨跌幅、或仅 catalyst 措辞下结论。
- **15m 整体+局部**：近 **16 根 15m**（4h）定趋势，近 **6 根 15m** 定入场时机。
- **1h 整体趋势**：仅背景参考（近 24 根 1h），**不得**替代 15m 做主判。
- **成交量**：对照 **15m** 表 volume 列 + 下方 15m 量能摘要；突破须放量、反弹衰竭须缩量。
- **RSI + 价格位置**：RSI(1h) 与 below/above 7d 须与 **15m** 形态一致。
- **reason 须写明**：15m 形态结论 + 量能/RSI 至少一项，再写 approve/reject。
"""

_KLINE_COMPREHENSIVE_READING_GENERIC = """
## K 线综合读法（通用：**15m 主** + 1h 背景）
- **禁止**仅凭单根 K 线、仅 24h 涨跌幅、或仅 catalyst 措辞下结论。
- **15m**：近 16 根定 4h 趋势 + 近 6 根定局部结构（**主判据**）。
- **1h**：仅背景趋势参考。
- **成交量 + RSI**：与 catalyst/entry_reason 交叉验证。
- **reason 须写明**：15m + 量能/RSI 要点，再写 approve/reject。
"""


def _kline_reading_block(profile: OpenAdvisorStrategyProfile) -> str:
    if profile.key in ("explore", "predict"):
        return _KLINE_COMPREHENSIVE_READING_EXPLORE_PREDICT
    if profile.key in _TACTICAL_PROFILE_KEYS:
        return _KLINE_COMPREHENSIVE_READING_TACTICAL
    return _KLINE_COMPREHENSIVE_READING_GENERIC


def build_strategy_review_steps(profile: OpenAdvisorStrategyProfile) -> str:
    """按策略类型生成审核步骤（非全策略共用同一套条文）."""
    key = profile.key
    title = profile.title_zh
    lines = [
        "## 审核步骤（仅适用于本策略）",
        f"- 本笔仅按 **「{title}」**（profile=`{key}`）审核；"
        "**禁止**用其它策略（回调/追涨/探索/预测等）的标准替代下文 rubric。",
        "1. **综合技术面**（所有策略必做）：对照 **15m 形态**、RSI、成交量、入场价；"
        "1h 仅背景；任一维与 proposed side 明显矛盾且无策略 rubric 豁免 → reject。",
        "2. 方向闸门 + Big4（上节）— 冲突则 reject。",
        f"3. **执行上文「{title}」专属 rubric** — 与 K 线表、客观统计、量化指标交叉验证。",
    ]
    if key in _TACTICAL_PROFILE_KEYS:
        lines.append(
            "4. **战术互斥**（仅四战术）：形态更符合其它战术名 → reject，即使 catalyst 措辞漂亮。"
        )
        lines.append(
            "5. catalyst 须与 1h/15m 形态、RSI、量能一致；仅24h涨跌 / 单根K线 → reject。"
        )
        lines.append("6. 完全符合**本战术**才 approve；存疑 reject。")
    elif key == "reversal":
        lines.append(
            "4. SHORT=真顶部反转、LONG=真底部反转；禁止「涨多了/跌多了」无结构。"
        )
        lines.append("5. 勿按回调/追涨/杀跌战术 checklist 审核反转单。")
        lines.append("6. 完全符合反转定义才 approve。")
    elif key == "explore":
        lines.append(
            "4. 核对 **15m 价格趋势 + 量价** 与 proposed side；趋势与 side 矛盾 → reject。"
        )
        lines.append(
            "5. 15m 趋势自洽且量价不背离可 approve；勿用四战术 checklist；1h 仅背景。"
        )
    elif key == "predict":
        lines.append("4. 审 4h 预测逻辑与 **15m 趋势/量价**依据，**勿**用战术 checklist。")
        lines.append(
            "5. catalyst 与 15m 趋势自洽、量价不背离可 approve；勿因单一 RSI(1h) 否决。"
        )
    else:
        lines.append("4. entry_reason/catalyst 与 1h/15m 形态、量能、方向一致；含糊 → reject。")
        lines.append("5. 勿套用未列出的其它策略专属标准。")
    return "\n".join(lines)


def build_tech_metrics_block(
    profile: OpenAdvisorStrategyProfile,
    ctx: dict,
    side: str,
    price: float,
) -> str:
    """所有策略开仓顾问：15m/1h 形态统计 + RSI + 量能 + 价格位置."""
    rsi = ctx.get("rsi_14_1h")
    b7h = ctx.get("below_7d_high_pct")
    a7l = ctx.get("above_7d_low_pct")
    rsi_s = f"{rsi:.1f}" if rsi is not None else "N/A"
    b7h_s = f"{b7h:.2f}%" if b7h is not None else "N/A"
    a7l_s = f"{a7l:.2f}%" if a7l is not None else "N/A"

    k1h_all = ctx.get("klines_1h") or []
    k15_all = ctx.get("klines_15m") or []
    k1h = _recent_klines(k1h_all, OPEN_KLINE_1H_RECENT_BARS)
    k15 = _recent_klines(k15_all, OPEN_KLINE_15M_BARS)
    k15_recent = _recent_klines(k15_all, OPEN_KLINE_15M_RECENT_BARS)
    k1h_win = _recent_klines(k1h_all, OPEN_KLINE_1H_WINDOW_BARS)
    s1h = _score_klines_for_side(k1h, side)
    s1h_win = _score_klines_for_side(k1h_win, side)
    s15 = _score_klines_for_side(k15, side)
    s15_recent = _score_klines_for_side(k15_recent, side)
    vol_1h = _volume_summary(k1h_all, OPEN_KLINE_1H_RECENT_BARS)
    vol_15m = _volume_summary(k15_all, OPEN_KLINE_15M_BARS)

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

    if profile.key in ("explore", "predict"):
        extra += (
            f"\n- 探索/预测：15m 核心（16根=4h）；"
            f"1h近{OPEN_KLINE_1H_WINDOW_BARS}根同窗口交叉验证（1h≈4×15m）"
        )

    is_ep = profile.key in ("explore", "predict")
    lines = [
        "## 综合量化指标（开仓必审，须与 rubric 交叉验证）",
        f"- 拟入场价: {price}",
        f"- RSI(1h): {rsi_s}（辅证）",
        f"- below_7d_high_pct: {b7h_s} | above_7d_low_pct: {a7l_s}",
        f"- 15m形态({OPEN_KLINE_15M_BARS}根/4h): {s15['summary']}"
        + (" ← **核心判据**" if is_ep else ""),
        f"- 15m近结构({OPEN_KLINE_15M_RECENT_BARS}根): {s15_recent['summary']}",
    ]
    if is_ep:
        lines.append(
            f"- 1h同窗口({OPEN_KLINE_1H_WINDOW_BARS}根≈16×15m): {s1h_win['summary']} ← 交叉验证"
        )
        lines.append(
            f"- 1h更长背景({OPEN_KLINE_1H_RECENT_BARS}根明细): {s1h['summary']}"
        )
    else:
        lines.append(f"- 1h形态({OPEN_KLINE_1H_RECENT_BARS}根): {s1h['summary']}")
    lines.extend([
        f"- 15m量能: {vol_15m}",
        f"- 1h量能: {vol_1h}",
        extra,
    ])
    return "\n".join(lines) + "\n"


def precheck_open_advisor(
    profile: OpenAdvisorStrategyProfile,
    side: str,
    ctx: dict,
    catalyst: str = "",
    source: str = "",
    data_signal: str = "",
) -> Tuple[bool, str]:
    """代码层策略专属预检（LLM 之前），避免所有单用同一套模糊标准."""
    s = (side or "").upper()
    rsi = ctx.get("rsi_14_1h")
    b7h = ctx.get("below_7d_high_pct")

    if (
        profile.key in ("explore", "predict")
        and (catalyst or "").strip()
        and not should_skip_upstream_catalyst_precheck(source, profile.key)
    ):
        from app.services.ai_explore_prompt import (
            explore_catalyst_technical_ok,
            sym_data_for_catalyst_gate,
        )
        sym_item = {
            "tech": {
                "rsi_14_1h": ctx.get("rsi_14_1h"),
                "below_7d_high_pct": ctx.get("below_7d_high_pct"),
                "above_7d_low_pct": ctx.get("above_7d_low_pct"),
            },
            "kline_narrative": {
                "15m": (ctx.get("narrative_15m") or "")[:500],
                "1h": (ctx.get("narrative_1h") or "")[:500],
            },
        }
        tech_ok, tech_reason = explore_catalyst_technical_ok(
            catalyst,
            data_signal or "",
            sym_data=sym_data_for_catalyst_gate(sym_item),
            side=side,
        )
        if not tech_ok:
            return False, f"[{profile.title_zh}] catalyst 预检: {tech_reason}"

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


def validate_open_advisor_approval(
    profile: OpenAdvisorStrategyProfile,
    side: str,
    ctx: dict,
    reason: str = "",
) -> Tuple[bool, str]:
    """LLM 返回 approve 后的代码层复核；用于压低边际单/追噪音单通过率."""
    s = (side or "").upper()
    k1h_all = ctx.get("klines_1h") or []
    k15_all = ctx.get("klines_15m") or []
    k1h = _recent_klines(k1h_all, OPEN_KLINE_1H_RECENT_BARS)
    k15 = _recent_klines(k15_all, OPEN_KLINE_15M_BARS)
    score_1h = _score_klines_for_side(k1h, s)
    score_15m = _score_klines_for_side(k15, s)
    rsi = ctx.get("rsi_14_1h")
    b7h = ctx.get("below_7d_high_pct")
    a7l = ctx.get("above_7d_low_pct")

    if not (reason or "").strip():
        return False, "顾问 approve 但 reason 为空，二次复核拒绝"

    if len(k1h) < OPEN_KLINE_1H_RECENT_BARS and profile.key not in ("explore", "predict"):
        return False, f"1h K线不足{OPEN_KLINE_1H_RECENT_BARS}根，二次复核拒绝"

    if profile.key in ("explore", "predict") and len(k15) < OPEN_KLINE_15M_RECENT_BARS:
        return False, f"15m K线不足{OPEN_KLINE_15M_RECENT_BARS}根，二次复核拒绝"

    if rsi is not None:
        r = float(rsi)
        if s == "LONG" and r >= 78:
            return False, f"LONG RSI(1h)={r:.1f} 极端过热，二次复核拒绝"
        if s == "SHORT" and r <= 22:
            return False, f"SHORT RSI(1h)={r:.1f} 极端超跌，二次复核拒绝"
        if s == "LONG" and b7h is not None and r >= 70 and float(b7h) > -1.0:
            return False, f"LONG RSI(1h)={r:.1f}且贴近7日高点，追高风险过大，二次复核拒绝"
        if s == "SHORT" and a7l is not None and r <= 30 and float(a7l) < 1.0:
            return False, f"SHORT RSI(1h)={r:.1f}且贴近7日低点，追空风险过大，二次复核拒绝"

    if profile.key in ("explore", "predict"):
        if score_15m["against"] >= 4 and score_15m["for"] <= 1:
            return (
                False,
                f"{profile.title_zh} 15m近4h结构明显反向({score_15m['summary']})，二次复核拒绝",
            )
        if score_15m["for"] == 0 and score_15m["against"] >= 2:
            return (
                False,
                f"{profile.title_zh} 15m没有顺向K线且存在反向压力({score_15m['summary']})，二次复核拒绝",
            )
        if score_15m["trail_against"] >= 3:
            return (
                False,
                f"{profile.title_zh} 15m连续反向{score_15m['trail_against']}根，二次复核拒绝",
            )
        return True, ""

    if profile.key in _TACTICAL_PROFILE_KEYS:
        if score_15m["for"] < score_15m["against"]:
            return (
                False,
                f"{profile.title_zh} 15m不支持{s}({score_15m['summary']})，二次复核拒绝",
            )
        if score_15m["trail_against"] >= 4:
            return (
                False,
                f"{profile.title_zh} 15m连续反向{score_15m['trail_against']}根，二次复核拒绝",
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
    k15_raw = ctx.get("klines_15m") or []
    k1h_raw = ctx.get("klines_1h") or []
    klines_15m = format_kline_table(k15_raw)
    klines_1h_win = format_kline_table(_recent_klines(k1h_raw, OPEN_KLINE_1H_WINDOW_BARS))
    klines_1h_full = format_kline_table(k1h_raw)
    narr_1h = (ctx.get("narrative_1h") or "").strip() or "(无)"
    narr_15m = (ctx.get("narrative_15m") or "").strip() or "(无)"
    is_ep = profile.key in ("explore", "predict")
    if is_ep:
        kline_tables = f"""## 周期说明
16 根 15m = 4 根 1h = 4h 窗口（1 根 1h ≈ 4 根 15m）。**核心判据看 15m**；1h 保留作交叉验证与更长背景。

## 近 {OPEN_KLINE_15M_BARS} 根 15m K 线 ← **核心判据**
{klines_15m}

## 近 {OPEN_KLINE_1H_WINDOW_BARS} 根 1h K 线 ← **同 4h 窗口，交叉验证**
{klines_1h_win}

## 近 24 根 1h K 线 ← **更长背景（非核心判据）**
{klines_1h_full}"""
    else:
        kline_tables = f"""## 近 4h 15m K 线
{klines_15m}

## 近 24 根 1h K 线
{klines_1h_full}"""
    tech_block = build_tech_metrics_block(profile, ctx, side, price)
    review_steps = build_strategy_review_steps(profile)
    kline_reading = _kline_reading_block(profile)
    self_check = _OPEN_ADVISOR_SELF_CHECK if profile.key in ("explore", "predict") else ""
    sl_s = f"{sl_pct}%" if sl_pct is not None else "默认"
    tp_s = f"{tp_pct}%" if tp_pct is not None else "默认"
    hold_s = f"{hold_hours}h" if hold_hours is not None else "策略默认"
    if profile.key in ("explore", "predict"):
        review_focus = (
            "**必须综合审查**：**15m 价格趋势 + 量价（主，近16根=4h）**、catalyst 自洽；"
            "RSI(1h) 仅辅证；1h 仅背景。不要把结构清楚的 B+ 机会按完美形态错杀。"
        )
        reason_tpl = (
            f'"reason": "<50字中文，须含**15m趋势/形态**+量价+catalyst自洽要点，'
            f'写明策略名「{profile.title_zh}」通过/驳回依据>"'
        )
    else:
        review_focus = (
            "**必须综合审查**：**15m 形态（主）**、RSI、成交量、入场价位置；1h 仅背景。"
        )
        reason_tpl = (
            f'"reason": "<50字中文，须含15m形态与量能/RSI要点，'
            f'写明策略名「{profile.title_zh}」通过/驳回依据>"'
        )
    return f"""你是超级交易大师。系统在**开模拟仓之前**请你审核是否允许开仓。

## 重要
- 本笔 **唯一** 审核标准：下方「{profile.title_zh}」专属 rubric（profile={profile.key}）。
- **禁止**用其它策略的标准审本单（例如用「追涨」标准审「回调」单）。
- {review_focus}

## 本笔策略
  策略名:     {profile.title_zh}
  策略 profile: {profile.key}
  来源 source: {source}
  固定方向:   {profile.expected_side or '按信号 LONG/SHORT'}
  计划持仓:   {hold_s}（探索/预测按 4h 交易窗口审）

### 「{profile.title_zh}」专属审核标准（仅此一节有效）
{profile.rubric}

{kline_reading}

{tech_block}

{self_check}

{big4_block}

## 拟开仓
  Symbol:     {symbol}
  方向:       {side}
  拟入场价:   {price}
  杠杆:       {leverage}x
  SL/TP:      {sl_s} / {tp_s}
  计划持仓:   {hold_s}
  入场依据 catalyst: {(catalyst or '')[:500]}

## 市场数据
  candidate_pool 15m 叙事（**核心**）:
{narr_15m}
  candidate_pool 1h 叙事（**保留**，24根更长背景 + 近6根明细）:
{narr_1h}

{kline_tables}

{review_steps}

只输出合法 JSON:
{{
  "decision": "approve" | "reject",
  {reason_tpl}
}}
"""


_GPT_PROFILE_TITLE_EN: dict[str, str] = {
    key: profile.title_zh for key, profile in _PROFILES.items()
}

_GPT_RUBRIC_EN: dict[str, str] = {
    key: profile.rubric for key, profile in _PROFILES.items()
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
    _ = profile_key
    return build_big4_subjective_block(
        big4_signal,
        big4_strength,
        allow_long,
        allow_short,
        side,
        btc_6h,
        eth_6h,
    )


def build_gpt_strategy_review_steps(profile: OpenAdvisorStrategyProfile) -> str:
    return build_strategy_review_steps(profile)


def build_gpt_tech_metrics_block(
    profile: OpenAdvisorStrategyProfile,
    ctx: dict,
    side: str,
    price: float,
) -> str:
    return build_tech_metrics_block(profile, ctx, side, price)


_KLINE_COMPREHENSIVE_READING_EN = _KLINE_COMPREHENSIVE_READING_GENERIC


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
    """兼容旧调用名：现在一律返回中文开仓顾问 prompt。"""
    return build_open_advisor_prompt(
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
