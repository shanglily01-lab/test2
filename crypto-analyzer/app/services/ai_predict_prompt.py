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
## 1h K 线读法（4 小时交易窗口）
- 主证据：1h **24 根整体趋势 + 最近 4~6 根结构**；必须引用成交量、RSI、距 7 日高低位距离。
- 15m/1d 仅作辅助，不得替代 1h 四维证据。
"""

PREDICT_PROMPT_TEMPLATE_ZH = """你是超级交易大师. 预测每个币种在未来 **4 小时**内的方向走势概率（本笔计划 {hold_hours}h）.

**未来 4 小时交易窗口**, SL={sl_pct}, TP={tp_pct}, 杠杆 5x; 满 15min 后 Gemini 持仓顾问每 15min 可建议平仓.

以 **1h K 线为主**，结合量价、RSI、价格位置，判断能否在 4 小时内走向 {tp_pct} 目标, 或至少不触发 {sl_pct} SL.
不要选"只波动 1-2%"的标的；但如果 1h 结构已经明确，不要因为不是完美形态而压成 skip.

# 全局市场环境 (含 big4_signal / market_regime / big4_trading_hint)
{global_context_json}
""" + BIG4_PROMPT_BLOCK_PREDICT + """
# 候选数据说明
每个 symbol 包含:
- kline_narrative: **1h 主证据** — 整体24根趋势 + 近4~6根明细; 15m/1d 仅辅助
""" + KLINE_1H_READING_BLOCK + """
- current_price / change_24h / quote_volume_24h
- funding_rate: 资金费率
- rsi_14_1h: 1h 级别 RSI
- above_7d_low_pct / below_7d_high_pct: 现价距 7 日极值距离

{symbols_data_json}

""" + CATALYST_EVIDENCE_BLOCK + """
# 任务
为列表中**每个** symbol 各输出一条 verdict (条数=symbol 数):
- category: 'bullish' / 'bearish' / 'skip' — 无 4h 方向优势才 skip（禁止强行多空，也禁止过度保守）
- confidence: 0.0-1.0
- catalyst: 写清 1h 整体趋势 + 近4~6根结构 + RSI数字(若有) + 量能词, 至少 2 句
- data_signal: 一行量化摘要
- risk_note: 反向风险一句

# 开仓纪律（质量优先，但需要识别可交易机会）
- skip：1h 震荡且无突破/破位、RSI 中性又没有结构变化、量价明显矛盾
- conf=0.60~0.64 是观察区，不会开仓；若 1h 结构清楚且 RSI/量能不反向，不要卡在 0.64
- 禁止为「每个币都要有方向」而硬给 0.65；也禁止把明确 B+ 结构全部压成 skip

# 置信度校准
| confidence | 需要的信号强度 |
|---|---|
| 0.75-1.00 | 1h 强趋势 + 量能确认 + RSI 顺向 + 距 7d 极值 ≥3% |
| 0.65-0.74 | 1h **24根整体**与**近4~6根**方向基本一致；RSI不极端反向；量能持平/放大/缩量回踩能解释方向, """ + CONFIDENCE_ROW_BIG4_OK + """ |
| 0.60-0.64 | 有方向但空间/量能/RSI仍有瑕疵 — 观察，不开仓 |
| 0.00-0.59 | 震荡/矛盾/边际不足 — **必须 skip**（多数币） |

# 判定原则 — 4 小时 / 1h 为主
- 仅趋势延续、费率背离+1h拐点、突破回踩（须在 catalyst 写清 1h+量价+RSI）
- Skip: 死猫反弹、震荡、仅24h%、Big4 整池偏见、空洞 catalyst

# 输出要求
仅一个合法 JSON, 不要 markdown 围栏.
质量优先: 空洞理由 → skip；1h 结构明确且 RSI/量能不反向 → 可给 0.65~0.72，不要过度保守.

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

PREDICT_PROMPT_TEMPLATE_EN = PREDICT_PROMPT_TEMPLATE_ZH


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
    """兼容旧调用名：现在一律返回中文主预测 prompt。"""
    return build_predict_prompt_zh(symbols_data, global_ctx)
