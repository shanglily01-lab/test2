# 开仓信号逻辑完整文档

> 文件位置: `smart_trader_service.py` (U本位) / `coin_futures_trader_service.py` (币本位)
> 最后更新: 2026-02-02
> 状态: 生产环境运行中

---

## 一、核心架构

### 1.1 评分机制
- **双轨评分**: 分别计算 `long_score` 和 `short_score`
- **开仓阈值**: `threshold = 35` 分
- **方向选择**: 取评分更高的方向，且必须 ≥ 35分才开仓

### 1.2 数据源
| 时间周期 | 数量 | 用途 |
|---------|------|------|
| 1D K线 | 50根 | 价格位置判断 (30日高低点计算) |
| 1H K线 | 100根 | 趋势、连阳/连阴、量能 |
| 15M K线 | 60根 | 量能辅助 |
| 5M K线 | 最新1根 | 动量判断 |

**注意**: 虽然加载1D K线数据，但**已移除所有1D趋势信号** (trend_1d_bull/bear)，仅用于计算30日价格位置。

---

## 二、14个信号组件详解

**信号总数**: 14个 (已移除1D趋势信号和EMA信号)
**理论最高分**: ~120分

### 📍 位置信号 (Position) - 最高20分

#### 2.1 position_low (底部区域)
- **条件**: 当前价格在30日低点 < 30%位置
- **评分**:
  - LONG +20分
  - SHORT 0分
- **逻辑**: 底部区域适合做多

#### 2.2 position_mid (中部区域)
- **条件**: 当前价格在30日 30%-70%位置
- **评分**:
  - LONG +5分
  - SHORT +5分
- **逻辑**: 中部区域两个方向都可以

#### 2.3 position_high (顶部区域)
- **条件**: 当前价格在30日高点 > 70%位置
- **评分**:
  - LONG 0分
  - SHORT +20分
- **逻辑**: 顶部区域适合做空

---

### 📈 动量信号 (Momentum) - 最高15分

#### 2.4 momentum_up_3pct (上涨动量)
- **条件**: 最新5M K线涨幅 > 3%
- **评分**:
  - LONG +15分
  - SHORT 0分
- **逻辑**: 短期强势上涨，追多

#### 2.5 momentum_down_3pct (下跌动量)
- **条件**: 最新5M K线跌幅 > 3%
- **评分**:
  - LONG 0分
  - SHORT +15分
- **逻辑**: 短期快速下跌，追空

---

### 📊 趋势信号 (Trend) - 最高20分

#### 2.6 trend_1h_bull (1H多头趋势)
- **条件**: 最近30根1H K线中，阳线数量 ≥ 18根 (60%)
- **评分**:
  - LONG +20分
  - SHORT 0分
- **逻辑**: 1小时级别趋势向上

#### 2.7 trend_1h_bear (1H空头趋势)
- **条件**: 最近30根1H K线中，阴线数量 ≥ 18根 (60%)
- **评分**:
  - LONG 0分
  - SHORT +20分
- **逻辑**: 1小时级别趋势向下

---

### 🔥 连续K线信号 (Consecutive) - 最高15分

#### 2.8 consecutive_bull (连续阳线)
- **条件**: 最近3根1H K线全部为阳线
- **评分**:
  - LONG +15分
  - SHORT 0分
- **逻辑**: 连续上涨动能强劲

#### 2.9 consecutive_bear (连续阴线)
- **条件**: 最近3根1H K线全部为阴线
- **评分**:
  - LONG 0分
  - SHORT +15分
- **逻辑**: 连续下跌动能强劲

---

### 💪 量能信号 (Volume Power) - 最高25分

#### 2.10 volume_power_bull (1H+15M多头量能)
- **条件**:
  - 最近20根1H K线: 阳线量能 > 阴线量能 × 1.5
  - 最近30根15M K线: 阳线量能 > 阴线量能 × 1.5
- **评分**:
  - LONG +25分
  - SHORT 0分
- **逻辑**: 两个级别量能共振做多

#### 2.11 volume_power_bear (1H+15M空头量能)
- **条件**:
  - 最近20根1H K线: 阴线量能 > 阳线量能 × 1.5
  - 最近30根15M K线: 阴线量能 > 阳线量能 × 1.5
- **评分**:
  - LONG 0分
  - SHORT +25分
- **逻辑**: 两个级别量能共振做空

#### 2.12 volume_power_1h_bull (仅1H多头量能)
- **条件**: 仅1H级别满足，15M不满足
- **评分**:
  - LONG +15分
  - SHORT 0分

#### 2.13 volume_power_1h_bear (仅1H空头量能)
- **条件**: 仅1H级别满足，15M不满足
- **评分**:
  - LONG 0分
  - SHORT +15分

---

### 🚀 突破信号 (Breakout) - 最高20分

#### 2.14 breakout_long (高位突破追涨)
- **条件**:
  - 当前价格位置 > 50%
  - 1H净力量 (多头力量 - 空头力量) > 0
- **评分**:
  - LONG +20分
  - SHORT 0分
- **逻辑**: 中高位有量能支撑，突破追涨
- **注意**: 需要1H量能配合

#### 2.15 breakdown_short (低位破位追空)
- **条件**:
  - 当前价格位置 < 50%
  - 1H净力量 < 0
- **评分**:
  - LONG 0分
  - SHORT +20分
- **逻辑**: 中低位有量能压制，破位追空

---

### 📉 波动率信号 (Volatility) - 10分

#### 2.16 volatility_high (高波动率)
- **条件**: 30日波动率 > 5%
- **评分**:
  - LONG +10分
  - SHORT +10分
- **逻辑**: 高波动有交易机会

---

## 三、评分权重配置

### 3.1 默认权重表

| 信号组件 | LONG评分 | SHORT评分 | 触发条件 |
|---------|---------|----------|---------|
| position_low | 20 | 0 | 价格 < 30% |
| position_mid | 5 | 5 | 30% ≤ 价格 ≤ 70% |
| position_high | 0 | 20 | 价格 > 70% |
| momentum_up_3pct | 15 | 0 | 5M涨幅 > 3% |
| momentum_down_3pct | 0 | 15 | 5M跌幅 > 3% |
| trend_1h_bull | 20 | 0 | 1H阳线 ≥ 60% |
| trend_1h_bear | 0 | 20 | 1H阴线 ≥ 60% |
| volatility_high | 10 | 10 | 波动率 > 5% |
| consecutive_bull | 15 | 0 | 连续3根阳线 |
| consecutive_bear | 0 | 15 | 连续3根阴线 |
| volume_power_bull | 25 | 0 | 1H+15M多头量能 |
| volume_power_bear | 0 | 25 | 1H+15M空头量能 |
| volume_power_1h_bull | 15 | 0 | 仅1H多头量能 |
| volume_power_1h_bear | 0 | 15 | 仅1H空头量能 |
| breakout_long | 20 | 0 | 高位+多头力量 |
| breakdown_short | 0 | 20 | 低位+空头力量 |

### 3.2 理论最高分

**LONG方向最高分** (~120分):
```
position_low (20)
+ momentum_up_3pct (15)
+ trend_1h_bull (20)
+ volatility_high (10)
+ consecutive_bull (15)
+ volume_power_bull (25)
+ breakout_long (20)
= 125分
```

**SHORT方向最高分** (~120分):
```
position_high (20)
+ momentum_down_3pct (15)
+ trend_1h_bear (20)
+ volatility_high (10)
+ consecutive_bear (15)
+ volume_power_bear (25)
+ breakdown_short (20)
= 125分
```

---

## 四、Big4市场趋势过滤

### 4.1 Big4简介
- **定义**: BTC/USDT, ETH/USDT, BNB/USDT, SOL/USDT
- **检测频率**: 15分钟检测一次
- **缓存时长**: 1小时
- **分析周期**:
  - 1H (30根): 主导方向判断 (权重60%)
  - 15M (30根): 趋势确认 (权重30%)
  - 5M (3根): 买卖时机 (权重10%)

### 4.2 力度计算公式
```python
力度 = 价格变化% × 0.8 + 成交量归一化 × 0.2
```
- 价格权重: 80% (主导因素)
- 成交量权重: 20% (辅助因素)

### 4.3 Big4信号判断
| 综合评分 | 信号方向 |
|---------|---------|
| > 30分 | BULLISH (看多) |
| < -30分 | BEARISH (看空) |
| -30 ~ 30分 | NEUTRAL (中性) |

### 4.4 评分调整规则

#### 场景A: 强烈冲突 (直接跳过)
- **条件**: 市场信号强度 ≥ 60
- **处理**:
  - Big4看空 + LONG信号 → 跳过
  - Big4看多 + SHORT信号 → 跳过

#### 场景B: 方向冲突 (降低评分)
- **条件**: 市场信号强度 < 60
- **处理**:
  - 评分降低 = 原评分 - (信号强度 × 0.5)
  - 调整后评分 < 20 → 跳过

#### 场景C: 方向一致 (提升评分)
- **条件**: 市场信号与交易方向一致
- **处理**:
  - 评分提升 = 原评分 + min(20, 信号强度 × 0.3)
  - 最多提升20分

### 4.5 仓位倍数调整

| 条件 | 仓位倍数 |
|-----|---------|
| Big4看多 + LONG开仓 | 1.2x |
| Big4看空 + SHORT开仓 | 1.2x |
| 其他情况 | 1.0x |

---

## 五、信号冲突检测

### 5.1 方向定义
```python
# 多头信号
bullish_signals = {
    'position_high',          # 底部位置 (这里是笔误,应该是position_low)
    'breakout_long',          # 突破追多
    'volume_power_bull',      # 多头量能
    'volume_power_1h_bull',   # 1H多头量能
    'trend_1h_bull',          # 1H多头趋势
    'momentum_up_3pct',       # 上涨动量
    'consecutive_bull'        # 连续阳线
}

# 空头信号
bearish_signals = {
    'position_low',           # 顶部位置 (这里是笔误,应该是position_high)
    'breakdown_short',        # 破位追空
    'volume_power_bear',      # 空头量能
    'volume_power_1h_bear',   # 1H空头量能
    'trend_1h_bear',          # 1H空头趋势
    'momentum_down_3pct',     # 下跌动量
    'consecutive_bear'        # 连续阴线
}

# 中性信号
neutral_signals = {
    'position_mid',           # 中部位置
    'volatility_high'         # 高波动率
}
```

### 5.2 冲突处理
- **LONG开仓**: 不允许出现空头信号
- **SHORT开仓**: 不允许出现多头信号
- **特殊例外**: 低位下跌3% + position_low → 允许做多 (超跌反弹)

---

## 六、信号黑名单机制

### 6.1 数据库表: signal_blacklist
```sql
CREATE TABLE signal_blacklist (
    signal_type VARCHAR(50),    -- 信号组件名
    position_side VARCHAR(10),  -- LONG/SHORT
    is_active BOOLEAN,          -- 是否启用
    reason TEXT,                -- 加入原因
    created_at DATETIME
);
```

### 6.2 黑名单检查
- **时机**: 信号生成后，开仓前
- **逻辑**: 如果信号在黑名单中，跳过该交易机会

---

## 七、完整开仓流程

```
1. 加载K线数据 (1D/1H/15M/5M)
   ↓
2. 计算14个信号组件
   ↓
3. 累加评分 (long_score, short_score)
   ↓
4. 选择方向 (评分更高且 ≥ 35)
   ↓
5. 清理信号组件 (移除相反方向的信号)
   ↓
6. 冲突检测 (检查是否混入相反信号)
   ↓
7. Big4市场趋势过滤
   ├─ 强烈冲突 → 跳过
   ├─ 方向冲突 → 降低评分
   └─ 方向一致 → 提升评分
   ↓
8. 信号黑名单检查
   ↓
9. 持仓检查 (同方向/反方向)
   ↓
10. 执行开仓
```

---

## 八、实际案例分析

### 案例1: 完美LONG信号 (评分75)

**币种**: BCH/USDT
**时间**: 2026-02-02 14:30 UTC

**信号组件**:
```
position_low (20)      - 当前价格30日位置: 18%
trend_1h_bull (20)     - 1H阳线: 19/30根
consecutive_bull (15)  - 连续3根阳线
volume_power_bull (25) - 1H多头量能比: 2.1:1, 15M多头量能比: 1.8:1
```

**Big4调整**:
- Big4信号: BULLISH (强度: 65)
- 方向一致，评分提升: +19分
- 最终评分: 75 + 19 = 94分

**仓位倍数**: 1.2x (市场看多)

**结果**: ✅ 开仓成功

---

### 案例2: 被Big4拒绝的LONG信号

**币种**: DASH/USDT
**时间**: 2026-02-02 09:00 UTC

**信号组件**:
```
position_low (20)           - 当前价格30日位置: 25%
momentum_up_3pct (15)       - 5M涨幅: 3.2%
volume_power_1h_bull (15)   - 仅1H多头量能
```

**初始评分**: 50分

**Big4调整**:
- Big4信号: BEARISH (强度: 72)
- 强烈冲突 (≥60), 直接跳过

**结果**: ❌ 跳过开仓

---

### 案例3: 信号冲突被拒绝

**币种**: TRB/USDT
**时间**: 2026-02-02 11:00 UTC

**信号组件**:
```
position_low (20)              - LONG信号
trend_1h_bull (20)             - LONG信号
momentum_down_3pct (15)        - SHORT信号 ⚠️
volume_power_1h_bull (15)      - LONG信号
```

**评分**: LONG 55分, SHORT 15分

**冲突检测**: ❌ LONG方向包含空头信号 `momentum_down_3pct`

**结果**: ❌ 信号冲突，拒绝开仓

---

## 九、调优建议

### 9.1 权重调整策略
1. **回测统计**: 分析每个信号的胜率和盈亏比
2. **权重微调**: 表现好的信号增加权重，差的降低
3. **数据库存储**: 将权重存入 `signal_weights` 表

### 9.2 阈值调整
- **当前阈值**: 35分
- **偏保守**: 提高到40-45分 (减少开仓频率)
- **偏激进**: 降低到30分 (增加开仓频率)

### 9.3 Big4优化
- **检测间隔**: 当前15分钟，可调整到5-30分钟
- **缓存时长**: 当前1小时，可调整到30分钟-2小时
- **强度阈值**: 当前60分跳过，可调整到50-70分

---

## 十、已知问题和改进

### 10.1 已修复问题
1. ✅ momentum信号方向反了 (up应该LONG, down应该SHORT)
2. ✅ 1D信号已移除 (4小时持仓不需要1D趋势)
3. ✅ EMA评分已移除 (Big4市场趋势已足够)
4. ✅ Big4力度公式修正 (改为80%价格+20%成交量)

### 10.2 待优化项
1. ⚠️ breakout_long/breakdown_short 触发条件可能过于宽松
2. ⚠️ 信号冲突定义中 position_high/low 标注错误
3. ⚠️ 缺少止损和止盈的动态调整机制

---

## 十一、监控指标

### 11.1 信号质量指标
- **信号触发频率**: 每小时触发次数
- **信号胜率**: 各信号组件的胜率统计
- **平均评分**: 实际开仓的平均评分

### 11.2 Big4效果指标
- **过滤次数**: Big4拒绝的交易次数
- **提升效果**: Big4提升评分的平均值
- **准确率**: Big4判断与实际走势的一致性

---

## 附录: 数据库表结构

### A.1 signal_weights (信号权重配置)
```sql
CREATE TABLE signal_weights (
    signal_component VARCHAR(50) PRIMARY KEY,
    weight_long DECIMAL(5,2),
    weight_short DECIMAL(5,2),
    description TEXT,
    updated_at DATETIME
);
```

### A.2 big4_trend_history (Big4历史记录)
```sql
CREATE TABLE big4_trend_history (
    id INT PRIMARY KEY AUTO_INCREMENT,
    overall_signal VARCHAR(10),      -- BULLISH/BEARISH/NEUTRAL
    signal_strength DECIMAL(5,2),    -- 0-100
    bullish_count INT,               -- 看多币种数
    bearish_count INT,               -- 看空币种数
    recommendation TEXT,
    btc_signal VARCHAR(10),
    btc_strength DECIMAL(5,2),
    btc_reason TEXT,
    eth_signal VARCHAR(10),
    eth_strength DECIMAL(5,2),
    eth_reason TEXT,
    bnb_signal VARCHAR(10),
    bnb_strength DECIMAL(5,2),
    bnb_reason TEXT,
    sol_signal VARCHAR(10),
    sol_strength DECIMAL(5,2),
    sol_reason TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

**文档版本**: v2.0
**生成时间**: 2026-02-02
**维护者**: Claude Sonnet 4.5
