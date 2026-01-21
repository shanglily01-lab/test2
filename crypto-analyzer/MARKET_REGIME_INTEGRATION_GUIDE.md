# 6小时市场状态集成指南

## 🎯 核心理念

**大盘决定方向,信号决定时机**

- 牛市时: 优先做多,降低做多门槛,提高做空门槛
- 熊市时: 优先做空,降低做空门槛,提高做多门槛
- 震荡时: 多空均衡,提高开仓门槛,快进快出

## 📊 市场状态判断标准

### 牛市 (Bull Market)
- 6小时内60%以上观察显示bullish
- BTC/ETH综合涨幅>1%
- **策略**: 增加30%仓位,降低5分门槛,优先LONG

### 熊市 (Bear Market)
- 6小时内60%以上观察显示bearish
- BTC/ETH综合跌幅>1%
- **策略**: 增加30%仓位,降低5分门槛,优先SHORT

### 震荡 (Neutral)
- 趋势不明确或涨跌幅<1%
- **策略**: 减少15%仓位,提高3分门槛,快进快出

## 🔗 集成到超级大脑

### 方法1: 在analyze()中应用市场倾向

```python
# 在 SmartDecisionBrain 类中添加

from app.services.market_regime_manager import MarketRegimeManager

class SmartDecisionBrain:
    def __init__(self, db_config: dict):
        # ... 现有代码 ...
        self.regime_manager = MarketRegimeManager(db_config)
        self.current_regime = None
        self.regime_last_check = None

    def analyze(self, symbol: str, current: float, klines_data: dict) -> dict:
        \"\"\"分析并返回交易信号\"\"\"

        # 定期检查市场状态 (每小时检查一次)
        self._update_market_regime()

        # ... 现有的信号分析代码 ...

        # 在最后判断时应用市场倾向
        if self.current_regime:
            # 应用市场状态调整
            score, side = self._apply_market_regime(score, side, symbol)

        return {
            'symbol': symbol,
            'side': side,
            'score': score,
            'current_price': current,
            'signal_components': signal_components,
            'market_regime': self.current_regime.get('regime') if self.current_regime else None
        }

    def _update_market_regime(self):
        \"\"\"更新市场状态\"\"\"
        from datetime import datetime, timedelta

        # 每小时检查一次,或首次运行
        if (not self.regime_last_check or
            datetime.now() - self.regime_last_check > timedelta(hours=1)):

            try:
                self.current_regime = self.regime_manager.get_current_regime()
                self.regime_last_check = datetime.now()

                if self.current_regime:
                    logger.info(f"📊 市场状态: {self.current_regime['regime']} | "
                              f"倾向: {self.current_regime.get('bias', 'balanced')}")
            except Exception as e:
                logger.error(f"更新市场状态失败: {e}")

    def _apply_market_regime(self, score: int, side: str, symbol: str) -> tuple:
        \"\"\"
        应用市场状态调整

        返回: (调整后的分数, 方向)
        \"\"\"
        if not self.current_regime:
            return score, side

        bias = self.current_regime.get('bias', 'balanced')
        score_adj = self.current_regime.get('score_threshold_adjustment', 0)

        # 根据市场倾向调整分数
        if bias == 'long' and side == 'LONG':
            # 牛市做多: 降低门槛
            adjusted_score = score - score_adj
            logger.debug(f"{symbol} LONG: 牛市加成 {score} -> {adjusted_score}")
            return adjusted_score, side

        elif bias == 'long' and side == 'SHORT':
            # 牛市做空: 提高门槛
            adjusted_score = score + abs(score_adj)
            logger.debug(f"{symbol} SHORT: 牛市惩罚 {score} -> {adjusted_score}")
            return adjusted_score, side

        elif bias == 'short' and side == 'SHORT':
            # 熊市做空: 降低门槛
            adjusted_score = score - score_adj
            logger.debug(f"{symbol} SHORT: 熊市加成 {score} -> {adjusted_score}")
            return adjusted_score, side

        elif bias == 'short' and side == 'LONG':
            # 熊市做多: 提高门槛
            adjusted_score = score + abs(score_adj)
            logger.debug(f"{symbol} LONG: 熊市惩罚 {score} -> {adjusted_score}")
            return adjusted_score, side

        elif bias == 'balanced':
            # 震荡市: 提高门槛
            adjusted_score = score + abs(score_adj)
            logger.debug(f"{symbol} {side}: 震荡市提高门槛 {score} -> {adjusted_score}")
            return adjusted_score, side

        return score, side
```

### 方法2: 在open_position()中应用仓位调整

```python
def open_position(self, opp: dict):
    \"\"\"开仓\"\"\"

    # ... 现有代码 ...

    # 应用市场状态仓位调整
    if self.current_regime:
        position_multiplier = self.current_regime.get('position_adjustment', 1.0)
        quantity = quantity * position_multiplier

        logger.info(f"市场状态仓位调整: {position_multiplier:.2f}x")

    # ... 继续现有开仓逻辑 ...
```

### 方法3: 添加市场状态过滤

```python
def should_open_position(self, opp: dict) -> bool:
    \"\"\"判断是否应该开仓\"\"\"

    # ... 现有检查 ...

    # 市场状态检查
    if self.current_regime:
        regime = self.current_regime.get('regime')
        side = opp['side']

        # 强势牛市拒绝做空
        if regime == 'bull_market' and side == 'SHORT':
            strength = self.current_regime.get('strength', 50)
            if strength > 80:
                logger.warning(f"拒绝开空仓: 强势牛市 (强度{strength})")
                return False

        # 强势熊市拒绝做多
        if regime == 'bear_market' and side == 'LONG':
            strength = self.current_regime.get('strength', 50)
            if strength < 20:
                logger.warning(f"拒绝开多仓: 强势熊市 (强度{strength})")
                return False

    return True
```

## 📅 定时任务配置

### Crontab设置

```bash
# 每6小时整点分析市场状态 (0, 6, 12, 18点)
0 */6 * * * cd /path/to/crypto-analyzer && python3 run_market_regime_analysis.py >> logs/regime_analysis_cron.log 2>&1

# 或者更精确的时间点
0 0,6,12,18 * * * cd /path/to/crypto-analyzer && python3 run_market_regime_analysis.py

# 同时保持5分钟的细粒度市场观察
*/5 * * * * cd /path/to/crypto-analyzer && python3 run_market_observer.py >> logs/market_observer.log 2>&1
```

## 📊 完整的优化频率配置

```bash
# ============================================================
# 完整的定时任务配置 (包含市场状态)
# ============================================================

# 1. 细粒度市场观察 - 每5分钟
*/5 * * * * python3 run_market_observer.py >> logs/market_observer.log 2>&1

# 2. 6小时市场状态分析 - 每6小时整点
0 */6 * * * python3 run_market_regime_analysis.py >> logs/regime_analysis.log 2>&1

# 3. 信号权重优化 - 每天凌晨2点
0 2 * * * python3 safe_weight_optimizer.py >> logs/weight_optimizer.log 2>&1

# 4. 重启服务(加载新权重) - 每天凌晨2:05
5 2 * * * pkill -f smart_trader_service.py && sleep 2 && nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &

# 5. 高级优化 - 每2天凌晨3点
0 3 */2 * * echo "y" | python3 run_advanced_optimization.py >> logs/advanced_optimizer.log 2>&1

# 6. 每日报告 - 每天早上8点
0 8 * * * python3 analyze_smart_brain_2days.py > logs/daily_report_$(date +\%Y\%m\%d).txt
```

## 🎯 决策流程

```
1. 每5分钟: 观察BTC/ETH/SOL/BNB/DOGE走势
   └─> 存入 market_observations 表

2. 每6小时: 分析市场状态
   ├─> 读取最近6小时的观察数据
   ├─> 判断牛市/熊市/震荡
   ├─> 计算仓位调整倍数
   ├─> 计算分数阈值调整
   └─> 存入 market_regime_states 表

3. 超级大脑每次分析时:
   ├─> 读取最新市场状态
   ├─> 计算信号分数
   ├─> 应用市场状态调整
   │   ├─> 牛市: LONG-5分, SHORT+5分
   │   ├─> 熊市: SHORT-5分, LONG+5分
   │   └─> 震荡: 都+3分
   ├─> 应用仓位倍数调整
   └─> 决定是否开仓
```

## 📈 预期效果

### 牛市场景
```
市场状态: BULL_MARKET (强度: 78)
BTC 6H: +2.5%, ETH 6H: +3.1%

原始信号: BTC/USDT LONG, 得分 35
市场调整: 35 - 5 = 30 (降低门槛!)
仓位调整: 1.3x (增加30%仓位)

结果: 更容易开LONG仓,仓位更大
```

### 熊市场景
```
市场状态: BEAR_MARKET (强度: 22)
BTC 6H: -3.2%, ETH 6H: -2.8%

原始信号: ETH/USDT LONG, 得分 32
市场调整: 32 + 5 = 37 (提高门槛!)
仓位调整: 1.0x (不增加)

结果: LONG很难开,即使开了仓位也正常
```

### 震荡场景
```
市场状态: NEUTRAL (强度: 48)
BTC 6H: +0.3%, ETH 6H: -0.2%

原始信号: SOL/USDT LONG, 得分 30
市场调整: 30 + 3 = 33 (提高门槛)
仓位调整: 0.85x (减少15%仓位)

结果: 开仓更谨慎,仓位更小
```

## 🔍 监控和验证

### 查看市场状态历史

```sql
-- 查看今天的市场状态变化
SELECT
    timestamp,
    regime,
    strength,
    bias,
    btc_6h_change,
    eth_6h_change,
    position_adjustment,
    score_threshold_adjustment
FROM market_regime_states
WHERE DATE(timestamp) = CURDATE()
ORDER BY timestamp DESC;

-- 统计最近7天的市场状态分布
SELECT
    regime,
    COUNT(*) as count,
    AVG(strength) as avg_strength,
    AVG(btc_6h_change) as avg_btc_change
FROM market_regime_states
WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY regime;
```

### 验证效果

```sql
-- 对比牛市和熊市期间的交易表现
SELECT
    mrs.regime,
    COUNT(fp.id) as total_trades,
    SUM(CASE WHEN fp.realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
    AVG(fp.realized_pnl) as avg_pnl,
    SUM(fp.realized_pnl) as total_pnl
FROM futures_positions fp
LEFT JOIN market_regime_states mrs
    ON fp.open_time >= mrs.timestamp
    AND fp.open_time < DATE_ADD(mrs.timestamp, INTERVAL 6 HOUR)
WHERE fp.source = 'smart_trader'
    AND fp.status = 'closed'
    AND fp.open_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY mrs.regime;
```

## ⚠️ 注意事项

1. **首次运行**: 需要至少3次市场观察数据(15分钟)才能分析
2. **市场突变**: 6小时窗口可能滞后,配合5分钟观察补充
3. **极端情况**: 强势市场可能拒绝反向开仓,注意监控
4. **回测验证**: 建议先运行1周观察效果再完全依赖

## 🚀 总结

6小时市场状态管理让超级大脑具备了**宏观判断能力**:

- ✅ **顺势而为**: 牛市做多,熊市做空
- ✅ **动态调整**: 自动调节开仓门槛和仓位大小
- ✅ **风险控制**: 震荡市减仓,趋势市增仓
- ✅ **智能过滤**: 极端市场拒绝反向交易

结合每天的信号权重优化和每2天的止盈止损优化,超级大脑将真正实现**全方位自适应**! 🎯

---

*创建时间: 2026-01-21*
*版本: v1.0 - Market Regime Integration*
