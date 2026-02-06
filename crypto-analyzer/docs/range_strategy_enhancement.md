# 震荡策略增强方案

## 一、当前问题

当前布林带均值回归策略过于简单，缺少以下关键要素：

1. **震荡区间识别** - 没有判断是否真的在震荡
2. **多次触碰确认** - 第一次触碰就开仓风险大
3. **支撑阻力确认** - 没有检查关键价位
4. **区间突破检测** - 可能误判趋势启动为震荡
5. **波动率分级** - 没有区分强震荡/弱震荡

---

## 二、增强方案

### 1. 震荡区间识别（新增）

```python
def detect_range_bound(self, symbol: str, lookback_hours: int = 48):
    """
    识别是否处于震荡区间

    判断标准:
    1. 最近48H价格波动在固定区间（高点和低点明确）
    2. 多次触碰相同高点/低点（至少2-3次）
    3. 趋势不明显（EMA纠缠，不形成明显趋势）
    4. 布林带收窄（波动率降低）

    Returns:
        {
            'is_range': True/False,
            'range_high': 区间上沿价格,
            'range_low': 区间下沿价格,
            'range_width_pct': 区间宽度百分比,
            'touch_high_count': 触碰上沿次数,
            'touch_low_count': 触碰下沿次数,
            'confidence': 置信度(0-100)
        }
    """
    # 获取最近48H的15M K线（192根）
    klines = self.load_klines(symbol, '15m', 192)

    # 1. 计算高低点
    highs = [k['high'] for k in klines]
    lows = [k['low'] for k in klines]
    closes = [k['close'] for k in klines]

    # 找出局部高点和低点（使用峰值检测）
    peaks = self._find_peaks(highs, distance=8)  # 至少间隔2小时
    troughs = self._find_troughs(lows, distance=8)

    if len(peaks) < 2 or len(troughs) < 2:
        return {'is_range': False}

    # 2. 检查高点是否在相似价位（震荡特征）
    recent_peaks = sorted(peaks[-5:])  # 最近5个高点
    peak_prices = [highs[i] for i in recent_peaks]
    peak_std = np.std(peak_prices) / np.mean(peak_prices)

    # 3. 检查低点是否在相似价位
    recent_troughs = sorted(troughs[-5:])
    trough_prices = [lows[i] for i in recent_troughs]
    trough_std = np.std(trough_prices) / np.mean(trough_prices)

    # 4. 判断是否震荡：高点低点都比较集中
    is_range = (peak_std < 0.02 and trough_std < 0.02)

    if not is_range:
        return {'is_range': False}

    # 5. 确定区间边界
    range_high = np.mean(peak_prices)
    range_low = np.mean(trough_prices)
    range_width_pct = (range_high - range_low) / range_low * 100

    # 6. 计算当前价格在区间中的位置
    current_price = closes[-1]
    position_in_range = (current_price - range_low) / (range_high - range_low)

    # 7. 计算置信度
    confidence = 100
    confidence -= (peak_std * 100 * 50)  # 高点分散度扣分
    confidence -= (trough_std * 100 * 50)  # 低点分散度扣分
    confidence = max(0, min(100, confidence))

    return {
        'is_range': True,
        'range_high': range_high,
        'range_low': range_low,
        'range_width_pct': range_width_pct,
        'position_in_range': position_in_range,
        'touch_high_count': len(recent_peaks),
        'touch_low_count': len(recent_troughs),
        'confidence': confidence,
        'current_price': current_price
    }
```

### 2. 多次触碰确认（新增）

```python
def check_bounce_history(self, symbol: str, price_level: float,
                         direction: str, tolerance: float = 0.005):
    """
    检查历史上是否多次在该价位反弹

    Args:
        symbol: 交易对
        price_level: 关键价位
        direction: 'support'支撑 or 'resistance'阻力
        tolerance: 价格容忍度（0.5%）

    Returns:
        {
            'bounce_count': 反弹次数,
            'last_bounce_time': 最近反弹时间,
            'avg_bounce_strength': 平均反弹强度%,
            'is_strong_level': 是否强支撑/阻力
        }
    """
    klines = self.load_klines(symbol, '15m', 192)

    bounce_count = 0
    bounce_strengths = []
    last_bounce_time = None

    for i in range(10, len(klines)):
        price = klines[i]['low'] if direction == 'support' else klines[i]['high']

        # 检查是否触碰价位
        if abs(price - price_level) / price_level < tolerance:
            # 检查之后是否反弹
            if direction == 'support':
                # 触及支撑后向上反弹
                next_5_closes = [klines[j]['close'] for j in range(i+1, min(i+6, len(klines)))]
                if next_5_closes and max(next_5_closes) > price * 1.002:
                    bounce_count += 1
                    bounce_strength = (max(next_5_closes) - price) / price * 100
                    bounce_strengths.append(bounce_strength)
                    last_bounce_time = klines[i]['open_time']
            else:
                # 触及阻力后向下回调
                next_5_closes = [klines[j]['close'] for j in range(i+1, min(i+6, len(klines)))]
                if next_5_closes and min(next_5_closes) < price * 0.998:
                    bounce_count += 1
                    bounce_strength = (price - min(next_5_closes)) / price * 100
                    bounce_strengths.append(bounce_strength)
                    last_bounce_time = klines[i]['open_time']

    return {
        'bounce_count': bounce_count,
        'last_bounce_time': last_bounce_time,
        'avg_bounce_strength': np.mean(bounce_strengths) if bounce_strengths else 0,
        'is_strong_level': bounce_count >= 2  # 至少2次反弹才算强支撑/阻力
    }
```

### 3. 增强版信号生成逻辑

```python
def generate_enhanced_signal(self, symbol: str, big4_signal: str):
    """
    增强版信号生成

    开仓条件更严格:
    1. 确认处于震荡区间（新增）
    2. 价格触及区间边界（替代布林带）
    3. 历史上该价位多次反弹（新增）
    4. RSI确认超买超卖
    5. 成交量放大
    6. 无明显趋势
    """
    # 1️⃣ 检测震荡区间
    range_info = self.detect_range_bound(symbol)

    if not range_info['is_range']:
        logger.debug(f"[RANGE_FILTER] {symbol} 不在震荡区间，跳过")
        return None

    if range_info['confidence'] < 60:
        logger.debug(f"[RANGE_FILTER] {symbol} 震荡区间置信度不足: {range_info['confidence']}")
        return None

    # 2️⃣ 计算基础指标（保留原有逻辑）
    indicators = self.calculate_indicators(symbol, '15m')
    if not indicators:
        return None

    current_price = indicators['current_price']
    position_in_range = range_info['position_in_range']

    signal = None
    score = 50  # 基础分
    reasons = []

    # 3️⃣ 做多信号：触及区间下沿
    if position_in_range < 0.15:  # 价格在区间下15%

        # 检查历史反弹
        bounce_info = self.check_bounce_history(
            symbol,
            range_info['range_low'],
            'support'
        )

        if not bounce_info['is_strong_level']:
            logger.debug(f"[BOUNCE_FILTER] {symbol} 支撑位历史反弹不足: {bounce_info['bounce_count']}次")
            return None

        # RSI确认
        if indicators['rsi'] > 35:  # 不够超卖
            logger.debug(f"[RSI_FILTER] {symbol} RSI不够超卖: {indicators['rsi']}")
            return None

        # 趋势过滤
        if indicators['has_downtrend']:
            logger.debug(f"[TREND_FILTER] {symbol} 存在下跌趋势")
            return None

        # 成交量确认
        if not indicators['volume_surge']:
            logger.debug(f"[VOLUME_FILTER] {symbol} 成交量未放大")
            return None

        # ✅ 通过所有条件
        signal = 'LONG'
        score = 60  # 基础分
        reasons.append(f'触及震荡区间下沿({position_in_range*100:.1f}%)')
        reasons.append(f'历史反弹{bounce_info["bounce_count"]}次')
        reasons.append(f'RSI超卖({indicators["rsi"]:.1f})')
        reasons.append('成交量放大')

        # 加分项
        if position_in_range < 0.10:
            score += 10
            reasons.append('紧贴下沿')

        if bounce_info['bounce_count'] >= 3:
            score += 15
            reasons.append('强支撑(≥3次反弹)')

        if range_info['confidence'] >= 80:
            score += 10
            reasons.append('震荡区间高置信度')

    # 4️⃣ 做空信号：触及区间上沿
    elif position_in_range > 0.85:  # 价格在区间上15%

        # 检查历史反弹
        bounce_info = self.check_bounce_history(
            symbol,
            range_info['range_high'],
            'resistance'
        )

        if not bounce_info['is_strong_level']:
            logger.debug(f"[BOUNCE_FILTER] {symbol} 阻力位历史回调不足: {bounce_info['bounce_count']}次")
            return None

        # RSI确认
        if indicators['rsi'] < 65:  # 不够超买
            logger.debug(f"[RSI_FILTER] {symbol} RSI不够超买: {indicators['rsi']}")
            return None

        # 趋势过滤
        if indicators['has_uptrend']:
            logger.debug(f"[TREND_FILTER] {symbol} 存在上涨趋势")
            return None

        # 成交量确认
        if not indicators['volume_surge']:
            logger.debug(f"[VOLUME_FILTER] {symbol} 成交量未放大")
            return None

        # ✅ 通过所有条件
        signal = 'SHORT'
        score = 60
        reasons.append(f'触及震荡区间上沿({position_in_range*100:.1f}%)')
        reasons.append(f'历史回调{bounce_info["bounce_count"]}次')
        reasons.append(f'RSI超买({indicators["rsi"]:.1f})')
        reasons.append('成交量放大')

        # 加分项
        if position_in_range > 0.90:
            score += 10
            reasons.append('紧贴上沿')

        if bounce_info['bounce_count'] >= 3:
            score += 15
            reasons.append('强阻力(≥3次回调)')

        if range_info['confidence'] >= 80:
            score += 10
            reasons.append('震荡区间高置信度')

    if signal:
        return {
            'symbol': symbol,
            'signal': signal,
            'score': min(score, 100),
            'strategy': 'enhanced_range_trading',
            'entry_price': current_price,
            'range_high': range_info['range_high'],
            'range_low': range_info['range_low'],
            'range_width_pct': range_info['range_width_pct'],
            'bounce_count': bounce_info['bounce_count'],
            'rsi': indicators['rsi'],
            'reason': ' + '.join(reasons),
            'timeframe': '15m'
        }

    return None
```

---

## 三、新旧对比

| 维度 | 旧版（当前） | 新版（增强） |
|-----|-------------|-------------|
| **震荡识别** | ❌ 无 | ✅ 检测48H区间 |
| **触碰确认** | ❌ 第一次触碰就开 | ✅ 至少2次历史反弹 |
| **支撑阻力** | ❌ 仅布林带 | ✅ 区间高低点 |
| **置信度** | ❌ 无量化 | ✅ 0-100分 |
| **开仓条件** | 4个 | 7个（更严格）|
| **信号质量** | 60-85分 | 60-95分 |
| **假信号率** | ~35% | ~20%（预期）|

---

## 四、实施步骤

### Phase 1: 添加区间识别（优先）
```python
1. 实现 detect_range_bound()
2. 实现 check_bounce_history()
3. 单元测试
```

### Phase 2: 集成到信号生成
```python
1. 修改 generate_signal() 调用新逻辑
2. 保留旧逻辑作为fallback
3. AB测试对比
```

### Phase 3: 参数优化
```python
1. 观察1周数据
2. 调整阈值（区间宽度、反弹次数等）
3. 优化置信度计算
```

---

## 五、预期效果

```
信号频率:
  旧版: 每天5-10个信号（假信号多）
  新版: 每天2-5个信号（质量更高）

信号质量:
  旧版: 胜率55-60%
  新版: 胜率65-70%（预期）

风险控制:
  旧版: 容易在趋势启动时逆势开仓
  新版: 震荡区间识别，避免趋势误判
```

---

## 六、风险提示

1. **计算复杂度增加** - 需要更多历史数据分析
2. **信号延迟** - 需要2次以上触碰才开仓
3. **错过首次机会** - 第一次触碰不开仓，可能错过
4. **参数敏感** - 区间判断标准需要优化

---

## 七、备用方案

如果新策略表现不佳，可以：

1. **降低门槛** - 1次反弹也开仓
2. **混合策略** - 新旧策略各占50%
3. **动态调整** - 根据实时胜率自动调整
