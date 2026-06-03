"""战术探索 prompt — 回调做多 / 反弹做空 / 追涨做多 / 杀跌做空 (Gemini/DeepSeek 共用)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger

from app.services.ai_big4_prompt import BIG4_PROMPT_BLOCK_EXPLORE
from app.services.ai_explore_prompt import (
    EXPLORE_LLM_MAX_SYMBOLS,
    _KBAR_COUNT_RE,
    explore_catalyst_technical_ok,
    normalize_explore_llm_payload,
    parse_explore_llm_json,
)
from app.services.ai_reversal_explore_prompt import _catalyst_pct_sane

TACTICAL_CONFIDENCE_THRESHOLD = 0.55
# GPT 战术：每轮至少尝试的 entry 数（由代码补位，不依赖 LLM 宏观 skip）
GPT_TACTICAL_MIN_ENTRIES = 2
GPT_TACTICAL_MAX_ENTRIES = 3
GPT_FALLBACK_SCAN_TOP = 40
# 与代码门槛 / 开仓顾问 rubric 对齐（改一处须同步另两处）
CHASE_RSI_MAX = 68
PULLBACK_RSI_MAX = 68
CHASE_MIN_ROOM_BELOW_7D_HIGH_PCT = 3.0  # below_7d_high_pct 须 ≤ -3（距 7d 高至少 3% 空间）

_KLINE_1H_RULES = """
## 1h K 线读法（必读，禁止只看 1 根）
- **整体趋势**：用 universe 中 **最近 24 根 1h**（约 24 小时）判断上涨/下跌/通道；与 `kline_narrative.1h` 的「整体 N 根趋势」一致。
- **近期结构**：用 **最近 4~6 根 1h** 描述回调、反弹、连阳/连阴、回踩支撑（catalyst 须写「最近 4~6 根」或「近 6 根 1h」等，**不得**只写「最近 1 根 1h」）。
- 15m/1d 仍作辅助；主判据以 1h 的 24 根 + 近 4~6 根为准。
"""

_JSON_OUTPUT = """
## 输出 JSON (仅此结构，无 markdown)
{{
  "summary_zh": "本轮机会概况，1-3 句中文；无机会时说明原因",
  "verdicts": [
    {{
      "symbol": "XXXUSDT",
      "category": "entry",
      "confidence": 0.68,
      "catalyst": "1h/15m 多周期 K 线 + RSI/EMA/7d/阳阴根数量化描述",
      "data_signal": "可选",
      "risk_note": "可选"
    }}
  ]
}}
无符合策略的标的时 verdicts 用空数组 []，不要逐条输出 skip。
"""


@dataclass(frozen=True)
class TacticalStrategyDef:
    key: str
    title_zh: str
    fixed_side: str  # LONG | SHORT
    prompt_body: str
    contrast_block: str = ""  # 与其它战术的边界，减轻「共用模板」同质化
    extra_catalyst_check: Optional[Callable[[str, str, Optional[dict]], Tuple[bool, str]]] = None


def _narrative_text(sym_data: Optional[dict]) -> str:
    if not isinstance(sym_data, dict):
        return ""
    kn = sym_data.get("kline_narrative") or {}
    return " ".join(str(v) for v in kn.values())


def _tech(sym_data: Optional[dict]) -> dict:
    return (sym_data or {}).get("tech") or {} if isinstance(sym_data, dict) else {}


def _rsi_1h(sym_data: Optional[dict]) -> Optional[float]:
    raw = _tech(sym_data).get("rsi_14_1h")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _below_7d_high_pct(sym_data: Optional[dict]) -> Optional[float]:
    """现价相对 7d 高点的百分比 (负=低于高点)."""
    t = _tech(sym_data)
    raw = t.get("below_7d_high_pct")
    if raw is None and isinstance(sym_data, dict):
        raw = sym_data.get("below_7d_high_pct")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _symbol_hard_precheck_fail(defn: TacticalStrategyDef, item: dict) -> Optional[str]:
    """用 universe 内 tech/叙事做预筛，排除必过不了 extra_catalyst 的币（避免 LLM 白跑）."""
    rsi = _rsi_1h(item)
    narr = _narrative_text(item).lower()
    b7h = _below_7d_high_pct(item)
    bear_narr = _has_any(narr, ("偏空", "强势下降", "连阴", "下行趋势"))
    bull_narr = _has_any(narr, ("偏多", "上升", "上攻", "连阳"))

    if defn.key == "pullback":
        if rsi is not None and rsi > PULLBACK_RSI_MAX:
            return f"RSI={rsi:.0f}>{PULLBACK_RSI_MAX}"
        if bear_narr and not bull_narr:
            return "1h叙事偏空"
        if b7h is not None and b7h > -2.0:
            return f"below_7d_high={b7h:.1f}%过近"
    elif defn.key == "chase":
        if rsi is not None and rsi > CHASE_RSI_MAX:
            return f"RSI={rsi:.0f}>{CHASE_RSI_MAX}"
        if b7h is not None and b7h > -CHASE_MIN_ROOM_BELOW_7D_HIGH_PCT:
            return f"below_7d_high={b7h:.1f}%空间不足"
        if bear_narr and not bull_narr:
            return "1h叙事偏空"
    elif defn.key == "rebound":
        if rsi is not None and rsi < 40:
            return f"RSI={rsi:.0f}<40"
        if bull_narr and not bear_narr:
            return "1h叙事仍偏多"
    elif defn.key == "dump":
        if rsi is not None and rsi > 55:
            return f"RSI={rsi:.0f}>55"
        if bull_narr and not bear_narr:
            return "1h叙事仍偏强"
    return None


def _tactical_universe_score(defn: TacticalStrategyDef, item: dict) -> float:
    """按策略语义排序 TOP 列表（须与 extra_catalyst_check 硬线一致）."""
    tech = item.get("tech") or {}
    rsi = tech.get("rsi_14_1h")
    try:
        rsi_f = float(rsi) if rsi is not None else 50.0
    except (TypeError, ValueError):
        rsi_f = 50.0
    chg = float(item.get("change_24h") or 0)
    b7h = tech.get("below_7d_high_pct")
    a7l = tech.get("above_7d_low_pct")
    if defn.fixed_side == "LONG":
        if defn.key == "pullback":
            # 回调：宜 RSI 回落至 45~65，距 7d 高有一定深度（勿按最高 RSI 排序）
            score = 100.0 - abs(rsi_f - 52.0) + chg * 0.15
            if b7h is not None:
                try:
                    score += max(0.0, min(25.0, -float(b7h) * 0.35))
                except (TypeError, ValueError):
                    pass
            return score
        if defn.key == "chase":
            # 追涨：在 RSI≤68 前提下偏强势；超买段降权
            if rsi_f > CHASE_RSI_MAX:
                return -500.0 - rsi_f
            score = rsi_f + chg * 0.3
            if b7h is not None:
                try:
                    bf = float(b7h)
                    if bf > -CHASE_MIN_ROOM_BELOW_7D_HIGH_PCT:
                        score -= 30.0
                    else:
                        score += min(15.0, max(0.0, (-bf - CHASE_MIN_ROOM_BELOW_7D_HIGH_PCT) * 0.5))
                except (TypeError, ValueError):
                    pass
            return score
        return rsi_f + chg * 0.25
    score = (100.0 - rsi_f) - chg * 0.25
    if defn.key == "rebound" and b7h is not None:
        try:
            score += max(0.0, -float(b7h) * 0.4)
        except (TypeError, ValueError):
            pass
    if defn.key == "dump" and a7l is not None:
        try:
            score += max(0.0, (25.0 - float(a7l)) * 0.2)
        except (TypeError, ValueError):
            pass
    return score


def prepare_tactical_universe_for_llm(
    definition: TacticalStrategyDef,
    universe: dict,
    max_symbols: int = EXPLORE_LLM_MAX_SYMBOLS,
) -> Tuple[List[dict], Dict[str, Any]]:
    pool = list(universe.values())
    eligible = []
    precheck_dropped = 0
    for item in pool:
        if _symbol_hard_precheck_fail(definition, item):
            precheck_dropped += 1
            continue
        eligible.append(item)
    if not eligible:
        eligible = pool
        precheck_dropped = 0
    items = sorted(
        eligible,
        key=lambda x: _tactical_universe_score(definition, x),
        reverse=True,
    )
    for item in items:
        item.pop("k_1d_ohlc", None)
        item.pop("k_1h_ohlc", None)
        item.pop("k_15m_ohlc", None)
    selected = items[:max_symbols]
    meta = {
        "universe_total": len(pool),
        "llm_symbol_count": len(selected),
        "llm_symbols_truncated": max(0, len(items) - len(selected)),
        "precheck_dropped": precheck_dropped,
        "selection": f"tactical_{definition.key}",
    }
    return selected, meta


def _build_prompt(definition: TacticalStrategyDef, universe: dict, global_ctx: dict, historical_stats: dict):
    universe_list, meta = prepare_tactical_universe_for_llm(definition, universe)
    compact = {"ensure_ascii": False, "separators": (",", ":"), "default": str}
    side_note = "做多" if definition.fixed_side == "LONG" else "做空"
    contrast = (definition.contrast_block or "").strip()
    contrast_section = f"{contrast}\n\n" if contrast else ""
    prompt = (
        f"你是加密货币 U 本位合约的**{definition.title_zh}**专属分析师（非泛化多空评论）。"
        f"只输出**符合下述定义**的 {side_note} entry；其它战术形态一律不要塞进本 JSON。\n\n"
        f"{definition.prompt_body}\n\n"
        f"{contrast_section}"
        f"{_KLINE_1H_RULES}\n"
        f"## 本策略量化硬门槛（与代码校验一致，违反则勿 entry）\n"
        f"{_strategy_quant_rules(definition.key)}\n"
        "## 通用输出规则\n"
        "1. catalyst 须写明 1h **24 根整体趋势** + **近 4~6 根结构**，并辅以 15m/1d + 量化技术位 (RSI/EMA/7d/阳阴根数)。\n"
        "2. 禁止仅用 24h 涨跌幅或资金费率做主因。\n"
        "3. **只输出 category=entry 的标的**（最多 5 个）；不符合的不写入 verdicts。\n"
        "4. entry 时 confidence 必填 0.55–0.85。\n"
        "5. **仅当下方 universe 全部无人满足本策略硬线** 时才 verdicts=[]。"
        "列表已由代码预筛（RSI/叙事/7d 空间）；若预筛后仍有标的，须从中选 **1~3 个最符合** 的 entry，"
        "**禁止**因 Big4/宏观偏空就对整表 skip。\n"
        "6. K 线涨跌幅须合理（单根通常 <20%），与 universe 中 kline_narrative / tech 一致。\n\n"
        f"本列表为全池 {meta['universe_total']} 个中按本策略硬门槛预筛"
        f"（剔除 {meta.get('precheck_dropped', 0)} 个必过不了代码校验的币）后取 TOP "
        f"{meta['llm_symbol_count']}（排序与 RSI/7d 空间等量化线一致，非单纯 |24h涨跌|）。\n"
        f"**禁止**对列表外币种 entry；**禁止** catalyst 与各行 tech/kline_narrative 矛盾。\n\n"
        f"## Big4 / 宏观 (仅参考)\n{BIG4_PROMPT_BLOCK_EXPLORE}\n\n"
        f"## 全局市场\n{json.dumps(global_ctx, **compact)}\n\n"
        f"## 本策略历史 (30d closed)\n{json.dumps(historical_stats, **compact)}\n\n"
        f"## universe\n{json.dumps(universe_list, **compact)}\n"
        f"{_JSON_OUTPUT}"
    )
    return prompt, meta


def _synth_tactical_catalyst(defn: TacticalStrategyDef, item: dict) -> str:
    """从 universe 叙事/tech 拼 catalyst，供空 verdicts 兜底（与代码门槛一致）."""
    kn = item.get("kline_narrative") or {}
    h1 = ""
    if isinstance(kn, dict):
        h1 = str(kn.get("1h") or kn.get("1H") or "")[:500]
    rsi = _rsi_1h(item)
    b7h = _below_7d_high_pct(item)
    parts = []
    if h1:
        parts.append(f"1h kline_narrative: {h1}")
    if rsi is not None:
        parts.append(f"1h RSI={rsi:.2f}")
    if b7h is not None:
        parts.append(f"below_7d_high_pct={b7h:.1f}%")
    tail = {
        "pullback": "近24根1h整体偏多；近6根1h回落回踩支撑企稳",
        "chase": "近24根1h持续上涨；近6根延续上攻；15m/1d同向",
        "rebound": "近24根1h下降；近6根1h缩量反弹至阻力；量能不支持",
        "dump": "近24根1h下跌延续；近6根反弹无量；15m/1d偏空",
    }.get(defn.key, "")
    if tail:
        parts.append(tail)
    return "；".join(parts)


def build_tactical_fallback_entries(
    definition: TacticalStrategyDef,
    universe: dict,
    *,
    max_entries: int = GPT_TACTICAL_MAX_ENTRIES,
    exclude_symbols: Optional[set] = None,
) -> List[dict]:
    """从预筛 TOP 生成已通过 tactical_catalyst_ok 的 entry（GPT 补位用）."""
    exclude = {str(s).upper().replace("/", "") for s in (exclude_symbols or set())}
    selected, _meta = prepare_tactical_universe_for_llm(definition, universe)
    out: List[dict] = []
    for item in selected[:GPT_FALLBACK_SCAN_TOP]:
        sym = str(item.get("symbol") or "").upper().replace("/", "")
        if not sym or sym in exclude:
            continue
        catalyst = _synth_tactical_catalyst(definition, item)
        rsi = _rsi_1h(item)
        ds = f"RSI={rsi:.1f}" if rsi is not None else ""
        ok, _reason = tactical_catalyst_ok(definition, catalyst, ds, item, 0.62)
        if not ok:
            continue
        out.append({
            "symbol": sym,
            "category": "entry",
            "confidence": 0.58,
            "catalyst": catalyst[:500],
            "data_signal": ds[:255],
            "risk_note": "eligible_fallback",
        })
        if len(out) >= max_entries:
            break
    return out


def supplement_empty_tactical_verdicts(
    definition: TacticalStrategyDef,
    universe: dict,
    verdicts: List[dict],
    *,
    max_entries: int = GPT_TACTICAL_MAX_ENTRIES,
) -> Tuple[List[dict], bool]:
    """LLM 返回空列表时，从预筛 TOP 中挑能通过代码门槛的标的（主要缓解 GPT 过度保守）."""
    if verdicts:
        return verdicts, False
    out = build_tactical_fallback_entries(
        definition, universe, max_entries=max_entries,
    )
    return out, len(out) > 0


def _coerce_verdict_fields(v: dict) -> dict:
    out = dict(v)
    for key in ("catalyst", "data_signal", "risk_note", "category"):
        val = out.get(key)
        if isinstance(val, list):
            out[key] = " ".join(str(x) for x in val if x is not None)
        elif val is not None and not isinstance(val, str):
            out[key] = str(val)
    sym = out.get("symbol")
    if sym is not None:
        out["symbol"] = str(sym).upper().replace("/", "")
    return out


def parse_tactical_llm_json(
    text: str,
    tag: str,
    fixed_side: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    parsed, err = parse_explore_llm_json(text, tag)
    if parsed is None:
        return parsed, err
    parsed = normalize_explore_llm_payload(parsed) or parsed
    flat: List[dict] = []
    for v in parsed.get("verdicts") or []:
        if isinstance(v, dict):
            flat.append(_coerce_verdict_fields(v))
        elif isinstance(v, list):
            for sub in v:
                if isinstance(sub, dict):
                    flat.append(_coerce_verdict_fields(sub))
    parsed["verdicts"] = flat
    for v in parsed["verdicts"]:
        v["category"] = normalize_tactical_category(v, fixed_side)
        v["confidence"] = parse_tactical_confidence(v)
    return parsed, err


def parse_tactical_confidence(verdict: dict) -> float:
    for key in ("confidence", "conf", "score", "prob", "probability"):
        raw = verdict.get(key)
        if raw is None:
            continue
        try:
            c = float(raw)
        except (TypeError, ValueError):
            continue
        if c > 1.0 and c <= 100.0:
            c /= 100.0
        return max(0.0, min(1.0, c))
    return 0.0


def normalize_tactical_category(verdict: dict, fixed_side: Optional[str] = None) -> str:
    for key in ("category", "cat", "action", "verdict"):
        raw = verdict.get(key)
        if not raw:
            continue
        cat = str(raw).lower().strip()
        if cat in ("skip", "none", "hold", "wait", "neutral", "跳过"):
            return "skip"
        if cat in ("entry", "signal", "open", "trade", "入场", "开仓"):
            return "entry"
        if cat in ("long", "buy", "做多", "bullish", "bottom_reversal") and (
            fixed_side in (None, "LONG")
        ):
            return "entry"
        if cat in ("short", "sell", "做空", "bearish", "top_reversal") and (
            fixed_side in (None, "SHORT")
        ):
            return "entry"
        if cat in ("bullish", "bearish", "top_reversal", "bottom_reversal"):
            return "entry"
        return cat
    return "skip"


def tactical_category_to_side(
    definition: TacticalStrategyDef,
    category: str,
    confidence: float,
) -> Optional[str]:
    cat = normalize_tactical_category({"category": category}, definition.fixed_side)
    if confidence < TACTICAL_CONFIDENCE_THRESHOLD:
        return None
    if cat == "entry":
        return definition.fixed_side
    if cat == "skip":
        return None
    if cat in ("signal", "bullish", "bearish", "top_reversal", "bottom_reversal"):
        return definition.fixed_side
    if cat == "long" and definition.fixed_side == "LONG":
        return "LONG"
    if cat == "short" and definition.fixed_side == "SHORT":
        return "SHORT"
    return None


def tactical_catalyst_ok(
    definition: TacticalStrategyDef,
    catalyst: str,
    data_signal: str = "",
    sym_data: Optional[dict] = None,
    confidence: float = 0.0,
) -> Tuple[bool, str]:
    # 战术 extra 门槛始终执行（高 confidence 不得绕过，避免超买追涨等漏网）
    if definition.extra_catalyst_check:
        ok, reason = definition.extra_catalyst_check(catalyst, data_signal, sym_data)
        if not ok:
            return ok, reason
    ok, reason = explore_catalyst_technical_ok(catalyst, data_signal, sym_data)
    if not ok:
        return ok, reason
    sane, sane_reason = _catalyst_pct_sane(catalyst, data_signal)
    if not sane:
        return False, sane_reason
    return True, ""


def _strategy_quant_rules(strategy_key: str) -> str:
    """各战术专属量化红线（写入 prompt，与 extra_catalyst_check 一致）."""
    rules = {
        "pullback": (
            f"- RSI 1h 须 ≤ {PULLBACK_RSI_MAX}；高于此多为强势末端浅调，应 skip 或归追涨。\n"
            f"- 须先确认 24h 上涨趋势，再在近 4~6 根出现**回落/阴线/回踩**；须写支撑企稳。\n"
            "- 禁止「跌多了抄底」、禁止单边下跌中继。"
        ),
        "chase": (
            f"- RSI 1h 须 ≤ {CHASE_RSI_MAX}；>68 禁止追涨（超买延伸段）。\n"
            f"- tech.below_7d_high_pct 须 ≤ -{CHASE_MIN_ROOM_BELOW_7D_HIGH_PCT:.0f}"
            f"（距 7d 高点至少 3% 上行空间，否则 TP=6% 不现实）。\n"
            "- 近 6 根须**延续上行**、无深度回踩；叙事主调不得是「回调到位」。"
        ),
        "rebound": (
            "- 须下降趋势 + 近 4~6 根缩量/无力反弹 + 相对阻力区；禁止突破新高追空。\n"
            "- RSI 不宜极低钝化区盲目空（须写明反弹衰竭）。"
        ),
        "dump": (
            "- 须下跌趋势延续 + 反弹无量/失败；RSI 1h 不宜 >55。\n"
            "- 禁止底部博反弹叙事。"
        ),
    }
    return rules.get(strategy_key, "- 遵守策略定义与多周期 K 线结构。")


def _has_any(text: str, words: tuple) -> bool:
    low = text.lower()
    return any(w in low for w in words)


def _catalyst_uses_multi_1h_bars(catalyst: str, data_signal: str) -> bool:
    """拒绝仅依据单根 1h K 线的 catalyst."""
    text = f"{catalyst} {data_signal}".lower()
    if _has_any(text, (
        "4~6", "4-6", "近4", "近5", "近6", "最近4", "最近5", "最近6",
        "4根", "5根", "6根", "四根", "五根", "六根", "连续2", "连续3", "连续4",
        "连续 2", "连续 3", "连续 4", "两根", "三根", "四根", "连阴", "连阳",
        "24根", "24 根", "整体24", "近24", "近 24",
        "last 4", "last 5", "last 6", "4-6 bar", "recent 4", "recent 5", "recent 6",
        "24 bar", "24h bars",
    )):
        return True
    if re.search(r"连续\s*\d+\s*根", text):
        return True
    if re.search(r"最近\s*\d+\s*根", text) and not re.search(r"最近\s*1\s*根", text):
        return True
    if _has_any(text, ("24根", "整体", "通道", "趋势延续")) and _has_any(
        text, ("回调", "回踩", "反弹", "连阴", "连阳", "回落", "上行", "下行"),
    ):
        return True
    if re.search(r"最近\s*1\s*根\s*1h", text) or re.search(r"单根\s*1h", text):
        return False
    return False


def _pullback_extra(catalyst: str, data_signal: str, sym_data: Optional[dict]) -> Tuple[bool, str]:
    text = f"{catalyst} {data_signal}"
    low = text.lower()
    narr = _narrative_text(sym_data).lower()
    rsi = _rsi_1h(sym_data)

    if not _catalyst_uses_multi_1h_bars(catalyst, data_signal):
        return False, "须基于 1h 近 4~6 根结构 + 24 根整体趋势描述，禁止只看 1 根 1h"

    if not _has_any(low, (
        "24根", "24 根", "整体", "24小时", "24 hour", "近24",
    )) and "整体" not in narr:
        return False, "回调做多须写明 1h 近 24 根整体处于上涨趋势"

    if not _has_any(low, (
        "回调", "回踩", "回落", "下跌", "阴线", "下影", "探底",
        "pullback", "retrace", "dip", "pull back",
    )):
        return False, "回调做多须写明近 4~6 根 1h 回落/回踩/阴线等"

    if not _has_any(low, (
        "支撑", "企稳", "止跌", "下影", "前低", "ema", "ma", "箱体下沿",
        "support", "hold", "bounce",
    )):
        return False, "回调做多须写明支撑/企稳/下影/均线支撑"

    uptrend_ok = _has_any(
        f"{low} {narr}",
        ("上涨", "上升趋势", "上行", "偏多", "连阳", "高点抬高", "上升通道", "uptrend", "bullish"),
    )
    chg = float((sym_data or {}).get("change_24h") or 0)
    if not uptrend_ok and rsi is not None and 40 <= rsi <= PULLBACK_RSI_MAX and chg >= 0:
        uptrend_ok = True
    if not uptrend_ok:
        return False, "回调做多须先确认 1h/多周期处于上涨趋势（非单边下跌中抄底）"

    if _has_any(narr, ("偏空", "强势下降", "连阴", "下行趋势")) and not _has_any(
        narr, ("偏多", "上升", "上攻", "连阳")
    ):
        return False, "kline_narrative 1h/1d 仍偏空，不符合回调做多前提"

    if rsi is not None and rsi > PULLBACK_RSI_MAX:
        return False, (
            f"1h RSI={rsi:.0f}>{PULLBACK_RSI_MAX}，强势末端浅调，应 skip 或归追涨策略"
        )

    b7h = _below_7d_high_pct(sym_data)
    if b7h is not None and b7h > -2.0:
        return False, (
            f"距7d高点仅{-b7h:.1f}%（below_7d_high_pct={b7h:.1f}），"
            "回调做多需更深回踩或更大上行空间"
        )

    return True, ""


def _rebound_extra(catalyst: str, data_signal: str, sym_data: Optional[dict]) -> Tuple[bool, str]:
    text = f"{catalyst} {data_signal}"
    low = text.lower()
    narr = _narrative_text(sym_data).lower()
    tech = _tech(sym_data)
    rsi = _rsi_1h(sym_data)

    if not _catalyst_uses_multi_1h_bars(catalyst, data_signal):
        return False, "须基于 1h 近 4~6 根结构 + 24 根整体趋势描述，禁止只看 1 根 1h"

    if not _has_any(low, ("反弹", "反抽", "回升", "rebound", "bounce", "relief")):
        return False, "反弹做空须写明近 4~6 根 1h 存在反弹"

    if not _has_any(low, (
        "见顶", "高点", "下降", "下跌", "下行", "偏空", "连阴", "跌破",
        "downtrend", "bearish", "top", "lower high",
    )):
        return False, "反弹做空须写明此前见顶/已进入下降趋势"

    vol_weak = _has_any(low, (
        "量能", "缩量", "萎缩", "背离", "不支持", "乏力", "无量",
        "volume", "weak", "diverg", "no follow",
    ))
    if not vol_weak:
        return False, "反弹做空须写明反弹量能不足/缩量/量价不支持"

    at_high = _has_any(low, (
        "相对高", "阻力", "前高", "上影", "滞涨", "遇阻", "7d高", "接近高",
        "resistance", "relative high", "reject",
    ))
    try:
        b7h = tech.get("below_7d_high_pct")
        if b7h is not None and float(b7h) > -12:
            at_high = True
    except (TypeError, ValueError):
        pass
    if not at_high:
        return False, "反弹做空须处于相对高点/阻力区（非下跌中继低位）"

    if rsi is not None and rsi < 40:
        return False, f"1h RSI={rsi} 偏低，更像杀跌追空而非反弹空"

    if _has_any(narr, ("强势上升", "偏多", "突破", "连阳")) and not _has_any(
        narr, ("偏空", "下降", "回落", "连阴")
    ):
        return False, "叙事仍偏强势上行，不符合反弹做空"

    return True, ""


def _chase_extra(catalyst: str, data_signal: str, sym_data: Optional[dict]) -> Tuple[bool, str]:
    text = f"{catalyst} {data_signal}"
    low = text.lower()
    narr = _narrative_text(sym_data).lower()
    rsi = _rsi_1h(sym_data)

    if not _catalyst_uses_multi_1h_bars(catalyst, data_signal):
        return False, "须基于 1h 近 4~6 根结构 + 24 根整体趋势描述，禁止只看 1 根 1h"

    if not _has_any(low, (
        "上涨", "上行", "趋势", "延续", "连阳", "新高", "通道", "多头",
        "uptrend", "rally", "continuation", "higher high",
    )):
        return False, "追涨做多须写明持续上涨/趋势延续"

    if _has_any(low, (
        "深度回调", "大幅回踩", "跌破支撑", "见顶", "滞涨做空", "逆势",
    )):
        return False, "追涨做多不应以深度回调/见顶为主叙事"

    if _has_any(low, ("放量", "volume surge", "巨量")) and _has_any(
        low, ("缩量", "量能萎缩", "量能未放大", "量能没有放大", "量能平平")
    ):
        pass  # 明确「量能未放大仍可追」时允许
    # 不要求必须放量 — 用户策略允许量能未放大

    no_pullback = _has_any(low, (
        "无明显回调", "无回调", "未见回调", "未回调", "直线", "持续上行",
        "no pullback", "without pullback",
    )) or not _has_any(low, ("深度回踩", "大幅回调", "回调到位", "支撑反弹"))
    if not no_pullback and _has_any(low, ("回调", "回踩", "探底", "支撑反弹")):
        return False, "叙事以回调做多为主，应交给回调策略而非追涨"

    if rsi is not None and rsi < 48:
        return False, f"1h RSI={rsi} 偏弱，不宜追涨"

    if rsi is not None and rsi > CHASE_RSI_MAX:
        return False, f"1h RSI={rsi:.0f}>{CHASE_RSI_MAX}，超买追涨禁止（延伸段易触发 SL）"

    b7h = _below_7d_high_pct(sym_data)
    if b7h is not None and b7h > -CHASE_MIN_ROOM_BELOW_7D_HIGH_PCT:
        return False, (
            f"距7d高点空间不足（below_7d_high_pct={b7h:.1f}，须≤"
            f"-{CHASE_MIN_ROOM_BELOW_7D_HIGH_PCT:.0f}），TP6% 不现实"
        )

    if _has_any(narr, ("偏空", "下降", "连阴", "下杀")) and not _has_any(narr, ("偏多", "上升", "上攻")):
        return False, "kline 叙事偏空，不符合追涨"

    return True, ""


def _dump_extra(catalyst: str, data_signal: str, sym_data: Optional[dict]) -> Tuple[bool, str]:
    text = f"{catalyst} {data_signal}"
    low = text.lower()
    narr = _narrative_text(sym_data).lower()
    rsi = _rsi_1h(sym_data)

    if not _catalyst_uses_multi_1h_bars(catalyst, data_signal):
        return False, "须基于 1h 近 4~6 根结构 + 24 根整体趋势描述，禁止只看 1 根 1h"

    if not _has_any(low, (
        "下跌", "下行", "下降", "趋势", "延续", "连阴", "新低", "跌破", "偏空",
        "downtrend", "breakdown", "lower low", "selloff",
    )):
        return False, "杀跌做空须写明下跌趋势延续"

    no_bounce_support = _has_any(low, (
        "无量反弹", "量能不支持", "量价不支持", "反弹乏力", "缩量反弹",
        "无明显反弹", "无反弹", "反弹无力", "weak bounce", "no bounce",
    )) or not _has_any(low, ("强势反弹", "放量反弹", "反弹突破"))
    if not no_bounce_support and _has_any(low, ("反弹", "回升", "反抽")):
        return False, "杀跌做空须说明反弹缺乏量能/量价支持（或趋势未改无像样反弹）"

    if _has_any(low, ("见顶回调", "支撑", "抄底", "做多")):
        return False, "叙事偏做多/抄底，不符合杀跌追空"

    if rsi is not None and rsi > 55:
        return False, f"1h RSI={rsi} 偏高，不宜杀跌追空"

    if _has_any(narr, ("偏多", "强势上升", "突破", "连阳")) and not _has_any(
        narr, ("偏空", "下降", "下杀", "连阴")
    ):
        return False, "kline 叙事仍偏强，不符合杀跌追空"

    return True, ""


PULLBACK_LONG = TacticalStrategyDef(
    key="pullback",
    title_zh="回调做多",
    fixed_side="LONG",
    prompt_body=(
        "## 策略定义（必须全部满足才 entry）\n"
        "1. **大前提**：**近 24 根 1h** 整体处于**上涨趋势**（高点抬高/通道上行；见 kline_narrative 整体形态）。\n"
        "2. **近 4~6 根 1h**：出现**回落/回调**（多根阴线、回踩、短线下跌），而非仅凭 1 根阴线。\n"
        "3. **支撑有效**：回踩至支撑（EMA/前低/箱体下沿/下影线企稳）并有止跌迹象。\n"
        "4. **RSI**：1h RSI 须 **≤68**；宜写「从 XX 回落至 YY」，禁止在 RSI>68 时把浅调当健康回调。\n"
        "5. **方向**：仅 **做多**；禁止在单边下跌或无趋势时「跌多了抄底」。\n"
        "典型 catalyst 示例：「1h 近24根上升通道；近5根连续阴线回踩 EMA20；"
        "15m 下影企稳；RSI 1h 从 58 回落至 48」。"
    ),
    contrast_block=(
        "## 本策略边界（勿混淆）\n"
        "✅ 只找：**上涨趋势里**的回踩做多。\n"
        "❌ 不是：连阳末端追高（→**追涨做多**）、跌深反弹（→反转/探索）、顶部做空（→反弹做空）。"
    ),
    extra_catalyst_check=_pullback_extra,
)

REBOUND_SHORT = TacticalStrategyDef(
    key="rebound",
    title_zh="反弹做空",
    fixed_side="SHORT",
    contrast_block=(
        "## 本策略边界（勿混淆）\n"
        "✅ 只找：**下降通道里**的缩量反弹至阻力做空。\n"
        "❌ 不是：顺势杀跌（→杀跌做空）、强势突破新高（应 skip）、底部抄底（→反转/探索）。"
    ),
    prompt_body=(
        "## 策略定义（必须全部满足才 entry）\n"
        "1. **曾见顶**：此前出现相对**最高价/阶段顶**，之后进入**下降趋势**（1h/1d 结构转弱）。\n"
        "2. **近 4~6 根 1h**：出现**反弹**（多根阳线/反抽/触及均线或前高），非单根异动。\n"
        "3. **量能不支持反弹**：反弹缩量、量价背离或量能未跟上（须写明）。\n"
        "4. **位置**：当前处于**相对高点/阻力区**（距 7d 高点较近或上影受阻）。\n"
        "5. **方向**：仅 **做空**；禁止在强趋势突破新高时做空。\n"
        "典型：「1h 近24根下降通道；近4根缩量反弹碰前高；"
        "15m 上影；RSI 1h 52 反弹无力」。"
    ),
    extra_catalyst_check=_rebound_extra,
)

CHASE_LONG = TacticalStrategyDef(
    key="chase",
    title_zh="追涨做多",
    fixed_side="LONG",
    prompt_body=(
        "## 策略定义（必须全部满足才 entry）\n"
        "1. **持续上涨**：**近 24 根 1h** 趋势向上，**近 4~6 根** 仍延续上行、**无明显深度回调**。\n"
        "2. **量能**：**不要求放量**；量能平平但结构未破仍可 entry。\n"
        "3. **RSI**：1h RSI 须 **≤68**；>68 禁止 entry（超买延伸，4h 内易回撤打 SL）。\n"
        "4. **空间**：`tech.below_7d_high_pct` 须 **≤-3**（距 7d 高点至少约 3% 上行空间）。\n"
        "5. **禁止**：把「大幅回踩支撑」当追涨——那是**回调做多**。\n"
        "6. **方向**：仅 **做多**；须有连阳/通道/高点抬升等延续证据。\n"
        "典型：「1h 近24根偏多；近6根连阳无像样回调；below_7d_high=-8%；RSI 1h 62」。"
    ),
    contrast_block=(
        "## 本策略边界（勿混淆）\n"
        "✅ 只找：趋势延续中的**顺势追多**（尚未超买、距 7d 高有空间）。\n"
        "❌ 不是：深回踩后做多（→回调做多）、涨多了摸顶空、仅因 24h 大涨而多。"
    ),
    extra_catalyst_check=_chase_extra,
)

DUMP_SHORT = TacticalStrategyDef(
    key="dump",
    title_zh="杀跌做空",
    fixed_side="SHORT",
    contrast_block=(
        "## 本策略边界（勿混淆）\n"
        "✅ 只找：**下跌趋势延续**中的顺势做空（反弹无力）。\n"
        "❌ 不是：下降通道里的缩量反弹空（→反弹做空）、见底反转多。"
    ),
    prompt_body=(
        "## 策略定义（必须全部满足才 entry）\n"
        "1. **下跌趋势**：**近 24 根 1h** 处于**下降通道**，趋势**未扭转**。\n"
        "2. **反弹无力**：无明显量价支持的反弹，或反弹缩量、反弹失败（须写明）。\n"
        "3. **追空**：顺势**做空**延续下跌，非在底部博反弹。\n"
        "4. **量能**：不要求杀跌必须放量；关键是**没有**像样的放量反弹。\n"
        "典型：「1h 近24根连阴下行；近5根反弹无量；15m 反抽失败；RSI 1h 38」。"
    ),
    extra_catalyst_check=_dump_extra,
)

TACTICAL_STRATEGIES: Dict[str, TacticalStrategyDef] = {
    "pullback": PULLBACK_LONG,
    "rebound": REBOUND_SHORT,
    "chase": CHASE_LONG,
    "dump": DUMP_SHORT,
}


def build_strategy_prompt(
    strategy_key: str,
    universe: dict,
    global_ctx: dict,
    historical_stats: dict,
) -> Tuple[str, Dict[str, Any]]:
    defn = TACTICAL_STRATEGIES[strategy_key]
    return _build_prompt(defn, universe, global_ctx, historical_stats)
