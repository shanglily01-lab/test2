"""Gemini / DeepSeek 探索 — 共用 prompt (v2 技术面强制).

核心: verdict 必须来自 K 线形态、成交量、RSI、多周期结构;
禁止仅用 24h 涨跌幅、资金费率、宏观/Big4 做主观多空.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from app.services.ai_big4_prompt import BIG4_PROMPT_BLOCK_EXPLORE

# 送入 LLM 的 symbol 上限 (全池 ~200 时 prompt/output 都会爆)
EXPLORE_LLM_MAX_SYMBOLS = 50
EXPLORE_LLM_MAX_OUTPUT_TOKENS = 8192

_KLINE_MARKERS = (
    "1h", "15m", "1d", "连阳", "连阴", "形态", "放量", "缩量",
    "突破", "回踩", "震荡", "上攻", "下杀", "k线", "k 线",
)
_STRUCTURE_MARKERS = ("连阳", "连阴", "阳线", "阴线", "放量", "缩量", "突破", "回踩", "形态")
_WEAK_ONLY_PHRASES = (
    "已经涨", "已经跌", "涨幅过大", "跌幅过大", "涨多了", "跌多了",
    "24h涨", "24h跌", "24h 涨", "24h 跌",
)
_TF_RE = re.compile(r"\b(1h|15m|1d)\b", re.I)
_KBAR_COUNT_RE = re.compile(r"\d+\s*[阳阴]")
_PCT_RE = re.compile(r"[-+]?\d+(\.\d+)?%")


def _multi_tf_kline_ok(text: str) -> bool:
    """至少两个周期 (1h/15m/1d), 或单周期但有具体 K 线结构描述."""
    low = text.lower()
    tfs = {tf for tf in ("1h", "15m", "1d") if tf in low}
    if len(tfs) >= 2:
        return True
    return bool(tfs) and any(m in text for m in _STRUCTURE_MARKERS)


def _quantified_technical_ok(text: str) -> bool:
    """RSI 或 EMA 数值、7d 距离、阳阴根数等可量化描述 (不要求字面 RSI)."""
    low = text.lower()
    if "rsi" in low:
        return True
    if "ema" in low and (_PCT_RE.search(text) or re.search(r"ema\s*[\d(]", low)):
        return True
    if ("7d" in low or "7 d" in low) and (
        "高点" in text or "低点" in text or "above_7d" in low or _PCT_RE.search(text)
    ):
        return True
    if _KBAR_COUNT_RE.search(text):
        return True
    return False


def explore_catalyst_technical_ok(
    catalyst: str,
    data_signal: str = "",
    sym_data: Optional[dict] = None,
) -> Tuple[bool, str]:
    """开仓前校验: 多周期 K 线 + 可量化技术位; 拦截仅涨跌幅/费率."""
    text = f"{catalyst or ''} {data_signal or ''}"
    low = text.lower()

    if not _multi_tf_kline_ok(text):
        return False, "catalyst 须写明至少两个周期(1h/15m/1d)的 K 线形态"

    if not _quantified_technical_ok(text):
        rsi = (sym_data or {}).get("tech") or {}
        rsi_v = rsi.get("rsi_14_1h")
        if rsi_v is not None:
            return False, f"catalyst 须含 RSI 1h={rsi_v} 或 EMA/7d/阳阴根数等量化描述"
        return False, "catalyst 须含 EMA/7d 距离/阳阴根数或 RSI 等量化技术位"

    weak_only = any(p in text for p in _WEAK_ONLY_PHRASES) and not any(
        m in text for m in _STRUCTURE_MARKERS
    )
    funding_only = ("资金费" in text or "funding" in low) and not any(
        m in text for m in ("背离", "拐点", "连阳", "连阴", "突破", "形态")
    )
    if weak_only:
        return False, "主因仅为 24h 涨跌幅, 缺少 K 线结构"
    if funding_only:
        return False, "主因仅为资金费率, 缺少 K 线结构确认"
    return True, ""


def prepare_universe_for_llm(
    universe: dict,
    max_symbols: int = EXPLORE_LLM_MAX_SYMBOLS,
) -> Tuple[List[dict], Dict[str, Any]]:
    """按 |24h 涨跌| 取 TOP N 送 LLM, 避免 15 万字符 prompt + JSON 被 max_tokens 截断."""
    items = sorted(
        universe.values(),
        key=lambda x: abs(float(x.get("change_24h") or 0)),
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
    }
    if meta["llm_symbols_truncated"]:
        logger.info(
            f"[Explore] LLM 候选截断: 全池 {meta['universe_total']} → "
            f"送模 {meta['llm_symbol_count']} (|24h涨跌| TOP{max_symbols})"
        )
    return selected, meta


def build_explore_prompt(
    universe: dict,
    global_ctx: dict,
    historical_stats: dict,
) -> Tuple[str, Dict[str, Any]]:
    """紧凑 JSON 拼 prompt, 降低 token."""
    universe_list, meta = prepare_universe_for_llm(universe)
    compact = {"ensure_ascii": False, "separators": (",", ":"), "default": str}
    prompt = EXPLORE_PROMPT_TEMPLATE.format(
        global_context_json=json.dumps(global_ctx, **compact),
        universe_json=json.dumps(universe_list, **compact),
        historical_stats_json=json.dumps(historical_stats, **compact),
        llm_universe_note=(
            f"本列表为全池 {meta['universe_total']} 个中按 |24h涨跌| 取 TOP {meta['llm_symbol_count']}，"
            f"仅对这些 symbol 输出 verdict (其余忽略)."
        ),
    )
    return prompt, meta


def parse_explore_llm_json(text: str, tag: str = "Explore") -> Tuple[Optional[dict], Optional[str]]:
    """解析 LLM JSON; 截断时尝试抢救已完整的 verdict 对象."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()

    err: Optional[str] = None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        err = str(e)
        parsed = _salvage_truncated_explore_json(raw)
        if parsed is None:
            logger.error(f"[{tag}] JSON 解析失败: {e}; raw[:500]={raw[:500]}")
            return None, err
        logger.warning(f"[{tag}] JSON 被截断, 已抢救 {len(parsed.get('verdicts') or [])} 条 verdict: {e}")

    # LLM 偶发返回顶层数组 [{symbol,...}, ...] 而非 {summary_zh, verdicts}
    if isinstance(parsed, list):
        logger.warning(f"[{tag}] JSON 顶层为 array, 已包装为 verdicts ({len(parsed)} 项)")
        parsed = {"summary_zh": "", "verdicts": parsed}
    elif not isinstance(parsed, dict):
        logger.error(f"[{tag}] JSON 顶层类型异常: {type(parsed).__name__}")
        return None, f"unexpected JSON type: {type(parsed).__name__}"

    raw_verdicts = parsed.get("verdicts")
    if isinstance(raw_verdicts, list):
        verdicts = [v for v in raw_verdicts if isinstance(v, dict)]
        dropped = len(raw_verdicts) - len(verdicts)
        if dropped:
            logger.warning(f"[{tag}] 丢弃 {dropped} 条非 object verdict")
        parsed["verdicts"] = verdicts
    else:
        parsed["verdicts"] = []
    if "summary_zh" not in parsed:
        parsed["summary_zh"] = ""
    return parsed, err


def _salvage_truncated_explore_json(text: str) -> Optional[dict]:
    """从截断响应中提取完整的 verdict 对象."""
    summary_m = re.search(r'"summary_zh"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    summary_zh = summary_m.group(1) if summary_m else ""

    verdicts: List[dict] = []
    obj_re = re.compile(
        r'\{\s*"symbol"\s*:\s*"([^"]+)"\s*,\s*"category"\s*:\s*"([^"]+)"\s*,'
        r'\s*"confidence"\s*:\s*([\d.]+)\s*,\s*"catalyst"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,'
        r'\s*"data_signal"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"risk_note"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}',
        re.DOTALL,
    )
    for m in obj_re.finditer(text):
        try:
            conf = float(m.group(3))
        except (TypeError, ValueError):
            conf = 0.0
        verdicts.append({
            "symbol": m.group(1),
            "category": m.group(2),
            "confidence": conf,
            "catalyst": m.group(4),
            "data_signal": m.group(5),
            "risk_note": m.group(6),
        })
    if not verdicts:
        return None
    return {"summary_zh": summary_zh, "verdicts": verdicts}


# catalyst 必须引用的字段类型 (写进 prompt 供 LLM 自检)
CATALYST_EVIDENCE_BLOCK = """
# catalyst 写法 (强制 — 违反则 confidence 不得超过 0.45, 应标 skip)

## 你必须做的 (每条 bullish/bearish 至少满足 3 项, 并在 catalyst 原文写出)

1. **kline_narrative** — 引用具体周期与形态 (必填至少 1 个周期):
   - 例: 「1h 形态: 连续3阳放量上攻」「15m 震荡、1h 跌破前低」
   - 必须来自数据里的 `kline_narrative.1h` / `15m` / `1d`, 不得空泛说「走势偏强」

2. **量化指标 (至少一项, 优先 RSI)** — 若 `tech.rsi_14_1h` 有值, **必须**写出 RSI 数字:
   - 例: 「RSI 1h=58 未超买」「RSI=72 价跌 → 顶背离」
   - 若无 RSI 或作补充: 写明 **EMA9 数值**、**7d 高低点距离%**、或 **N阳M阴** 根数

3. **成交量** — 来自 kline_narrative 里的放量/缩量描述:
   - 例: 「突破时量放大、回踩缩量」「近几根量萎缩」

4. **结构位置** — `tech.above_7d_low_pct` / `below_7d_high_pct`:
   - 例: 「距 7d 高点还有 4% 空间够 TP=5%」

5. **多周期共振** — 至少 2 个周期 (1h+15m 或 1d+1h) 方向一致才给 confidence≥0.65

## 仅可作辅助、不能单独作为开仓理由 (写了这些而没有多周期 K 线结构 → 必须 skip)

- `change_24h` 涨跌幅 (「已经涨了 15% 所以做空」= 无效)
- `current_rate` 资金费率 (除非配合**价格与费率背离**且 1h K 线已拐点, 并写明背离逻辑)
- Big4 / BTC 大盘一句话 (只能写在 risk_note, 不能作为 catalyst 主因)
- triggers 标签本身 (「异动入选」不是技术理由)

## data_signal 字段

- **一行**, 只写最强的一条可量化事实, 例: `1h:4连阳+量放大, RSI=58, 距7d高4%`
- 禁止只写 `24h+12%` 或 `资金费+0.05%`

## 自检 (输出前在心里过一遍)

- catalyst 里有没有 **至少两个周期** (1h/15m/1d) + **RSI 或 EMA/7d/阳阴根数**?
- 若删掉 change_24h 和 funding_rate 后, 理由还成立吗? 不成立 → skip
"""

EXPLORE_PROMPT_TEMPLATE = """你是超级交易大师. 持仓期 4 小时 (4h), SL=3%, TP=5%, 杠杆 5x, 不做任何中途干预.

你的任务是: 基于**个股技术面**判断未来 4 小时内是否值得持有; 不是复述行情、不是宏观押注.

# 仓位设置 (供你理解容错空间)
- 杠杆 5x, 名义本金 ~2500U, SL=3% 价格跌幅, TP=5% 涨幅
- 4 小时到期强制平仓 — 方向须能在 4h 内走出约 5% 或至少不触发 3% SL
- 不要选「只会小幅波动 1-2%」的标的

# 全局市场环境 (宏观仅背景, 见 big4_trading_hint)
{global_context_json}
""" + BIG4_PROMPT_BLOCK_EXPLORE + """
# 历史表现 (校准尺度, 勿用宏观理由开仓)
{historical_stats_json}

# 候选数据说明
{llm_universe_note}
每个 symbol 包含:
- triggers: 入选原因 (不能当作 catalyst)
- current_price / change_24h / quote_volume_24h: 涨跌幅**不能单独**作为多空依据
- current_rate: 资金费率 — 仅在与 K 线背离同时出现时作辅助
- kline_narrative: **1d / 1h / 15m 自然语言 K 线形态与成交量** (主证据)
- tech.rsi_14_1h, tech.above_7d_low_pct, tech.below_7d_high_pct

{universe_json}

""" + CATALYST_EVIDENCE_BLOCK + """
# 任务
为**每个** symbol 标注:
- category: 'bullish' / 'bearish' / 'skip'
- confidence: 0.0-1.0
- catalyst: 技术依据, **多周期 kline_narrative + RSI(有则必填) 或 EMA/7d 量化**, 至少 2 句
- data_signal: 一行量化摘要 (见上)
- risk_note: 反向风险一句 (可提 Big4/BTC)

# 置信度校准 (无技术面支撑则不得给高分)

| confidence | 条件 (全部满足才可给该档) |
|---|---|
| 0.80-1.00 | 1h+15m 形态同向 + 成交量确认 + RSI 支持方向 + 距 7d 极值仍有 ≥3% 空间 |
| 0.65-0.79 | 1h 趋势明确 + 15m 不反向 + catalyst 含多周期 K 线 + RSI/EMA/7d 量化 |
| 0.50-0.64 | 仅单周期(1h)有方向, 15m 中性 — **最多开 1-2 个** |
| 0.00-0.49 | 无清晰 K 线结构 / 仅涨跌幅或费率 / 震荡 — **必须 skip** |

# 判定原则 — 4 小时持仓

## ✅ 可给 bullish/bearish (须技术面)

**A. 趋势延续** — 1h 连续同向 K 线 + 15m 同向 + 放量 (写在 catalyst 里)
**B. 费率背离** — 费率极端 + RSI 反向 + **1h K 线已出现拐点形态** (三者缺一不可)
**C. 突破回踩** — 突破时放量、回踩缩量 (引用 narrative 原文)

## ❌ 必须 skip

**D. 死猫反弹** — 24h ±20%+ 且量异常, 无 1h 结构反转
**E. 震荡** — 1h/15m narrative 均为震荡、量萎缩
**F. 超跌抄底 / 超涨摸顶** — 无 1h 拐点 K 线, 只有涨跌幅
**G. 主观宏观** — catalyst 主因是「大盘」「Big4」「涨多了」而无 K 线/RSI
**H. Big4 单边偏见** — 不得整池只空或只多; 逐币独立技术面

# 反例 (禁止模仿)

BAD — 仅复述行情 (应 skip, confidence≤0.35):
{{
  "symbol": "ABC/USDT",
  "category": "bearish",
  "confidence": 0.70,
  "catalyst": "24h 已涨 18%, 资金费率 +0.06% 多头拥挤, 涨幅过大应回调",
  "data_signal": "24h+18%",
  "risk_note": ""
}}

GOOD — 技术面 (可 bullish 0.72):
{{
  "symbol": "NEAR/USDT",
  "category": "bullish",
  "confidence": 0.72,
  "catalyst": "1h 形态: 连续4阳放量上攻; 15m 沿强势整理未破. RSI 1h=58 未超买. 距7d高点 4% 空间够 TP. 资金费 +0.003% 正常(辅助)",
  "data_signal": "1h:4连阳+量放大, RSI=58, 距7d高4%",
  "risk_note": "BTC 若急跌可能拖累"
}}

GOOD — skip:
{{
  "symbol": "XRP/USDT",
  "category": "skip",
  "confidence": 0.30,
  "catalyst": "1h/15m narrative 均为震荡, 量持平; RSI=52 中性. 虽 24h+3% 但无 4h 可交易结构",
  "data_signal": "双周期震荡, RSI=52",
  "risk_note": ""
}}

# 输出要求
**仅** 一个合法 JSON, 不要 markdown 围栏.
优先 quality: 无多周期 K 线+量化指标则 skip; **仅对列表内 symbol 给 verdict**, 宁可 5-15 条高质量, 不要凑满全表.

{{
  "summary_zh": "整体技术面氛围 1-2 句 (勿写只做多/只做空)",
  "verdicts": [
    {{
      "symbol": "FOO/USDT",
      "category": "bullish",
      "confidence": 0.72,
      "catalyst": "必须含 kline 周期+RSI 数字...",
      "data_signal": "一行量化",
      "risk_note": "..."
    }}
  ]
}}
"""
