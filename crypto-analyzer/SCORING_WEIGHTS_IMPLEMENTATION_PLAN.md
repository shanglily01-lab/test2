# 评分权重自适应系统完整实施计划

## 📋 实施进度

- [x] **阶段1**: 数据库表设计（已完成）
- [x] **阶段2**: SmartDecisionBrain权重加载（已完成）
- [ ] **阶段3**: analyze()方法重构（进行中）
- [ ] **阶段4**: 开仓记录信号组成（待完成）
- [ ] **阶段5**: 权重优化算法（待完成）
- [ ] **阶段6**: 集成到AdaptiveOptimizer（待完成）
- [ ] **阶段7**: 测试和验证（待完成）

---

## ✅ 已完成工作

### 阶段1: 数据库表设计

**文件**: `app/database/signal_scoring_weights_schema.sql`

**创建的表**:
1. `signal_scoring_weights` - 12个评分组件的权重
2. `signal_component_performance` - 组件表现统计
3. `futures_positions` 添加字段:
   - `signal_components` TEXT - 信号组成（JSON）
   - `entry_score` INT - 开仓得分

**部署**:
```bash
mysql -h 13.212.252.171 -u admin -p'Tonny@1000' binance-data < app/database/signal_scoring_weights_schema.sql
```

### 阶段2: SmartDecisionBrain权重加载

**已修改**: `smart_trader_service.py` - `_load_config()`方法

**功能**:
- 从数据库加载12个评分组件的权重
- 如果表不存在，降级到硬编码默认权重
- 启动日志显示权重加载状态

---

## 🔄 待完成工作

### 阶段3: analyze()方法重构 ⏳

**目标**: 使用数据库权重替代硬编码权重

**需要修改的代码**:

```python
def analyze(self, symbol: str):
    """分析并决策 - 使用数据库权重"""
    if symbol not in self.whitelist:
        return None

    try:
        klines_1d = self.load_klines(symbol, '1d', 50)
        klines_1h = self.load_klines(symbol, '1h', 100)

        if len(klines_1d) < 30 or len(klines_1h) < 72:
            return None

        current = klines_1h[-1]['close']

        # 分别计算做多和做空得分
        long_score = 0
        short_score = 0

        # 记录信号组成（用于后续优化）
        signal_components = {}

        # ========== 1小时K线分析 ==========

        # 1. 位置评分 - 使用数据库权重
        high_72h = max(k['high'] for k in klines_1h[-72:])
        low_72h = min(k['low'] for k in klines_1h[-72:])
        position_pct = (current - low_72h) / (high_72h - low_72h) * 100 if high_72h != low_72h else 50

        if position_pct < 30:
            # 使用数据库权重而不是硬编码的20
            weight = self.scoring_weights.get('position_low', {'long': 20, 'short': 0})
            long_score += weight['long']
            short_score += weight['short']
            signal_components['position_low'] = weight['long'] if weight['long'] > 0 else weight['short']
        elif position_pct > 70:
            weight = self.scoring_weights.get('position_high', {'long': 0, 'short': 20})
            long_score += weight['long']
            short_score += weight['short']
            signal_components['position_high'] = weight['short'] if weight['short'] > 0 else weight['long']
        else:
            weight = self.scoring_weights.get('position_mid', {'long': 5, 'short': 5})
            long_score += weight['long']
            short_score += weight['short']
            signal_components['position_mid'] = weight['long']

        # 2. 短期动量 - 使用数据库权重
        gain_24h = (current - klines_1h[-24]['close']) / klines_1h[-24]['close'] * 100
        if gain_24h < -3:
            weight = self.scoring_weights.get('momentum_down_3pct', {'long': 15, 'short': 0})
            long_score += weight['long']
            signal_components['momentum_down_3pct'] = weight['long']
        elif gain_24h > 3:
            weight = self.scoring_weights.get('momentum_up_3pct', {'long': 0, 'short': 15})
            short_score += weight['short']
            signal_components['momentum_up_3pct'] = weight['short']

        # 3. 1小时趋势评分 - 使用数据库权重
        bullish_1h = sum(1 for k in klines_1h[-48:] if k['close'] > k['open'])
        bearish_1h = 48 - bullish_1h

        if bullish_1h > 30:
            weight = self.scoring_weights.get('trend_1h_bull', {'long': 20, 'short': 0})
            long_score += weight['long']
            signal_components['trend_1h_bull'] = weight['long']
        elif bearish_1h > 30:
            weight = self.scoring_weights.get('trend_1h_bear', {'long': 0, 'short': 20})
            short_score += weight['short']
            signal_components['trend_1h_bear'] = weight['short']

        # 4. 波动率评分 - 使用数据库权重
        recent_24h = klines_1h[-24:]
        volatility = (max(k['high'] for k in recent_24h) - min(k['low'] for k in recent_24h)) / current * 100

        if volatility > 5:
            weight = self.scoring_weights.get('volatility_high', {'long': 10, 'short': 10})
            if long_score > short_score:
                long_score += weight['long']
                signal_components['volatility_high'] = weight['long']
            else:
                short_score += weight['short']
                signal_components['volatility_high'] = weight['short']

        # 5. 连续趋势强化信号 - 使用数据库权重
        recent_10h = klines_1h[-10:]
        bullish_10h = sum(1 for k in recent_10h if k['close'] > k['open'])
        bearish_10h = 10 - bullish_10h
        gain_10h = (current - recent_10h[0]['close']) / recent_10h[0]['close'] * 100

        if bullish_10h >= 7 and gain_10h < 5 and position_pct < 70:
            weight = self.scoring_weights.get('consecutive_bull', {'long': 15, 'short': 0})
            long_score += weight['long']
            signal_components['consecutive_bull'] = weight['long']
        elif bearish_10h >= 7 and gain_10h > -5 and position_pct > 30:
            weight = self.scoring_weights.get('consecutive_bear', {'long': 0, 'short': 15})
            short_score += weight['short']
            signal_components['consecutive_bear'] = weight['short']

        # ========== 1天K线确认 ==========

        # 大趋势确认 - 使用数据库权重
        bullish_1d = sum(1 for k in klines_1d[-30:] if k['close'] > k['open'])
        bearish_1d = 30 - bullish_1d

        if bullish_1d > 18 and long_score > short_score:
            weight = self.scoring_weights.get('trend_1d_bull', {'long': 10, 'short': 0})
            long_score += weight['long']
            signal_components['trend_1d_bull'] = weight['long']
        elif bearish_1d > 18 and short_score > long_score:
            weight = self.scoring_weights.get('trend_1d_bear', {'long': 0, 'short': 10})
            short_score += weight['short']
            signal_components['trend_1d_bear'] = weight['short']

        # 选择得分更高的方向
        if long_score >= self.threshold or short_score >= self.threshold:
            if long_score >= short_score:
                side = 'LONG'
                score = long_score
            else:
                side = 'SHORT'
                score = short_score

            # 检查信号黑名单
            signal_key = f"SMART_BRAIN_{score}_{side}"
            if signal_key in self.signal_blacklist:
                logger.debug(f"{symbol} 信号 {signal_key} 在黑名单中，跳过")
                return None

            return {
                'symbol': symbol,
                'side': side,
                'score': score,
                'current_price': current,
                'signal_components': signal_components  # ✅ 新增：返回信号组成
            }

        return None

    except Exception as e:
        logger.error(f"{symbol} 分析失败: {e}")
        return None
```

**工作量**: 1-2小时

---

### 阶段4: 开仓记录信号组成

**目标**: 将signal_components写入数据库

**需要修改**: `SmartTraderService.open_position()`

```python
def open_position(self, opp: dict):
    """开仓 - 记录信号组成"""
    # ... 现有代码 ...

    # ✅ 新增：记录信号组成
    import json
    signal_components_json = json.dumps(opp.get('signal_components', {}))

    # INSERT时添加signal_components和entry_score字段
    cursor.execute("""
        INSERT INTO futures_positions
        (account_id, symbol, position_side, quantity, entry_price,
         stop_loss, take_profit, leverage, margin, status,
         entry_signal_type, entry_score, signal_components, open_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', %s, %s, %s, NOW())
    """, (
        self.account_id, symbol, side, quantity, current_price,
        stop_loss, take_profit, self.leverage, margin,
        f"SMART_BRAIN_{opp['score']}",  # entry_signal_type
        opp['score'],                    # ✅ entry_score
        signal_components_json           # ✅ signal_components
    ))
```

**工作量**: 30分钟

---

### 阶段5: 权重优化算法

**目标**: 根据历史表现自动调整权重

**新建文件**: `app/services/scoring_weight_optimizer.py`

```python
"""
评分权重优化器
根据历史表现动态调整各评分组件的权重
"""
import pymysql
import json
from loguru import logger

class ScoringWeightOptimizer:
    """评分权重优化器"""

    def __init__(self, db_config: dict):
        self.db_config = db_config

    def _get_connection(self):
        return pymysql.connect(
            **self.db_config,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

    def analyze_component_performance(self, days: int = 7):
        """分析各组件的表现"""
        conn = self._get_connection()
        cursor = conn.cursor()

        # 获取所有已平仓订单的信号组成
        cursor.execute("""
            SELECT
                signal_components,
                position_side,
                realized_pnl
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
            AND signal_components IS NOT NULL
            AND signal_components != ''
        """, (days,))

        orders = cursor.fetchall()
        cursor.close()
        conn.close()

        # 统计各组件的表现
        component_stats = {}

        for order in orders:
            try:
                components = json.loads(order['signal_components'])
                side = order['position_side']
                pnl = float(order['realized_pnl'])

                for component_name, weight in components.items():
                    key = f"{component_name}_{side}"

                    if key not in component_stats:
                        component_stats[key] = {
                            'total_orders': 0,
                            'win_orders': 0,
                            'total_pnl': 0,
                            'pnl_list': []
                        }

                    component_stats[key]['total_orders'] += 1
                    if pnl > 0:
                        component_stats[key]['win_orders'] += 1
                    component_stats[key]['total_pnl'] += pnl
                    component_stats[key]['pnl_list'].append(pnl)
            except:
                continue

        # 计算每个组件的表现评分
        results = {}
        for key, stats in component_stats.items():
            if stats['total_orders'] < 5:  # 至少5笔订单
                continue

            win_rate = stats['win_orders'] / stats['total_orders']
            avg_pnl = stats['total_pnl'] / stats['total_orders']

            # 组件表现评分：基于胜率和平均盈亏
            # 基准：胜率50%，平均盈亏$0
            win_rate_score = (win_rate - 0.50) * 100  # -50 to +50
            pnl_score = avg_pnl / 5  # 归一化到-20 to +20范围

            performance_score = (win_rate_score * 0.6) + (pnl_score * 0.4)

            results[key] = {
                'total_orders': stats['total_orders'],
                'win_rate': win_rate,
                'avg_pnl': avg_pnl,
                'performance_score': performance_score
            }

        return results

    def adjust_weights(self, component_performance: dict):
        """根据表现调整权重"""
        conn = self._get_connection()
        cursor = conn.cursor()

        adjustments = []

        for key, perf in component_performance.items():
            # 解析组件名和方向
            parts = key.rsplit('_', 1)
            if len(parts) != 2:
                continue

            component_name, side = parts
            performance_score = perf['performance_score']

            # 计算调整量
            if performance_score > 10:
                adjustment = +3  # 表现优秀，增加权重
            elif performance_score > 5:
                adjustment = +2
            elif performance_score < -10:
                adjustment = -3  # 表现差，减少权重
            elif performance_score < -5:
                adjustment = -2
            else:
                adjustment = 0  # 表现正常，不调整

            if adjustment == 0:
                continue

            # 更新数据库
            if side == 'LONG':
                cursor.execute("""
                    UPDATE signal_scoring_weights
                    SET weight_long = GREATEST(5, LEAST(30, weight_long + %s)),
                        performance_score = %s,
                        last_adjusted = NOW(),
                        adjustment_count = adjustment_count + 1
                    WHERE signal_component = %s
                """, (adjustment, performance_score, component_name))
            else:  # SHORT
                cursor.execute("""
                    UPDATE signal_scoring_weights
                    SET weight_short = GREATEST(5, LEAST(30, weight_short + %s)),
                        performance_score = %s,
                        last_adjusted = NOW(),
                        adjustment_count = adjustment_count + 1
                    WHERE signal_component = %s
                """, (adjustment, performance_score, component_name))

            if cursor.rowcount > 0:
                adjustments.append({
                    'component': component_name,
                    'side': side,
                    'adjustment': adjustment,
                    'performance_score': performance_score,
                    'win_rate': perf['win_rate'],
                    'avg_pnl': perf['avg_pnl']
                })

        conn.commit()
        cursor.close()
        conn.close()

        return adjustments

    def optimize(self, days: int = 7):
        """执行优化"""
        logger.info(f"🔍 分析最近{days}天的组件表现...")

        # 分析表现
        performance = self.analyze_component_performance(days)

        if not performance:
            logger.warning("⚠️  没有足够的数据进行优化")
            return []

        # 调整权重
        adjustments = self.adjust_weights(performance)

        if adjustments:
            logger.info(f"✅ 调整了 {len(adjustments)} 个组件的权重:")
            for adj in adjustments:
                logger.info(
                    f"   {adj['component']} {adj['side']}: "
                    f"{adj['adjustment']:+d} "
                    f"(胜率{adj['win_rate']*100:.1f}%, "
                    f"平均{adj['avg_pnl']:+.2f}, "
                    f"评分{adj['performance_score']:+.1f})"
                )
        else:
            logger.info("✅ 所有组件表现正常，无需调整")

        return adjustments
```

**工作量**: 2-3小时

---

### 阶段6: 集成到AdaptiveOptimizer

**修改文件**: `app/services/adaptive_optimizer.py`

在`run_daily_optimization()`方法中添加：

```python
def run_daily_optimization(self):
    """每日优化 - 包含权重优化"""

    # 现有的黑名单和参数优化...

    # ✅ 新增：权重优化
    from app.services.scoring_weight_optimizer import ScoringWeightOptimizer

    weight_optimizer = ScoringWeightOptimizer(self.db_config)
    adjustments = weight_optimizer.optimize(days=7)

    logger.info(f"📊 评分权重优化完成，调整了 {len(adjustments)} 个组件")
```

**工作量**: 30分钟

---

### 阶段7: 测试和验证

**测试步骤**:

1. **部署数据库表**
   ```bash
   mysql < app/database/signal_scoring_weights_schema.sql
   ```

2. **验证权重加载**
   ```bash
   # 重启服务，查看日志
   tail -100 logs/smart_trader.log | grep "评分权重"
   # 预期: "📊 评分权重: 从数据库加载 12 个组件"
   ```

3. **验证信号记录**
   ```sql
   SELECT id, symbol, entry_signal_type, entry_score, signal_components
   FROM futures_positions
   WHERE signal_components IS NOT NULL
   ORDER BY id DESC
   LIMIT 5;
   ```

4. **手动触发优化**
   ```python
   from app.services.scoring_weight_optimizer import ScoringWeightOptimizer
   optimizer = ScoringWeightOptimizer(db_config)
   adjustments = optimizer.optimize(days=7)
   print(adjustments)
   ```

5. **验证权重更新**
   ```sql
   SELECT signal_component, weight_long, weight_short,
          performance_score, last_adjusted
   FROM signal_scoring_weights
   ORDER BY last_adjusted DESC;
   ```

**工作量**: 1小时

---

## 📊 预期效果

### 第1周（数据收集）
- 记录所有信号组成
- 无权重调整

### 第2周（首次优化）
- 分析各组件表现
- 调整权重（+/-2到3分）
- 表现好的组件增加权重
- 表现差的组件减少权重

### 第3-4周（持续优化）
- 权重逐步趋于最优
- 整体胜率提升5-10%
- 盈利因子提升20-30%

### 长期（3个月）
- 完全自适应的评分系统
- 权重根据市场自动调整
- 系统达到最优状态

---

## ⚠️ 注意事项

1. **数据积累期**
   - 前7天不调整权重，只收集数据
   - 确保每个组件至少有5笔订单

2. **权重边界**
   - 最小权重: 5分（避免完全禁用）
   - 最大权重: 30分（避免过度依赖）

3. **调整频率**
   - 建议每7天调整一次
   - 不要每天调整（避免过拟合）

4. **回滚机制**
   - 保留base_weight作为基准
   - 如果系统表现变差，可以重置权重

---

## 🚀 快速开始

如果您现在想立即部署，按以下步骤：

```bash
# 1. 部署数据库表
cd ~/crypto-analyzer
git pull origin master
mysql -h 13.212.252.171 -u admin -p'Tonny@1000' binance-data < app/database/signal_scoring_weights_schema.sql

# 2. 重启服务（已包含权重加载）
kill $(pgrep -f smart_trader_service.py)
nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &

# 3. 验证权重加载
tail -100 logs/smart_trader.log | grep "评分权重"

# 4. 后续完成阶段3-6的代码（需要4-6小时）
```

---

**创建时间**: 2026-01-20
**版本**: 1.0
**状态**: 阶段1-2已完成，阶段3-7待实施
**预计总工作量**: 4-6小时
