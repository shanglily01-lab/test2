"""主预测 prompt 构建 — 生产默认中文（build_predict_prompt）；英文仅 A/B 对照。"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from app.services.ai_big4_prompt import (
    BIG4_PROMPT_BLOCK_PREDICT,
    BIG4_PROMPT_BLOCK_PREDICT_EN,
    CONFIDENCE_ROW_BIG4_OK,
)
from app.services.ai_explore_prompt import CATALYST_EVIDENCE_BLOCK, KLINE_1H_READING_BLOCK

KLINE_1H_READING_BLOCK_EN = """
## 1h K-line reading
- 1h narrative: **24-bar overall trend + last 4-6 bars** detail; 15m/1d auxiliary.
"""

PREDICT_PROMPT_TEMPLATE_ZH = """你是超级交易大师. 预测每个币种在未来 2 小时内的方向走势概率.

持仓期 2 小时 (2h), SL=2%, TP=3%, 杠杆 5x; 满 15min 后 Gemini 持仓顾问每 15min 可建议平仓.

选中的币种需要能在 2 小时持仓期内尽量走向 3% 的涨幅/跌幅目标, 或至少抗住 2 小时不触发 2% SL.
不要选"只波动 1-2%"的标的.

# 全局市场环境 (含 big4_signal / market_regime / big4_trading_hint)
{global_context_json}
""" + BIG4_PROMPT_BLOCK_PREDICT + """
# 候选数据说明
每个 symbol 包含:
- kline_narrative: 1h 含 **整体24根趋势 + 近4~6根明细**; 15m/1d 辅助
""" + KLINE_1H_READING_BLOCK + """
- current_price / change_24h / quote_volume_24h
- funding_rate: 资金费率
- rsi_14_1h: 1h 级别 RSI
- above_7d_low_pct / below_7d_high_pct: 现价距 7 日极值距离

{symbols_data_json}

""" + CATALYST_EVIDENCE_BLOCK + """
# 任务
为**每个** symbol 各输出**恰好一条** verdict (verdicts 条数必须等于上方 symbol 数量):
- category: 'bullish' / 'bearish' / 'skip'
- confidence: 0.0-1.0
- catalyst: 判断依据, 必须引用具体数据, 至少 2 句
- data_signal: 最支持判断的关键数据点
- risk_note: 反向风险一句

# 置信度校准
| confidence | 需要的信号强度 |
|---|---|
| 0.80-1.00 | 1h 级别强趋势 + 成交量确认, 方向明确, 距极值还有 3%+ 空间 |
| 0.65-0.79 | 1h **24根整体** + **近4~6根** 同向 + 15m 不反向, """ + CONFIDENCE_ROW_BIG4_OK + """ |
| 0.60-0.64 | 结构尚可 — 可开但须 catalyst 含多周期+RSI |
| 0.00-0.59 | 方向模糊/震荡 — 跳过 |

# 判定原则 — 2 小时持仓
- Trend continuation / funding divergence with 1h reversal / breakout pullback (in catalyst).
- Skip dead-cat, chop, 24h%-only, Big4 pool bias.

# 输出要求
仅一个合法 JSON, 不要 markdown 围栏.
优先 quality 而非 quantity.

{{
  "summary_zh": "整体市场氛围 1-2 句",
  "verdicts": [
    {{
      "symbol": "FOO/USDT",
      "category": "bullish",
      "confidence": 0.72,
      "catalyst": "具体依据...",
      "data_signal": "...",
      "risk_note": "..."
    }}
  ]
}}
"""

PREDICT_PROMPT_TEMPLATE_EN = """You predict each symbol's probable direction over the next **2 hours** (USDT-M futures).

Hold 2h, SL=2%, TP=3%, 5x. Pick names that can reach ~3% move or hold through 2h without 2% SL.

# Global context
{global_context_json}
""" + BIG4_PROMPT_BLOCK_PREDICT_EN + """
# Each symbol row
- kline_narrative: 1h **24-bar trend + last 4-6 bars**; 15m/1d auxiliary
""" + KLINE_1H_READING_BLOCK_EN + """
- price, 24h change, volume, funding, rsi_14_1h, 7d distance fields

{symbols_data_json}

""" + CATALYST_EVIDENCE_BLOCK + """
# Task — exactly one verdict per input symbol (len(verdicts) == input count)
- category: bullish | bearish | skip
- confidence: 0.0-1.0
- catalyst: cite data (Chinese OK), ≥2 sentences worth of structure
- data_signal: strongest quant line
- risk_note: one counter-risk line

# Confidence
| 0.80+ | strong 1h trend + volume + room to 7d extreme |
| 0.65-0.79 | 24h + 4-6 bars aligned, """ + CONFIDENCE_ROW_BIG4_OK + """ |
| 0.60-0.64 | marginal — few names only |
| <0.60 | skip |

- No pool-wide bias from Big4 alone.
- Output ONE JSON; summary_zh in Chinese.

{{
  "summary_zh": "1-2 sentences Chinese",
  "verdicts": [
    {{
      "symbol": "FOO/USDT",
      "category": "bullish",
      "confidence": 0.72,
      "catalyst": "...",
      "data_signal": "...",
      "risk_note": "..."
    }}
  ]
}}
"""


def build_predict_prompt_zh(
    symbols_data: List[Dict[str, Any]],
    global_ctx: dict,
) -> str:
    """Chinese predict prompt (A/B benchmark only)."""
    return PREDICT_PROMPT_TEMPLATE_ZH.format(
        global_context_json=json.dumps(global_ctx, ensure_ascii=False, indent=2),
        symbols_data_json=json.dumps(symbols_data, ensure_ascii=False, indent=2, default=str),
    )


def build_predict_prompt(
    symbols_data: List[Dict[str, Any]],
    global_ctx: dict,
) -> str:
    """Production default: Chinese predict prompt."""
    return build_predict_prompt_zh(symbols_data, global_ctx)


def build_predict_prompt_en(
    symbols_data: List[Dict[str, Any]],
    global_ctx: dict,
) -> str:
    return PREDICT_PROMPT_TEMPLATE_EN.format(
        global_context_json=json.dumps(global_ctx, ensure_ascii=False, indent=2),
        symbols_data_json=json.dumps(symbols_data, ensure_ascii=False, indent=2, default=str),
    )
