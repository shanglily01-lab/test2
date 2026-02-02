# 智能决策大脑 (SmartDecisionBrain) 完整逻辑分析

> 文件位置: `smart_trader_service.py` (内嵌版本, 当前正在使用)
> 分析时间: 2026-02-02
> 最后更新: 2026-02-02 (优化版本)
> 状态: 生产环境运行中 (Account 2 U本位合约)

## 📝 最新优化 (2026-02-02)

### 优化内容:
1. ✅ **移除所有1D信号** - 持仓时间4小时,不需要1D趋势判断
   - 移除: `trend_1d_bull`, `trend_1d_bear`
   - 移除对应的权重配置

2. ✅ **添加EMA四大天王评分维度** - 作为评分项而非硬性过滤
   - 新增: `ema_bull` (+15分) - EMA9 > EMA21 > EMA60 > EMA120
   - 新增: `ema_bear` (+15分) - EMA9 < EMA21 < EMA60 < EMA120
   - EMA混乱排列: 不加分不减分

3. ✅ **4小时强制平仓** - 统一托底时间,移除6小时选项
   - 修改: `max_hold_minutes = 240` (固定4小时)
   - 移除原有的动态时长 (6h/4h/2h)

### 信号组件更新:
- **总数**: 17个 → 16个 (移除2个1D,新增2个EMA)
- **最高评分**: ~105分 (position_low + volume_power_bull + trend_1h_bull + ema_bull + consecutive_bull + momentum_up_3pct)
- **开仓阈值**: 35分

---

## 一、核心架构概览

### 1.1 评分机制
- **双轨评分**: 分别计算 `long_score` 和 `short_score`
- **开仓阈值**: `threshold = 35` 分 (提高到35分,过滤低质量信号)
- **方向选择**: 取评分更高的方向,且必须 ≥ 阈值才开仓
- **信号清理**: 最终只保留与选定方向一致的信号组件

### 1.2 数据源
- **1日K线** (1d): 50根,用于大趋势确认
- **1小时K线** (1h): 100根,核心分析周期
- **15分钟K线** (15m): 96根(24小时),用于短期量能确认

### 1.3 最低数据要求
- 1d K线 ≥ 30根
- 1h K线 ≥ 72根 (3天)
- 15m K线 ≥ 48根

---

## 二、信号组件详解 (共17个)

### 2.1 位置信号 (Position Signals) - 基于72小时高低点

#### `position_low` - 底部区域做多
- **触发条件**: `position_pct < 30%` AND `net_power_1h > -2` (无强空头量能)
- **评分**: LONG +20
- **逻辑**: 价格在72H区间底部30%,且无破位量能,可以抄底做多
- **防破位保护**: 如果有强空头量能(net_power_1h ≤ -2),不做多

#### `position_mid` - 中部区域双向
- **触发条件**: `30% ≤ position_pct ≤ 70%`
- **评分**: LONG +5, SHORT +5
- **逻辑**: 中部区域,趋势不明确,给予较低分数

#### `position_high` - 顶部区域做空
- **触发条件**: `position_pct > 70%`
- **评分**: SHORT +20
- **逻辑**: 价格在72H区间顶部30%,做空概率高
- **⚠️ 硬性禁止**: 代码第620行硬性禁止 `LONG + position_high` 组合

---

### 2.2 动量信号 (Momentum Signals) - 基于24小时涨跌幅

#### `momentum_down_3pct` - 下跌动量
- **触发条件**: `gain_24h < -3%`
- **评分**: SHORT +15
- **逻辑**: 24小时跌超3%,看跌信号

#### `momentum_up_3pct` - 上涨动量
- **触发条件**: `gain_24h > 3%`
- **评分**: LONG +15
- **逻辑**: 24小时涨超3%,看涨信号

---

### 2.3 趋势信号 (Trend Signals)

#### `trend_1h_bull` - 1小时多头趋势
- **触发条件**: 最近48根1H K线中,阳线 > 30根 (62.5%)
- **评分**: LONG +20
- **逻辑**: 2天内多数时间上涨,趋势向上

#### `trend_1h_bear` - 1小时空头趋势
- **触发条件**: 最近48根1H K线中,阴线 > 30根 (62.5%)
- **评分**: SHORT +20
- **逻辑**: 2天内多数时间下跌,趋势向下

#### `trend_1d_bull` - 1日多头趋势 (辅助确认)
- **触发条件**: 最近30根1D K线中,阳线 > 18根 (60%) AND `long_score > short_score`
- **评分**: LONG +10
- **逻辑**: 大周期趋势与1H趋势共振

#### `trend_1d_bear` - 1日空头趋势 (辅助确认)
- **触发条件**: 最近30根1D K线中,阴线 > 18根 (60%) AND `short_score > long_score`
- **评分**: SHORT +10
- **逻辑**: 大周期趋势与1H趋势共振

---

### 2.4 波动率信号 (Volatility Signal)

#### `volatility_high` - 高波动率
- **触发条件**: 24H波动率 > 5%
- **评分**: LONG +10 或 SHORT +10 (加到当前领先的方向)
- **逻辑**: 高波动环境更适合交易,增强已有优势方向

---

### 2.5 连续趋势信号 (Consecutive Trend Signals)

#### `consecutive_bull` - 连续阳线强化
- **触发条件**:
  - 最近10根1H K线中,阳线 ≥ 7根
  - 10H涨幅 < 5% (防止追高)
  - `position_pct < 70%` (不在高位)
- **评分**: LONG +15
- **逻辑**: 连续上涨但涨幅适中,趋势延续概率高

#### `consecutive_bear` - 连续阴线强化
- **触发条件**:
  - 最近10根1H K线中,阴线 ≥ 7根
  - 10H跌幅 < 5% (防止杀跌)
  - `position_pct > 30%` (不在底部)
- **评分**: SHORT +15
- **逻辑**: 连续下跌但跌幅适中,趋势延续概率高

---

### 2.6 量能信号 (Volume Power Signals) - 核心趋势判断

#### 量能计算逻辑
```python
# 1H量能统计 (最近24根)
for k in klines_1h[-24:]:
    is_bull = k['close'] > k['open']
    is_high_volume = k['volume'] > avg_volume_1h * 1.2

    if is_bull and is_high_volume:
        strong_bull_1h += 1  # 有力量的阳线
    elif not is_bull and is_high_volume:
        strong_bear_1h += 1  # 有力量的阴线

net_power_1h = strong_bull_1h - strong_bear_1h

# 15M量能统计 (最近24根,6小时)
# 同样逻辑计算 net_power_15m
```

#### `volume_power_bull` - 双周期量能多头 (最强信号)
- **触发条件**: `net_power_1h ≥ 2` AND `net_power_15m ≥ 2`
- **评分**: LONG +25 ⭐⭐⭐
- **逻辑**: 1H和15M都显示强力多头量能,高确信度做多

#### `volume_power_bear` - 双周期量能空头 (最强信号)
- **触发条件**: `net_power_1h ≤ -2` AND `net_power_15m ≤ -2`
- **评分**: SHORT +25 ⭐⭐⭐
- **逻辑**: 1H和15M都显示强力空头量能,高确信度做空

#### `volume_power_1h_bull` - 单周期量能多头 (辅助)
- **触发条件**: `net_power_1h ≥ 3` (仅1H强力多头)
- **评分**: LONG +15
- **逻辑**: 单一时间框架支撑,确信度低于双周期

#### `volume_power_1h_bear` - 单周期量能空头 (辅助)
- **触发条件**: `net_power_1h ≤ -3` (仅1H强力空头)
- **评分**: SHORT +15
- **逻辑**: 单一时间框架支撑,确信度低于双周期

---

### 2.7 突破/破位信号 (Breakout/Breakdown Signals)

#### `breakout_long` - 高位突破追涨 (有严格过滤)
- **触发条件**:
  - `position_pct > 70%` (高位)
  - `net_power_1h ≥ 2` 或双周期量能多头
  - **必须通过3层过滤**:
    1. 最近3根1H K线无长上影线(>1.5%)
    2. 最近5天连续上涨天数 < 4
    3. (可选警告)30天阳线 ≥ 18根
- **评分**: LONG +20
- **逻辑**: 高位有强量能支撑且无抛压,可以追涨突破
- **⚠️ 硬性禁止**: 代码第620行硬性禁止 `LONG + position_high` 组合,与此信号矛盾

#### `breakdown_short` - 低位破位追空
- **触发条件**:
  - `position_pct < 30%` (低位)
  - `net_power_1h ≤ -2` 或双周期量能空头
- **评分**: SHORT +20
- **逻辑**: 低位有强量能压制,可以追空破位
- **⚠️ 硬性禁止**: 代码第624行硬性禁止 `SHORT + position_low` 组合,与此信号矛盾

---

## 三、信号矛盾检测机制

### 3.1 方向清理 (Line 575-598)
最终开仓前,会清理掉与方向相反的信号:

- **多头信号**: `position_high`, `breakout_long`, `volume_power_bull`, `volume_power_1h_bull`, `trend_1h_bull`, `trend_1d_bull`, `momentum_up_3pct`, `consecutive_bull`
- **空头信号**: `position_low`, `breakdown_short`, `volume_power_bear`, `volume_power_1h_bear`, `trend_1h_bear`, `trend_1d_bear`, `momentum_down_3pct`, `consecutive_bear`
- **中性信号**: `position_mid`, `volatility_high` (可以在任何方向)

### 3.2 方向验证 (Line 613-617)
调用 `_validate_signal_direction()` 检测信号矛盾:

#### 检测规则:
- **做多不允许包含**: `breakdown_short`, `volume_power_bear`, `volume_power_1h_bear`, `trend_1h_bear`, `trend_1d_bear`, `momentum_down_3pct`, `consecutive_bear`
  - **例外**: `momentum_down_3pct` + `position_low` 允许 (超跌反弹)

- **做空不允许包含**: `breakout_long`, `volume_power_bull`, `volume_power_1h_bull`, `trend_1h_bull`, `trend_1d_bull`, `momentum_up_3pct`, `consecutive_bull`
  - **例外**: `momentum_up_3pct` + `position_high` 允许 (超涨回调)

### 3.3 硬性禁止规则 (Line 619-626)
- **禁止高位做多**: `LONG + position_high` → 直接拒绝
- **禁止低位做空**: `SHORT + position_low` → 直接拒绝

**⚠️ 逻辑矛盾**: 这两条硬性规则与 `breakout_long`/`breakdown_short` 信号存在矛盾!
- `breakout_long` 需要 `position_pct > 70%` → 必然会触发 `position_high`
- `breakdown_short` 需要 `position_pct < 30%` → 必然会触发 `position_low`
- 导致这两个信号永远无法生效

---

## 四、防追高/追跌过滤器 (Line 227-283)

### 4.1 基于24H价格位置的硬性过滤

#### 做多防追高
- **条件1**: 价格位于24H区间 > 80% → 拒绝
- **条件2**: 24H涨幅 > 15% AND 价格位于 > 70% → 拒绝

#### 做空防杀跌
- **条件1**: 价格位于24H区间 < 20% → 拒绝
- **条件2**: 24H跌幅 > 15% AND 价格位于 < 30% → 拒绝

### 4.2 数据来源
从 `price_stats_24h` 表读取 `high_24h`, `low_24h`, `change_24h`

---

## 五、评分权重体系

### 5.1 权重配置来源
1. **优先**: 从数据库 `signal_scoring_weights` 表读取 (动态可调)
2. **备用**: 硬编码默认权重 (Line 170-190)

### 5.2 默认权重表

| 信号组件 | LONG权重 | SHORT权重 | 说明 |
|---------|---------|----------|------|
| `position_low` | 20 | 0 | 底部区域 |
| `position_mid` | 5 | 5 | 中部区域 |
| `position_high` | 0 | 20 | 顶部区域 |
| `momentum_down_3pct` | 15 | 0 | 下跌动量 (矛盾:应该SHORT) |
| `momentum_up_3pct` | 0 | 15 | 上涨动量 (矛盾:应该LONG) |
| `trend_1h_bull` | 20 | 0 | 1H多头趋势 |
| `trend_1h_bear` | 0 | 20 | 1H空头趋势 |
| `volatility_high` | 10 | 10 | 高波动率 |
| `consecutive_bull` | 15 | 0 | 连续阳线 |
| `consecutive_bear` | 0 | 15 | 连续阴线 |
| `volume_power_bull` | 25 | 0 | 双周期量能多头⭐⭐⭐ |
| `volume_power_bear` | 0 | 25 | 双周期量能空头⭐⭐⭐ |
| `volume_power_1h_bull` | 15 | 0 | 单周期量能多头 |
| `volume_power_1h_bear` | 0 | 15 | 单周期量能空头 |
| `breakout_long` | 20 | 0 | 高位突破 (⚠️永不生效) |
| `breakdown_short` | 0 | 20 | 低位破位 (⚠️永不生效) |
| `trend_1d_bull` | 10 | 0 | 1D多头确认 |
| `trend_1d_bear` | 0 | 10 | 1D空头确认 |

**⚠️ 注意权重配置错误**:
- Line 174: `momentum_down_3pct` 配置为 LONG +15 (应该是 SHORT)
- Line 175: `momentum_up_3pct` 配置为 SHORT +15 (应该是 LONG)
- 但代码第387-395行使用时是正确的,说明数据库配置已修正

---

## 六、典型信号组合示例

### 6.1 强做多组合 (可达70+分)
```
position_low (20) +
volume_power_bull (25) +
trend_1h_bull (20) +
momentum_up_3pct (15) +
trend_1d_bull (10) = 90分
```

### 6.2 中等做多组合 (可达50+分)
```
position_mid (5) +
volume_power_1h_bull (15) +
trend_1h_bull (20) +
consecutive_bull (15) = 55分
```

### 6.3 昨天亏损的实际信号组合
根据数据库查询结果,昨天UTC 12:00-20:00亏损订单主要使用:
- `momentum_up_3pct + position_mid` (43分?)
- `volume_power_1h_bull` (15分,不足阈值)
- `consecutive_bull + momentum_up_3pct + position_mid + volume_power_1h_bull` (40分?)

**问题**:
1. 这些组合评分应该达不到35分阈值,但却开仓了 → 说明实际运行的权重配置与代码不一致
2. 或者阈值被调低了

---

## 七、已知问题与矛盾

### 7.1 逻辑矛盾
1. **突破信号永不生效**:
   - `breakout_long` 需要 `position_high` (Line 503)
   - 但代码硬性禁止 `LONG + position_high` (Line 620)
   - 结果: 突破做多信号永远无法生效

2. **破位信号永不生效**:
   - `breakdown_short` 需要 `position_low` (Line 541)
   - 但代码硬性禁止 `SHORT + position_low` (Line 624)
   - 结果: 破位做空信号永远无法生效

### 7.2 权重配置错误 (可能已在数据库修正)
- 默认硬编码权重中 `momentum_down_3pct` 和 `momentum_up_3pct` 方向反了

### 7.3 阈值与实际不符
- 代码阈值: 35分
- 昨天亏损订单使用的信号组合评分疑似不足35分
- 需确认数据库 `adaptive_params` 中是否有动态阈值

---

## 八、与EMA四大天王的关系

### 8.1 现有趋势判断
现有系统已包含多个趋势判断维度:
- **1H趋势**: 48根K线阳/阴线比例 (62.5%阈值)
- **1D趋势**: 30根K线阳/阴线比例 (60%阈值)
- **连续趋势**: 10根K线连续方向 (70%阈值)
- **量能趋势**: 1H+15M量能方向确认

### 8.2 EMA四大天王的潜在价值
EMA四大天王 (EMA9/21/60/120 on 1h) 可以提供:
- **更精确的趋势方向**: 均线排列比K线涨跌更平滑
- **趋势强度**: 均线间距可以量化趋势强度
- **支撑阻力**: 均线本身是动态支撑阻力位

### 8.3 集成建议
**不应该作为硬性过滤** (昨天的三层过滤太严格)

**建议作为评分维度**:
- EMA多头排列 → LONG +10~15
- EMA空头排列 → SHORT +10~15
- EMA混乱排列 → 不加分也不减分 (或减5分)

**或作为信号增强**:
- 现有信号 + EMA确认 → 权重×1.2
- 现有信号 + EMA矛盾 → 权重×0.8

---

## 九、总结与建议

### 9.1 当前系统优势
1. ✅ 多维度评分体系完善 (17个信号组件)
2. ✅ 量能分析是核心亮点 (双周期确认)
3. ✅ 有防追高/追跌保护
4. ✅ 有信号矛盾检测
5. ✅ 支持数据库动态配置权重

### 9.2 当前系统问题
1. ❌ `breakout_long`/`breakdown_short` 信号永不生效 (逻辑矛盾)
2. ❌ 默认权重配置有误 (momentum方向反了)
3. ⚠️ 阈值35分可能偏低 (昨天产生大量亏损订单)
4. ⚠️ 缺乏对大周期趋势的硬性约束 (可能逆势交易)

### 9.3 优化方向建议
1. **修复逻辑矛盾**: 移除 Line 620/624 的硬性禁止,或重新设计突破/破位逻辑
2. **提高阈值**: 建议调整到40-45分,过滤更多低质量信号
3. **添加EMA评分维度** (而非硬性过滤):
   ```python
   # EMA确认信号 (+10分)
   if ema_bull_aligned and long_score > short_score:
       long_score += 10
   elif ema_bear_aligned and short_score > long_score:
       short_score += 10
   ```
4. **优化量能信号**: 当前量能占比过高 (25分),可能导致过度依赖短期量能
5. **添加趋势强度检测**: 不仅看方向,还要看趋势是否够强

---

**文档版本**: v1.0
**分析者**: Claude Sonnet 4.5
**最后更新**: 2026-02-02
