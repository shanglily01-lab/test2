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
    KLINE_15M_READING_BLOCK,
    KLINE_1H_REFERENCE_BLOCK,
    KLINE_TIMEFRAME_EQUIV_BLOCK,
    _sl_tp_prompt_kwargs,
)

KLINE_15M_READING_BLOCK_EN = """
## 15m K-line reading (4h trading window)
- Primary: 15m **16 bars / 4h trend + last 4~6 bar structure**; volume required; RSI(1h) auxiliary only.
- 1h/1d background only; do not replace 15m evidence.
"""

PREDICT_TASK_BLOCK_EACH = """# 任务
为列表中**每个** symbol 各输出一条 verdict (条数=symbol 数):
- category: 'bullish' / 'bearish' / 'skip' — 只有高质量 4h 方向优势才给方向；不确定、边界、证据不完整一律 skip
- confidence: 0.0-1.0
- catalyst: 写清 **15m 价格趋势（定方向）** + **量价关系** + 近4~6根结构 + RSI(1h,可选), 至少 2 句
- data_signal: 一行量化摘要
- risk_note: 反向风险一句
"""

GEMINI_PREDICT_TASK_BLOCK_TOP = """# 任务
从列表中筛选未来 4 小时值得交易的少数高质量机会；宁可输出空数组，也不要输出边界机会。
不要为普通 skip 标的输出 verdict；若没有任何可交易机会，输出空数组 `verdicts: []`。
- category: 'bullish' / 'bearish' / 'skip' — 只有高质量 4h 方向优势才给方向；不确定、边界、证据不完整一律 skip
- confidence: 0.0-1.0
- catalyst: 写清 **15m 价格趋势（定方向）** + **量价关系** + 近4~6根结构 + RSI(1h,可选), 至少 2 句
- data_signal: 一行量化摘要
- risk_note: 反向风险一句
"""

DEEPSEEK_PREDICT_TASK_BLOCK_TOP = """# 任务
从候选列表中筛选未来 4 小时值得交易的少数高质量机会；宁可输出空数组，也不要输出边界机会。
不要为普通 skip 标的输出 verdict；若没有任何可交易机会，输出空数组 `verdicts: []`。
- category: 'bullish' / 'bearish'；不要输出普通 skip 标的。
- confidence: 0.0-1.0；只有 A 级结构才给 0.75+；0.70~0.74 视为观察区不要输出为可开仓。
- catalyst: 写清 **15m 价格趋势（定方向）** + **量价关系** + 近4~6根结构 + RSI(1h,可选), 至少 2 句。
- data_signal: 一行量化摘要，必须包含 15m 顺/反向根数或最近结构。
- risk_note: 反向风险一句；SHORT 必须说明是否接近 7d 低点/RSI 超卖。
"""

PREDICT_PROMPT_TEMPLATE_ZH = """你是超级交易大师. 目标不是给每个币种打标签，而是从候选中找出未来 **4 小时**内少数真正值得交易的方向机会（本笔计划 {hold_hours}h）.

**未来 4 小时交易窗口**, SL={sl_pct}, TP={tp_pct}, 杠杆 5x; 满 15min 后持仓顾问每 15min 可建议平仓.

以 **15m 量价 + 价格变化趋势为核心判据**（16根=4h），**趋势定方向**；RSI(1h,辅证)、1h/24h 不能单独定方向.
**1h 完整保留**（narrative 近24根 + K线），其中近4根1h≈16根15m，用于交叉验证与更长背景 — **不作核心 verdict**.

# 全局市场环境 (含 big4_signal / market_regime / big4_trading_hint)
{global_context_json}
""" + BIG4_PROMPT_BLOCK_PREDICT + """
# 候选数据说明
""" + KLINE_TIMEFRAME_EQUIV_BLOCK + """
每个 symbol 包含:
- kline_narrative.**15m** (**核心判据**, 16根=4h)
- kline_narrative.**1h** (**保留**, 24根更长背景；近4根≈同窗口16根15m，仅交叉验证)
""" + KLINE_15M_READING_BLOCK + KLINE_1H_REFERENCE_BLOCK + """
- current_price / change_24h / quote_volume_24h
- funding_rate: 资金费率
- rsi_14_1h: 1h 级别 RSI（**辅证**）
- above_7d_low_pct / below_7d_high_pct: 现价距 7 日极值距离

{symbols_data_json}

""" + CATALYST_EVIDENCE_BLOCK + """
{task_block}

# DeepSeek 预测校准（必须遵守）
- 当前若 market_regime 为低波动/盘整：默认认为 15m 信号噪声偏高，只有 **15m 16根整体 + 最近4~6根 + 量能** 三者同向时才可输出。
- 不要因为候选按 24h 波动排序就优先选择暴涨暴跌币；24h 涨跌幅只用于风险说明，不能作为择优依据。
- 0.70~0.74 是观察区，**不会开仓**；若存在 RSI 极端、贴近 7d 高/低点、量价背离、近端反向 K 线，必须剔除。
- bearish/SHORT 额外要求：不能只写“下跌延续”。若 RSI(1h)<35 或接近 7d 低点，必须有 15m 缩量反弹失败/放量破位继续下杀，否则不要输出。
- bullish/LONG 额外要求：不能只写“强势上涨”。若 RSI(1h)>70 或贴近 7d 高点，必须有放量突破后回踩不破/缩量回踩确认，否则不要输出。
- 允许 `verdicts: []`；不要为了输出机会而放宽证据标准。

# 先打分再输出（必须内化执行，不需要额外字段）
每个 symbol 先从 10 分扣分，低于 9 分不得输出 bullish/bearish：
- 15m 16根整体方向不清或与 category 不一致：-4
- 最近4~6根没有同向延续/突破回踩/反弹失败之一：-3
- 量能不支持方向或有量价背离：-3
- LONG 贴近7d高点或 RSI(1h)>70 且无回踩确认：-2
- SHORT 贴近7d低点或 RSI(1h)<35 且无反弹失败确认：-2
- catalyst 主因是 24h涨跌幅、资金费率、Big4 或“涨多/跌多”：不得输出

# 开仓纪律（准确率优先）
- skip：15m 震荡且无突破/破位、量价明显矛盾、只有24h涨跌幅或资金费率
- conf=0.60~0.74 是观察区，不会开仓；可交易须 **≥0.75**
- 禁止为「每个币都要有方向」而硬给 0.75；边界单不输出
- **禁止**仅写 1h 结构、无 15m（后端会拒）；后端另做真实 15m OHLC 方向复核

# 置信度校准
| confidence | 需要的信号强度 |
|---|---|
| 0.75-1.00 | 打分≥9；15m 强趋势 + 量能确认 + RSI(1h)顺向 + 距 7d 极值 ≥3% |
| 0.70-0.74 | 打分≥8 但仍有瑕疵 — **观察，不开仓**, """ + CONFIDENCE_ROW_BIG4_OK + """ |
| 0.60-0.69 | 有方向但空间/量能/RSI 仍有瑕疵 — 观察，不开仓 |
| 0.00-0.59 | 震荡/矛盾/边际不足 — **必须 skip**（多数币） |

# 判定原则 — 4 小时 / **15m 量价 + 趋势定方向**
- **方向**：15m 价格变化趋势决定 bullish/bearish；须量价配合
- 仅趋势延续、费率背离+**15m**拐点、突破回踩（须在 catalyst 写清 15m 趋势+量价）
- Skip: 死猫反弹、震荡、仅24h%、Big4 整池偏见、空洞 catalyst、**仅1h无15m**、量价背离

# 输出要求
仅一个合法 JSON, 不要 markdown 围栏.
准确率优先: 空洞理由 → skip；只有 **15m** 结构明确、量能支持、RSI/空间不过热过冷且打分≥9 的机会，才可给 0.75+。

{{
  "summary_zh": "整体市场氛围 1-2 句",
  "verdicts": [
    {{
      "symbol": "FOO/USDT",
      "category": "bullish",
      "confidence": 0.76,
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
    task_block: str = PREDICT_TASK_BLOCK_EACH,
) -> str:
    """Chinese predict prompt (A/B benchmark only)."""
    return PREDICT_PROMPT_TEMPLATE_ZH.format(
        global_context_json=json.dumps(global_ctx, ensure_ascii=False, indent=2),
        symbols_data_json=json.dumps(symbols_data, ensure_ascii=False, indent=2, default=str),
        task_block=task_block,
        **_sl_tp_prompt_kwargs(),
    )


def build_predict_prompt(
    symbols_data: List[Dict[str, Any]],
    global_ctx: dict,
) -> str:
    """Production default: top opportunities only to avoid long-list truncation."""
    return build_predict_prompt_zh(
        symbols_data,
        global_ctx,
        task_block=DEEPSEEK_PREDICT_TASK_BLOCK_TOP,
    )


def build_gemini_predict_prompt(
    symbols_data: List[Dict[str, Any]],
    global_ctx: dict,
) -> str:
    """Gemini predict: top opportunities only (avoid truncated JSON)."""
    return build_predict_prompt_zh(
        symbols_data,
        global_ctx,
        task_block=GEMINI_PREDICT_TASK_BLOCK_TOP,
    )


def build_predict_prompt_en(
    symbols_data: List[Dict[str, Any]],
    global_ctx: dict,
    task_block: str = PREDICT_TASK_BLOCK_EACH,
) -> str:
    """兼容旧调用名：现在一律返回中文预测 prompt。"""
    return build_predict_prompt_zh(symbols_data, global_ctx, task_block=task_block)
