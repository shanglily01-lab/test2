# 交易信号优化说明

> 更新日期: 2026-01-01
> 影响文件: `app/services/strategy_executor_v2.py`

## 概述

本次更新优化了金叉/死叉和RSI信号的触发机制和开仓方式，解决了信号触发率低和成交困难的问题。

---

## 优化1: 降低金叉/死叉最小强度要求

### 问题分析

**现象**: 金叉/死叉开仓记录很少（仅7次）

**根本原因**:
- 金叉/死叉刚发生时，EMA9和EMA26刚穿越，差值极小（接近0%）
- 之前要求EMA差值 ≥ 0.05%的最小强度
- 刚发生穿越时很难满足这个要求

**实际案例**:
```
UNI/USDT:  EMA差值 0.058543% ✅ 刚好通过
LINK/USDT: EMA差值 0.150845% ✅ 通过
很多 < 0.05% 的金叉被过滤 ❌
```

### 解决方案

为金叉/死叉设置**独立的、更低的**最小强度阈值：

```python
# 之前：使用通用强度 0.05%
min_strength = self.MIN_SIGNAL_STRENGTH  # 0.05%

# 现在：金叉/死叉独立阈值 0.01%
crossover_min_strength = 0.01  # 默认
# 支持策略配置自定义
crossover_min_strength = strategy.get('minSignalStrength', {}).get('crossover', 0.01)
```

### 配置示例

```json
{
  "minSignalStrength": {
    "ema9_26": 0.05,      // 普通信号强度要求
    "crossover": 0.01     // 金叉/死叉强度要求（可选）
  }
}
```

### 预期效果

- ✅ 更多金叉/死叉信号能够触发
- ✅ 不会错过刚发生的穿越信号
- ✅ 保留0.01%基本阈值过滤噪音

---

## 优化2: 金叉/死叉改为市价单开仓

### 问题分析

**现象**: 金叉/死叉信号触发但限价单成交不了

**根本原因**:

1. **金叉做多场景**:
   - 金叉发生 → EMA9穿过EMA26向上 → 上涨趋势开始
   - 限价单: 市价 - 0.6% (等待回调)
   - 但趋势向上，价格可能继续上涨，**不回调到限价** ❌

2. **死叉做空场景**:
   - 死叉发生 → EMA9穿过EMA26向下 → 下跌趋势开始
   - 限价单: 市价 + 0.6% (等待反弹)
   - 但趋势向下，价格可能继续下跌，**不反弹到限价** ❌

### 解决方案

金叉/死叉/反转信号**强制使用市价单**：

```python
# _do_open_position 方法中
if signal_type in ['golden_cross', 'death_cross', 'reversal_cross']:
    limit_price = current_price  # 使用市价
    logger.info(f"⚡ {symbol} {signal_type}信号使用市价开仓")
```

### 理由

- 金叉/死叉是**趋势反转的强信号**，时机很重要
- 等待回调可能错过最佳入场点
- 穿越发生后，价格往往会沿新趋势快速移动

### 预期效果

- ✅ 金叉/死叉成交率接近100%
- ✅ 不会错过趋势反转的最佳入场时机
- ✅ 执行更及时，减少滑点风险

---

## 优化3: RSI信号改为市价单开仓

### 问题分析

**现象**: RSI信号使用限价单导致成交困难

**根本原因**:

1. **RSI超卖做多**:
   - RSI < 30 (已极度超卖) + EMA强度上升
   - 限价单: 市价 - 0.6% (等待继续下跌)
   - 但RSI超卖通常意味着**即将反弹**，不会继续下跌到限价 ❌

2. **RSI超买做空**:
   - RSI > 75 (已极度超买) + EMA强度下降
   - 限价单: 市价 + 0.6% (等待继续上涨)
   - 但RSI超买通常意味着**即将回落**，不会继续上涨到限价 ❌

### 解决方案

RSI信号**强制使用市价单**：

```python
# _do_open_position 方法中
if signal_type in ['golden_cross', 'death_cross', 'reversal_cross', 'rsi_signal']:
    limit_price = current_price  # 使用市价
    logger.info(f"⚡ {symbol} {signal_type}信号使用市价开仓")

# 主执行循环中
open_result = await self.execute_open_position(
    symbol, rsi_signal, 'rsi_signal', strategy, account_id,
    signal_reason=entry_reason,
    force_market=True  # RSI信号强制市价开仓
)
```

### 理由

- RSI超买/超卖是**极端信号**，通常意味着即将反转
- 等待回调可能错过最佳入场点
- RSI极值本身已经是强信号，应立即执行

### 预期效果

- ✅ RSI信号成交率接近100%
- ✅ 不会错过RSI极值反转的最佳入场时机
- ✅ 提高策略整体胜率

---

## 信号类型汇总

### 使用市价单的信号（立即成交）

| 信号类型 | 说明 | 理由 |
|---------|------|------|
| `golden_cross` | 金叉 | 趋势反转强信号，时机重要 |
| `death_cross` | 死叉 | 趋势反转强信号，时机重要 |
| `reversal_cross` | 反转平仓后立即开仓 | 趋势反转强信号，时机重要 |
| `rsi_signal` | RSI极值信号 | 超买/超卖极端信号，应立即入场 |

### 使用限价单的信号（等待回调）

| 信号类型 | 说明 | 理由 |
|---------|------|------|
| `sustained_trend` | 连续趋势（5M放大） | 趋势持续中，等待回调入场更优 |
| `sustained_trend_entry` | 持续趋势开仓 | 趋势持续中，等待回调入场更优 |
| `oscillation_reversal` | 震荡反向 | 震荡行情，等待回调降低风险 |
| `limit_order_trend` | 限价单信号 | 专为限价单设计的信号 |

---

## 配置建议

### 推荐配置

```json
{
  "minSignalStrength": {
    "ema9_26": 0.05,      // 普通信号强度
    "crossover": 0.01     // 金叉/死叉强度（更宽松）
  },
  "rsiSignal": {
    "enabled": true,
    "longThreshold": 30,
    "shortThreshold": 75,
    "minEmaStrengthLong": 0.15,
    "maxEmaStrengthShort": 1.0,
    "emaStrengthTrendBars": 2
  }
}
```

### 调优建议

1. **金叉/死叉强度**:
   - 保守策略：`crossover: 0.02`
   - 激进策略：`crossover: 0.005`

2. **RSI阈值**:
   - 保守策略：`longThreshold: 25, shortThreshold: 80`
   - 激进策略：`longThreshold: 35, shortThreshold: 70`

---

## 数据库查询

### 查看各信号类型的开仓统计

```sql
SELECT
    entry_signal_type,
    COUNT(*) as total,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
    ROUND(AVG(realized_pnl), 2) as avg_pnl
FROM futures_positions
WHERE status = 'closed'
AND created_at >= '2026-01-01'
GROUP BY entry_signal_type
ORDER BY total DESC;
```

### 查看金叉/死叉开仓记录

```sql
SELECT
    symbol,
    position_side,
    entry_signal_type,
    entry_ema_diff,
    realized_pnl,
    created_at
FROM futures_positions
WHERE entry_signal_type IN ('golden_cross', 'death_cross')
ORDER BY created_at DESC
LIMIT 20;
```

---

## 测试建议

1. **观察期**: 先在模拟盘运行1-2周，观察效果
2. **关键指标**:
   - 金叉/死叉触发频率（预期增加）
   - 金叉/死叉成交率（预期接近100%）
   - RSI信号触发频率和胜率
3. **对比分析**: 与之前的数据对比，验证改进效果

---

## 风险提示

1. **市价单滑点**: 市价单可能有轻微滑点，但强信号的及时性更重要
2. **信号频率**: 金叉/死叉触发频率可能增加，注意仓位管理
3. **参数调优**: 不同币种可能需要不同的参数配置

---

## 技术实现

### 代码位置

- 金叉/死叉强度检查: `strategy_executor_v2.py:820-865`
- 市价单判断逻辑: `strategy_executor_v2.py:2877-2893`
- RSI信号检测: `strategy_executor_v2.py:1023-1113`
- 主执行循环: `strategy_executor_v2.py:3495-3636`

### 相关提交

- 金叉/死叉强度优化: commit `723c17d`
- 金叉/死叉市价单: commit `956059b`
- RSI信号市价单: commit `19e07da`

---

## 参考文档

- [RSI信号功能说明](./rsi_signal_feature.md)
- [交易逻辑文档](./trading_logic.md)
- [数据库表结构](./futures_database_schema.md)
