# 交易逻辑详解（基于实际策略配置）

基于数据库中的策略 **ZEC-BCH** (ID: 1764130914476)

---

## 📊 策略总览

```
策略名称: ZEC-BCH
交易对: BCH/USDT, ZEC/USDT
交易方向: 做多 + 做空
杠杆: 10x
仓位大小: 5 USDT
最大持仓: 4 (做多2个, 做空2个)
实盘同步: ✅ 启用 (100%数量)
```

---

## 🔵 第一部分：模拟合约开仓逻辑

### 1️⃣ 开仓信号检测

#### 主信号源
```
买入信号: EMA 15分钟周期
  - 检测 EMA9 和 EMA26 的交叉
  - 金叉（EMA9上穿EMA26）→ 做多信号
  - 死叉（EMA9下穿EMA26）→ 做空信号
```

#### 信号强度过滤
```javascript
minSignalStrength: {
  ema9_26: 0.1%    // EMA9和EMA26的距离必须 > 0.1%
  ma10_ema10: 0.15% // MA10和EMA10的距离必须 > 0.15%
}

计算公式:
signal_strength = abs(EMA9 - EMA26) / price * 100
```

**❌ 如果信号强度 < 0.1%，直接拒绝开仓**

---

### 2️⃣ 入场条件检查（所有条件必须同时满足）

#### A. 趋势确认
```javascript
trendConfirmBars: 1
trendConfirmEMAThreshold: 0.1%

检查逻辑:
1. 查看最近1根K线
2. 做多: EMA9 必须 > EMA26，且距离 > 0.1%
3. 做空: EMA9 必须 < EMA26，且距离 > 0.1%
```

#### B. MA10/EMA10同向过滤
```javascript
ma10Ema10TrendFilter: true (启用)

做多信号:
  ✅ MA10 > EMA10 (同向向上) → 通过
  ❌ MA10 < EMA10 (不同向) → 拒绝

做空信号:
  ✅ MA10 < EMA10 (同向向下) → 通过
  ❌ MA10 > EMA10 (不同向) → 拒绝
```

#### C. 持续趋势验证
```javascript
sustainedTrend: {
  enabled: true,
  minStrength: 0.15%,      // 最小趋势强度
  maxStrength: 2%,         // 最大趋势强度（防过热）
  requireMA10Confirm: true,
  requirePriceConfirm: true,
  minBars: 2,              // 至少持续2根K线
  cooldownMinutes: 60      // 冷却期60分钟
}

检查步骤:
1. 计算 EMA9-EMA26 距离
2. 验证: 0.15% ≤ 距离 ≤ 2%
3. 检查 MA10 是否确认趋势方向
4. 检查价格是否确认（价格在EMA9同侧）
5. 检查最近2根K线趋势是否一致
6. 检查该交易对是否在60分钟冷却期内
```

**❌ 任何一项不满足，拒绝开仓**

#### D. 价格偏离限制
```javascript
priceDistanceLimit: {
  enabled: true,
  maxAboveEMA: 1%,  // 价格最多高于EMA26 1%
  maxBelowEMA: 1%   // 价格最多低于EMA26 1%
}

做多信号:
  current_price > EMA26 * 1.01 → ❌ 拒绝（追高）

做空信号:
  current_price < EMA26 * 0.99 → ❌ 拒绝（追跌）
```

#### E. 入场冷却时间
```javascript
entryCooldown: {
  enabled: true,
  minutes: 30,
  perDirection: true  // 分方向独立冷却
}

逻辑:
- 同一交易对、同一方向（做多/做空）
- 距离上次开仓必须 ≥ 30分钟
- 做多和做空的冷却期独立计算
```

#### F. 最大持仓限制
```javascript
maxPositions: 4
maxLongPositions: 2
maxShortPositions: 2

检查:
1. 当前总持仓数 < 4
2. 做多信号: 当前做多持仓 < 2
3. 做空信号: 当前做空持仓 < 2
```

---

### 3️⃣ 行情自适应（Adaptive Regime）

系统会先判断当前市场行情状态，然后应用对应的参数：

```javascript
adaptiveRegime: true

regimeParams: {
  strong_uptrend: {      // 强上升趋势
    allowDirection: "long_only",
    stopLossPercent: 2,
    takeProfitPercent: 6,
    sustainedTrend: true
  },
  weak_uptrend: {        // 弱上升趋势
    allowDirection: "long_only",
    stopLossPercent: 1.5,
    takeProfitPercent: 4,
    sustainedTrend: true
  },
  ranging: {             // 震荡行情
    allowDirection: "none",  // ❌ 禁止开仓
    stopLossPercent: null,
    takeProfitPercent: null,
    sustainedTrend: false
  },
  weak_downtrend: {      // 弱下降趋势
    allowDirection: "short_only",
    stopLossPercent: 1.5,
    takeProfitPercent: 4,
    sustainedTrend: true
  },
  strong_downtrend: {    // 强下降趋势
    allowDirection: "short_only",
    stopLossPercent: 2,
    takeProfitPercent: 6,
    sustainedTrend: true
  }
}
```

**行情判断标准**（基于EMA趋势强度）：
- `strong_uptrend`: EMA9 > EMA26，且距离 > 0.8%
- `weak_uptrend`: EMA9 > EMA26，且距离在 0.15% - 0.8% 之间
- `ranging`: |EMA9 - EMA26| < 0.15%
- `weak_downtrend`: EMA9 < EMA26，且距离在 0.15% - 0.8% 之间
- `strong_downtrend`: EMA9 < EMA26，且距离 > 0.8%

**❌ 震荡行情（ranging）时，完全禁止开仓**

---

### 4️⃣ 订单类型选择

```javascript
longPrice: "market_minus_1"   // 做多用限价单，价格 = 市价 - 0.01%
shortPrice: "market_plus_1"   // 做空用限价单，价格 = 市价 + 0.01%

limitOrderTimeoutMinutes: 30  // 限价单超时时间

执行流程:
1. 下限价单到数据库 (futures_orders 表)
2. FuturesLimitOrderExecutor 监控订单状态
3. 每10秒检查一次订单是否成交
4. 如果30分钟内未成交:
   - 取消原限价单
   - 下市价单立即成交
   - 更新订单状态
```

---

### 5️⃣ 开仓完整流程图

```
开始
  ↓
检测 EMA15m 金叉/死叉
  ↓
信号强度 ≥ 0.1%? ────NO───→ 拒绝
  ↓ YES
趋势确认（1根K线）────NO───→ 拒绝
  ↓ YES
MA10/EMA10同向? ────NO───→ 拒绝
  ↓ YES
持续趋势验证 ────NO───→ 拒绝
  ↓ YES
价格偏离 ≤ 1%? ────NO───→ 拒绝
  ↓ YES
冷却期已过? ────NO───→ 拒绝
  ↓ YES
持仓数未满? ────NO───→ 拒绝
  ↓ YES
判断当前行情状态
  ↓
震荡行情? ────YES───→ 拒绝
  ↓ NO
方向允许开仓? ────NO───→ 拒绝（如：下跌趋势中的做多信号）
  ↓ YES
下限价单（市价±0.01%）
  ↓
监控订单状态
  ↓
30分钟内成交? ────YES───→ 成功开仓
  ↓ NO
转市价单成交
  ↓
成功开仓
```

---

## 🔴 第二部分：模拟合约平仓逻辑

### 1️⃣ 基础止损止盈

这个值会被行情自适应覆盖：

```javascript
// 默认值（仅在行情判断失败时使用）
stopLoss: 1.5%
takeProfit: 4%

// 实际使用值（根据行情状态）
强趋势: 止损2%, 止盈6%
弱趋势: 止损1.5%, 止盈4%
```

执行方式：
```
开仓成功后立即下达:
1. STOP_MARKET 订单（止损）
2. TAKE_PROFIT_MARKET 订单（止盈）

使用 Binance Algo Order API:
POST /fapi/v1/algo/order
{
  type: "STOP_MARKET" / "TAKE_PROFIT_MARKET",
  stopPrice: 计算出的触发价,
  positionSide: "LONG" / "SHORT"
}
```

---

### 2️⃣ 动态止盈调整

```javascript
dynamicTakeProfit: {
  enabled: true,
  weakTrendThreshold: 0.5%,   // 弱趋势判断阈值
  weakRatio: 0.6,             // 弱趋势时止盈降为60%
  strongTrendThreshold: 1.5%, // 强趋势判断阈值
  strongRatio: 1.5            // 强趋势时止盈提高到150%
}

调整逻辑:
当前趋势强度 = |EMA9 - EMA26| / price * 100

if 趋势强度 < 0.5%:
    实际止盈 = 4% * 0.6 = 2.4%
elif 趋势强度 > 1.5%:
    实际止盈 = 4% * 1.5 = 6%
else:
    实际止盈 = 4%

每5分钟重新计算并更新止盈订单
```

---

### 3️⃣ 智能止损（4种机制）

#### A. 移动止损（Trailing Stop Loss）✅ 启用

```javascript
trailingStopLoss: {
  enabled: true,
  activatePct: 0.5%,    // 盈利达到0.5%时激活
  distancePct: 0.3%,    // 距离最高点0.3%触发
  stepPct: 0.1%         // 每次移动0.1%
}

工作流程:
1. 持仓盈利达到0.5%时，启动追踪
2. 记录最高盈利点（highest_profit）
3. 每分钟检查:
   current_profit = (current_price - entry_price) / entry_price * 100

   if current_profit < highest_profit - 0.3%:
       平仓！  // 从最高点回撤0.3%
   elif current_profit > highest_profit + 0.1%:
       highest_profit += 0.1%  // 更新最高点

例子:
- 入场价: 100, 当前价: 100.5 → 盈利0.5%，启动追踪
- 价格涨到101 → 盈利1%，更新highest_profit = 1%
- 价格回落到100.7 → 盈利0.7%，回撤0.3%，触发平仓
```

#### B. EMA支撑止损 ✅ 启用

```javascript
emaSupportStopLoss: {
  enabled: true,
  bufferPct: 0.3%,      // 低于EMA26的缓冲区
  minProfitPct: 0.6%    // 盈利达到0.6%才启用
}

工作逻辑:
1. 持仓盈利 < 0.6%: 不检查
2. 持仓盈利 ≥ 0.6%: 启动EMA支撑检查

做多持仓:
  if current_price < EMA26 * (1 - 0.003):
      平仓！  // 价格跌破EMA26的0.3%

做空持仓:
  if current_price > EMA26 * (1 + 0.003):
      平仓！  // 价格升破EMA26的0.3%

检查周期: 每5分钟（使用平仓信号周期）
```

#### C. ATR动态止损 ❌ 未启用

```javascript
atrStopLoss: {
  enabled: false,
  multiplier: 2,
  minPct: 0.5%,
  maxPct: 5%
}
```

#### D. 时间衰减止损 ❌ 未启用

```javascript
timeDecayStopLoss: {
  enabled: false,
  initialPct: 3%,
  finalPct: 1%,
  decayHours: 24
}
```

---

### 4️⃣ 趋势反转退出

#### A. MA反转退出 ✅ 启用

```javascript
exitOnMAFlip: true
exitOnMAFlipThreshold: 0.5%
exitOnMAFlipConfirmBars: 1

检查逻辑（每5分钟）:
做多持仓:
  if MA10 < EMA10 且 距离 > 0.5%:
      检查最近1根K线是否确认
      → 确认则平仓

做空持仓:
  if MA10 > EMA10 且 距离 > 0.5%:
      检查最近1根K线是否确认
      → 确认则平仓
```

#### B. EMA过弱退出 ❌ 未启用

```javascript
exitOnEMAWeak: false
exitOnEMAWeakThreshold: 0.05%
```

---

### 5️⃣ 价格穿越EMA平仓 ✅ 启用

```javascript
exitOnPriceCrossEMA: {
  enabled: true,
  minProfitPct: 0.3%,   // 盈利达到0.3%才启用
  confirmBars: 1        // 需要1根K线确认
}

检查逻辑（每5分钟）:
做多持仓 且 盈利 ≥ 0.3%:
  if current_price < EMA9:
      检查最近1根K线收盘价是否也在EMA9下方
      → 确认则平仓

做空持仓 且 盈利 ≥ 0.3%:
  if current_price > EMA9:
      检查最近1根K线收盘价是否也在EMA9上方
      → 确认则平仓
```

---

### 6️⃣ 连续下跌K线止盈 ✅ 启用

```javascript
consecutiveBearishExit: {
  enabled: true,
  bars: 3,               // 连续3根
  timeframe: "5m",       // 5分钟K线
  minProfitPct: 0.2%     // 盈利达到0.2%才启用
}

检查逻辑（每5分钟）:
做多持仓 且 盈利 ≥ 0.2%:
  获取最近3根5分钟K线
  if 3根K线全部下跌（收盘价 < 开盘价）:
      平仓！  // 保护利润

做空持仓:
  不检查（做空本来就希望价格下跌）
```

---

### 7️⃣ 连续K线止损（新增）✅ 启用

```javascript
consecutiveBearishStopLoss: {
  enabled: true,
  bars: 3,               // 连续3根
  timeframe: "5m",       // 5分钟K线
  maxLossPct: -0.5%      // 亏损达到0.5%才启用
}

检查逻辑（每5分钟）:
做多持仓 且 亏损 ≥ 0.5%:
  获取最近3根5分钟K线
  if 3根K线全部下跌:
      平仓！  // 止损离场

做空持仓 且 亏损 ≥ 0.5%:
  获取最近3根5分钟K线
  if 3根K线全部上涨（收盘价 > 开盘价）:
      平仓！  // 止损离场
```

---

### 8️⃣ 平仓信号周期

```javascript
sellSignals: "ema_5m"

含义:
- 所有平仓条件检查都使用5分钟K线数据
- 每5分钟执行一次平仓检查
- 包括: MA反转、EMA穿越、连续K线等
```

---

### 9️⃣ 平仓完整优先级

```
每5分钟执行一次检查，按以下优先级:

1. 止损/止盈订单（Algo Order）
   → 由交易所自动执行，优先级最高

2. 连续K线止损
   → 亏损 ≥ 0.5% 且 连续3根K线逆向

3. 移动止损
   → 盈利 ≥ 0.5% 且 从最高点回撤 ≥ 0.3%

4. EMA支撑止损
   → 盈利 ≥ 0.6% 且 价格跌破EMA26缓冲区

5. MA反转退出
   → MA10/EMA10交叉且距离 > 0.5%

6. 价格穿越EMA平仓
   → 盈利 ≥ 0.3% 且 价格穿越EMA9

7. 连续下跌K线止盈
   → 盈利 ≥ 0.2% 且 连续3根K线下跌

任何一个条件满足，立即平仓
```

---

## 🟢 第三部分：实盘同步逻辑

### 1️⃣ 实盘同步配置

```javascript
syncLive: true              // ✅ 启用实盘同步
liveQuantityPct: 100        // 使用100%的模拟仓位数量
liveMaxPositionUsdt: null   // 无单笔最大保证金限制（使用全局默认100 USDT）
```

---

### 2️⃣ 开仓实盘同步流程

```
模拟开仓成功
  ↓
检查策略配置: syncLive = true?
  ↓ YES
计算实盘数量:
  live_quantity = simulation_quantity * (liveQuantityPct / 100)
  live_quantity = 5 USDT * (100 / 100) = 5 USDT
  ↓
检查最大保证金限制:
  required_margin = 5 USDT / 10 (杠杆) = 0.5 USDT

  全局限制: 100 USDT（来自 BinanceFuturesEngine）
  策略限制: null（使用全局）

  if required_margin > 100 USDT:
      ❌ 拒绝实盘开仓，记录日志
  else:
      ✅ 继续
  ↓
调用 BinanceFuturesEngine.open_position():
  1. 查询用户API密钥（从 user_binance_keys 表）
  2. 连接 Binance Futures API
  3. 设置杠杆: POST /fapi/v1/leverage
  4. 下市价单: POST /fapi/v1/order
     {
       symbol: "BCHUSDT" / "ZECUSDT",
       side: "BUY" / "SELL",
       type: "MARKET",
       quantity: 计算出的数量,
       positionSide: "LONG" / "SHORT"
     }
  5. 等待订单成交
  6. 获取成交价格和数量
  ↓
下止损止盈订单:
  POST /fapi/v1/algo/order (止损)
  {
    symbol: "BCHUSDT",
    side: "SELL" (做多) / "BUY" (做空),
    type: "STOP_MARKET",
    stopPrice: entry_price * (1 - 0.015) (做多) / (1 + 0.015) (做空),
    quantity: position_quantity,
    positionSide: "LONG" / "SHORT",
    workingType: "MARK_PRICE"
  }

  POST /fapi/v1/algo/order (止盈)
  {
    type: "TAKE_PROFIT_MARKET",
    stopPrice: entry_price * (1 + 0.04) (做多) / (1 - 0.04) (做空),
    ... 其他参数同上
  }
  ↓
记录到数据库:
  1. 更新 futures_positions 表
     - live_synced = 1
     - live_order_id = 订单ID
     - live_entry_price = 成交价格

  2. 插入 futures_orders 表（实盘订单记录）
  ↓
发送 Telegram 通知:
  POST https://api.telegram.org/bot{token}/sendMessage
  {
    chat_id: user.telegram_chat_id,
    text: "
      🟢 实盘开仓成功
      📊 交易对: BCH/USDT
      📈 方向: 做多
      💰 数量: 0.05 BCH
      💵 成交价: 450.50
      🎯 止盈: 468.52 (+4%)
      🛡️ 止损: 443.73 (-1.5%)
    ",
    parse_mode: "HTML"
  }
```

---

### 3️⃣ 限价单成交后同步实盘

```
限价单成交（模拟）
  ↓
LiveOrderMonitor 检测到成交
  （每10秒扫描 futures_positions 表中 status='PENDING' 的记录）
  ↓
检查是否有限价订单:
  SELECT * FROM futures_orders
  WHERE position_id = ?
    AND order_type = 'LIMIT'
    AND status = 'FILLED'
  ↓
找到已成交的限价单
  ↓
调用同步实盘逻辑（同上面开仓流程）
  ↓
下止损止盈订单
  ↓
更新数据库和发送通知
```

**注意**: 这就是之前修复的问题 —— 限价单超时转市价后，没有同步实盘。现在已修复。

---

### 4️⃣ 平仓实盘同步流程

```
模拟平仓信号触发
  ↓
检查持仓记录: live_synced = 1?
  ↓ YES (已同步过实盘)
调用 BinanceFuturesEngine.close_position():
  1. 查询实盘持仓:
     GET /fapi/v2/positionRisk

  2. 取消止损止盈订单:
     DELETE /fapi/v1/algo/order
     （根据数据库记录的 stop_loss_order_id 和 take_profit_order_id）

  3. 下市价平仓单:
     POST /fapi/v1/order
     {
       symbol: "BCHUSDT",
       side: "SELL" (平多) / "BUY" (平空),
       type: "MARKET",
       quantity: position_quantity,
       positionSide: "LONG" / "SHORT"
     }

  4. 等待订单成交
  5. 获取成交价格
  ↓
计算盈亏:
  做多: pnl = (exit_price - entry_price) * quantity * leverage
  做空: pnl = (entry_price - exit_price) * quantity * leverage

  pnl_pct = pnl / (quantity * entry_price / leverage) * 100
  ↓
更新数据库:
  UPDATE futures_positions
  SET status = 'CLOSED',
      exit_price = ?,
      exit_time = NOW(),
      live_exit_price = ?,
      live_pnl = ?,
      live_pnl_pct = ?
  WHERE id = ?
  ↓
发送 Telegram 通知:
  🔴 实盘平仓
  📊 BCH/USDT
  📈 做多
  💰 数量: 0.05 BCH
  💵 开仓: 450.50
  💵 平仓: 468.52
  💸 盈亏: +18.02 USDT (+4%)
  ⏱️ 持仓时长: 2小时30分钟
  🎯 平仓原因: 止盈触发
```

---

### 5️⃣ 实盘同步的特殊情况处理

#### A. API密钥不存在
```
检查 user_binance_keys 表
  ↓ 密钥不存在
记录错误日志: "用户未配置实盘API密钥"
  ↓
继续模拟交易（不影响模拟）
  ↓
不发送 Telegram 通知
```

#### B. 保证金不足
```
Binance API 返回错误: "Insufficient balance"
  ↓
记录错误日志
  ↓
数据库更新: live_synced = 0, notes = "实盘失败: 保证金不足"
  ↓
发送 Telegram 通知:
  ⚠️ 实盘开仓失败
  原因: 保证金不足
  建议: 请充值或降低仓位
```

#### C. 订单被拒绝
```
Binance API 返回错误: "Order would immediately trigger"
  ↓
记录详细错误日志
  ↓
尝试调整订单参数（如果可能）
  ↓ 失败
标记为失败，不重试
  ↓
发送 Telegram 通知告知用户
```

#### D. 网络超时
```
API请求超时（10秒）
  ↓
重试机制: 最多重试3次
  ↓ 仍失败
记录错误，标记同步失败
  ↓
发送 Telegram 通知
```

---

## 🔄 第四部分：完整交易生命周期示例

### 场景：BCH/USDT 做多交易

```
时间: 2025-12-10 10:00:00
行情: 强上升趋势

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏰ 10:00 - 信号检测
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ EMA15m 金叉检测到
   EMA9: 452.30
   EMA26: 450.00
   信号强度: 0.51% ✅ (> 0.1%)

✅ 趋势确认
   最近1根K线: EMA9 > EMA26 ✅

✅ MA10/EMA10同向
   MA10: 451.50, EMA10: 450.20
   MA10 > EMA10 ✅

✅ 持续趋势验证
   趋势强度: 0.51% (0.15% - 2% 范围内) ✅
   最近2根K线趋势一致 ✅
   冷却期检查: 上次开仓 65分钟前 ✅

✅ 价格偏离检查
   当前价: 452.00
   EMA26: 450.00
   偏离: +0.44% (< 1%) ✅

✅ 持仓数检查
   当前做多: 1 (< 2) ✅
   总持仓: 3 (< 4) ✅

✅ 行情状态: weak_uptrend
   允许方向: long_only ✅
   止损: 1.5%
   止盈: 4%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏰ 10:00:05 - 下单
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📝 模拟订单:
   类型: LIMIT
   价格: 451.95 (市价 - 0.01%)
   数量: 5 USDT
   杠杆: 10x

💾 写入数据库 futures_orders:
   order_id: 1234567890
   status: PENDING

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏰ 10:05:00 - 限价单成交
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ 限价单在451.95成交

💾 更新数据库:
   futures_orders.status = FILLED
   futures_positions.status = OPEN
   futures_positions.entry_price = 451.95

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏰ 10:05:10 - LiveOrderMonitor 扫描
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔍 检测到新成交持仓: position_id = 9876

✅ 策略配置: syncLive = true

🚀 开始同步实盘...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏰ 10:05:15 - 实盘开仓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔑 获取API密钥: user_id = 1

⚙️ 设置杠杆:
   POST /fapi/v1/leverage
   {symbol: "BCHUSDT", leverage: 10}

📊 下市价单:
   POST /fapi/v1/order
   {
     symbol: "BCHUSDT",
     side: "BUY",
     type: "MARKET",
     quantity: 0.011 BCH,  // 5 USDT / 452
     positionSide: "LONG"
   }

✅ 订单成交:
   orderId: 98765432
   executedQty: 0.011 BCH
   avgPrice: 452.10

🛡️ 下止损单:
   POST /fapi/v1/algo/order
   {
     type: "STOP_MARKET",
     stopPrice: 445.32,  // -1.5%
     quantity: 0.011 BCH
   }
   → algoId: 11111

🎯 下止盈单:
   POST /fapi/v1/algo/order
   {
     type: "TAKE_PROFIT_MARKET",
     stopPrice: 470.18,  // +4%
     quantity: 0.011 BCH
   }
   → algoId: 22222

💾 更新数据库:
   live_synced = 1
   live_order_id = 98765432
   live_entry_price = 452.10
   stop_loss_order_id = 11111
   take_profit_order_id = 22222

📱 发送 Telegram:
   🟢 实盘开仓成功
   📊 BCH/USDT
   📈 做多
   💰 0.011 BCH
   💵 成交价: 452.10
   🎯 止盈: 470.18 (+4%)
   🛡️ 止损: 445.32 (-1.5%)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏰ 10:25 - 价格上涨，启动移动止损
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

当前价: 454.36
入场价: 452.10
当前盈利: +0.5%

✅ 移动止损激活！
   记录 highest_profit = 0.5%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏰ 11:00 - 价格继续上涨
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

当前价: 460.50
当前盈利: +1.86%

✅ 更新 highest_profit = 1.86%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏰ 11:30 - 价格开始回落
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

当前价: 458.00
当前盈利: +1.30%

回撤检查:
   highest_profit = 1.86%
   当前盈利 = 1.30%
   回撤 = 0.56% (> 0.3%)

⚠️ 移动止损触发！

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏰ 11:30:05 - 执行平仓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📝 模拟平仓:
   exit_price: 458.00
   status: CLOSED

🚀 开始实盘平仓...

🔴 取消止损止盈订单:
   DELETE /fapi/v1/algo/order?algoId=11111
   DELETE /fapi/v1/algo/order?algoId=22222

📊 下市价平仓单:
   POST /fapi/v1/order
   {
     symbol: "BCHUSDT",
     side: "SELL",
     type: "MARKET",
     quantity: 0.011 BCH,
     positionSide: "LONG"
   }

✅ 平仓成交:
   avgPrice: 457.85

💰 计算盈亏:
   开仓: 452.10
   平仓: 457.85
   数量: 0.011 BCH
   杠杆: 10x

   PNL = (457.85 - 452.10) * 0.011 * 10
       = 5.75 * 0.11
       = 0.6325 USDT

   PNL% = 0.6325 / (0.011 * 452.10 / 10) * 100
        = 0.6325 / 0.497 * 100
        = +1.27%

💾 更新数据库:
   status = CLOSED
   exit_price = 458.00
   live_exit_price = 457.85
   live_pnl = 0.6325
   live_pnl_pct = 1.27

📱 发送 Telegram:
   🔴 实盘平仓
   📊 BCH/USDT 📈 做多
   💵 开仓: 452.10
   💵 平仓: 457.85
   💸 盈亏: +0.63 USDT (+1.27%)
   ⏱️ 持仓: 1小时25分钟
   🎯 原因: 移动止损触发

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ 交易完成
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 📌 关键要点总结

### 模拟交易
1. **开仓极其严格** - 需要通过7-8个过滤条件
2. **平仓灵活多样** - 9种平仓机制保护利润
3. **行情自适应** - 不同行情使用不同参数
4. **限价单优先** - 节省手续费，超时转市价

### 实盘同步
1. **完全自动化** - 模拟成功后自动同步
2. **独立止损止盈** - 使用交易所 Algo Order API
3. **Telegram通知** - 所有关键操作都通知
4. **错误处理完善** - 网络、余额、参数错误都有处理

### 风控机制
1. **最大持仓限制** - 总共4个，做多/做空各2个
2. **冷却时间** - 同交易对同方向30分钟
3. **价格偏离限制** - 防止追高追低
4. **保证金限制** - 单笔最大100 USDT（全局默认）
5. **智能止损** - 4种止损机制保护本金

### 代码文件对应关系
- 开仓逻辑: `app/trading/futures_trading_engine.py` 的 `check_entry_signals()`
- 平仓逻辑: `app/trading/futures_trading_engine.py` 的 `check_exit_signals()`
- 限价单执行: `app/services/futures_limit_order_executor.py`
- 实盘开仓: `app/trading/binance_futures_engine.py` 的 `open_position()`
- 实盘平仓: `app/trading/binance_futures_engine.py` 的 `close_position()`
- 实盘监控: `app/services/live_order_monitor.py`
- Telegram通知: `app/services/telegram_notifier.py`

---

**最后更新**: 2025-12-10
**策略版本**: ZEC-BCH (ID: 1764130914476)
