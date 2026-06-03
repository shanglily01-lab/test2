"""顶空底多探索 — 共用 prompt (Gemini / DeepSeek).

策略: 顶部反转做空 (top_reversal → SHORT), 底部反转做多 (bottom_reversal → LONG).
仅模拟仓; 技术面 catalyst 门槛与方向探索共用多周期 K 线校验.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from app.services.ai_big4_prompt import (
    BIG4_PROMPT_BLOCK_EXPLORE,
    BIG4_PROMPT_BLOCK_EXPLORE_EN,
)
from app.services.ai_explore_prompt import (
    AI_POSITION_HOLD_HOURS,
    EXPLORE_LLM_MAX_OUTPUT_TOKENS,
    EXPLORE_LLM_MAX_SYMBOLS,
    explore_catalyst_technical_ok,
    parse_explore_llm_json,
)

REVERSAL_CONFIDENCE_THRESHOLD = 0.65
REVERSAL_SL_PCT = 3.0
REVERSAL_TP_PCT = 5.0
REVERSAL_HOLD_HOURS = float(AI_POSITION_HOLD_HOURS)

# catalyst 中单根涨跌幅 >50% 多为叙事 bug 或幻觉
_CATALYST_PCT_RE = re.compile(r"[-+]?\d+(?:\.\d+)?\s*%")

REVERSAL_PROMPT_TEMPLATE = """你是加密货币 U 本位合约的**反转交易**分析师。任务：在候选池中找出「顶部做空、底部做多」机会。

## 策略定义
- **top_reversal**（顶部反转）→ 后续应 **SHORT**：多周期滞涨、上影线、RSI 超买区、假突破后回落、连阴确认等。
- **bottom_reversal**（底部反转）→ 后续应 **LONG**：多周期止跌、下影线、RSI 超卖区、假跌破后反弹、连阳确认等。
- **skip**：无清晰反转结构，或仅因 24h 涨跌幅/资金费率主观判断。

## 硬性规则
1. 每个 verdict 的 catalyst 必须写明 **至少两个周期** (1h/15m/1d) 的 K 线形态 + **量化技术位** (RSI 数值、EMA 偏离、7d 高低距离、阳阴根数等)。
2. 禁止仅用「涨多了做空 / 跌多了做多」；须有多周期结构证据。
3. 全池仅对下方 universe 列表中的 symbol 输出 verdict；其余忽略。
4. confidence 0~1；仅当结构清晰时给 ≥0.65。
5. 宁缺毋滥：无把握一律 skip。
6. 引用 K 线涨跌幅须合理（单根通常 <20%）；须与 universe 中 tech / kline_narrative 一致。

{llm_universe_note}

## Big4 / 宏观 (仅参考，不可替代 K 线)
{big4_block}

## 全局市场
{global_context_json}

## 本策略历史表现 (closed 模拟单)
{historical_stats_json}

## 候选 universe (JSON 数组)
{universe_json}

## 输出 JSON (仅此结构，无 markdown)
{{
  "summary_zh": "本轮反转机会概况，1-3 句中文",
  "verdicts": [
    {{
      "symbol": "BTCUSDT",
      "category": "top_reversal|bottom_reversal|skip",
      "confidence": 0.0,
      "catalyst": "多周期 K 线 + RSI/EMA 量化描述",
      "data_signal": "可选补充",
      "risk_note": "可选"
    }}
  ]
}}
"""

REVERSAL_PROMPT_TEMPLATE_EN = """You are a **reversal** analyst for USDT-M perpetual futures. Find top/bottom reversal setups in the candidate pool.

## Strategy
- **top_reversal** → expect **SHORT**: multi-TF stall, upper wicks, RSI overbought zone, failed breakout, bearish follow-through.
- **bottom_reversal** → expect **LONG**: multi-TF base, lower wicks, RSI oversold zone, failed breakdown, bullish follow-through.
- **skip**: no clear reversal structure, or 24h % / funding-only opinion.

## Hard rules
1. Each catalyst must cite **≥2 timeframes** (1h/15m/1d) + **quant levels** (RSI, EMA distance, 7d high/low proximity, bar counts).
2. No "up too much short / down too much long" without structure.
3. Verdicts only for symbols in the universe list below.
4. confidence 0~1; use ≥0.65 only when structure is clear.
5. Prefer skip when unsure.
6. Bar % moves must be plausible (<20% per bar) and match universe tech / kline_narrative.

{llm_universe_note}

## Big4 / macro (background only)
{big4_block}

## Global market
{global_context_json}

## Strategy history (closed paper)
{historical_stats_json}

## Candidate universe (JSON array)
{universe_json}

## Output JSON only (no markdown)
{{
  "summary_zh": "1-3 sentence Chinese summary of reversal opportunities",
  "verdicts": [
    {{
      "symbol": "BTCUSDT",
      "category": "top_reversal|bottom_reversal|skip",
      "confidence": 0.0,
      "catalyst": "multi-TF K-line + RSI/EMA quant",
      "data_signal": "optional",
      "risk_note": "optional"
    }}
  ]
}}
"""


def _reversal_extremity_score(item: dict) -> float:
    """距 7d 高低 / RSI 极端程度，用于选币（非 |24h| 动量）."""
    tech = item.get("tech") or {}
    score = 0.0

    b7h = tech.get("below_7d_high_pct")
    if b7h is not None:
        try:
            # 越接近 7d 高点（-5%）得分越高
            score = max(score, -float(b7h))
        except (TypeError, ValueError):
            pass

    a7l = tech.get("above_7d_low_pct")
    if a7l is not None:
        try:
            a7l_f = float(a7l)
            if a7l_f <= 25:
                score = max(score, 25.0 - a7l_f)
        except (TypeError, ValueError):
            pass

    rsi = tech.get("rsi_14_1h")
    if rsi is not None:
        try:
            rsi_f = float(rsi)
            if rsi_f >= 60:
                score = max(score, (rsi_f - 55) * 1.2)
            if rsi_f <= 40:
                score = max(score, (45 - rsi_f) * 1.2)
        except (TypeError, ValueError):
            pass

    return score


def prepare_reversal_universe_for_llm(
    universe: dict,
    max_symbols: int = EXPLORE_LLM_MAX_SYMBOLS,
) -> Tuple[List[dict], Dict[str, Any]]:
    """按 RSI / 距 7d 高低极端程度选 TOP N，供顶空底多 LLM."""
    items = sorted(
        universe.values(),
        key=_reversal_extremity_score,
        reverse=True,
    )
    for item in items:
        item.pop("k_1d_ohlc", None)
        item.pop("k_1h_ohlc", None)
        item.pop("k_15m_ohlc", None)

    selected = items[:max_symbols]
    meta = {
        "universe_total": len(items),
        "llm_symbol_count": len(selected),
        "llm_symbols_truncated": max(0, len(items) - len(selected)),
        "selection": "reversal_extremity",
    }
    if meta["llm_symbols_truncated"]:
        logger.info(
            f"[Reversal] LLM 候选截断: 全池 {meta['universe_total']} → "
            f"送模 {meta['llm_symbol_count']} (RSI/7d极端 TOP{max_symbols})"
        )
    return selected, meta


def build_reversal_explore_prompt_en(
    universe: dict,
    global_ctx: dict,
    historical_stats: dict,
) -> Tuple[str, Dict[str, Any]]:
    universe_list, meta = prepare_reversal_universe_for_llm(universe)
    compact = {"ensure_ascii": False, "separators": (",", ":"), "default": str}
    prompt = REVERSAL_PROMPT_TEMPLATE_EN.format(
        big4_block=BIG4_PROMPT_BLOCK_EXPLORE_EN,
        global_context_json=json.dumps(global_ctx, **compact),
        universe_json=json.dumps(universe_list, **compact),
        historical_stats_json=json.dumps(historical_stats, **compact),
        llm_universe_note=(
            f"TOP {meta['llm_symbol_count']} of {meta['universe_total']} by RSI extreme + "
            f"7d high/low proximity (not |24h| sort). Verdicts only for listed symbols."
        ),
    )
    return prompt, meta


def build_reversal_explore_prompt(
    universe: dict,
    global_ctx: dict,
    historical_stats: dict,
) -> Tuple[str, Dict[str, Any]]:
    """Production default: English reversal explore prompt."""
    return build_reversal_explore_prompt_en(universe, global_ctx, historical_stats)


def parse_reversal_llm_json(text: str, tag: str = "Reversal") -> Tuple[Optional[dict], Optional[str]]:
    return parse_explore_llm_json(text, tag)


def _synth_reversal_catalyst(category: str, item: dict) -> str:
    kn = item.get("kline_narrative") or {}
    h1 = str((kn.get("1h") if isinstance(kn, dict) else "") or "")[:500]
    tech = item.get("tech") or {}
    rsi = tech.get("rsi_14_1h")
    b7h = tech.get("below_7d_high_pct")
    a7l = tech.get("above_7d_low_pct")
    parts = [f"1h/15m/1d kline: {h1}"] if h1 else []
    if rsi is not None:
        parts.append(f"1h RSI={float(rsi):.2f}")
    if b7h is not None:
        parts.append(f"below_7d_high_pct={float(b7h):.1f}%")
    if a7l is not None:
        parts.append(f"above_7d_low_pct={float(a7l):.1f}%")
    if category == "top_reversal":
        parts.append("近24根1h高位滞涨；近6根1h上影遇阻回落；15m/1d共振")
    else:
        parts.append("近24根1h低位止跌；近6根1h下影企稳反弹；15m/1d共振")
    return "；".join(parts)


def build_reversal_fallback_entries(
    universe: dict,
    *,
    max_entries: int = 2,
    exclude_symbols: Optional[set] = None,
) -> List[dict]:
    exclude = {str(s).upper().replace("/", "") for s in (exclude_symbols or set())}
    items, _meta = prepare_reversal_universe_for_llm(universe)
    out: List[dict] = []
    for item in items[:40]:
        sym = str(item.get("symbol") or "").upper().replace("/", "")
        if not sym or sym in exclude:
            continue
        tech = item.get("tech") or {}
        try:
            rsi_f = float(tech.get("rsi_14_1h"))
        except (TypeError, ValueError):
            continue
        if rsi_f >= 58:
            cat = "top_reversal"
        elif rsi_f <= 42:
            cat = "bottom_reversal"
        else:
            continue
        catalyst = _synth_reversal_catalyst(cat, item)
        ok, _ = reversal_catalyst_technical_ok(cat, catalyst, "", item)
        if not ok:
            continue
        out.append({
            "symbol": sym,
            "category": cat,
            "confidence": 0.66,
            "catalyst": catalyst[:500],
            "data_signal": f"RSI={rsi_f:.1f}",
            "risk_note": "eligible_fallback",
        })
        if len(out) >= max_entries:
            break
    return out


def supplement_empty_reversal_verdicts(
    universe: dict,
    verdicts: List[dict],
    *,
    max_entries: int = 2,
) -> Tuple[List[dict], bool]:
    if verdicts:
        return verdicts, False
    out = build_reversal_fallback_entries(universe, max_entries=max_entries)
    return out, len(out) > 0


def reversal_category_to_side(category: str, confidence: float) -> Optional[str]:
    """top_reversal → SHORT, bottom_reversal → LONG."""
    cat = (category or "").lower().strip()
    if confidence < REVERSAL_CONFIDENCE_THRESHOLD:
        return None
    if cat == "top_reversal":
        return "SHORT"
    if cat == "bottom_reversal":
        return "LONG"
    return None


def _catalyst_pct_sane(catalyst: str, data_signal: str = "") -> Tuple[bool, str]:
    text = f"{catalyst or ''} {data_signal or ''}"
    for m in _CATALYST_PCT_RE.finditer(text):
        raw = m.group(0).replace("%", "").strip()
        try:
            val = abs(float(raw))
        except ValueError:
            continue
        if val > 50:
            return False, f"catalyst 涨跌幅 {m.group(0)} 异常(>50%), 请核对 K 线叙事"
    return True, ""


def reversal_catalyst_technical_ok(
    category: str,
    catalyst: str,
    data_signal: str = "",
    sym_data: Optional[dict] = None,
) -> Tuple[bool, str]:
    """多周期 K 线门槛 + 反转方向与 RSI / 7d 位置硬校验."""
    ok, reason = explore_catalyst_technical_ok(catalyst, data_signal, sym_data)
    if not ok:
        return ok, reason

    sane, sane_reason = _catalyst_pct_sane(catalyst, data_signal)
    if not sane:
        return False, sane_reason

    cat = (category or "").lower().strip()
    text = f"{catalyst or ''} {data_signal or ''}".lower()
    tech = (sym_data or {}).get("tech") or {}
    rsi = tech.get("rsi_14_1h")
    rsi_f: Optional[float] = None
    if rsi is not None:
        try:
            rsi_f = float(rsi)
        except (TypeError, ValueError):
            rsi_f = None

    b7h = tech.get("below_7d_high_pct")
    a7l = tech.get("above_7d_low_pct")
    try:
        near_high = b7h is not None and float(b7h) > -15
    except (TypeError, ValueError):
        near_high = False
    try:
        near_low = a7l is not None and float(a7l) <= 18
    except (TypeError, ValueError):
        near_low = False

    if cat == "top_reversal":
        rsi_top = rsi_f is not None and rsi_f >= 58
        if not rsi_top and not near_high:
            if "超买" not in text and "overbought" not in text:
                return False, (
                    f"顶部做空需 1h RSI≥58 或距 7d 高点<15% "
                    f"(RSI={rsi_f}, below_7d_high={b7h}%)"
                )
        if rsi_f is not None and rsi_f < 52 and not near_high:
            return False, f"顶部做空 1h RSI={rsi_f} 偏低且无近高结构"
        weak_top = ("涨多了", "涨幅过大", "已经涨", "24h涨") and not any(
            x in text for x in ("上影", "滞涨", "假突破", "连阴", "回落", "阻力")
        )
        if weak_top:
            return False, "顶部做空不能仅因涨幅大，须写明滞涨/上影/假突破等结构"

    if cat == "bottom_reversal":
        rsi_bot = rsi_f is not None and rsi_f <= 42
        if not rsi_bot and not near_low:
            if "超卖" not in text and "oversold" not in text:
                return False, (
                    f"底部做多需 1h RSI≤42 或距 7d 低点<18% "
                    f"(RSI={rsi_f}, above_7d_low={a7l}%)"
                )
        if rsi_f is not None and rsi_f > 48 and not near_low:
            return False, f"底部做多 1h RSI={rsi_f} 偏高且无近低结构"
        weak_bot = ("跌多了", "跌幅过大", "已经跌", "24h跌") and not any(
            x in text for x in ("下影", "止跌", "假跌破", "连阳", "反弹", "支撑")
        )
        if weak_bot:
            return False, "底部做多不能仅因跌幅大，须写明止跌/下影/假跌破等结构"

    return True, ""
