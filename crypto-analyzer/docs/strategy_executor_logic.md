# StrategyExecutor 执行逻辑详解

## 一、整体架构

`StrategyExecutor` 是一个策略自动执行服务，用于定期检查启用的策略，并根据技术指标信号自动执行买入和平仓操作。

### 核心组件

1. **StrategyExecutor** - 策略执行器主类
2. **FuturesTradingEngine** - 合约交易引擎（负责实际开仓/平仓）
3. **TechnicalIndicators** - 技术分析器（计算技术指标）
4. **StrategyHitRecorder** - 策略命中记录器（记录信号命中情况）
5. **DatabaseService** - 数据库服务（保存交易记录）

---

## 二、初始化流程

### 1. 初始化参数

```python
def __init__(self, db_config: Dict, futures_engine: FuturesTradingEngine, technical_analyzer=None):
```

- `db_config`: 数据库配置（MySQL连接信息）
- `futures_engine`: 合约交易引擎实例
- `technical_analyzer`: 技术分析器实例（可选，默认创建新实例）

### 2. 初始化内容

- 设置本地时区（UTC+8）
- 初始化数据库服务（用于保存交易记录）
- 初始化策略命中记录器

---

## 三、执行流程

### 主循环：`run_loop(interval=5)`

```
启动 → 循环执行 → 停止
  ↓         ↓         ↓
start()  check_and_execute_strategies()  stop()
```

**执行步骤：**
1. 设置 `running = True`
2. 每 `interval` 秒（默认5秒）执行一次 `check_and_execute_strategies()`
3. 如果出错，记录日志但继续运行
4. 当 `running = False` 时退出循环

### 策略检查：`check_and_execute_strategies()`

```
检查运行状态 → 加载策略 → 执行每个策略
```

**执行步骤：**
1. 检查 `self.running`，如果为 `False` 则返回
2. 调用 `_load_strategies()` 从数据库加载所有启用的策略
3. 遍历每个策略，调用 `execute_strategy(strategy, account_id)` 执行

### 策略加载：`_load_strategies()`

**数据来源：** `trading_strategies` 表

**SQL查询：**
```sql
SELECT * FROM trading_strategies 
WHERE enabled = 1
ORDER BY id ASC
```

**处理逻辑：**
1. 查询所有 `enabled = 1` 的策略
2. 解析每个策略的 `config` 字段（JSON格式）
3. 合并策略基本信息（id, name, account_id, enabled）和配置信息
4. 返回策略配置字典列表

---

## 四、策略执行：`execute_strategy(strategy, account_id)`

### 1. 解析策略配置参数

从策略配置字典中提取所有参数：

#### 基础参数
- `symbols`: 交易对列表，如 `['BTC/USDT', 'ETH/USDT']`
- `buyDirection`: 买入方向，如 `['long']` 或 `['short']` 或 `['long', 'short']`
- `leverage`: 杠杆倍数，默认 5
- `positionSize`: 仓位大小（账户权益的百分比），默认 10%

#### 买入信号参数
- `buySignals`: 买入信号类型
  - `'ema_5m'` → 5分钟EMA交叉
  - `'ema_15m'` → 15分钟EMA交叉
  - `'ema_1h'` → 1小时EMA交叉
  - `'ma_ema5'` → 5分钟MA10/EMA10交叉
  - `'ma_ema10'` → 5分钟MA10/EMA10交叉
- `buyVolumeEnabled`: 是否启用买入成交量过滤
- `buyVolumeLong`: 做多成交量要求
- `buyVolumeShort`: 做空成交量要求

#### 卖出信号参数
- `sellSignals`: 卖出信号类型（同上）
- `sellVolumeEnabled`: 是否启用卖出成交量过滤
- `sellVolume`: 卖出成交量要求

#### 价格类型
- `longPrice`: 做多价格类型，`'market'` 或 `'limit'`
- `shortPrice`: 做空价格类型，`'market'` 或 `'limit'`

#### 风险控制参数
- `stopLoss`: 止损百分比（如 2.0 表示 2%）
- `takeProfit`: 止盈百分比
- `maxPositions`: 最大持仓数
- `maxLongPositions`: 最大做多持仓数
- `maxShortPositions`: 最大做空持仓数
- `minHoldingTimeHours`: 最小持仓时间（小时）

#### 趋势过滤参数
- `ma10Ema10TrendFilter`: 是否启用MA10/EMA10趋势过滤
- `minEMACrossStrength`: 最小EMA交叉强度（%）
- `minMA10CrossStrength`: 最小MA10/EMA10交叉强度（%）
- `trendConfirmBars`: 趋势确认K线数量
- `trendConfirmEMAThreshold`: 趋势确认EMA差值阈值（%）

#### 平仓条件参数
- `exitOnMAFlip`: MA10/EMA10反转时立即平仓
- `exitOnMAFlipThreshold`: MA反转阈值（%）
- `exitOnEMAWeak`: EMA差值<阈值时平仓
- `exitOnEMAWeakThreshold`: EMA弱信号阈值（%）
- `earlyStopLossPct`: 早期止损百分比

#### 其他参数
- `preventDuplicateEntry`: 防止重复开仓
- `closeOppositeOnEntry`: 开仓前先平掉相反方向的持仓
- `feeRate`: 手续费率，默认 0.0004

#### 技术指标过滤参数
- `rsiFilter`: RSI过滤配置
  - `enabled`: 是否启用
  - `longMax`: 做多最大RSI值（默认70）
  - `shortMin`: 做空最小RSI值（默认30）
- `macdFilter`: MACD过滤配置
  - `enabled`: 是否启用
  - `longRequirePositive`: 做多要求MACD为正
  - `shortRequireNegative`: 做空要求MACD为负
- `kdjFilter`: KDJ过滤配置
  - `enabled`: 是否启用
  - `longMaxK`: 做多最大K值（默认80）
  - `shortMinK`: 做空最小K值（默认20）
  - `allowStrongSignal`: 允许强信号
  - `strongSignalThreshold`: 强信号阈值
- `bollingerFilter`: 布林带过滤配置
  - `enabled`: 是否启用

### 2. 确定时间周期

根据买入/卖出信号类型，映射到对应的时间周期：

```python
timeframe_map = {
    'ema_5m': '5m',
    'ema_15m': '15m',
    'ema_1h': '1h',
    'ma_ema5': '5m',
    'ma_ema10': '5m'
}
buy_timeframe = timeframe_map.get(buy_signal, '15m')
sell_timeframe = timeframe_map.get(sell_signal, '5m')
```

### 3. 计算时间范围

- **当前时间**: 本地时间（UTC+8）
- **起始时间**: 当前时间 - 24小时
- **扩展起始时间**: 起始时间 - 30天（用于计算技术指标需要的历史数据）

### 4. 遍历交易对执行

对每个 `symbol` 执行以下步骤：

#### 4.1 获取K线数据

**买入时间周期K线：**
```sql
SELECT timestamp, open_price, high_price, low_price, close_price, volume 
FROM kline_data 
WHERE symbol = %s AND timeframe = %s 
AND timestamp >= %s AND timestamp <= %s
ORDER BY timestamp ASC
```

**卖出时间周期K线：** 同上，但使用 `sell_timeframe`

#### 4.2 验证K线数据

- 买入时间周期：至少需要 100 条（5m/15m）或 50 条（1h）
- 卖出时间周期：同上
- 最近24小时内的K线：至少需要 10 条

如果数据不足，记录错误并跳过该交易对。

#### 4.3 调用内部执行方法

调用 `_execute_symbol_strategy()` 执行单个交易对的策略逻辑。

---

## 五、单交易对执行：`_execute_symbol_strategy()`

这是核心执行逻辑，包含完整的买入/卖出判断和持仓管理。

### 1. 获取当前持仓

从 `futures_engine.get_open_positions(account_id)` 获取当前账户的所有持仓，筛选出当前交易对的持仓。

**持仓信息包括：**
- `position_id`: 持仓ID
- `direction`: 方向（'long' 或 'short'）
- `entry_price`: 开仓价格
- `quantity`: 数量
- `entry_time`: 开仓时间
- `leverage`: 杠杆倍数
- `stop_loss_price`: 止损价格
- `take_profit_price`: 止盈价格

### 2. 计算技术指标

对买入和卖出时间周期的K线分别计算技术指标：

#### 2.1 为每个K线计算指标

对每个测试时间范围内的K线：
1. 获取到当前K线为止的所有历史K线
2. 转换为DataFrame格式
3. 调用 `technical_analyzer.analyze(df)` 计算指标

**计算的指标包括：**
- EMA9 和 EMA26（用于EMA交叉信号）
- MA10 和 EMA10（用于MA10/EMA10交叉信号）
- RSI（用于RSI过滤）
- MACD（用于MACD过滤）
- KDJ（用于KDJ过滤）
- 布林带（用于布林带过滤）

#### 2.2 构建指标-K线对

将每个K线和对应的指标组合成 `indicator_pairs` 列表，用于后续的信号检测。

### 3. 遍历时间点执行交易逻辑

对每个时间点（K线）执行以下逻辑：

#### 3.1 获取当前K线和指标

- 当前K线：`kline`
- 当前指标：`indicator`
- 当前时间：`current_time`（UTC）和 `current_time_local`（本地时间）
- 当前价格：`close_price`

#### 3.2 检查持仓的平仓条件

对每个持仓，按优先级检查以下平仓条件：

**优先级1：止损/止盈**
- 如果 `stop_loss_price` 存在且价格触及止损价 → 平仓
- 如果 `take_profit_price` 存在且价格触及止盈价 → 平仓

**优先级2：MA10/EMA10反转（如果启用 `exitOnMAFlip`）**
- 计算MA10和EMA10的差值变化
- 如果差值从正变负（或从负变正），且变化幅度超过阈值 → 平仓

**优先级3：EMA弱信号（如果启用 `exitOnEMAWeak`）**
- 计算EMA9和EMA26的差值百分比
- 如果差值 < 阈值（默认0.05%） → 平仓

**优先级4：趋势反转（如果启用 `exitOnMAFlip`）**
- 检测MA10/EMA10死叉（做多持仓）或金叉（做空持仓） → 平仓

**优先级5：卖出信号**
- 检测卖出时间周期的死叉信号（EMA或MA10/EMA10）
- 如果检测到死叉 → 平仓

**优先级6：卖出成交量条件**
- 如果启用 `sellVolumeEnabled`，检查成交量是否满足条件
- 条件包括：`>1`, `0.8-1`, `0.6-0.8`, `<0.6`

如果满足任何平仓条件，调用 `futures_engine.close_position()` 执行平仓，并保存交易记录。

#### 3.3 检查买入信号

如果没有持仓或满足开仓条件，检查买入信号：

**步骤1：检查持仓限制**
- 如果 `preventDuplicateEntry=True` 且已有持仓 → 跳过
- 如果达到 `maxPositions` 限制 → 跳过
- 如果达到 `maxLongPositions` 或 `maxShortPositions` 限制 → 跳过

**步骤2：检测买入信号**

根据 `buy_signal` 类型检测信号：

**EMA交叉信号（ema_5m, ema_15m, ema_1h）：**
- 获取当前和前一个K线的EMA9和EMA26值
- 检测金叉：`prev_ema_short <= prev_ema_long and ema_short > ema_long`
- 检测死叉：`prev_ema_short >= prev_ema_long and ema_short < ema_long`
- 计算交叉强度：`abs(ema_short - ema_long) / ema_long * 100`
- 如果强度 < `min_ema_cross_strength` → 跳过

**MA10/EMA10交叉信号（ma_ema5, ma_ema10）：**
- 获取当前和前一个K线的MA10和EMA10值
- 检测金叉：`prev_ema10 <= prev_ma10 and ema10 > ma10`
- 检测死叉：`prev_ema10 >= prev_ma10 and ema10 < ma10`
- 计算交叉强度：`abs(ema10 - ma10) / ma10 * 100`
- 如果强度 < `min_ma10_cross_strength` → 跳过

**步骤3：趋势确认（如果启用）**

如果 `trend_confirm_bars > 0`：
- 检查最近N个K线的趋势是否一致
- 对于做多：要求最近N个K线都是EMA多头（EMA9 > EMA26）
- 对于做空：要求最近N个K线都是EMA空头（EMA9 < EMA26）

**步骤4：技术指标过滤**

**RSI过滤：**
- 如果启用 `rsi_filter_enabled`：
  - 做多：RSI <= `rsi_long_max`（默认70）
  - 做空：RSI >= `rsi_short_min`（默认30）

**MACD过滤：**
- 如果启用 `macd_filter_enabled`：
  - 做多：MACD > 0（如果 `macd_long_require_positive=True`）
  - 做空：MACD < 0（如果 `macd_short_require_negative=True`）

**KDJ过滤：**
- 如果启用 `kdj_filter_enabled`：
  - 做多：K <= `kdj_long_max_k`（默认80），或允许强信号且信号强度 >= 阈值
  - 做空：K >= `kdj_short_min_k`（默认20），或允许强信号且信号强度 >= 阈值

**布林带过滤：**
- 如果启用 `bollinger_filter_enabled`：
  - 做多：价格在下轨附近或突破下轨
  - 做空：价格在上轨附近或突破上轨

**步骤5：成交量过滤**

计算成交量比率：`当前K线成交量 / 前N个K线平均成交量`

**做多成交量条件：**
- 如果启用 `buy_volume_enabled` 和 `buy_volume_long_enabled`：
  - 检查 `buy_volume_long` 或 `buy_volume` 条件
  - 如果成交量比率 < 要求值 → 跳过

**做空成交量条件：**
- 如果启用 `buy_volume_enabled` 和 `buy_volume_short_enabled`：
  - 检查 `buy_volume_short` 条件
  - 支持格式：数值（如 `0.3`）、`>1`、`0.8-1`、`0.6-0.8`、`<0.6`

**步骤6：确定交易方向**

根据检测到的交叉类型和 `buyDirection` 配置确定方向：
- 金叉 → 做多（如果 `'long'` 在 `buyDirection` 中）
- 死叉 → 做空（如果 `'short'` 在 `buyDirection` 中）
- 如果没有检测到交叉，根据EMA状态判断

**步骤7：开仓前处理**

如果启用 `close_opposite_on_entry`：
- 平掉所有相反方向的持仓
- 调用 `futures_engine.close_position()` 执行平仓

**步骤8：执行开仓**

1. **计算持仓数量：**
   - 从数据库获取账户权益：`SELECT total_equity FROM paper_trading_accounts WHERE id = %s`
   - 计算持仓价值：`account_equity * (position_size / 100)`
   - 计算数量：`(position_value * leverage) / entry_price`
   - 根据交易对精度四舍五入

2. **调用交易引擎开仓：**
   ```python
   open_result = self.futures_engine.open_position(
       account_id=account_id,
       symbol=symbol,
       position_side='LONG' if direction == 'long' else 'SHORT',
       quantity=quantity_decimal,
       leverage=leverage,
       limit_price=entry_price_decimal if price_type != 'market' else None,
       stop_loss_pct=Decimal(str(stop_loss_pct)) if stop_loss_pct else None,
       take_profit_pct=Decimal(str(take_profit_pct)) if take_profit_pct else None,
       source='strategy',
       signal_id=None
   )
   ```

3. **保存交易记录：**
   - 调用 `_save_trade_record()` 保存到 `strategy_trade_records` 表

4. **更新持仓列表：**
   - 将新持仓添加到 `positions` 列表

### 4. 返回执行结果

返回包含以下信息的字典：
- `symbol`: 交易对
- `trades_count`: 交易次数
- `trades`: 交易记录列表
- `open_positions`: 当前持仓数
- `debug_info`: 调试信息列表
- `klines_count`: K线数量
- `indicators_count`: 指标数量
- `golden_cross_count`: 金叉次数
- `death_cross_count`: 死叉次数

---

## 六、保存执行结果

执行完成后，将结果保存到数据库：

### 1. 计算汇总统计

- `total_symbols`: 交易对总数
- `total_trades`: 总交易次数
- `winning_trades`: 盈利交易次数
- `losing_trades`: 亏损交易次数
- `win_rate`: 胜率
- `total_pnl`: 总盈亏
- `total_pnl_percent`: 总盈亏百分比

### 2. 保存到主表

**表名：** `strategy_execution_results`

**字段：**
- `strategy_id`, `strategy_name`, `account_id`
- `strategy_config`: 策略配置（JSON格式）
- `execution_start_time`, `execution_end_time`
- `execution_duration_hours`
- `total_symbols`, `total_trades`, `winning_trades`, `losing_trades`
- `win_rate`, `initial_balance`, `final_balance`
- `total_pnl`, `total_pnl_percent`
- `status`: 'completed'

### 3. 保存到详情表

**表名：** `strategy_execution_result_details`

**字段：**
- `execution_result_id`: 关联主表ID
- `symbol`: 交易对
- `trades_count`, `buy_count`, `sell_count`
- `winning_trades`, `losing_trades`, `win_rate`
- `total_pnl`, `total_pnl_percent`
- `golden_cross_count`, `death_cross_count`
- `klines_count`, `indicators_count`
- `error_message`: 错误信息（如果有）
- `execution_result_data`: 完整结果数据（JSON格式）
- `debug_info`: 调试信息（JSON格式）

---

## 七、配置文件参数说明

策略配置存储在数据库 `trading_strategies` 表的 `config` 字段中（JSON格式）。

### 示例配置

```json
{
  "symbols": ["BTC/USDT", "ETH/USDT"],
  "buyDirection": ["long"],
  "leverage": 5,
  "positionSize": 10,
  "buySignals": "ema_15m",
  "sellSignals": "ema_5m",
  "buyVolumeEnabled": true,
  "buyVolumeLong": "1.5",
  "longPrice": "market",
  "shortPrice": "market",
  "stopLoss": 2.0,
  "takeProfit": 5.0,
  "maxPositions": 3,
  "maxLongPositions": 2,
  "maxShortPositions": 1,
  "minHoldingTimeHours": 1,
  "ma10Ema10TrendFilter": true,
  "minEMACrossStrength": 0.1,
  "trendConfirmBars": 3,
  "exitOnMAFlip": true,
  "exitOnMAFlipThreshold": 0.1,
  "preventDuplicateEntry": true,
  "closeOppositeOnEntry": false,
  "feeRate": 0.0004,
  "rsiFilter": {
    "enabled": true,
    "longMax": 70,
    "shortMin": 30
  },
  "macdFilter": {
    "enabled": true,
    "longRequirePositive": true,
    "shortRequireNegative": true
  },
  "kdjFilter": {
    "enabled": false,
    "longMaxK": 80,
    "shortMinK": 20
  },
  "bollingerFilter": {
    "enabled": false
  }
}
```

---

## 八、启动和停止

### 启动

```python
executor = StrategyExecutor(db_config, futures_engine)
executor.start(interval=30)  # 每30秒检查一次
```

### 停止

```python
executor.stop()
```

---

## 九、关键特性

1. **实时执行**：定期检查策略并执行交易
2. **多交易对支持**：可以同时监控多个交易对
3. **多策略支持**：可以同时运行多个策略
4. **风险控制**：支持止损、止盈、最大持仓数等风险控制
5. **技术指标过滤**：支持RSI、MACD、KDJ、布林带等指标过滤
6. **成交量过滤**：支持买入/卖出成交量条件
7. **趋势确认**：支持趋势确认机制，避免假信号
8. **详细记录**：保存完整的执行结果和调试信息

---

## 十、注意事项

1. **时间处理**：所有时间都使用UTC存储，显示时转换为本地时间（UTC+8）
2. **持仓管理**：持仓信息从 `futures_engine` 获取，确保与实际持仓一致
3. **错误处理**：如果某个策略执行失败，不会影响其他策略的执行
4. **数据要求**：需要足够的历史K线数据才能计算技术指标
5. **账户余额**：开仓时从数据库获取账户权益，确保使用最新余额

