# 交易逻辑文档

> 最后更新: 2025-12-23

---

## 目录

1. [开仓逻辑](#一开仓逻辑)
2. [平仓逻辑](#二平仓逻辑)
3. [限价单监控](#三限价单监控)
4. [止损止盈机制](#四止损止盈机制)
5. [模拟盘与实盘对比](#五模拟盘与实盘对比)
6. [关键配置参数](#六关键配置参数)

---

## 一、开仓逻辑

### 1.1 开仓信号来源

| 信号类型 | 说明 | 文件位置 |
|----------|------|----------|
| **金叉/死叉** | EMA9上穿/下穿EMA26 | strategy_executor_v2.py |
| **连续趋势** | 5分钟K线连续放大 | strategy_executor_v2.py |
| **持续趋势** | EMA9持续高于/低于EMA26 | strategy_executor_v2.py |
| **反转开仓** | 平仓后立即反向开仓 | strategy_executor_v2.py |

### 1.2 开仓前条件检查

**文件**: `app/services/strategy_executor_v2.py` (2064-2137行)

```
开仓信号触发
    ↓
[1] 信号去重检查（同一15分钟K线内不重复触发）
    ↓
[2] 持仓+挂单数量检查（同方向最多3个）
    ↓
[3] 获取当前价格和EMA数据
    ↓
[4] 计算限价（市价 ± 偏移百分比）
    ↓
[5] 创建限价单（一次最多创建3个）
```

#### 条件检查详情

| 检查项 | 条件 | 结果 |
|--------|------|------|
| 信号去重 | 同一15分钟K线内已触发过 | 静默跳过 |
| 持仓上限 | 持仓数 + 挂单数 >= 3 | 静默跳过 |
| 账户余额 | 可用余额 < 保证金 + 手续费 | 拒绝开仓 |
| 单笔限制 | 保证金 > 总权益的10% | 拒绝开仓 |

### 1.3 限价单价格计算

**文件**: `app/services/strategy_executor_v2.py` (1741-1770行)

| 价格类型 | 做多限价 | 做空限价 |
|----------|----------|----------|
| market_minus_0_6 | 市价 × 0.994 | 市价 × 1.006 |
| market_minus_0_8 | 市价 × 0.992 | 市价 × 1.008 |
| market_minus_1 | 市价 × 0.99 | 市价 × 1.01 |
| market_minus_1_2 | 市价 × 0.988 | 市价 × 1.012 |

### 1.4 模拟盘开仓流程

**文件**: `app/trading/futures_trading_engine.py` (364-868行)

```
1. 获取当前价格（优先实时API，回退数据库缓存）
2. 限价单判断:
   - 做多: 当前价 > 限价 → 创建PENDING订单
   - 做多: 当前价 ≤ 限价 → 立即成交
   - 做空: 当前价 < 限价 → 创建PENDING订单
   - 做空: 当前价 ≥ 限价 → 立即成交
3. 计算保证金 = 名义价值 / 杠杆
4. 计算手续费 = 名义价值 × 0.04%
5. 计算止损止盈价格
6. 计算开仓时EMA差值（用于趋势反转检测）
7. 创建持仓记录、订单记录、交易记录
8. 冻结保证金（限价单不冻结，成交时才扣除）
```

### 1.5 实盘开仓流程

**文件**: `app/trading/binance_futures_engine.py` (504-750行)

```
1. 设置杠杆倍数和逐仓模式
2. 获取价格和精度处理
3. 构建订单参数（限价/市价）
4. 发送订单到币安API
5. 等待成交（市价单轮询最多3次）
6. 计算止盈止损价格
7. 设置止盈止损订单（币安条件单）
```

---

## 二、平仓逻辑

### 2.1 平仓触发条件（优先级顺序）

**文件**: `app/trading/stop_loss_monitor.py` (558-656行)

| 优先级 | 条件 | 做多触发 | 做空触发 |
|--------|------|----------|----------|
| **1** | 强制平仓 | 当前价 ≤ 强平价 | 当前价 ≥ 强平价 |
| **2** | 连续K线止损 | 连续N根阴线 | 连续N根阳线 |
| **3** | 移动止损 | 当前价 ≤ 移动止损价 | 当前价 ≥ 移动止损价 |
| **3** | 固定止损 | 当前价 ≤ 止损价 | 当前价 ≥ 止损价 |
| **4** | 止盈 | 当前价 ≥ 止盈价 | 当前价 ≤ 止盈价 |
| **5** | EMA差值反转 | 差值收窄/变号 | 差值收窄/变号 |
| **6** | 盈利保护 | 从峰值回撤超阈值 | 从峰值回撤超阈值 |

### 2.2 止损价格计算

| 方向 | 止损价计算 | 止盈价计算 |
|------|------------|------------|
| 做多 | 开仓价 × (1 - 止损%) | 开仓价 × (1 + 止盈%) |
| 做空 | 开仓价 × (1 + 止损%) | 开仓价 × (1 - 止盈%) |

### 2.3 模拟盘平仓流程

**文件**: `app/trading/futures_trading_engine.py` (870-1343行)

```
1. 获取持仓信息
2. 获取平仓价格（止盈止损用触发价，否则用实时价）
3. 计算盈亏:
   - 多头: PnL = (平仓价 - 开仓价) × 数量
   - 空头: PnL = (开仓价 - 平仓价) × 数量
   - 实际盈亏 = PnL - 手续费
   - ROI = 盈亏 / 保证金 × 100
4. 更新持仓状态（全部平仓: closed, 部分平仓: 更新数量）
5. 释放保证金回到余额
6. 更新账户统计（总交易数、胜率）
7. 同步实盘平仓（如果启用syncLive）
8. 发送Telegram通知
```

### 2.4 实盘平仓流程

**文件**: `app/trading/binance_futures_engine.py` (978-1150行)

```
1. 从数据库获取持仓信息
2. 从币安获取最新持仓量（避免精度问题）
3. 发送市价平仓订单
4. 计算实际盈亏和ROI
5. 更新数据库持仓状态
6. 取消相关止盈止损单
7. 发送Telegram通知
```

---

## 三、限价单监控

**文件**: `app/services/futures_limit_order_executor.py`

### 3.1 扫描频率

- **默认5秒**一次扫描所有PENDING状态的限价单

### 3.2 检查流程

```
每5秒扫描
    ↓
[1] 趋势转向检测 → 出现反向EMA交叉 → 取消限价单
    ↓
[2] 超时检测 → 超过配置时间（默认15分钟）→ 取消限价单
    ↓
[3] 价格触发检测:
    - 做多: 当前价 ≤ 限价 → 触发
    - 做空: 当前价 ≥ 限价 → 触发
    ↓
[4] 持仓数量检查 → 同方向已有3个持仓 → 取消限价单
    ↓
[5] RSI过滤检查:
    - 做多: RSI > 65 (超买) → 取消
    - 做空: RSI < 35 (超卖) → 取消
    ↓
[6] EMA差值检查 → 差值 < 0.05% → 取消限价单
    ↓
[7] 执行开仓 → 创建持仓记录
```

### 3.3 检查详情

#### 趋势转向检测 (`_check_trend_reversal`)

| 订单方向 | 取消条件 | 说明 |
|----------|----------|------|
| 做多限价单 | 出现死叉 | EMA9下穿EMA26 |
| 做空限价单 | 出现金叉 | EMA9上穿EMA26 |

- **周期**: 策略配置的买入时间周期（默认15分钟）
- **配置**: `cancelOnTrendReversal: true` (默认启用)

#### RSI过滤 (`_check_rsi_filter`)

| 订单方向 | 取消条件 | 默认阈值 |
|----------|----------|----------|
| 做多限价单 | RSI > longMax | 65 |
| 做空限价单 | RSI < shortMin | 35 |

- **周期**: 默认15分钟
- **配置**: `rsiFilter.enabled: true`

#### EMA差值检查 (`_check_ema_diff_too_small`)

| 条件 | 结果 |
|------|------|
| EMA差值 < 0.05% | 取消限价单（趋势不明朗） |

- **周期**: 15分钟
- **配置**: `minEmaDiff: 0.05`

---

## 四、止损止盈机制

### 4.1 止损类型

**文件**: `app/services/strategy_executor.py` (1819-2039行)

| 类型 | 说明 | 配置 |
|------|------|------|
| **固定止损** | 开仓时设置的止损价格 | `stopLossPct` |
| **ATR止损** | 基于波动率的动态止损 | `smartStopLoss.atrStopLoss` |
| **时间衰减止损** | 持仓越久止损越紧 | `smartStopLoss.timeDecayStopLoss` |
| **移动止损** | 止损价随盈利上移/下移 | `smartStopLoss.trailingStopLoss` |
| **EMA支撑止损** | 使用EMA26作为动态支撑位 | `smartStopLoss.emaSupportStopLoss` |
| **连续K线止损** | 连续反向K线提前离场 | `consecutiveBearishExit` |

#### 移动止损参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| activatePct | 1.0% | 盈利超过此值时激活 |
| distancePct | 0.5% | 从最高盈利回撤此值时触发 |
| stepPct | 0.1% | 止损价每次移动的最小幅度 |

#### 时间衰减止损参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| initialPct | 3.0% | 初始止损百分比 |
| finalPct | 1.0% | 最终止损百分比 |
| hours | 8 | 衰减时间 |

### 4.2 止盈类型

| 类型 | 说明 | 文件位置 |
|------|------|----------|
| **固定止盈** | 开仓时设置的止盈价格 | strategy_executor.py |
| **动态止盈** | 根据趋势强度调整 | strategy_executor.py |
| **盈利保护** | 盈利超阈值后回撤触发 | strategy_executor.py |
| **EMA差值止盈** | 趋势过度延伸时止盈 | strategy_executor_v2.py |
| **EMA差值反转** | 趋势减弱/反转时出场 | strategy_executor.py |

#### EMA差值止盈 (`emaDiffTakeProfit`)

| 参数 | 默认值 | 说明 |
|------|--------|------|
| enabled | false | 是否启用 |
| threshold | 0.5% | EMA差值超过此值时止盈 |
| minProfitPct | 0.3% | 最小盈利要求 |

**注意**: 当差值**变大**时止盈（趋势过热），建议阈值设置1.2%-1.5%

#### EMA差值反转 (`emaDiffReversal`)

| 参数 | 默认值 | 说明 |
|------|--------|------|
| enabled | false | 是否启用 |
| shrinkPct | 50% | 差值收窄超过此比例时出场 |
| minHoldMinutes | 30 | 最小持仓时间 |

**触发条件**:
- 差值变号（完全反转）→ 立即平仓
- 差值收窄超过阈值 → 平仓

#### 盈利保护 (`profitProtection`)

| 参数 | 默认值 | 说明 |
|------|--------|------|
| activatePct | 1.0% | 盈利超过此值时激活保护 |
| trailingPct | 0.5% | 从峰值回撤此值时触发 |
| minLockPct | 0.3% | 保底盈利线 |

---

## 五、模拟盘与实盘对比

| 特性 | 模拟盘 | 实盘 |
|------|--------|------|
| **数据表** | futures_positions | live_futures_positions |
| | futures_orders | live_futures_orders |
| | futures_trades | live_futures_trades |
| **交易引擎** | FuturesTradingEngine | BinanceFuturesEngine |
| **订单执行** | 本地数据库模拟 | 币安API真实执行 |
| **止盈止损** | StopLossMonitor监控 | 币安条件单 |
| **保证金** | 本地账户表管理 | 币安账户实际冻结 |
| **手续费** | 0.04%（固定） | 0.04%（币安实际费率） |
| **强平** | 本地计算 | 币安自动强平 |
| **同步机制** | 可同步到实盘 | 独立运行 |

### 同步配置

```yaml
# 策略配置中
syncLive: true  # 启用同步到实盘
liveQuantityPct: 100  # 实盘开仓数量百分比
```

---

## 六、关键配置参数

### 6.1 开仓配置

```yaml
entryCooldown:
  enabled: true
  maxPositionsPerDirection: 3  # 同方向最多3个持仓

limitOrderTimeoutMinutes: 15  # 限价单超时时间
limitOrderMaxDeviation: 1.5   # 限价单最大偏离百分比

crossSignalForceMarket: true  # 金叉/死叉信号强制市价开仓
cancelOnTrendReversal: true   # 趋势转向时取消限价单
minEmaDiff: 0.05              # 最小EMA差值阈值
```

### 6.2 止损配置

```yaml
stopLossPct: 1.5  # 固定止损百分比
takeProfitPct: 5  # 固定止盈百分比

smartStopLoss:
  atrStopLoss:
    enabled: false
    multiplier: 2.0
    minPct: 0.5
    maxPct: 5.0

  trailingStopLoss:
    enabled: true
    activatePct: 1.0
    distancePct: 0.5
    stepPct: 0.1

  timeDecayStopLoss:
    enabled: true
    initialPct: 3.0
    finalPct: 1.0
    hours: 8
```

### 6.3 止盈配置

```yaml
emaDiffTakeProfit:
  enabled: true
  threshold: 1.2      # 建议1.2-1.5%
  minProfitPct: 0.3

emaDiffReversal:
  enabled: true
  shrinkPct: 50
  minHoldMinutes: 30
  timeframe: "15m"

profitProtection:
  enabled: true
  activatePct: 1.0
  trailingPct: 0.5
  minLockPct: 0.3
```

### 6.4 RSI过滤配置

```yaml
rsiFilter:
  enabled: true
  period: 14
  timeframe: "15m"
  longMax: 65   # 做多时RSI上限
  shortMin: 35  # 做空时RSI下限
```

### 6.5 连续K线出场配置

```yaml
consecutiveBearishExit:
  enabled: true
  bars: 3          # 连续K线数量
  timeframe: "5m"  # K线周期
```

---

## 附录：关键文件位置

| 功能 | 文件路径 |
|------|----------|
| 策略执行器V2 | app/services/strategy_executor_v2.py |
| 策略执行器V1 | app/services/strategy_executor.py |
| 模拟盘交易引擎 | app/trading/futures_trading_engine.py |
| 实盘交易引擎 | app/trading/binance_futures_engine.py |
| 止损监控器 | app/trading/stop_loss_monitor.py |
| 限价单执行器 | app/services/futures_limit_order_executor.py |
| 实盘订单监控 | app/services/live_order_monitor.py |
