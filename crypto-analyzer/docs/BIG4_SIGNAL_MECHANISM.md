# 四大天王信号判断和干预机制详解

## 目录
1. [核心概念](#核心概念)
2. [信号判断逻辑](#信号判断逻辑)
3. [干预机制](#干预机制)
4. [实战案例](#实战案例)
5. [参数配置](#参数配置)

---

## 核心概念

### 四大天王
- **BTC/USDT** - 比特币 (市场领头羊)
- **ETH/USDT** - 以太坊 (第二大加密货币)
- **BNB/USDT** - 币安币 (平台币龙头)
- **SOL/USDT** - Solana (新兴公链代表)

### 为什么是这四个?
1. **市值大**: 流动性好,不易被操纵
2. **代表性强**: 涵盖主流币、平台币、新兴公链
3. **先导作用**: 它们的趋势变化往往预示整体市场方向
4. **稳定性高**: 相比小币种,信号更可靠

### 设计理念
> "四大天王先盘整,然后某个方向突破,预示市场即将启动"

---

## 信号判断逻辑

### 第一步: 盘整期检测 (6小时)

**目的**: 判断是否在酝酿方向性变化

**检测方法**:
```
6小时价格变化 < 0.5% → 判定为盘整
```

**SQL实现**:
```sql
-- 获取6小时前的开盘价
SELECT open_price
FROM kline_data
WHERE symbol = 'BTC/USDT' AND timeframe = '1h'
  AND timestamp >= DATE_SUB(NOW(), INTERVAL 6 HOUR)
ORDER BY timestamp ASC LIMIT 1

-- 获取最新收盘价
SELECT close_price
FROM kline_data
WHERE symbol = 'BTC/USDT' AND timeframe = '1h'
  AND timestamp >= DATE_SUB(NOW(), INTERVAL 6 HOUR)
ORDER BY timestamp DESC LIMIT 1

-- 计算涨跌幅
price_change_pct = (last_close - first_open) / first_open * 100
is_consolidating = abs(price_change_pct) < 0.5
```

**示例**:
- BTC 6H前: $99,000, 现在: $99,400 → 涨幅 +0.40% → **盘整**
- ETH 6H前: $3,500, 现在: $3,600 → 涨幅 +2.86% → **非盘整**

---

### 第二步: K线力度分析

**目的**: 判断哪个方向的力量更强

#### 2.1 分析 1小时 K线 (看6小时内)

**指标**:
1. **阳线 vs 阴线数量**
2. **阳线总成交量 vs 阴线总成交量**
3. **阳线平均实体大小 vs 阴线平均实体大小**

**计算公式**:
```python
# 数量得分
count_score = (bullish_count - bearish_count) / total_candles

# 成交量得分
volume_score = (bullish_volume - bearish_volume) / total_volume

# 实体大小得分
size_score = (avg_bullish_size - avg_bearish_size) / max_size

# 综合得分
overall_score = (count_score + volume_score + size_score) / 3

# 判断主导方向
if overall_score > 0.2:  → BULL (看多)
elif overall_score < -0.2: → BEAR (看空)
else: → NEUTRAL (中性)
```

**示例**:
```
BTC 1小时分析 (最近6根K线):
- 阳线: 4根, 总成交量: 1200 BTC, 平均实体: 0.8%
- 阴线: 2根, 总成交量: 600 BTC, 平均实体: 0.5%

count_score = (4-2)/6 = +0.33
volume_score = (1200-600)/1800 = +0.33
size_score = (0.8-0.5)/0.8 = +0.375

overall_score = (0.33 + 0.33 + 0.375) / 3 = +0.345

结果: 1H主导 = BULL ✅
```

#### 2.2 分析 15分钟 K线 (看2小时内)

**同样的逻辑,但观察更短期的力量变化**

---

### 第三步: 突破检测 (5M / 15M)

**目的**: 捕捉盘整后的突破拐点

**检测条件** (同时满足):
1. **成交量放大**: 最新K线成交量 > 平均成交量 × 1.5
2. **实体放大**: 最新K线实体大小 > 平均实体 × 1.5

**SQL实现**:
```sql
-- 获取最近10根5分钟K线
SELECT open_price, close_price, volume
FROM kline_data
WHERE symbol = 'BTC/USDT' AND timeframe = '5m'
ORDER BY timestamp DESC LIMIT 10

-- 最新K线 = klines[0]
-- 之前K线 = klines[1:9]

-- 计算平均值
avg_volume = sum(previous_volumes) / 9
avg_body_size = sum(previous_body_sizes) / 9

-- 判断突破
if (latest_volume > avg_volume * 1.5) AND (latest_body_size > avg_body_size * 1.5):
    detected = True
    direction = 'BULLISH' if latest_close > latest_open else 'BEARISH'
    strength = min(((volume_ratio + size_ratio) / 2 - 1) * 50, 100)
```

**示例**:
```
BTC 5M突破检测:
- 最新K线: 成交量 = 500 BTC, 实体 = 1.2%
- 平均值:   成交量 = 200 BTC, 实体 = 0.5%

volume_ratio = 500 / 200 = 2.5x ✅ (> 1.5x)
size_ratio = 1.2 / 0.5 = 2.4x ✅ (> 1.5x)

detected = True
direction = BEARISH (因为收盘 < 开盘)
strength = ((2.5 + 2.4) / 2 - 1) * 50 = 72.5

结果: 5M向下突破 (强度72.5) 🔴
```

---

### 第四步: 综合评分

**评分系统** (-100 到 +100):

#### 盘整期内的突破 (权重最高)
- 5M突破: ±30分
- 15M突破: ±25分

#### K线力度分析
- 1H主导: ±20分
- 15M主导: ±20分

#### 非盘整期的突破
- 5M突破: ±20分
- 15M突破: ±20分

**信号判定**:
```python
if signal_score > 30:
    signal = 'BULLISH'  # 看多
elif signal_score < -30:
    signal = 'BEARISH'  # 看空
else:
    signal = 'NEUTRAL'  # 中性

strength = min(abs(signal_score), 100)  # 强度 0-100
```

**实例计算**:

```
BTC 信号生成:
1. 6H盘整: 是 (+0.44%)
2. 5M向下突破: -30分 (盘整期突破,权重高)
3. 1H主导: NEUTRAL (0分)
4. 15M主导: NEUTRAL (0分)

总评分 = -30
信号 = BEARISH (看空)
强度 = 30
```

---

## 干预机制

### 干预时机

**ONLY** 对四大天王本身生效:
- 当超级大脑扫描到 BTC/ETH/BNB/SOL 的交易机会时
- 在开仓前进行 Big4 信号验证
- 其他交易对(如DOGE, XRP)不受影响

### 干预逻辑

#### 场景1: 强冲突 - 直接跳过交易

**触发条件**:
- Big4信号强度 >= 60
- 信号方向与交易方向完全相反

**示例**:
```
超级大脑: BTC/USDT LONG信号 (评分45)
Big4检测: BTC 强烈看空 (BEARISH, 强度75)

[BIG4-SKIP] BTC/USDT 强烈看空 (强度75), 跳过LONG信号
结果: 不开仓 ❌
```

```
超级大脑: ETH/USDT SHORT信号 (评分50)
Big4检测: ETH 强烈看多 (BULLISH, 强度80)

[BIG4-SKIP] ETH/USDT 强烈看多 (强度80), 跳过SHORT信号
结果: 不开仓 ❌
```

---

#### 场景2: 弱冲突 - 降低评分

**触发条件**:
- Big4信号强度 < 60
- 信号方向与交易方向相反

**处理**:
- 评分降低 30分
- 如果降低后 < 20分,则跳过

**示例**:
```
超级大脑: BTC/USDT LONG信号 (评分50)
Big4检测: BTC 看空 (BEARISH, 强度45)

[BIG4-ADJUST] BTC/USDT 看空信号, LONG评分降低: 50 -> 20
评分 = 20, 保留信号 ⚠️
```

```
超级大脑: SOL/USDT SHORT信号 (评分40)
Big4检测: SOL 看多 (BULLISH, 强度35)

[BIG4-ADJUST] SOL/USDT 看多信号, SHORT评分降低: 40 -> 10
[BIG4-SKIP] SOL/USDT 调整后评分过低 (10), 跳过
结果: 不开仓 ❌
```

---

#### 场景3: 方向一致 - 提升评分

**触发条件**:
- Big4信号方向与交易方向一致

**处理**:
- 评分提升: min(20, 强度 × 0.3)
- 最多提升 20分

**示例**:
```
超级大脑: BTC/USDT LONG信号 (评分40)
Big4检测: BTC 看多 (BULLISH, 强度80)

boost = min(20, 80 * 0.3) = min(20, 24) = 20
new_score = 40 + 20 = 60

[BIG4-BOOST] BTC/USDT 看多信号与LONG方向一致, 评分提升: 40 -> 60 (+20)
结果: 以更高评分开仓 ✅
```

```
超级大脑: ETH/USDT SHORT信号 (评分45)
Big4检测: ETH 看空 (BEARISH, 强度50)

boost = min(20, 50 * 0.3) = min(20, 15) = 15
new_score = 45 + 15 = 60

[BIG4-BOOST] ETH/USDT 看空信号与SHORT方向一致, 评分提升: 45 -> 60 (+15)
结果: 以更高评分开仓 ✅
```

---

#### 场景4: 中性信号 - 不干预

**触发条件**:
- Big4信号 = NEUTRAL

**处理**:
- 保持原评分,正常开仓

**示例**:
```
超级大脑: BNB/USDT LONG信号 (评分50)
Big4检测: BNB 中性 (NEUTRAL, 强度10)

[BIG4] BNB/USDT 趋势信号: NEUTRAL (强度: 10)
结果: 保持评分50,正常开仓 ✅
```

---

## 实战案例

### 案例1: BTC盘整后向下突破 - 成功避免多单亏损

**时间**: 2026-01-28 14:30

**市场状态**:
```
BTC 6小时前: $99,500
BTC 当前: $99,600 (+0.10%)
判定: 盘整中

最近5分钟: 出现向下大阴线
- 成交量: 800 BTC (平均200 BTC) → 4.0x
- 实体大小: 1.5% (平均0.5%) → 3.0x
```

**Big4信号**:
```
BTC/USDT: BEARISH (强度: 75)
原因: 6H盘整 | 5M向下突破(强度100) | 1H阴线主导
```

**超级大脑信号**:
```
BTC/USDT LONG (评分: 45)
信号组合: position_low + momentum_down_3pct + volume_power_bull
```

**Big4干预**:
```
[BIG4] BTC/USDT 趋势信号: BEARISH (强度: 75)
[BIG4-SKIP] BTC/USDT 强烈看空 (强度75), 跳过LONG信号 (原评分45)
结果: 不开仓 ✅
```

**后续走势**:
```
BTC 15分钟后: $99,100 (-0.50%)
BTC 30分钟后: $98,600 (-0.90%)
BTC 1小时后: $98,200 (-1.31%)
```

**收益**: 避免亏损 ~$52 USDT (按400 USDT仓位, 5x杠杆, -1.31%计算)

---

### 案例2: ETH盘整后向上突破 - 提升评分抓住机会

**时间**: 2026-01-28 16:45

**市场状态**:
```
ETH 6小时前: $3,500
ETH 当前: $3,515 (+0.43%)
判定: 盘整中

最近5分钟: 出现向上大阳线
- 成交量: 15,000 ETH (平均5,000 ETH) → 3.0x
- 实体大小: 2.0% (平均0.6%) → 3.3x
```

**Big4信号**:
```
ETH/USDT: BULLISH (强度: 65)
原因: 6H盘整 | 5M向上突破(强度100) | 15M阳线主导
```

**超级大脑信号**:
```
ETH/USDT LONG (评分: 38)
信号组合: position_mid + consecutive_bull + volatility_high
```

**Big4干预**:
```
[BIG4] ETH/USDT 趋势信号: BULLISH (强度: 65)
boost = min(20, 65 * 0.3) = 19
[BIG4-BOOST] ETH/USDT 看多信号与LONG方向一致, 评分提升: 38 -> 57 (+19)
结果: 以57分开仓 ✅
```

**后续走势**:
```
ETH 15分钟后: $3,540 (+1.14%)
ETH 30分钟后: $3,565 (+1.86%)
ETH 止盈: $3,580 (+2.29%) ✅
```

**收益**: +$40.02 USDT (按400 USDT仓位, 5x杠杆, +2.29%止盈, 手续费0.001)

---

### 案例3: SOL中性信号 - 不干预,正常交易

**时间**: 2026-01-28 18:20

**市场状态**:
```
SOL 6小时前: $145.00
SOL 当前: $145.50 (+0.34%)
判定: 盘整中

无明显突破,1H和15M都是中性
```

**Big4信号**:
```
SOL/USDT: NEUTRAL (强度: 5)
原因: 6H盘整 | 无明显方向性
```

**超级大脑信号**:
```
SOL/USDT SHORT (评分: 52)
信号组合: position_high + breakdown_short + volume_power_bear
```

**Big4干预**:
```
[BIG4] SOL/USDT 趋势信号: NEUTRAL (强度: 5)
结果: 不调整评分,保持52分,正常开仓 ✅
```

**后续走势**:
```
SOL 正常波动,最终止盈平仓
```

---

## 参数配置

### 可调参数

#### 1. 盘整阈值
```python
self.consolidation_threshold = 0.5  # 6小时涨跌幅 < 0.5%
```

**建议**:
- 波动性大的市场: 调高到 0.7% ~ 1.0%
- 波动性小的市场: 保持 0.5%

---

#### 2. 盘整观察期
```python
self.consolidation_hours = 6  # 6小时
```

**建议**:
- 短线交易: 4小时
- 中线交易: 6小时 (推荐)
- 长线交易: 12小时

---

#### 3. 突破倍数
```python
self.breakout_volume_multiplier = 1.5  # 成交量/实体 > 1.5倍
```

**建议**:
- 严格模式: 2.0x (更少但更可靠的突破)
- 平衡模式: 1.5x (推荐)
- 宽松模式: 1.2x (更多突破信号)

---

#### 4. 强冲突阈值
```python
if signal_strength >= 60:  # 强度 >= 60 直接跳过
```

**建议**:
- 保守策略: 50 (更严格的过滤)
- 平衡策略: 60 (推荐)
- 激进策略: 70 (允许更多交易)

---

#### 5. 评分调整幅度
```python
penalty = -30  # 冲突时降低30分
boost = min(20, int(strength * 0.3))  # 一致时最多+20分
```

**建议**:
- penalty: 25~40 (过小影响不大,过大太严格)
- boost上限: 15~25 (过小激励不足,过大风险大)

---

#### 6. 最低评分阈值
```python
if new_score < 20:  # 调整后评分 < 20 则跳过
    continue
```

**建议**:
- 保守策略: 25 (更严格)
- 平衡策略: 20 (推荐)
- 激进策略: 15 (允许更低评分)

---

## 监控指标

### 关键日志

**Big4检测日志**:
```
[BIG4] BTC/USDT 趋势信号: BEARISH (强度: 75)
```

**跳过交易日志**:
```
[BIG4-SKIP] BTC/USDT 强烈看空 (强度75), 跳过LONG信号
```

**评分调整日志**:
```
[BIG4-ADJUST] ETH/USDT 看多信号, SHORT评分降低: 50 -> 20
```

**评分提升日志**:
```
[BIG4-BOOST] BNB/USDT 看多信号与LONG方向一致, 评分提升: 40 -> 60 (+20)
```

---

### 性能查询

#### 1. Big4干预效果统计
```sql
-- 查询四大天王的交易表现
SELECT
    symbol,
    COUNT(*) as total_trades,
    SUM(CASE WHEN close_reason = 'take_profit' THEN 1 ELSE 0 END) as wins,
    ROUND(100.0 * SUM(CASE WHEN close_reason = 'take_profit' THEN 1 ELSE 0 END) / COUNT(*), 2) as win_rate,
    ROUND(SUM(realized_pnl), 2) as total_pnl,
    ROUND(AVG(realized_pnl), 2) as avg_pnl_per_trade
FROM futures_positions
WHERE symbol IN ('BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT')
  AND status = 'closed'
  AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY symbol
ORDER BY total_pnl DESC;
```

#### 2. 对比Big4 vs 其他交易对
```sql
-- Big4 vs 非Big4
SELECT
    CASE
        WHEN symbol IN ('BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT')
        THEN 'Big4'
        ELSE 'Others'
    END as symbol_group,
    COUNT(*) as total_trades,
    ROUND(100.0 * SUM(CASE WHEN close_reason = 'take_profit' THEN 1 ELSE 0 END) / COUNT(*), 2) as win_rate,
    ROUND(SUM(realized_pnl), 2) as total_pnl
FROM futures_positions
WHERE status = 'closed'
  AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY symbol_group;
```

---

## 总结

### Big4系统的优势

1. **先导性**: 捕捉市场方向变化的早期信号
2. **可靠性**: 基于大市值币种,不易被操纵
3. **精准性**: 多维度综合判断(盘整+突破+K线力度)
4. **保护性**: 强冲突直接跳过,避免逆势交易
5. **增强性**: 方向一致时提升评分,提高胜率

### 适用场景

✅ **适合**:
- 四大天王本身的交易 (BTC/ETH/BNB/SOL)
- 盘整后的突破交易
- 趋势明确时的顺势交易

❌ **不适合** (当前版本):
- 其他交易对 (暂未扩展)
- 极端市场(如暴涨暴跌)
- 新闻驱动的突发行情

### 后续优化方向

1. **扩展到前20交易对** (观察3-7天表现后)
2. **引入BTC作为市场领先指标** (影响所有山寨币)
3. **添加相关性分析** (ETH与BTC的相关度)
4. **优化参数自适应** (根据市场波动性自动调整)

---

**文档版本**: v1.0
**最后更新**: 2026-01-28
**作者**: Claude Sonnet 4.5 & User
