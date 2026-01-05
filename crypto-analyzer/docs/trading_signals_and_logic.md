# 交易信号和平仓逻辑说明

> 更新日期: 2026-01-05

---

## 一、开仓信号

所有开仓信号都需要**双周期确认**：15M信号 + 1H趋势方向一致才能开仓。

### 1. EMA金叉/死叉信号 (ema_cross)

**做多条件**：
- 15M周期：EMA9上穿EMA26（金叉）
- 1H周期：EMA9 > EMA26（多头趋势）

**做空条件**：
- 15M周期：EMA9下穿EMA26（死叉）
- 1H周期：EMA9 < EMA26（空头趋势）

**信号类型**: `ema_cross`

---

### 2. 持续趋势入场 (sustained_trend_entry)

错过金叉/死叉后，在趋势持续时仍可入场。

**做多条件**：
- 15M周期：EMA9 > EMA26
- 1H周期：EMA9 > EMA26（多头确认）
- 15M趋势强度：0.15% ≤ EMA差值百分比 ≤ 1.0%
- MA10/EMA10确认：EMA10 > MA10
- 价格确认：当前价 > EMA9
- 冷却时间：60分钟

**做空条件**：
- 15M周期：EMA9 < EMA26
- 1H周期：EMA9 < EMA26（空头确认）
- 15M趋势强度：0.15% ≤ EMA差值百分比 ≤ 1.0%
- MA10/EMA10确认：EMA10 < MA10
- 价格确认：当前价 < EMA9
- 冷却时间：60分钟

**信号类型**: `sustained_trend_entry`

---

### 3. 连续趋势信号 (sustained_trend)

检测15M和5M周期EMA差值同时放大。

**做多条件**：
- 15M周期：EMA9 > EMA26
- 5M周期：EMA9 > EMA26，且差值放大
- 1H周期：EMA9 > EMA26（方向确认）

**做空条件**：
- 15M周期：EMA9 < EMA26
- 5M周期：EMA9 < EMA26，且差值放大
- 1H周期：EMA9 < EMA26（方向确认）

**信号类型**: `sustained_trend`

---

### 4. 震荡反向信号 (oscillation_reversal)

检测价格在震荡区间反转。

**做多条件**：
- 15M周期：价格触及下轨后反弹
- 1H周期：EMA9 > EMA26（多头确认）

**做空条件**：
- 15M周期：价格触及上轨后回落
- 1H周期：EMA9 < EMA26（空头确认）

**信号类型**: `oscillation_reversal`

---

### 5. RSI超买超卖信号 (rsi_signal)

基于RSI极值的反转信号。

**做多条件**：
- 15M RSI ≤ 30（超卖）
- 1H周期：EMA9 > EMA26（多头确认）
- EMA强度 ≥ 0.15%

**做空条件**：
- 15M RSI ≥ 75（超买）
- 1H周期：EMA9 < EMA26（空头确认）

**信号类型**: `rsi_signal`

---

### 6. 5M快速金叉/死叉信号 (ema_5m_fast_cross)

5M周期的快速交叉信号。

**做多条件**：
- 5M周期：EMA9上穿EMA26
- 1H周期：EMA9 > EMA26（多头确认）

**做空条件**：
- 5M周期：EMA9下穿EMA26
- 1H周期：EMA9 < EMA26（空头确认）

**信号类型**: `ema_5m_fast_cross`

---

## 二、平仓逻辑

### 1. 硬止损 (hard_stop_loss)

固定止损，达到亏损百分比立即平仓。

**触发条件**：
- 做多：当前价 ≤ 入场价 × (1 - 止损%)
- 做空：当前价 ≥ 入场价 × (1 + 止损%)

**默认值**：2.5%（可配置 `stopLoss`）

**冷却时间**：无（立即触发）

**平仓代码**: `hard_stop_loss`

---

### 2. 最大止盈 (max_take_profit)

固定止盈，达到盈利目标立即平仓。

**触发条件**：
- 盈利百分比 ≥ 止盈目标

**默认值**：3.0%（可配置 `takeProfit`）

**平仓代码**: `max_take_profit|pnl:X.XX%`

---

### 3. 移动止盈 (trailing_take_profit)

跟踪最高盈利，回撤一定比例时平仓，锁定利润。

**触发条件**：
1. **激活条件**：最高盈利 ≥ 激活阈值
2. **平仓条件**：回撤百分比 ≥ 回撤阈值

**配置参数**：
- `trailingActivate`: 激活阈值（默认1.5%）
- `trailingCallback`: 回撤阈值（默认0.5%）

**计算公式**：
- 回撤% = 最高盈利% - 当前盈利%

**最小持仓时间**：15分钟（避免刚开仓就被平）

**冷却时间**：15分钟（可配置 `trailingCooldownMinutes`）

**平仓代码**: `trailing_take_profit|max:X.XX%|cb:X.XX%`

**示例**：
- 入场后盈利达到2.1%，移动止盈激活
- 价格回落，当前盈利1.6%
- 回撤 = 2.1% - 1.6% = 0.5% ≥ 回撤阈值0.5%
- 触发平仓，实际盈利1.6%

---

### 4. EMA差值收窄止盈 (ema_diff_narrowing_tp)

趋势减弱时止盈，EMA9和EMA26距离收窄。

**触发条件**：
- 当前EMA差值百分比 < 0.5%
- 当前盈利 ≥ 1.5%

**配置参数**：
- `emaDiffTakeProfit.threshold`: 差值阈值（默认0.5%）
- `emaDiffTakeProfit.minProfitPct`: 最小盈利（默认1.5%）

**平仓代码**: `ema_diff_narrowing_tp|diff:X.XX%|pnl:X.XX%`

---

### 5. 趋势减弱平仓 (trend_weakening)

开仓后30分钟，EMA差值连续3次减弱时平仓。

**触发条件**：
- 持仓时间 ≥ 30分钟
- 当前盈利 ≥ 1.0%
- EMA差值百分比 < 0.5%

**配置参数**：
- `trendWeakeningExit.enabled`: 是否启用（默认true）
- `trendWeakeningExit.emaDiffThreshold`: 差值阈值（默认0.5%）
- `trendWeakeningExit.minProfitPct`: 最小盈利（默认1.0%）

**平仓代码**: `trend_weakening|ema_diff:X.XX%|pnl:X.XX%`

---

### 6. 信号反转平仓

#### 6.1 金叉/死叉反转 (ema_direction_reversal_tp)

15M周期EMA交叉反向时平仓。

**触发条件**：
- 做多持仓：15M出现死叉（EMA9下穿EMA26）
- 做空持仓：15M出现金叉（EMA9上穿EMA26）
- 当前盈利 ≥ 1.0%

**最小盈利要求**：1.0%

**平仓代码**: `death_cross_reversal|pnl:X.XX%` 或 `golden_cross_reversal|pnl:X.XX%`

---

## 三、移动止损逻辑 (trailing_stop_loss)

动态调整止损价格，保护浮盈。

**激活条件**：
- 当前盈利 ≥ 激活阈值

**止损价格计算**：
- 做多：止损价 = 当前价 × (1 - 距离%)
- 做空：止损价 = 当前价 × (1 + 距离%)

**入场价保护**：
- 做多：止损价不能 ≥ 入场价
- 做空：止损价不能 ≤ 入场价

**最小移动阈值**：0.1%（避免频繁微调）

**配置参数**：
- `smartStopLoss.trailingStopLoss.enabled`: 是否启用
- `smartStopLoss.trailingStopLoss.activatePct`: 激活阈值（默认1.0%）
- `smartStopLoss.trailingStopLoss.distancePct`: 距离百分比（默认1.0%）

**冷却时间**：15分钟（可配置 `trailingCooldownMinutes`）

**示例**：
- 入场价 $100，当前价 $102（盈利2%）
- 激活阈值1%，距离1%
- 止损价 = $102 × (1 - 1%) = $100.98
- 价格继续上涨到$103，止损价更新为 $103 × 0.99 = $101.97

---

## 四、紧急停止机制

当连续硬止损时自动暂停交易。

**触发条件**：
- 最近3笔交易中有2笔是硬止损

**执行动作**：
1. 暂停所有策略（enabled = 0）
2. 平掉所有持仓
3. 等待4小时后可恢复

**实现位置**：`app/services/circuit_breaker.py`

---

## 五、关键配置参数总结

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `stopLoss` | 硬止损百分比 | 2.5% |
| `takeProfit` | 最大止盈百分比 | 3.0% |
| `trailingActivate` | 移动止盈激活 | 1.5% |
| `trailingCallback` | 移动止盈回撤 | 0.5% |
| `trailingCooldownMinutes` | 移动止盈冷却时间 | 15分钟 |
| `smartStopLoss.trailingStopLoss.enabled` | 移动止损启用 | false |
| `smartStopLoss.trailingStopLoss.activatePct` | 移动止损激活 | 1.0% |
| `smartStopLoss.trailingStopLoss.distancePct` | 移动止损距离 | 1.0% |
| `emaDiffTakeProfit.threshold` | EMA收窄阈值 | 0.5% |
| `emaDiffTakeProfit.minProfitPct` | EMA收窄最小盈利 | 1.5% |
| `trendWeakeningExit.emaDiffThreshold` | 趋势减弱阈值 | 0.5% |
| `trendWeakeningExit.minProfitPct` | 趋势减弱最小盈利 | 1.0% |

---

## 六、平仓优先级

系统按以下顺序检查平仓条件：

1. **硬止损** - 最高优先级，防止大额亏损
2. **移动止损** - 保护浮盈
3. **最大止盈** - 固定目标
4. **移动止盈** - 跟踪最高点回撤
5. **趋势减弱** - 趋势弱化时退出
6. **EMA收窄止盈** - 趋势即将反转
7. **信号反转** - 交叉信号反向

每个平仓条件都有独立的冷却时间和最小盈利要求，避免过早平仓。

---

## 七、价格使用说明

**关键修复（2026-01-05）**：
- ✅ 所有平仓决策使用**实时价格**（`get_current_price(use_realtime=True)`）
- ✅ 不再使用1小时前的K线收盘价
- ✅ 止盈平仓执行时检查是否变成亏损，如果是则取消平仓

---

## 八、实盘同步

模拟盘开仓后，可选同步到实盘（`syncLive=1`）：
- 实盘仓位 = 模拟盘仓位 × `liveQuantityPct`
- 移动止损、移动止盈**不同步**到实盘
- 实盘止损止盈由币安交易所自动执行
