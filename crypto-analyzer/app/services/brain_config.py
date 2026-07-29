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
BRAIN_SL_PCT = 3.0   # 百分点：3.0=3%（engine / paper_limit_entry 均做 pct/100）
BRAIN_TP_PCT = 5.0   # 百分点：5.0=5%
# 测试期：True=直接市价开仓（INV-BRAIN-06 限价防插针暂缓）；恢复限价时改 False
BRAIN_USE_MARKET_ENTRY = True

WIN_PROB_MIN = 0.55
# 开仓方向胜率须比反方向至少高这么多（百分点，0.05=5pp）
WIN_PROB_REL_EDGE = 0.05
WINRATE_LOOKBACK_DAYS = 7
WINRATE_FORWARD_HOURS = 4
WINRATE_MIN_SAMPLES = 20
WINRATE_SYMBOL_MIN_N = 5

# Playbook v1（§7.3.11）
TRADEABLE_PLAYBOOKS = frozenset({
    "A1", "A2", "B1", "B2", "B3", "B4", "C1", "C2", "C3", "C4",
})
FLAT_PLAYBOOKS = frozenset({"D1", "D2"})
PLAYBOOK_SIDE = {
    "A1": "LONG", "B1": "LONG", "B4": "LONG", "C2": "LONG", "C3": "LONG",
    "A2": "SHORT", "B2": "SHORT", "B3": "SHORT", "C1": "SHORT", "C4": "SHORT",
    "D1": "FLAT", "D2": "FLAT",
}

# 冲击判定：近 N 根 15m 跌/涨幅相对 ATR
CRASH_ATR_MULT = 2.5
CRASH_LOOKBACK_BARS = 8

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

# 轮询扫描（L0+L1 约 100+ 币）：每批 5 个、间隔 15 秒，发现机会立即下单
BRAIN_TICK_BATCH_SIZE = 5
BRAIN_TICK_INTERVAL_SECONDS = 15
BRAIN_SYMBOL_OPEN_COOLDOWN_MINUTES = 60
BRAIN_TICK_MAX_OPENS = 2  # 单批最多触发几次 DS 开仓
BRAIN_CLOSE_CHECK_EVERY_TICKS = 20  # ~5min 做一次 thesis 平仓检查
BRAIN_POOL_REFRESH_EVERY_TICKS = 40  # ~10min 刷新 L0/L1 池
# 开仓后最短持仓：期间不做 thesis/Big4 战略平（硬 SL/TP 仍生效）
BRAIN_CLOSE_MIN_HOLD_MINUTES = 45
# True=仅在 Playbook 方向明确反转时战略平；False=D1/D2 也可平（仍受最短持仓约束）
BRAIN_CLOSE_ONLY_ON_FLIP = True

# 兼容旧文档字段（全量轮已改为 tick）
BRAIN_SCAN_INTERVAL_HOURS = 2


def is_brain_source(source: str) -> bool:
    s = (source or "").strip().lower()
    return s in BRAIN_SOURCES or s.startswith("brain_")
