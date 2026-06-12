"""主预测 prompt 构建 — 生产默认中文（build_predict_prompt）；英文仅 A/B 对照。"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from app.services.ai_big4_prompt import (
    BIG4_PROMPT_BLOCK_PREDICT,
    BIG4_PROMPT_BLOCK_PREDICT_EN,
    CONFIDENCE_ROW_BIG4_OK,
)
from app.services.ai_explore_prompt import (
    CATALYST_EVIDENCE_BLOCK,
    KLINE_1H_READING_BLOCK,
    _sl_tp_prompt_kwargs,
)

KLINE_1H_READING_BLOCK_EN = """
## 1h K-line reading (6~8h hold horizon)
- Primary: 1h **24-bar trend + last 4~8 bars**; cite volume, RSI, price vs 7d.
- 15m/1d auxiliary only.
"""

PREDICT_PROMPT_TEMPLATE_ZH = """你是超级交易大师. 预测每个币种在未来 **6~8 小时**内的方向走势概率（本笔计划 {hold_hours}h）.

**未来 6~8 小时持仓**, SL={sl_pct}, TP={tp_pct}, 杠杆 5x; 满 15min 后 Gemini 持仓顾问每 15min 可建议平仓.

以 **1h K 线为主**，结合量价、RSI、价格位置，判断能否在 6~8 小时内走向 {tp_pct} 目标, 或至少不触发 {sl_pct} SL.
不要选"只波动 1-2%"的标的.

# 全局市场环境 (含 big4_signal / market_regime / big4_trading_hint)
{global_context_json}
""" + BIG4_PROMPT_BLOCK_PREDICT + """
# 候选数据说明
每个 symbol 包含:
- kline_narrative: **1h 主证据** — 整体24根趋势 + 近4~8根明细; 15m/1d 仅辅助
""" + KLINE_1H_READING_BLOCK + """
- current_price / change_24h / quote_volume_24h
- funding_rate: 资金费率
- rsi_14_1h: 1h 级别 RSI
- above_7d_low_pct / below_7d_high_pct: 现价距 7 日极值距离

{symbols_data_json}

""" + CATALYST_EVIDENCE_BLOCK + """
# 任务
为列表中**每个** symbol 各输出一条 verdict (条数=symbol 数):
- category: 'bullish' / 'bearish' / 'skip' — **无 6~8h 方向优势必须 skip**（禁止强行多空）
- confidence: 0.0-1.0
- catalyst: **1h 四条必填**（整体趋势+近4~8根结构+RSI数字+量能词）, 至少 2 句
- data_signal: 一行量化摘要
- risk_note: 反向风险一句

# 开仓纪律（质量优先）
- **默认 skip**：1h 震荡、RSI 中性(45~55)无突破、量价矛盾 → skip
- bullish+bearish **合计不超过 6 个**；预期 skip **≥70%**
- conf=0.60~0.64 整轮**最多 2 个**；拿不准一律 skip
- 禁止为「每个币都要有方向」而硬给 0.60

# 置信度校准
| confidence | 需要的信号强度 |
|---|---|
| 0.75-1.00 | 1h 强趋势 + 量能确认 + RSI 顺向 + 距 7d 极值 ≥3% |
| 0.65-0.74 | 1h **24根整体** + **近4~8根** 同向 + RSI数字 + 量能词, """ + CONFIDENCE_ROW_BIG4_OK + """ |
| 0.60-0.64 | 四维齐但边际 — 整轮≤2，否则 skip |
| 0.00-0.59 | 震荡/矛盾 — **必须 skip**（多数币） |

# 判定原则 — 6~8 小时 / 1h 为主
- 仅趋势延续、费率背离+1h拐点、突破回踩（须在 catalyst 写清 1h+量价+RSI）
- Skip: 死猫反弹、震荡、仅24h%、Big4 整池偏见、空洞 catalyst

# 输出要求
仅一个合法 JSON, 不要 markdown 围栏.
质量优先: 无 1h 四维证据 → skip; 开仓≤6.

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

PREDICT_PROMPT_TEMPLATE_EN = """You predict each symbol over **6~8 hours** (plan {hold_hours}h). **1h K-lines primary** + volume/RSI/price.

SL={sl_pct}, TP={tp_pct}, 5x. Skip when no clear 6~8h edge — do not force bullish/bearish on every row.

# Global context
{global_context_json}
""" + BIG4_PROMPT_BLOCK_PREDICT_EN + """
# Each symbol row
- kline_narrative: 1h **24-bar trend + last 4-6 bars**; 15m/1d auxiliary
""" + KLINE_1H_READING_BLOCK_EN + """
- price, 24h change, volume, funding, rsi_14_1h, 7d distance fields

{symbols_data_json}

""" + CATALYST_EVIDENCE_BLOCK + """
# Task — one verdict per symbol; **skip default when unsure**
- catalyst: 1h trend + 4~8 bars + RSI number + volume (required for bullish/bearish)
- Max 6 bullish+bearish total; ≥70% skip expected

# Confidence
| 0.75+ | strong 1h + volume + RSI + 7d room |
| 0.65-0.74 | full 1h four-part block, """ + CONFIDENCE_ROW_BIG4_OK + """ |
| 0.60-0.64 | ≤2 per round |
| <0.60 | skip |

- No pool-wide bias from Big4; quality over quantity.

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
        **_sl_tp_prompt_kwargs(),
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
        **_sl_tp_prompt_kwargs(),
    )
