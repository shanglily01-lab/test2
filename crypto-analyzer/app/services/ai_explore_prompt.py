"""Gemini / DeepSeek 探索 — 共用 prompt (v2 技术面强制).

核心: verdict 必须来自 K 线形态、成交量、RSI、多周期结构;
禁止仅用 24h 涨跌幅、资金费率、宏观/Big4 做主观多空.
"""
from __future__ import annotations

from typing import Tuple

from app.services.ai_big4_prompt import BIG4_PROMPT_BLOCK_EXPLORE

_KLINE_MARKERS = (
    "1h", "15m", "1d", "连阳", "连阴", "形态", "放量", "缩量",
    "突破", "回踩", "震荡", "上攻", "下杀", "k线", "k 线",
)
_WEAK_ONLY_PHRASES = (
    "已经涨", "已经跌", "涨幅过大", "跌幅过大", "涨多了", "跌多了",
    "24h涨", "24h跌", "24h 涨", "24h 跌",
)


def explore_catalyst_technical_ok(catalyst: str, data_signal: str = "") -> Tuple[bool, str]:
    """开仓前校验: catalyst 须含 K 线结构 + RSI, 不能仅靠涨跌幅/费率."""
    text = f"{catalyst or ''} {data_signal or ''}"
    low = text.lower()
    has_rsi = "rsi" in low
    has_kline = any(m in text for m in _KLINE_MARKERS)

    if not has_kline or not has_rsi:
        return False, "catalyst 须引用 kline_narrative(1h/15m/1d) 并写明 RSI 数值"

    weak_only = any(p in text for p in _WEAK_ONLY_PHRASES) and not any(
        m in text for m in ("连阳", "连阴", "突破", "回踩", "形态", "放量", "缩量")
    )
    funding_only = ("资金费" in text or "funding" in low) and not any(
        m in text for m in ("背离", "拐点", "连阳", "连阴", "突破", "形态")
    )
    if weak_only:
        return False, "主因仅为 24h 涨跌幅, 缺少 K 线结构"
    if funding_only:
        return False, "主因仅为资金费率, 缺少 K 线+RSI 确认"
    return True, ""

# catalyst 必须引用的字段类型 (写进 prompt 供 LLM 自检)
CATALYST_EVIDENCE_BLOCK = """
# catalyst 写法 (强制 — 违反则 confidence 不得超过 0.45, 应标 skip)

## 你必须做的 (每条 bullish/bearish 至少满足 3 项, 并在 catalyst 原文写出)

1. **kline_narrative** — 引用具体周期与形态 (必填至少 1 个周期):
   - 例: 「1h 形态: 连续3阳放量上攻」「15m 震荡、1h 跌破前低」
   - 必须来自数据里的 `kline_narrative.1h` / `15m` / `1d`, 不得空泛说「走势偏强」

2. **RSI 数值** — 写出 `tech.rsi_14_1h` 具体数字及含义:
   - 例: 「RSI 1h=58 未超买仍有空间」「RSI=72 价格仍跌 → 顶背离」

3. **成交量** — 来自 kline_narrative 里的放量/缩量描述:
   - 例: 「突破时量放大、回踩缩量」「近几根量萎缩」

4. **结构位置** — `tech.above_7d_low_pct` / `below_7d_high_pct`:
   - 例: 「距 7d 高点还有 4% 空间够 TP=5%」

5. **多周期共振** — 至少 2 个周期 (1h+15m 或 1d+1h) 方向一致才给 confidence≥0.65

## 仅可作辅助、不能单独作为开仓理由 (写了这些而没有 K 线/RSI → 必须 skip)

- `change_24h` 涨跌幅 (「已经涨了 15% 所以做空」= 无效)
- `current_rate` 资金费率 (除非配合**价格与费率背离**且 1h K 线已拐点, 并写明背离逻辑)
- Big4 / BTC 大盘一句话 (只能写在 risk_note, 不能作为 catalyst 主因)
- triggers 标签本身 (「异动入选」不是技术理由)

## data_signal 字段

- **一行**, 只写最强的一条可量化事实, 例: `1h:4连阳+量放大, RSI=58, 距7d高4%`
- 禁止只写 `24h+12%` 或 `资金费+0.05%`

## 自检 (输出前在心里过一遍)

- catalyst 里有没有出现「1h」「15m」「1d」「RSI」「量」中的至少两个?
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
- catalyst: 技术依据, **必须引用 kline_narrative + RSI 数值**, 至少 2 句
- data_signal: 一行量化摘要 (见上)
- risk_note: 反向风险一句 (可提 Big4/BTC)

# 置信度校准 (无技术面支撑则不得给高分)

| confidence | 条件 (全部满足才可给该档) |
|---|---|
| 0.80-1.00 | 1h+15m 形态同向 + 成交量确认 + RSI 支持方向 + 距 7d 极值仍有 ≥3% 空间 |
| 0.65-0.79 | 1h 趋势明确 + 15m 不反向 + catalyst 含具体 K 线形态与 RSI 数字 |
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
优先 quality: 无扎实 K 线+RSI 则 skip; 宁可 2-3 个高质量 verdict, 不要凑数.

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
