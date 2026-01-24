# 超级大脑开仓和平仓需求文档

## 文档信息
- 创建时间: 2026-01-24
- 状态: 需求收集中
- 相关组件: SmartDecisionBrain, RealtimePositionMonitor

---

## 📋 当前系统概况

### 开仓逻辑 (SmartDecisionBrain)
- **过滤**: 黑名单(5个) + 白名单(12个LONG币种)
- **评分系统**: 仓位分析 + 趋势分析 + 支撑阻力分析
- **开仓阈值**: 总分 >= 30分
- **动态参数**:
  - 45+分: 持仓6小时
  - 30-44分: 持仓4小时
  - <30分: 持仓2小时

### 平仓逻辑 (RealtimePositionMonitor)
- **硬止损**: 亏损 >= 2.5% 立即平仓
- **开仓冷却**: 15分钟内只更新盈利，不触发移动止盈
- **移动止盈**:
  - 激活条件: 盈利 >= 1.5%
  - 回撤平仓: 从最高盈利回撤 >= 0.5%

### 已知问题
- [ ] 第358行变量名错误: `score` 应为 `total_score`
- [ ] 没有重复持仓检查
- [ ] 没有基于时间的强制平仓
- [ ] 没有重评分机制

---

## 💡 需求描述

### 1. 开仓需求

(请在此处填写您的开仓需求)
　１、现在我们的逻辑是比较粗糙的，检查到开仓的信号就直接开仓了，这是明智的。
　(1) 同一个方向只开一个单，是限制了分批建仓的，一个信号是否优秀，一次性建仓可能并不明智，应该分3次建仓，30/30/40完成建仓；
（2） 建仓的时机是否是最佳的？如果开多单，当前正在下跌（通过实时数据检测得到），那为什么一定要在这个价位建仓呢？是不是等几根5M K线更好，等出现一根阳线K线建仓 效果更优？如果是开空，当前价格是上涨的，在此价格开空也是不明智的。
（3）但信号已经发出，需要在30分钟内完成建仓，且分3次建仓，什么时候建仓，以什么价位进的，就非常关键，需要一个合理的算法支撑；




### 2. 平仓需求

(请在此处填写您的平仓需求)

2、关于平仓，我们现在做的也是比较武断的，到了时间就平了，或者信号一发出我们就平仓了，是否是最优呢？我看未必
（1）在平仓信号发出前30分钟，其实我们就该检测实时价格了，我们需要计算当前订单是否盈利，盈利多少的问题。如果盈利大于3%，实时价格又高于3%的时候，我们可以放任不管，如果价格下调呢？该怎么处理？应该实时观察5M K线了，如果发现K线开始下调2根，应该止盈；盈利1% 以上，我觉得都应该这么处理，应为平仓的时间快到了。如果是亏损呢，亏损0.5% 或以内，我们是不是可以考虑在 实时价格不亏损的情况下或者微盈利的情况下平仓，至少不亏手续费。如果亏损超过1% 或以上的就不适用了。
（2）如果到了平仓时间，或者平仓信号已经发出，但是目前还亏损 0.5% 以内，我们是否可以延长30分钟，在这30分钟内捕捉 在价格不亏损的情况下平仓。这也需要综合考虑，需要算法支持。我觉得5M ，15M K线和动向是不错的标的。



### 3. 风险控制需求

(请在此处填写风险控制相关需求)





### 4. 其他需求

(请在此处填写其他需求或想法)





---

## 📊 技术实现建议

### 开仓优化：智能分批建仓系统

#### 1. 分批建仓策略
```python
建仓计划：
- 第1批：30% (探仓)
- 第2批：30% (确认)
- 第3批：40% (主仓)
时间窗口：30分钟内完成
```

#### 2. 入场时机算法（动态价格评估体系）

**核心理念：前5分钟建立价格基线，动态评估最优入场点**

**阶段一：价格基线建立期（信号发出后0-5分钟）**

```python
# 数据采集阶段
price_samples = []  # 价格样本池
price_update_interval = 10  # 每10秒采样一次

for t in range(0, 300, 10):  # 5分钟 = 300秒
    current_price = get_realtime_price(symbol)
    price_samples.append({
        'price': current_price,
        'timestamp': now(),
        'elapsed': t
    })

# 统计分析
baseline = {
    'signal_price': signal_price,  # 信号价格
    'avg_price': mean(price_samples),  # 5分钟平均价
    'max_price': max(price_samples),   # 5分钟最高价
    'min_price': min(price_samples),   # 5分钟最低价
    'volatility': std(price_samples),  # 价格波动率
    'trend': calculate_trend(price_samples),  # 趋势：'up'/'down'/'sideways'
}

# 计算价格分位数（用于判断价格优劣）
percentiles = {
    'p90': percentile(price_samples, 90),  # 90%分位数
    'p75': percentile(price_samples, 75),  # 75%分位数
    'p50': percentile(price_samples, 50),  # 中位数
    'p25': percentile(price_samples, 25),  # 25%分位数
    'p10': percentile(price_samples, 10),  # 10%分位数
}
```

**阶段二：动态入场执行期（5-30分钟）**

**做多单智能入场策略：**
```python
# 目标：在30分钟内以低于p25分位数的价格分批买入

# 第1批建仓(30%) - 捕捉低价机会
入场条件（满足任一）：
1. current_price <= baseline['p10']:
   # 价格跌到10%分位数以下（极优价格）
   → 立即建仓第1批，权重100%

2. current_price <= baseline['p25'] AND 检测到止跌信号():
   # 价格在25%分位数以下且出现止跌
   → 立即建仓第1批，权重90%

3. current_price <= baseline['min_price'] * 0.999:
   # 价格跌破5分钟最低价
   → 立即建仓第1批，权重95%

4. 距离信号时间 >= 12分钟 AND current_price <= baseline['p50']:
   # 12分钟后价格仍低于中位数
   → 建仓第1批，权重70%

5. baseline['trend'] == 'up' AND current_price >= baseline['p75']:
   # 强上涨趋势且价格已升至75%分位数
   → 立即建仓避免错过，权重60%

超时兜底：
- 距离信号15分钟仍未建仓 → 按当前价强制建仓第1批

# 第2批建仓(30%) - 等待回调或确认趋势
入场条件（满足任一）：
1. current_price <= batch1_price * 0.997:
   # 回调至第1批价格-0.3%
   → 立即建仓第2批

2. current_price <= baseline['p25'] AND 距离第1批 >= 3分钟:
   # 价格仍在25%分位数以下
   → 建仓第2批

3. 检测到第二次止跌信号() AND 距离第1批 >= 5分钟:
   # 再次出现止跌信号
   → 建仓第2批

超时兜底：
- 距离第1批12分钟仍未建仓 → 按当前价强制建仓第2批

# 第3批建仓(40%) - 完成建仓
入场条件（满足任一）：
1. current_price <= (batch1_price + batch2_price) / 2:
   # 价格不高于前两批均价
   → 立即建仓第3批

2. current_price <= baseline['p50'] AND 距离第2批 >= 3分钟:
   # 价格仍低于中位数
   → 建仓第3批

超时兜底：
- 距离信号28分钟 → 按当前价强制建仓第3批
```

**做空单智能入场策略：**（镜像逻辑）
```python
# 目标：在30分钟内以高于p75分位数的价格分批卖空

# 第1批建仓(30%) - 捕捉高价机会
入场条件（满足任一）：
1. current_price >= baseline['p90']:
   # 价格升至90%分位数以上（极优价格）
   → 立即建仓第1批，权重100%

2. current_price >= baseline['p75'] AND 检测到止涨信号():
   # 价格在75%分位数以上且出现止涨
   → 立即建仓第1批，权重90%

3. current_price >= baseline['max_price'] * 1.001:
   # 价格突破5分钟最高价
   → 立即建仓第1批，权重95%

4. 距离信号时间 >= 12分钟 AND current_price >= baseline['p50']:
   # 12分钟后价格仍高于中位数
   → 建仓第1批，权重70%

5. baseline['trend'] == 'down' AND current_price <= baseline['p25']:
   # 强下跌趋势且价格已降至25%分位数
   → 立即建仓避免错过，权重60%

超时兜底：
- 距离信号15分钟仍未建仓 → 按当前价强制建仓第1批

# 第2批、第3批：逻辑镜像做多（反向阈值）
```

**价格评估指标说明：**

| 指标 | 做多目标 | 做空目标 | 含义 |
|------|---------|---------|------|
| p10分位数 | 最优买入价 | - | 5分钟内仅10%时间低于此价 |
| p25分位数 | 优秀买入价 | - | 5分钟内仅25%时间低于此价 |
| p50中位数 | 合理买入价 | 合理卖空价 | 5分钟中间价格 |
| p75分位数 | - | 优秀卖空价 | 5分钟内仅25%时间高于此价 |
| p90分位数 | - | 最优卖空价 | 5分钟内仅10%时间高于此价 |
| volatility | 波动率调整 | 波动率调整 | 高波动时放宽标准 |
| trend | 趋势确认 | 趋势确认 | 强趋势时优先入场 |

#### 3. 趋势与反转信号检测

**趋势计算（基于5分钟价格样本）：**
```python
def calculate_trend(price_samples):
    """
    计算价格趋势方向和强度

    Args:
        price_samples: 5分钟内的价格样本列表

    Returns:
        {'direction': 'up'/'down'/'sideways', 'strength': 0-1}
    """
    prices = [p['price'] for p in price_samples]

    # 方法1: 线性回归斜率
    from scipy.stats import linregress
    x = list(range(len(prices)))
    slope, intercept, r_value, p_value, std_err = linregress(x, prices)

    # 方法2: 首尾价格对比
    first_price = prices[0]
    last_price = prices[-1]
    change_pct = (last_price - first_price) / first_price * 100

    # 判断趋势
    if abs(change_pct) < 0.15:
        return {'direction': 'sideways', 'strength': 0.3}
    elif change_pct > 0:
        strength = min(abs(change_pct) / 0.5, 1.0)  # 0.5%变化=100%强度
        return {'direction': 'up', 'strength': strength}
    else:
        strength = min(abs(change_pct) / 0.5, 1.0)
        return {'direction': 'down', 'strength': strength}
```

**止跌信号检测（做多用）：**
```python
def detect_bottom_signal(symbol, price_history):
    """
    检测止跌信号（多种方法综合评分）

    Returns:
        信号强度 0-100分
    """
    score = 0

    # 方法1: 实时价格连续上涨（权重30分）
    recent_prices = price_history[-6:]  # 最近6次采样（约1分钟）
    if len(recent_prices) >= 3:
        consecutive_ups = 0
        for i in range(1, len(recent_prices)):
            if recent_prices[i] > recent_prices[i-1]:
                consecutive_ups += 1

        if consecutive_ups >= 2:
            score += 15
        if consecutive_ups >= 4:
            score += 15  # 连续上涨加强信号

    # 方法2: V型反转检测（权重30分）
    if len(price_history) >= 30:  # 至少5分钟数据
        recent_5m = price_history[-30:]
        min_price = min([p['price'] for p in recent_5m])
        current_price = recent_5m[-1]['price']
        rebound_pct = (current_price - min_price) / min_price * 100

        if rebound_pct >= 0.15:
            score += 15
        if rebound_pct >= 0.3:
            score += 15  # 强反弹

    # 方法3: 成交量放大检测（权重20分）- 需要订阅实时成交数据
    # 暂时跳过，后续可接入

    # 方法4: K线实体确认（权重20分）
    latest_kline = get_latest_kline(symbol, '5m')
    if latest_kline and latest_kline['close'] > latest_kline['open']:
        body_pct = (latest_kline['close'] - latest_kline['open']) / latest_kline['open'] * 100
        if body_pct >= 0.2:
            score += 10
        if body_pct >= 0.4:
            score += 10  # 强阳线

    return score

# 使用示例
signal_strength = detect_bottom_signal(symbol, price_history)
if signal_strength >= 50:
    # 强止跌信号，可以配合入场
    pass
```

**止涨信号检测（做空用）：**
```python
def detect_top_signal(symbol, price_history):
    """
    检测止涨信号（逻辑镜像止跌检测）

    Returns:
        信号强度 0-100分
    """
    score = 0

    # 方法1: 实时价格连续下跌
    recent_prices = price_history[-6:]
    if len(recent_prices) >= 3:
        consecutive_downs = 0
        for i in range(1, len(recent_prices)):
            if recent_prices[i] < recent_prices[i-1]:
                consecutive_downs += 1

        if consecutive_downs >= 2:
            score += 15
        if consecutive_downs >= 4:
            score += 15

    # 方法2: 倒V型检测
    if len(price_history) >= 30:
        recent_5m = price_history[-30:]
        max_price = max([p['price'] for p in recent_5m])
        current_price = recent_5m[-1]['price']
        pullback_pct = (max_price - current_price) / max_price * 100

        if pullback_pct >= 0.15:
            score += 15
        if pullback_pct >= 0.3:
            score += 15

    # 方法4: K线实体确认
    latest_kline = get_latest_kline(symbol, '5m')
    if latest_kline and latest_kline['close'] < latest_kline['open']:
        body_pct = (latest_kline['open'] - latest_kline['close']) / latest_kline['open'] * 100
        if body_pct >= 0.2:
            score += 10
        if body_pct >= 0.4:
            score += 10  # 强阴线

    return score

实现：
def detect_top_signal(symbol):
    # 方法1: 检查实时价格趋势
    recent_prices = get_recent_prices(symbol, count=3)
    if all(recent_prices[i] < recent_prices[i-1] for i in range(1, 3)):
        return True  # 连续下跌

    # 方法2: 检查5分钟回落幅度
    prices_5m = get_prices_last_5_minutes(symbol)
    max_price = max(prices_5m)
    current_price = prices_5m[-1]
    pullback_pct = (max_price - current_price) / max_price * 100
    if pullback_pct >= 0.2:
        return True  # 回落>=0.2%

    # 方法3: 检查最新5M K线
    latest_kline = get_latest_kline(symbol, '5m')
    if latest_kline['close'] < latest_kline['open']:
        body_pct = (latest_kline['open'] - latest_kline['close']) / latest_kline['open'] * 100
        if body_pct >= 0.3:
            return True  # 强阴线

    return False
```

#### 4. 实时价格采样与管理

**价格采样策略（滚动窗口）：**
```python
class PriceSampler:
    """实时价格采样器（建仓期间使用）"""

    def __init__(self, symbol, window_seconds=300):
        """
        初始化采样器

        Args:
            symbol: 交易对
            window_seconds: 滚动窗口大小（秒），默认5分钟
        """
        self.symbol = symbol
        self.window_seconds = window_seconds  # 滚动窗口: 5分钟
        self.samples = []  # 价格样本（滚动更新）
        self.baseline = None  # 初始价格基线
        self.sampling_started = False
        self.initial_baseline_built = False

    async def start_background_sampling(self):
        """
        启动后台持续采样（独立协程）

        在整个30分钟建仓期间持续运行
        """
        self.sampling_started = True
        logger.info(f"📊 {self.symbol} 开始后台价格采样（滚动窗口5分钟）")

        while self.sampling_started:
            current_price = await self.get_realtime_price()
            current_time = datetime.now()

            # 添加新样本
            self.samples.append({
                'price': current_price,
                'timestamp': current_time
            })

            # 清理超出窗口的旧样本
            cutoff_time = current_time - timedelta(seconds=self.window_seconds)
            self.samples = [
                s for s in self.samples
                if s['timestamp'] >= cutoff_time
            ]

            # 前5分钟建立初始基线
            if not self.initial_baseline_built:
                elapsed = (current_time - self.samples[0]['timestamp']).seconds
                if elapsed >= 300:  # 5分钟后
                    self.baseline = self._build_baseline()
                    self.initial_baseline_built = True
                    logger.info(f"✅ {self.symbol} 初始基线建立完成: "
                               f"中位数={self.baseline['p50']:.6f}, "
                               f"波动率={self.baseline['volatility']:.4f}%, "
                               f"趋势={self.baseline['trend']['direction']}")

            await asyncio.sleep(10)  # 每10秒采样一次

    def stop_sampling(self):
        """停止采样"""
        self.sampling_started = False
        logger.info(f"⏹️ {self.symbol} 停止价格采样，共采集 {len(self.samples)} 个样本")

    def _build_baseline(self):
        """
        根据当前采样数据建立/更新价格基线

        Returns:
            价格基线字典（包含分位数、趋势等）
        """
        if len(self.samples) < 10:
            return None  # 样本不足

        prices = [s['price'] for s in self.samples]

        import numpy as np

        baseline = {
            'signal_price': prices[0] if not self.baseline else self.baseline['signal_price'],  # 保持初始信号价格
            'avg_price': np.mean(prices),
            'max_price': np.max(prices),
            'min_price': np.min(prices),
            'volatility': (np.std(prices) / np.mean(prices)) * 100,  # 波动率%

            # 分位数（基于滚动窗口实时计算）
            'p90': np.percentile(prices, 90),
            'p75': np.percentile(prices, 75),
            'p50': np.percentile(prices, 50),  # 中位数
            'p25': np.percentile(prices, 25),
            'p10': np.percentile(prices, 10),

            # 趋势（基于滚动窗口）
            'trend': self._calculate_trend(prices),

            # 采样元数据
            'sample_count': len(prices),
            'window_seconds': self.window_seconds,
            'time_range': f"{self.samples[0]['timestamp'].strftime('%H:%M:%S')} - {self.samples[-1]['timestamp'].strftime('%H:%M:%S')}",
            'updated_at': datetime.now()
        }

        return baseline

    def get_current_baseline(self):
        """
        获取当前实时基线（基于滚动窗口）

        Returns:
            实时更新的价格基线
        """
        if len(self.samples) >= 10:
            return self._build_baseline()
        elif self.baseline:
            return self.baseline  # 返回初始基线
        else:
            return None

    def _calculate_trend(self, prices):
        """计算趋势方向和强度"""
        first_price = prices[0]
        last_price = prices[-1]
        change_pct = (last_price - first_price) / first_price * 100

        if abs(change_pct) < 0.15:
            return {'direction': 'sideways', 'strength': 0.3, 'change_pct': change_pct}
        elif change_pct > 0:
            strength = min(abs(change_pct) / 0.5, 1.0)
            return {'direction': 'up', 'strength': strength, 'change_pct': change_pct}
        else:
            strength = min(abs(change_pct) / 0.5, 1.0)
            return {'direction': 'down', 'strength': strength, 'change_pct': change_pct}

    def is_good_long_price(self, current_price):
        """
        判断当前价格是否适合做多入场（基于实时滚动基线）

        Returns:
            {'suitable': bool, 'score': 0-100, 'reason': str}
        """
        # 获取实时基线（基于滚动窗口）
        baseline = self.get_current_baseline()

        if not baseline:
            return {'suitable': False, 'score': 0, 'reason': '基线未建立'}

        score = 0
        reasons = []

        # 评分标准1: 价格分位数（权重50分）
        if current_price <= baseline['p10']:
            score += 50
            reasons.append(f"极优价格(p10={baseline['p10']:.6f})")
        elif current_price <= baseline['p25']:
            score += 40
            reasons.append(f"优秀价格(p25={baseline['p25']:.6f})")
        elif current_price <= baseline['p50']:
            score += 25
            reasons.append(f"合理价格(p50={baseline['p50']:.6f})")
        else:
            score += 10
            reasons.append(f"偏高价格(>p50)")

        # 评分标准2: 相对最低价（权重30分）
        if current_price <= baseline['min_price']:
            score += 30
            reasons.append(f"跌破滚动最低价({baseline['min_price']:.6f})")
        elif current_price <= baseline['min_price'] * 1.002:
            score += 20
            reasons.append(f"接近滚动最低价")

        # 评分标准3: 趋势确认（权重20分）
        if baseline['trend']['direction'] == 'down':
            score += 10
            reasons.append("下跌趋势（利于做多抄底）")
        elif baseline['trend']['direction'] == 'up' and baseline['trend']['strength'] > 0.7:
            score += 20
            reasons.append("强上涨趋势（利于做多追涨）")

        suitable = score >= 50  # 50分以上认为合适
        return {
            'suitable': suitable,
            'score': score,
            'reason': ' | '.join(reasons),
            'current_price': current_price,
            'baseline_updated_at': baseline['updated_at']
        }

    def is_good_short_price(self, current_price):
        """判断当前价格是否适合做空入场（基于实时滚动基线）"""
        # 获取实时基线（基于滚动窗口）
        baseline = self.get_current_baseline()

        if not baseline:
            return {'suitable': False, 'score': 0, 'reason': '基线未建立'}

        score = 0
        reasons = []

        # 评分标准1: 价格分位数（权重50分）
        if current_price >= baseline['p90']:
            score += 50
            reasons.append(f"极优价格(p90={baseline['p90']:.6f})")
        elif current_price >= baseline['p75']:
            score += 40
            reasons.append(f"优秀价格(p75={baseline['p75']:.6f})")
        elif current_price >= baseline['p50']:
            score += 25
            reasons.append(f"合理价格(p50={baseline['p50']:.6f})")
        else:
            score += 10
            reasons.append(f"偏低价格(<p50)")

        # 评分标准2: 相对最高价（权重30分）
        if current_price >= baseline['max_price']:
            score += 30
            reasons.append(f"突破滚动最高价({baseline['max_price']:.6f})")
        elif current_price >= baseline['max_price'] * 0.998:
            score += 20
            reasons.append(f"接近滚动最高价")

        # 评分标准3: 趋势确认（权重20分）
        if baseline['trend']['direction'] == 'up':
            score += 10
            reasons.append("上涨趋势（利于做空高点）")
        elif baseline['trend']['direction'] == 'down' and baseline['trend']['strength'] > 0.7:
            score += 20
            reasons.append("强下跌趋势（利于做空追跌）")

        suitable = score >= 50
        return {
            'suitable': suitable,
            'score': score,
            'reason': ' | '.join(reasons),
            'current_price': current_price,
            'baseline_updated_at': baseline['updated_at']
        }
```

**滚动窗口示意图：**
```
时间线: 0----5----10----15----20----25----30分钟
       |<--- 5分钟窗口 --->|
初始:  [采样建立基线.........]
                     |<--- 5分钟窗口 --->|
第10分钟:              [实时更新基线.........]
                               |<--- 5分钟窗口 --->|
第15分钟:                        [实时更新基线.........]

特点：
- 每10秒采样一次价格
- 始终保持最近5分钟的样本（30个样本）
- 基线指标（p10/p25/p50/p75/p90）实时更新
- 趋势判断基于滚动窗口，更灵敏
```

---

### 平仓优化：智能退出系统

#### 1. 提前30分钟监控机制

**监控触发条件：**
```python
距离预定平仓时间 <= 30分钟时激活
或
检测到反向信号时激活
```

**盈利分层处理：**

**A. 盈利 >= 3%：**
```python
策略：回撤止盈机制

监控逻辑：
- 实时监控价格（WebSocket推送）
- 记录最高盈利点
- 从最高盈利点回撤 >= 0.5% → 立即平仓

示例：
- 盈利从3.5%回撤到3.0% → 回撤0.5%，立即平仓
- 盈利从5%回撤到4.4% → 回撤0.6%，立即平仓

目标：锁定高额利润，避免大幅回撤
```

**B. 盈利 1% - 3%：**
```python
策略：回撤止盈机制（更敏感）

监控逻辑：
- 实时监控价格（WebSocket推送）
- 记录最高盈利点
- 从最高盈利点回撤 >= 0.4% → 立即平仓

示例：
- 盈利从2.5%回撤到2.1% → 回撤0.4%，立即平仓
- 盈利从1.8%回撤到1.4% → 回撤0.4%，立即平仓

目标：保护中等利润，避免利润缩水
```

**C. 盈利 0% - 1%：**
```python
策略：突破1%立即平仓

监控逻辑：
- 实时监控价格（WebSocket推送）
- 盈利 >= 1.0% → 立即平仓

原因：
- 微盈利区间波动大
- 达到1%即确认盈利，快速落袋为安
- 避免来回震荡变成亏损

示例：
- 盈利从0.5%上升到1.0% → 立即平仓
- 盈利从0.8%上升到1.05% → 立即平仓
```

**D. 亏损 0% - 0.5%：**
```python
策略：实时捕捉盈亏平衡点 + 延长机制

第一阶段（提前30分钟监控期）：
- 实时监控价格（WebSocket推送）
- 价格 >= 入场均价（盈亏平衡） → 立即平仓
- 价格盈利 >= 0.1% → 立即平仓

第二阶段（如果到达预定平仓时间仍亏损）：
- 延长30分钟
- 继续实时监控价格
- 价格 >= 入场均价 → 立即平仓
- 30分钟后仍亏损 → 市价平仓

目标：
- 优先等待价格回到不亏损
- 尽量避免亏损出场
- 至少不亏手续费
```

**E. 亏损 > 0.5%：**
```python
策略：按原计划平仓，不延长

原因：
- 亏损已较大
- 延长可能扩大损失
- 及时止损，保护本金

执行：
- 到达预定平仓时间 → 立即市价平仓
- 或反向信号出现 → 立即市价平仓
```

#### 2. 实时价格监控机制

**WebSocket实时价格推送：**
```python
监控频率：毫秒级（价格变动即触发）

价格变动触发检查：
1. 计算当前盈亏百分比
2. 根据盈亏区间执行对应策略
3. 触发平仓条件则立即执行

优势：
- 响应速度快（毫秒级）
- 不依赖K线周期（5分钟太长）
- 精确捕捉盈亏平衡点
- 避免错过最佳平仓时机
```

**盈亏计算：**
```python
对于做多单：
current_pnl_pct = (current_price - avg_entry_price) / avg_entry_price * 100

对于做空单：
current_pnl_pct = (avg_entry_price - current_price) / avg_entry_price * 100

最高盈利记录：
if current_pnl_pct > max_profit_pct:
    max_profit_pct = current_pnl_pct
    max_profit_price = current_price
```

#### 3. 延迟平仓算法

**延迟条件：**
```python
1. 亏损 <= 0.5%
2. 距离预定平仓时间 <= 5分钟
3. 尚未触发硬止损

延迟操作：
- 延长时间：30分钟
- 监控频率：每根5M K线（5分钟一次）
- 退出条件：
  a) 价格回到盈亏平衡 → 平仓
  b) 盈利 >= 0.1% → 平仓
  c) 30分钟超时 → 市价平仓
  d) 亏损扩大到 > 1% → 立即市价平仓
```

---

### 数据需求

#### 实时数据流
```python
需要订阅：
1. 5M K线实时推送（WebSocket）
2. 15M K线实时推送（WebSocket）
3. 实时价格（已有）

数据缓存：
- 最近10根5M K线
- 最近10根15M K线
```

#### 持仓扩展字段
```sql
需要在 futures_positions 表增加字段：
- batch_plan: JSON - 分批建仓计划
- batch_filled: JSON - 已完成批次记录
- entry_signal_time: DATETIME - 信号发出时间
- planned_close_time: DATETIME - 计划平仓时间
- close_extended: BOOLEAN - 是否延长平仓
- extended_close_time: DATETIME - 延长后的平仓时间
```


---

## ✅ 实施计划

### 阶段一：基础架构准备（1-2天）

#### 1.1 创建新服务组件
- [ ] `app/services/smart_entry_executor.py` - 智能分批建仓执行器
- [ ] `app/services/smart_exit_optimizer.py` - 智能平仓优化器
- [ ] `app/services/kline_monitor.py` - K线实时监控服务

#### 1.2 数据库扩展
```sql
-- 扩展 futures_positions 表
ALTER TABLE futures_positions
ADD COLUMN batch_plan JSON COMMENT '分批建仓计划',
ADD COLUMN batch_filled JSON COMMENT '已完成批次',
ADD COLUMN entry_signal_time DATETIME COMMENT '信号发出时间',
ADD COLUMN planned_close_time DATETIME COMMENT '计划平仓时间',
ADD COLUMN close_extended BOOLEAN DEFAULT FALSE COMMENT '是否延长平仓',
ADD COLUMN extended_close_time DATETIME COMMENT '延长后平仓时间',
ADD COLUMN avg_entry_price DECIMAL(20,8) COMMENT '平均入场价';
```

#### 1.3 K线订阅服务
- [ ] 扩展 `BinanceWSPriceService` 支持K线订阅
- [ ] 添加5M、15M K线缓存机制
- [ ] 实现K线趋势检测算法

---

### 阶段二：智能分批建仓（3-4天）

#### 2.1 SmartEntryExecutor 核心功能

**文件结构：**
```python
class SmartEntryExecutor:
    """智能分批建仓执行器（价格优势导向）"""

    def __init__(self, db_config, live_engine, ws_price_service):
        self.batch_ratio = [0.3, 0.3, 0.4]  # 分批比例
        self.time_window = 30  # 30分钟建仓窗口（分钟）
        self.ws_price_service = ws_price_service  # WebSocket价格服务
        self.price_history = {}  # 实时价格历史

        # 价格优势阈值
        self.thresholds = {
            'long': {
                'batch1_advantage': -0.3,  # 第1批：下跌0.3%立即买入
                'batch1_breakout': 0.5,    # 第1批：上涨0.5%避免错过
                'batch2_pullback': -0.1,   # 第2批：回调0.1%加仓
                'batch3_tolerance': 0.2,   # 第3批：允许偏离0.2%
            },
            'short': {
                'batch1_advantage': 0.3,   # 第1批：上涨0.3%立即卖空
                'batch1_breakout': -0.5,   # 第1批：下跌0.5%避免错过
                'batch2_bounce': 0.1,      # 第2批：反弹0.1%加仓
                'batch3_tolerance': -0.2,  # 第3批：允许偏离-0.2%
            }
        }

    async def execute_entry(self, signal):
        """
        执行智能分批建仓（基于动态价格评估 + 滚动窗口）

        流程：
        1. 启动后台采样器（滚动5分钟窗口）
        2. 前5分钟：建立初始基线
        3. 5-30分钟：基于实时更新的基线动态入场
        """
        symbol = signal['symbol']
        direction = signal['direction']
        signal_time = datetime.now()

        logger.info(f"🚀 {symbol} 开始智能建仓流程 | 方向: {direction}")

        # 初始化建仓计划
        plan = {
            'symbol': symbol,
            'direction': direction,
            'signal_time': signal_time,
            'batches': [
                {'ratio': 0.3, 'filled': False, 'price': None, 'time': None, 'score': None},
                {'ratio': 0.3, 'filled': False, 'price': None, 'time': None, 'score': None},
                {'ratio': 0.4, 'filled': False, 'price': None, 'time': None, 'score': None},
            ]
        }

        # 启动后台采样器（独立协程，持续运行30分钟）
        sampler = PriceSampler(symbol, window_seconds=300)
        sampling_task = asyncio.create_task(sampler.start_background_sampling())

        logger.info(f"📊 等待5分钟建立初始价格基线...")

        # 等待初始基线建立（5分钟）
        while not sampler.initial_baseline_built:
            await asyncio.sleep(1)

        baseline = sampler.baseline
        logger.info(f"✅ 初始基线: 范围 {baseline['min_price']:.6f} - {baseline['max_price']:.6f}, "
                   f"中位数 {baseline['p50']:.6f}, "
                   f"趋势 {baseline['trend']['direction']} ({baseline['trend']['change_pct']:.2f}%)")

        # 动态入场执行（5-30分钟）
        logger.info(f"⚡ 开始动态入场执行（基线实时更新）...")

        while (datetime.now() - signal_time).seconds < 1800:  # 总共30分钟
            current_price = self.get_current_price(symbol)
            elapsed_since_signal = (datetime.now() - signal_time).seconds / 60

            # 获取实时更新的基线
            current_baseline = sampler.get_current_baseline()

            # 第1批建仓判断
            if not plan['batches'][0]['filled']:
                should_fill, reason = await self.should_fill_batch1(
                    plan, current_price, current_baseline, sampler, elapsed_since_signal
                )
                if should_fill:
                    await self.execute_batch(plan, 0, current_price, reason)

            # 第2批建仓判断
            elif not plan['batches'][1]['filled']:
                should_fill, reason = await self.should_fill_batch2(
                    plan, current_price, current_baseline, elapsed_since_signal
                )
                if should_fill:
                    await self.execute_batch(plan, 1, current_price, reason)

            # 第3批建仓判断
            elif not plan['batches'][2]['filled']:
                should_fill, reason = await self.should_fill_batch3(
                    plan, current_price, current_baseline, elapsed_since_signal
                )
                if should_fill:
                    await self.execute_batch(plan, 2, current_price, reason)
                    logger.info(f"🎉 {symbol} 全部建仓完成！")
                    break

            await asyncio.sleep(10)  # 每10秒检查一次

        # 停止采样器
        sampler.stop_sampling()
        sampling_task.cancel()

        # 超时强制建仓
        await self.force_fill_remaining(plan)

    async def should_fill_batch1(self, plan, current_price, baseline, sampler, elapsed_minutes):
        """
        判断是否应该建仓第1批（基于价格评估体系）

        Returns:
            (bool, str): (是否建仓, 原因)
        """
        direction = plan['direction']

        if direction == 'LONG':
            # 做多：评估当前价格
            evaluation = sampler.is_good_long_price(current_price)

            # 条件1: 价格评分>=80分（极优价格）
            if evaluation['score'] >= 80:
                return True, f"极优价格(评分{evaluation['score']}): {evaluation['reason']}"

            # 条件2: 价格评分>=60分 + 止跌信号
            if evaluation['score'] >= 60:
                signal_strength = detect_bottom_signal(plan['symbol'], sampler.samples)
                if signal_strength >= 50:
                    return True, f"优秀价格(评分{evaluation['score']}) + 止跌信号({signal_strength}分)"

            # 条件3: 价格跌破基线最低价
            if current_price <= baseline['min_price'] * 0.999:
                return True, f"突破基线最低价({baseline['min_price']:.6f})"

            # 条件4: 强上涨趋势 + 价格已升至p75以上（避免错过）
            if baseline['trend']['direction'] == 'up' and baseline['trend']['strength'] > 0.7:
                if current_price >= baseline['p75']:
                    return True, f"强上涨趋势({baseline['trend']['change_pct']:.2f}%)，避免错过"

            # 条件5: 超时兜底（12分钟后价格合理即入场）
            if elapsed_minutes >= 12 and evaluation['score'] >= 40:
                return True, f"超时兜底(已{elapsed_minutes:.1f}分钟)，评分{evaluation['score']}"

            # 条件6: 强制超时（15分钟）
            if elapsed_minutes >= 15:
                return True, f"强制入场(已{elapsed_minutes:.1f}分钟)"

        else:  # SHORT
            # 做空：镜像逻辑
            evaluation = sampler.is_good_short_price(current_price)

            if evaluation['score'] >= 80:
                return True, f"极优价格(评分{evaluation['score']}): {evaluation['reason']}"

            if evaluation['score'] >= 60:
                signal_strength = detect_top_signal(plan['symbol'], sampler.samples)
                if signal_strength >= 50:
                    return True, f"优秀价格(评分{evaluation['score']}) + 止涨信号({signal_strength}分)"

            if current_price >= baseline['max_price'] * 1.001:
                return True, f"突破基线最高价({baseline['max_price']:.6f})"

            if baseline['trend']['direction'] == 'down' and baseline['trend']['strength'] > 0.7:
                if current_price <= baseline['p25']:
                    return True, f"强下跌趋势({baseline['trend']['change_pct']:.2f}%)，避免错过"

            if elapsed_minutes >= 12 and evaluation['score'] >= 40:
                return True, f"超时兜底(已{elapsed_minutes:.1f}分钟)，评分{evaluation['score']}"

            if elapsed_minutes >= 15:
                return True, f"强制入场(已{elapsed_minutes:.1f}分钟)"

        return False, ""

    async def should_fill_batch2(self, plan, current_price, baseline, elapsed_minutes):
        """
        判断是否应该建仓第2批

        Returns:
            (bool, str): (是否建仓, 原因)
        """
        direction = plan['direction']
        batch1_price = plan['batches'][0]['price']
        batch1_time = plan['batches'][0]['time']
        time_since_batch1 = (datetime.now() - batch1_time).seconds / 60

        # 至少等待3分钟
        if time_since_batch1 < 3:
            return False, ""

        if direction == 'LONG':
            # 条件1: 价格回调至第1批价格-0.3%（优质加仓点）
            if current_price <= batch1_price * 0.997:
                return True, f"回调加仓(第1批价{batch1_price:.6f}, 当前{current_price:.6f})"

            # 条件2: 价格仍低于p25分位数
            if current_price <= baseline['p25']:
                return True, f"价格仍在p25以下({baseline['p25']:.6f})"

            # 条件3: 检测到第二次止跌信号
            if time_since_batch1 >= 5:
                signal_strength = detect_bottom_signal(plan['symbol'], price_history)
                if signal_strength >= 60:
                    return True, f"检测到强止跌信号({signal_strength}分)"

            # 条件4: 超时兜底（距第1批10分钟）
            if time_since_batch1 >= 10:
                return True, f"超时建仓(距第1批{time_since_batch1:.1f}分钟)"

            # 条件5: 强制超时（距信号20分钟）
            if elapsed_minutes >= 20:
                return True, f"强制建仓(距信号{elapsed_minutes:.1f}分钟)"

        else:  # SHORT
            if current_price >= batch1_price * 1.003:
                return True, f"反弹加仓(第1批价{batch1_price:.6f}, 当前{current_price:.6f})"

            if current_price >= baseline['p75']:
                return True, f"价格仍在p75以上({baseline['p75']:.6f})"

            if time_since_batch1 >= 5:
                signal_strength = detect_top_signal(plan['symbol'], price_history)
                if signal_strength >= 60:
                    return True, f"检测到强止涨信号({signal_strength}分)"

            if time_since_batch1 >= 10:
                return True, f"超时建仓(距第1批{time_since_batch1:.1f}分钟)"

            if elapsed_minutes >= 20:
                return True, f"强制建仓(距信号{elapsed_minutes:.1f}分钟)"

        return False, ""

    async def should_fill_batch3(self, plan, current_price, baseline, elapsed_minutes):
        """
        判断是否应该建仓第3批（完成建仓）

        Returns:
            (bool, str): (是否建仓, 原因)
        """
        direction = plan['direction']
        batch2_time = plan['batches'][1]['time']
        time_since_batch2 = (datetime.now() - batch2_time).seconds / 60

        # 至少等待3分钟
        if time_since_batch2 < 3:
            return False, ""

        # 计算前两批平均价
        avg_price = (plan['batches'][0]['price'] + plan['batches'][1]['price']) / 2

        if direction == 'LONG':
            # 条件1: 价格不高于前两批平均价
            if current_price <= avg_price:
                return True, f"价格优于平均成本({avg_price:.6f})"

            # 条件2: 价格仍低于p50中位数
            if current_price <= baseline['p50']:
                return True, f"价格仍低于中位数({baseline['p50']:.6f})"

            # 条件3: 价格略高于平均价但在容忍范围（+0.3%）
            if current_price <= avg_price * 1.003:
                return True, f"价格接近平均成本(偏离{((current_price/avg_price-1)*100):.2f}%)"

            # 条件4: 超时兜底（距第2批8分钟）
            if time_since_batch2 >= 8:
                return True, f"超时建仓(距第2批{time_since_batch2:.1f}分钟)"

            # 条件5: 强制超时（距信号28分钟）
            if elapsed_minutes >= 28:
                return True, f"强制完成建仓(距信号{elapsed_minutes:.1f}分钟)"

        else:  # SHORT
            if current_price >= avg_price:
                return True, f"价格优于平均成本({avg_price:.6f})"

            if current_price >= baseline['p50']:
                return True, f"价格仍高于中位数({baseline['p50']:.6f})"

            if current_price >= avg_price * 0.997:
                return True, f"价格接近平均成本(偏离{((1-current_price/avg_price)*100):.2f}%)"

            if time_since_batch2 >= 8:
                return True, f"超时建仓(距第2批{time_since_batch2:.1f}分钟)"

            if elapsed_minutes >= 28:
                return True, f"强制完成建仓(距信号{elapsed_minutes:.1f}分钟)"

        return False, ""

    async def execute_batch(self, plan, batch_num, price, reason):
        """
        执行单批建仓

        Args:
            plan: 建仓计划
            batch_num: 批次编号（0,1,2）
            price: 入场价格
            reason: 入场原因
        """
        batch = plan['batches'][batch_num]

        # 调用live_engine开仓
        # TODO: 实际开仓逻辑
        # await self.live_engine.open_position(
        #     symbol=plan['symbol'],
        #     direction=plan['direction'],
        #     size=batch['ratio'],
        #     price=price
        # )

        # 记录建仓信息
        batch['filled'] = True
        batch['price'] = price
        batch['time'] = datetime.now()

        logger.info(f"✅ {plan['symbol']} 第{batch_num+1}批建仓完成 | "
                   f"价格: {price:.6f} | "
                   f"比例: {batch['ratio']*100:.0f}% | "
                   f"原因: {reason}")

        # 计算当前平均成本
        filled_batches = [b for b in plan['batches'] if b['filled']]
        if len(filled_batches) > 0:
            total_weight = sum(b['ratio'] for b in filled_batches)
            avg_cost = sum(b['price'] * b['ratio'] for b in filled_batches) / total_weight
            logger.info(f"   当前平均成本: {avg_cost:.6f} | "
                       f"已完成: {len(filled_batches)}/3批 ({total_weight*100:.0f}%)")
```

#### 2.2 K线趋势检测

**功能实现：**
```python
class KlineTrendDetector:
    """K线趋势检测器"""

    def detect_5m_trend(self, klines):
        """检测5M K线趋势"""
        # 分析最近3根K线
        # 返回: 'up', 'down', 'sideways'

    def is_bullish_candle(self, kline):
        """判断是否阳线"""
        return kline['close'] > kline['open']

    def is_bearish_candle(self, kline):
        """判断是否阴线"""
        return kline['close'] < kline['open']

    def count_consecutive_candles(self, klines, direction):
        """统计连续同向K线数量"""
        pass
```

#### 2.3 集成到交易流程
- [ ] 修改 `SmartAutoTrader` 调用分批建仓
- [ ] 替换原有的一次性开仓逻辑
- [ ] 添加建仓进度监控

---

### 阶段三：智能平仓优化（3-4天）

#### 3.1 SmartExitOptimizer 核心功能

**文件结构：**
```python
class SmartExitOptimizer:
    """智能平仓优化器"""

    def __init__(self, db_config, live_engine):
        self.pre_close_minutes = 30  # 提前30分钟监控
        self.extend_minutes = 30     # 延长30分钟

    async def start_monitoring(self):
        """启动平仓监控"""
        while True:
            positions = self.get_positions_near_close()
            for pos in positions:
                await self.optimize_exit(pos)
            await asyncio.sleep(60)  # 每分钟检查

    async def optimize_exit(self, position):
        """优化单个持仓的平仓（实时价格触发）"""
        current_price = self.get_current_price(position['symbol'])
        current_pnl = self.calculate_pnl(position, current_price)
        max_profit = position.get('max_profit_pct', 0)

        # 更新最高盈利
        if current_pnl > max_profit:
            self.update_max_profit(position['id'], current_pnl, current_price)
            max_profit = current_pnl

        # 分层平仓逻辑
        if current_pnl >= 3.0:
            # A. 盈利>=3%，检查回撤0.5%
            drawdown = max_profit - current_pnl
            if drawdown >= 0.5:
                await self.close_position(position, f"高盈利回撤止盈(最高{max_profit:.2f}%，回撤{drawdown:.2f}%)")

        elif 1.0 <= current_pnl < 3.0:
            # B. 盈利1-3%，检查回撤0.4%
            drawdown = max_profit - current_pnl
            if drawdown >= 0.4:
                await self.close_position(position, f"中盈利回撤止盈(最高{max_profit:.2f}%，回撤{drawdown:.2f}%)")

        elif 0 <= current_pnl < 1.0:
            # C. 盈利0-1%，突破1%立即平仓
            if current_pnl >= 1.0:
                await self.close_position(position, f"突破1%盈利({current_pnl:.2f}%)")

        elif -0.5 <= current_pnl < 0:
            # D. 亏损0-0.5%，实时捕捉盈亏平衡点
            if current_pnl >= 0:
                # 价格回到盈亏平衡或微盈利
                await self.close_position(position, f"捕捉盈亏平衡点({current_pnl:.2f}%)")
            elif self.should_extend_close(position):
                # 到达预定平仓时间且仍亏损，延长30分钟
                await self.extend_close_time(position)

        else:
            # E. 亏损>0.5%，按原计划平仓
            if self.is_close_time_reached(position):
                await self.close_position(position, f"到期平仓(亏损{current_pnl:.2f}%)")

    def should_extend_close(self, position):
        """判断是否应该延长平仓时间"""
        # 检查是否到达预定平仓时间
        # 检查是否已经延长过
        # 检查亏损是否在-0.5%以内
        planned_time = position.get('planned_close_time')
        already_extended = position.get('close_extended', False)

        if not planned_time or already_extended:
            return False

        now = datetime.now()
        return now >= planned_time

    async def extend_close_time(self, position):
        """延长平仓时间30分钟"""
        new_close_time = datetime.now() + timedelta(minutes=30)

        self.update_position_db(position['id'], {
            'close_extended': True,
            'extended_close_time': new_close_time
        })

        logger.info(f"💡 {position['symbol']} 微亏延长平仓30分钟，继续等待价格回升")
```

#### 3.2 多周期确认机制

```python
class MultiTimeframeAnalyzer:
    """多周期分析器"""

    def confirm_reversal(self, symbol, direction):
        """确认趋势反转"""
        # 5M: 连续2根反向K线
        # 15M: 最新1根反向K线
        # 双重确认则高置信度反转

        klines_5m = self.get_klines(symbol, '5m', 3)
        klines_15m = self.get_klines(symbol, '15m', 2)

        reversal_5m = self.check_5m_reversal(klines_5m, direction)
        reversal_15m = self.check_15m_reversal(klines_15m, direction)

        return reversal_5m and reversal_15m
```

#### 3.3 延迟平仓管理

```python
class DelayedCloseManager:
    """延迟平仓管理器（无需独立管理，集成在实时监控中）"""

    # 延迟平仓逻辑已集成到 SmartExitOptimizer 的实时监控中
    # 通过 WebSocket 实时价格推送触发，无需轮询

    # 工作流程：
    # 1. 到达预定平仓时间
    # 2. 检查当前盈亏：-0.5% <= pnl < 0
    # 3. 标记 close_extended = True
    # 4. 设置 extended_close_time = now + 30分钟
    # 5. 实时监控价格（WebSocket触发）
    # 6. pnl >= 0 → 立即平仓
    # 7. 超过 extended_close_time → 市价平仓
    # 8. pnl < -1.0% → 立即止损平仓
```

**延长期间实时监控：**
```python
async def on_price_update_during_extension(self, position, current_price):
    """延长期间的价格更新处理"""
    current_pnl = self.calculate_pnl(position, current_price)

    # 1. 达到盈亏平衡 → 立即平仓
    if current_pnl >= 0:
        await self.close_position(
            position,
            f"延长期间捕捉盈亏平衡({current_pnl:.2f}%)"
        )
        return

    # 2. 亏损扩大到-1% → 止损平仓
    if current_pnl < -1.0:
        await self.close_position(
            position,
            f"延长期间亏损扩大({current_pnl:.2f}%)，止损"
        )
        return

    # 3. 检查是否超时
    extended_time = position.get('extended_close_time')
    if datetime.now() >= extended_time:
        await self.close_position(
            position,
            f"延长超时市价平仓(亏损{current_pnl:.2f}%)"
        )
        return
```

---

### 阶段四：测试验证（2-3天）

#### 4.1 单元测试
- [ ] 分批建仓逻辑测试
- [ ] K线趋势检测测试
- [ ] 平仓优化逻辑测试
- [ ] 延迟平仓测试

#### 4.2 模拟盘测试
- [ ] 完整流程验证
- [ ] 极端市场情况测试
- [ ] 性能压力测试

#### 4.3 实盘小规模测试
- [ ] 选择1-2个币种
- [ ] 小仓位测试
- [ ] 收集数据和反馈

---

### 阶段五：优化迭代（持续）

#### 5.1 数据收集
- [ ] 记录每次分批建仓的价格差异
- [ ] 统计平仓优化的效果
- [ ] 延迟平仓成功率

#### 5.2 参数优化
- [ ] 调整分批比例（是否30/30/40最优）
- [ ] 调整K线判断标准
- [ ] 优化延迟时间窗口

#### 5.3 策略进化
- [ ] 根据历史数据优化算法
- [ ] 机器学习辅助决策
- [ ] 自适应参数调整

---

### 关键风险点

#### 1. 分批建仓风险
⚠️ **风险**: 30分钟窗口内价格大幅波动，错过最佳入场点
🛡️ **缓解**: 设置最大偏离阈值，超过则立即全仓建仓

#### 2. 延迟平仓风险
⚠️ **风险**: 延迟期间亏损扩大
🛡️ **缓解**:
- 设置亏损扩大阈值(-1%)自动止损
- 只对小亏损(<0.5%)启用延迟

#### 3. 数据延迟风险
⚠️ **风险**: K线数据延迟导致判断滞后
🛡️ **缓解**:
- 使用WebSocket实时K线
- 降级时禁用精细化策略

#### 4. 并发冲突风险
⚠️ **风险**: 多个服务同时修改持仓状态
🛡️ **缓解**:
- 数据库乐观锁
- 状态机严格控制

---

### 预期效果

#### 量化指标
- **入场价格优化**: 平均节省 0.3-0.5% 滑点
- **平仓时机优化**: 减少 20-30% 过早止盈
- **盈亏平衡率**: 微亏单转盈率提升至 40-60%
- **整体收益提升**: 预计提升 15-25%

#### 定性效果
- ✅ 更精细的入场时机把握
- ✅ 更灵活的平仓策略
- ✅ 更好的风险控制
- ✅ 更高的资金使用效率


---

## 📝 备注

### 平仓策略对比表（提前30分钟监控期）

| 盈亏区间 | 策略 | 触发条件 | 监控方式 | 目标 |
|---------|------|---------|---------|------|
| 盈利 ≥ 3% | 回撤止盈 | 从最高点回撤 ≥ 0.5% | WebSocket实时 | 锁定高额利润 |
| 盈利 1-3% | 回撤止盈 | 从最高点回撤 ≥ 0.4% | WebSocket实时 | 保护中等利润 |
| 盈利 0-1% | 突破止盈 | 盈利 ≥ 1.0% | WebSocket实时 | 快速落袋为安 |
| 亏损 0-0.5% | 捕捉平衡点 | 价格 ≥ 入场均价 | WebSocket实时 | 不亏损出场 |
| 亏损 0-0.5% (到期) | 延长30分钟 | 到达平仓时间仍亏损 | WebSocket实时 | 等待价格回升 |
| 亏损 > 0.5% | 按时平仓 | 到达平仓时间 | 定时检查 | 及时止损 |

### 关键优化点

**相比原方案的改进：**

1. ✅ **去除K线依赖**
   - 原方案：依赖5M/15M K线（最快5分钟才能响应）
   - 新方案：WebSocket实时价格（毫秒级响应）

2. ✅ **更精确的止盈机制**
   - 原方案：2根反向K线（模糊）
   - 新方案：精确的回撤百分比（0.4%/0.5%）

3. ✅ **分层止盈策略**
   - 盈利越高，允许回撤越大
   - 盈利越低，要求越严格

4. ✅ **实时捕捉盈亏平衡点**
   - 微亏情况下精确捕捉不亏损的瞬间
   - 避免"差一点就盈利"的遗憾

### 实时监控优势

```python
传统K线方式问题：
- 5M K线周期：5分钟才能获得一次信号
- 延迟大：错过最佳平仓点
- 不精确：无法捕捉瞬时价格

WebSocket实时价格优势：
- 响应速度：毫秒级
- 精确度高：捕捉每次价格变动
- 灵活性强：可实现任意复杂的平仓逻辑
```

### 技术实现要点

1. **实时价格监控已实现**
   - `RealtimePositionMonitor` 已使用 WebSocket
   - `BinanceWSPriceService` 提供毫秒级价格推送
   - 只需在现有基础上添加新的平仓逻辑

2. **需要新增的字段**
   ```sql
   max_profit_pct DECIMAL(10,4)  -- 最高盈利百分比
   max_profit_price DECIMAL(20,8) -- 最高盈利时价格
   avg_entry_price DECIMAL(20,8)  -- 平均入场价（分批建仓）
   ```

3. **核心算法**
   ```python
   # 每次价格更新时
   current_pnl = calculate_pnl(position, current_price)

   # 更新最高盈利
   if current_pnl > max_profit_pct:
       max_profit_pct = current_pnl

   # 检查回撤
   if current_pnl >= 3.0:
       drawdown = max_profit_pct - current_pnl
       if drawdown >= 0.5:
           close_position()
   ```


