# 震荡行情优化方案

> 创建时间: 2026-02-02
> 问题: Big4 NEUTRAL时止损频繁，momentum信号胜率低

---

## 一、问题诊断

### 当前表现（震荡行情）
1. ✅ **Big4信号经常NEUTRAL** - 市场方向不明确
2. ✅ **止损次数明显增加** - 假突破频繁
3. ✅ **momentum信号开仓多但胜率低** - 追涨杀跌被套

### 根本原因
- 策略设计：**趋势跟踪型**
- 市场状态：**震荡行情**
- 矛盾：趋势策略 + 震荡市 = 反复止损

---

## 二、立即优化方案

### 优化1: Big4 NEUTRAL时提高开仓阈值 🚨 高优先级

**位置**: Line 2703-2759

**当前逻辑**:
```python
# 仅在强烈冲突时跳过 (strength >= 60)
# NEUTRAL时无特殊处理
```

**优化后**:
```python
# 在 Line 2718 后添加 NEUTRAL 处理

# ========== 新增: NEUTRAL时提高门槛 ==========
if symbol_signal == 'NEUTRAL':
    # 震荡市场,提高开仓要求
    if signal_strength < 30:  # 弱信号
        threshold_boost = 15  # 需要额外15分才能开仓
        if new_score < 35 + threshold_boost:  # 原阈值35 + 15 = 50分
            logger.info(f"[BIG4-NEUTRAL-SKIP] {symbol} 市场震荡且评分不足 ({new_score} < 50), 跳过")
            continue
        else:
            logger.info(f"[BIG4-NEUTRAL-OK] {symbol} 市场震荡但评分足够 ({new_score} >= 50), 允许开仓")
    else:
        logger.info(f"[BIG4-NEUTRAL] {symbol} 市场中性,正常开仓")
# ========== NEUTRAL 处理结束 ==========

# 原有的 BEARISH/BULLISH 冲突检测...
elif symbol_signal == 'BEARISH' and new_side == 'LONG':
    ...
```

**预期效果**:
- Big4 NEUTRAL + 弱信号时,开仓阈值从35分提高到50分
- 过滤掉震荡市中的低质量信号
- 预计减少30-40%的震荡市开仓

---

### 优化2: momentum信号降权 🚨 高优先级

**位置**: SmartDecisionBrain.__init__() 权重配置

**当前权重**:
```python
'momentum_up_3pct': {'long': 15, 'short': 0},
'momentum_down_3pct': {'long': 0, 'short': 15},
```

**优化后**:
```python
'momentum_up_3pct': {'long': 10, 'short': 0},    # 15 -> 10
'momentum_down_3pct': {'long': 0, 'short': 10},  # 15 -> 10
```

**原因**:
- BAD_SIGNALS_ANALYSIS.md 显示: momentum相关30笔交易，胜率仅32.3%，亏损$481
- 震荡市中最容易追高/追空被套
- 降低权重后需要更多信号配合才能开仓

---

### 优化3: 震荡市禁用momentum信号 ⚙️ 中优先级

**位置**: SmartDecisionBrain.analyze() 评分逻辑

**新增震荡市判断**:
```python
def analyze(self, symbol: str):
    # ... 获取K线数据 ...

    # ========== 新增: 震荡市检测 ==========
    # 获取Big4市场状态 (需要传入或缓存)
    is_ranging_market = False
    if hasattr(self, 'big4_result'):
        market_signal = self.big4_result.get('overall_signal', 'NEUTRAL')
        market_strength = self.big4_result.get('signal_strength', 0)

        # 定义震荡市: NEUTRAL且强度<30
        if market_signal == 'NEUTRAL' and market_strength < 30:
            is_ranging_market = True
            logger.info(f"[RANGING-MARKET] {symbol} 检测到震荡市场")
    # ========== 震荡市检测结束 ==========

    # ... 原有评分逻辑 ...

    # ========== 修改: momentum评分 ==========
    # 5M 动量 (涨跌幅 > 3%)
    if last_kline_5m and last_kline_5m['close_price'] and last_kline_5m['open_price']:
        change_pct = (last_kline_5m['close_price'] - last_kline_5m['open_price']) / last_kline_5m['open_price'] * 100

        # 震荡市禁用momentum
        if is_ranging_market:
            logger.info(f"[RANGING-SKIP] {symbol} 震荡市,跳过momentum信号 (5M涨跌{change_pct:.2f}%)")
        else:
            # 趋势市正常使用
            if change_pct > 3:
                long_score += self.scoring_weights.get('momentum_up_3pct', {}).get('long', 0)
                signal_components['momentum_up_3pct'] = self.scoring_weights.get('momentum_up_3pct', {}).get('long', 0)
            elif change_pct < -3:
                short_score += self.scoring_weights.get('momentum_down_3pct', {}).get('short', 0)
                signal_components['momentum_down_3pct'] = self.scoring_weights.get('momentum_down_3pct', {}).get('short', 0)
    # ========== momentum评分结束 ==========
```

---

### 优化4: 动态止损止盈 ⚙️ 中优先级

**位置**: 自适应优化器参数应用

**当前参数**:
```python
long_stop_loss_pct: 0.0213 (2.13%)
long_take_profit_pct: 0.035 (3.5%)
short_stop_loss_pct: 0.02 (2.0%)
short_take_profit_pct: 0.05 (5.0%)
```

**震荡市调整**:
```python
# 在开仓前检测市场状态
if big4_result['overall_signal'] == 'NEUTRAL':
    # 震荡市: 缩小止损,提高止盈
    stop_loss_multiplier = 0.7   # 止损减小到70%
    take_profit_multiplier = 1.5  # 止盈提高到150%

    actual_stop_loss = stop_loss_pct * stop_loss_multiplier
    actual_take_profit = take_profit_pct * take_profit_multiplier

    logger.info(f"[RANGING-SL/TP] 震荡市调整: SL {stop_loss_pct*100:.2f}% -> {actual_stop_loss*100:.2f}%, TP {take_profit_pct*100:.2f}% -> {actual_take_profit*100:.2f}%")
else:
    # 趋势市: 使用原参数
    actual_stop_loss = stop_loss_pct
    actual_take_profit = take_profit_pct
```

**原因**:
- 震荡市波动小,需要更紧的止损避免回撤
- 震荡市止盈难,需要更高的止盈目标
- 趋势市则正常使用自适应参数

---

## 三、实施优先级

### Phase 1: 立即执行 (今天)
1. ✅ **Big4 NEUTRAL时提高阈值到50分** (优化1)
   - 修改文件: smart_trader_service.py Line 2718
   - 测试: 观察NEUTRAL时的开仓数量是否减少

2. ✅ **momentum权重降低 15->10** (优化2)
   - 修改文件: SmartDecisionBrain.__init__()
   - 影响: 需要更多信号配合才能达到35分阈值

### Phase 2: 明天验证 (2026-02-03)
3. ⚙️ **震荡市禁用momentum** (优化3)
   - 需要传递Big4结果到analyze()
   - 架构调整: 添加big4_result缓存机制

4. ⚙️ **动态止损止盈** (优化4)
   - 修改开仓逻辑
   - 需要测试不同市场状态下的表现

### Phase 3: 长期优化 (本周)
5. 📊 **统计分析**: 按Big4信号分组统计胜率
   ```sql
   SELECT
       big4_market_signal,
       COUNT(*) as total,
       SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
       AVG(realized_pnl) as avg_pnl,
       SUM(realized_pnl) as total_pnl
   FROM orders_futures
   WHERE account_id = 2
   GROUP BY big4_market_signal
   ```

6. 🔧 **A/B测试**: 对比优化前后的震荡市表现

---

## 四、预期效果

### 优化前（震荡市）
- 开仓频率: 高
- 胜率: 30-40%
- 主要亏损: momentum追涨杀跌

### 优化后（震荡市）
- 开仓频率: 降低30-40%
- 胜率: 提升到45-55%
- 过滤: 低质量momentum信号

### 对趋势市的影响
- ✅ 基本无影响 (Big4 BULLISH/BEARISH时正常开仓)
- ✅ momentum权重降低,但配合其他信号仍能达到阈值
- ✅ 整体提高了信号质量

---

## 五、代码修改清单

### 修改1: smart_trader_service.py (Line 2718后)
```python
# 在获取symbol_signal后添加
if symbol_signal == 'NEUTRAL':
    if signal_strength < 30:
        threshold_boost = 15
        if new_score < 35 + threshold_boost:
            logger.info(f"[BIG4-NEUTRAL-SKIP] {symbol} 震荡市且评分不足 ({new_score} < 50), 跳过")
            continue
        else:
            logger.info(f"[BIG4-NEUTRAL-OK] {symbol} 震荡市但评分足够 ({new_score} >= 50)")
```

### 修改2: SmartDecisionBrain.__init__() 权重
```python
self.scoring_weights = {
    # ... 其他权重 ...
    'momentum_up_3pct': {'long': 10, 'short': 0},    # 从15改为10
    'momentum_down_3pct': {'long': 0, 'short': 10},  # 从15改为10
    # ... 其他权重 ...
}
```

---

## 六、监控指标

优化后需要监控:
1. **Big4 NEUTRAL时的开仓数量** (应显著减少)
2. **momentum信号的胜率** (应有所提升)
3. **整体胜率** (震荡市应从30-40%提升到45-55%)
4. **日均开仓数** (可能减少,但质量提高)

---

**文档版本**: v1.0
**创建者**: Claude Sonnet 4.5
**实施日期**: 2026-02-02
**预期完成**: 2026-02-03
