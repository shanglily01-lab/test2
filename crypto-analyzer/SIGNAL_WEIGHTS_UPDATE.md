# 信号权重优化更新

**更新时间**: 2026-01-31
**目的**: 修复LONG信号评分过低问题，适配4-6小时短线交易策略

---

## 问题背景

用户反馈: 系统24H内100%做空(144笔)，0%做多，即使市场有明显上涨趋势。

**诊断结果**:
- 20个币种1H上涨趋势明显(阳线>52%)
- LONG信号评分太低，很难超过35分阈值
- 1D信号权重过高(30分)，不适合4-6小时短线交易
- 多个信号配置错误(如momentum_up_3pct给SHORT加分)

---

## 核心改进策略

基于用户建议: **"合约开单只有4-6小时，1D信号影响应该很低"**

### 1. 降低1D信号权重

| 信号 | 修改前 LONG | 修改前 SHORT | 修改后 LONG | 修改后 SHORT |
|------|------------|-------------|------------|-------------|
| trend_1d_bull | 5 | 0 | **3** | 0 |
| trend_1d_bear | 0 | **30** | 0 | **3** |

**改进**: 1D空头信号从30分降到3分，降低90%权重

---

### 2. 提高1H信号权重

| 信号 | 修改前 LONG | 修改前 SHORT | 修改后 LONG | 修改后 SHORT |
|------|------------|-------------|------------|-------------|
| trend_1h_bull | 5 | 5 ❌ | **25** | 0 |
| trend_1h_bear | 5 ❌ | 26 | 0 | **25** |

**改进**:
- 1H多头信号从5分提升到25分，增加400%
- 修正配置错误: trend_1h_bull不应给SHORT加分

---

### 3. 修正LONG动量信号

| 信号 | 修改前 LONG | 修改前 SHORT | 修改后 LONG | 修改后 SHORT |
|------|------------|-------------|------------|-------------|
| momentum_up_3pct | 5 | 23 ❌ | **30** | 0 |
| momentum_down_3pct | 5 ❌ | 30 | 0 | **30** |

**改进**: 上涨3%应该是LONG信号，修正为30分

---

### 4. 修正价格位置信号

| 信号 | 修改前 LONG | 修改前 SHORT | 修改后 LONG | 修改后 SHORT |
|------|------------|-------------|------------|-------------|
| position_low | 5 | 22 ❌ | **25** | 0 |
| position_high | 5 ❌ | 30 | 0 | **25** |
| position_mid | 5 | 30 | 5 | 5 |

**改进**:
- 低位应该做多，修正为LONG 25分
- 高位应该做空，调整为SHORT 25分
- 中位降低权重为5分(中性)

---

### 5. 添加缺失的LONG信号

以下关键LONG信号之前不在数据库中，现已添加:

| 信号 | LONG | SHORT | 说明 |
|------|------|-------|------|
| **volume_power_bull** | 30 | 0 | 1H+15M多头放量 |
| **volume_power_1h_bull** | 20 | 0 | 1H多头放量 |
| **breakout_long** | 25 | 0 | 阻力位突破做多 |
| **consecutive_bull** | 20 | 0 | 连续阳线 |

---

## 最终权重总览

### LONG信号 (按权重排序)

| 排名 | 信号组件 | 权重 | 说明 |
|------|---------|------|------|
| 1 | momentum_up_3pct | 30 | 24H上涨>3% |
| 1 | volume_power_bull | 30 | 1H+15M多头放量 |
| 3 | breakout_long | 25 | 阻力位突破 |
| 3 | position_low | 25 | 价格在低位<30% |
| 3 | **trend_1h_bull** | **25** | **1H多头趋势** ⭐核心 |
| 6 | volume_power_1h_bull | 20 | 1H多头放量 |
| 6 | consecutive_bull | 20 | 连续阳线 |
| 8 | volatility_high | 10 | 高波动(中性) |
| 9 | position_mid | 5 | 中间位置(中性) |
| 10 | trend_1d_bull | 3 | 1D多头趋势(降权) |

**最高总分**: 173分

---

### SHORT信号 (按权重排序)

| 排名 | 信号组件 | 权重 | 说明 |
|------|---------|------|------|
| 1 | momentum_down_3pct | 30 | 24H下跌>3% |
| 1 | volume_power_bear | 30 | 1H+15M空头放量 |
| 3 | breakdown_short | 25 | 支撑位破位 |
| 3 | position_high | 25 | 价格在高位>70% |
| 3 | **trend_1h_bear** | **25** | **1H空头趋势** ⭐核心 |
| 6 | volume_power_1h_bear | 20 | 1H空头放量 |
| 6 | consecutive_bear | 20 | 连续阴线 |
| 8 | volatility_high | 10 | 高波动(中性) |
| 9 | position_mid | 5 | 中间位置(中性) |
| 10 | trend_1d_bear | 3 | 1D空头趋势(降权) |

**最高总分**: 173分

---

## 预期效果

### 修改前问题:

```
ENSO/USDT 示例:
- 1H: 65.8% 阳线 (强势上涨)
- 1D: 42.9% 阳线 (整体下跌)

信号评分:
SHORT: trend_1d_bear(30) + momentum_down(30) + volatility(10) = 70分 ✅
LONG: consecutive_bull(15) = 15分 ❌ (低于35阈值)

结果: 只能做空，在上涨行情中亏损
```

### 修改后预期:

```
ENSO/USDT 示例:
- 1H: 65.8% 阳线 (强势上涨)
- 1D: 42.9% 阳线 (整体下跌)

信号评分:
LONG: trend_1h_bull(25) + consecutive_bull(20) + position_low(25) = 70分 ✅
SHORT: trend_1d_bear(3) + volatility(10) = 13分 ❌

结果: 能够做多，跟随1H上涨趋势
```

---

## 关键优化点

### ✅ 1. 适配短线交易周期

- **4-6小时持仓** → 1H信号权重最高(25分)
- 1D信号降低到3分，不影响短期决策

### ✅ 2. LONG/SHORT完全平衡

- LONG最高总分: 173分
- SHORT最高总分: 173分
- 完全公平竞争

### ✅ 3. 修正所有错误配置

- momentum_up_3pct: ❌ SHORT 23 → ✅ LONG 30
- position_low: ❌ SHORT 22 → ✅ LONG 25
- trend_1h_bull: ❌ SHORT 5 → ✅ LONG 25

### ✅ 4. 添加缺失的LONG信号

- volume_power_bull: 30分
- breakout_long: 25分
- consecutive_bull: 20分

---

## 实施步骤

1. ✅ 更新数据库 signal_scoring_weights 表
2. ⏳ 重启交易服务加载新权重
3. ⏳ 观察24H内LONG/SHORT比例
4. ⏳ 监控盈亏改善情况

---

## 验证方法

### 24H后检查:

```sql
-- 检查LONG/SHORT比例
SELECT
    position_side,
    COUNT(*) as count,
    COUNT(*) * 100.0 / SUM(COUNT(*)) OVER() as percentage
FROM futures_positions
WHERE open_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
GROUP BY position_side;

-- 预期: LONG 40-60%, SHORT 40-60% (更平衡)
```

### 盈亏改善:

```sql
SELECT
    position_side,
    COUNT(*) as trades,
    SUM(realized_pnl) as total_pnl,
    AVG(realized_pnl) as avg_pnl
FROM futures_positions
WHERE close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
AND status = 'CLOSED'
GROUP BY position_side;

-- 预期: LONG在上涨行情中盈利
```

---

## 下一步优化建议

如果24H后仍有问题:

1. **进一步提高1H权重** - 如果1H趋势仍被忽略
2. **添加止损保护** - 高位做空/低位做多的硬性限制
3. **动态调整阈值** - 根据市场波动调整35分阈值
4. **增加信号组合奖励** - 多个LONG信号同时出现时额外加分

---

**更新完成时间**: 2026-01-31
**下次检查时间**: 2026-02-01 (24小时后)
**预期效果**: LONG持仓比例从0%提升到40-60%，整体盈利改善
