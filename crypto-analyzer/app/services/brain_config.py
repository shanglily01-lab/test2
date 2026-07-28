"""REQ-BRAIN 常量与 source 识别 — docs/REQUIREMENTS_LOGIC_ZH.md §7.3"""
from __future__ import annotations

BRAIN_SOURCE = "brain_swing"
BRAIN_SOURCES = frozenset({BRAIN_SOURCE, "brain_long", "brain_short"})

BRAIN_ACCOUNT_ID = 2
BRAIN_LEVERAGE = 5
BRAIN_MARGIN_USD = 500.0

# 持仓/限价（与系统 AI 默认对齐；落地可再调）
BRAIN_HOLD_HOURS = 4
BRAIN_LIMIT_TIMEOUT_MINUTES = 30
BRAIN_SL_PCT = 0.03
BRAIN_TP_PCT = 0.05

WIN_PROB_MIN = 0.55
WINRATE_LOOKBACK_DAYS = 7
WINRATE_FORWARD_HOURS = 4
WINRATE_MIN_SAMPLES = 20

# K 线窗口
BARS_1H_WEEK = 168
BARS_15M_DAY = 96
BARS_15M_WICK_7D = 7 * 24 * 4  # 672

# 插针：影线 > 实体 × 2；频繁 = 近7日 15m 插针占比 ≥ 阈值
WICK_BODY_RATIO = 2.0
WICK_FREQUENT_RATIO = 0.05

# Big4 疲软：|动量|低 且 相对成交量低
BIG4_SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT")
BIG4_WEAK_ABS_CHANGE_PCT = 0.8  # 近 6 根 1h 均 |涨跌| 低于此视为动量弱
BIG4_WEAK_REL_VOLUME = 0.55  # 近量 / 7日均量

# kill switch
BRAIN_ENABLED_KEY = "brain_swing_enabled"

# 调度
BRAIN_SCAN_INTERVAL_HOURS = 2


def is_brain_source(source: str) -> bool:
    s = (source or "").strip().lower()
    return s in BRAIN_SOURCES or s.startswith("brain_")
