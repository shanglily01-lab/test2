# Strategy Executor（策略自动执行器）完整逻辑文档

## 一、概述

`strategy_executor.py` 是一个实时自动执行交易策略的服务，它会定期检查启用的策略，根据EMA信号自动执行买入和平仓操作。

## 二、核心类和方法

### 1. StrategyExecutor 类

#### 1.1 初始化 (`__init__`)
- **输入参数**：
  - `db_config`: 数据库配置字典
  - `futures_engine`: 合约交易引擎实例
  - `technical_analyzer`: 技术分析器实例（可选）
  
- **初始化内容**：
  - 保存数据库配置、交易引擎、技术分析器
  - 初始化策略命中记录器 (`StrategyHitRecorder`)
  - 设置本地时区（UTC+8）
  - 初始化数据库服务（用于保存交易记录）

#### 1.2 辅助方法

**`get_local_time()`**
- 获取当前本地时间（UTC+8）

**`get_current_price(symbol)`**
- 获取指定交易对的实时价格
- 通过交易引擎的 `get_current_price` 方法获取

**`get_quantity_precision(symbol)`**
- 根据交易对获取数量精度（小数位数）
- 默认返回8位

**`round_quantity(quantity, symbol)`**
- 根据交易对精度对数量进行四舍五入

**`parse_time(t)`**
- 解析时间字符串或datetime对象
- 统一转换为UTC时间（无时区信息）

**`utc_to_local(utc_dt)`**
- 将UTC时间转换为本地时间（仅用于显示）

**`_save_trade_record(...)`**
- 保存交易记录到数据库
- 保存到 `strategy_trade_records` 表

**`_get_connection()`**
- 获取数据库连接

**`_load_strategies()`**
- 从数据库加载启用的策略
- 从 `trading_strategies` 表查询 `enabled = 1` 的策略
- 解析策略配置JSON并返回策略列表

---

## 三、主要执行流程

### 3.1 execute_strategy（执行策略主方法）

#### 输入参数
- `strategy`: 策略配置字典
- `account_id`: 账户ID（默认2）

#### 执行流程

**步骤1：提取策略配置参数**
- 交易对列表 (`symbols`)
- 买入方向 (`buyDirection`)
- 杠杆倍数 (`leverage`)
- 买入信号类型 (`buySignals`: ema_5m/ema_15m/ema_1h/ma_ema10)
- 卖出信号类型 (`sellSignals`)
- 成交量过滤配置
- 持仓限制配置（最大持仓数、最大做多/做空持仓数）
- 价格类型（市价/限价）
- 止损止盈百分比
- 信号强度过滤配置
- 趋势确认配置
- 技术指标过滤配置（RSI、MACD、KDJ、布林带）
- 其他高级配置

**步骤2：确定时间周期**
- 根据买入信号类型映射到K线时间周期：
  - `ema_5m` → `5m`
  - `ema_15m` → `15m`
  - `ema_1h` → `1h`
  - `ma_ema10` → `5m`
- 卖出时间周期默认 `5m`

**步骤3：确定时间范围**
- **当前时间**（本地）：`now_local = datetime.now(LOCAL_TZ)`
- **结束时间**（本地）：`end_time_local = now_local`
- **开始时间**（本地）：`start_time_local = now_local - timedelta(hours=24)` （检查过去24小时）
- **扩展开始时间**（UTC）：`extended_start_time = end_time - timedelta(days=30)` （用于计算技术指标的历史数据）
- 转换为UTC时间用于数据库查询

**步骤4：遍历每个交易对**

对每个交易对执行以下操作：

1. **获取K线数据**
   - 从数据库获取买入时间周期的K线（从 `extended_start_time` 到 `end_time`）
   - 从数据库获取卖出时间周期的K线（从 `extended_start_time` 到 `end_time`）
   - 检查K线数量是否满足最小要求（5m/15m需要100条，1h需要50条）

2. **筛选测试K线**
   - 筛选出过去24小时内的买入K线（`buy_test_klines`）
   - 筛选出过去24小时内的卖出K线（`sell_test_klines`）
   - 如果没有找到，至少使用最新的K线

3. **调用内部方法执行策略**
   - 调用 `_execute_symbol_strategy` 方法，传入所有参数

**步骤5：保存执行结果**
- 计算执行结果汇总（总交易数、盈亏统计等）
- 保存到 `strategy_execution_results` 表
- 保存每个交易对的详情到 `strategy_execution_result_details` 表

---

### 3.2 _execute_symbol_strategy（执行单个交易对策略）

这是核心方法，包含完整的策略执行逻辑。

#### 输入参数
- `symbol`: 交易对
- `buy_klines`: 买入时间周期的历史K线（30天）
- `sell_klines`: 卖出时间周期的历史K线（30天）
- `buy_test_klines`: 买入时间周期的测试K线（过去24小时）
- `sell_test_klines`: 卖出时间周期的测试K线（过去24小时）
- 其他策略配置参数...

#### 执行流程

**阶段1：准备阶段**

1. **获取当前持仓**
   - 从交易引擎获取账户的当前持仓
   - 筛选出当前交易对的持仓
   - 转换为内部格式（包含持仓ID、方向、入场价、数量、入场时间等）

2. **计算技术指标**
   - 定义内部函数 `calculate_indicators(klines, test_klines, timeframe_name)`
   - 对每个测试K线：
     - 获取到该K线为止的所有历史K线
     - 检查历史K线数量是否满足最小要求
     - 转换为DataFrame格式
     - 使用技术分析器计算指标：
       - EMA9/26 (`ema_short`, `ema_long`)
       - MA10/EMA10 (`ma10`, `ema10`)
       - MA5/EMA5 (`ma5`, `ema5`)
       - 成交量比率 (`volume_ratio`)
       - RSI (`rsi`)
       - MACD (`macd_histogram`)
       - KDJ (`kdj_k`)
     - 将K线和指标配对保存
   - 计算买入时间周期的指标对列表 (`buy_indicator_pairs`)
   - 计算卖出时间周期的指标对列表 (`sell_indicator_pairs`)

3. **获取实时价格**
   - 调用 `get_current_price(symbol)` 获取实时价格
   - 如果获取失败，返回错误

**阶段2：卖出信号检测（平仓）**

1. **检查止损止盈**
   - 遍历所有持仓
   - 对每个持仓：
     - 检查止损价格（使用实时价格）
     - 检查止盈价格（使用实时价格，需要满足最小持仓时间）
     - 如果触发，使用交易引擎平仓，保存交易记录

2. **检查趋势反转退出**
   - 检查MA10/EMA10反转（如果启用 `exit_on_ma_flip`）
   - 检查EMA弱信号（如果启用 `exit_on_ema_weak`）
   - 检查早期止损（如果启用 `early_stop_loss_pct`）
   - 如果触发，使用交易引擎平仓

3. **检查卖出信号（EMA/MA死叉）**
   - 获取最新卖出指标对 (`latest_sell_pair`)
   - 检查前3个K线（从最新开始向前）
   - 对每个K线对：
     - 比较前一个K线和当前K线的指标值
     - 检测死叉信号：
       - `ma_ema5`: MA5/EMA5死叉
       - `ma_ema10`: MA10/EMA10死叉
       - `ema_5m/ema_15m/ema_1h`: EMA9/26死叉
   - 检查卖出成交量条件
   - 如果触发，使用交易引擎平仓（使用实时价格）

**阶段3：买入信号检测（开仓）**

1. **遍历过去24小时内的所有K线对**
   - 从最新的K线开始，向前遍历所有K线对
   - 对每个K线对（`curr_pair` 和 `prev_pair`）：
     - 提取当前K线和前一个K线的指标值
     - 检查EMA数据是否完整

2. **检测EMA穿越信号**
   - **EMA9/26金叉**：前一个K线 `EMA9 <= EMA26`，当前K线 `EMA9 > EMA26`
   - **EMA9/26死叉**：前一个K线 `EMA9 >= EMA26`，当前K线 `EMA9 < EMA26`
   - **MA10/EMA10金叉**：前一个K线 `EMA10 <= MA10`，当前K线 `EMA10 > MA10`

3. **信号过滤和验证**
   如果检测到信号：
   - **信号强度过滤**：检查EMA差值是否满足最小强度要求
   - **成交量条件**：检查成交量比率是否满足要求
   - **持仓限制**：
     - 防止重复开仓（如果启用）
     - 检查最大持仓数限制
     - 检查最大做多/做空持仓数限制
   - **技术指标过滤**：
     - RSI过滤（如果启用）
     - MACD过滤（如果启用）
     - KDJ过滤（如果启用）
   - **MA10/EMA10趋势过滤**（如果启用）
   - **趋势持续性确认**（如果启用 `trend_confirm_bars`）：
     - 找到金叉发生的K线位置
     - 检查从金叉到当前的所有K线，趋势是否一直维持
     - 检查EMA差值是否满足阈值

4. **执行开仓**
   如果所有检查都通过：
   - 确定交易方向（金叉=做多，死叉=做空）
   - 计算入场价格（根据 `longPrice`/`shortPrice` 配置，使用实时价格）
   - 获取账户余额
   - 计算仓位大小（根据 `positionSize` 百分比）
   - 计算止损止盈价格
   - 调用交易引擎开仓（`futures_engine.open_position`）
   - 保存交易记录到数据库
   - 添加到持仓列表

**阶段4：返回结果**
- 统计信号检测情况（金叉/死叉数量）
- 返回执行结果字典，包含：
  - 交易数量
  - 交易列表
  - 当前持仓数
  - 调试信息
  - K线数量
  - 指标数量
  - 金叉/死叉数量

---

## 四、循环执行机制

### 4.1 check_and_execute_strategies
- 从数据库加载所有启用的策略
- 对每个策略调用 `execute_strategy` 方法
- 记录执行日志

### 4.2 run_loop
- 定时循环执行（默认5秒间隔）
- 在循环中调用 `check_and_execute_strategies`
- 处理异常和取消信号

### 4.3 start
- 启动后台任务
- 创建事件循环并运行 `run_loop`

### 4.4 stop
- 停止后台任务
- 取消事件循环任务

---

## 五、关键特性

### 5.1 实时执行
- 使用实时价格执行交易
- 从交易引擎获取真实持仓
- 调用交易引擎执行真实交易

### 5.2 信号检测
- 检查过去24小时内的所有K线，寻找EMA穿越信号
- 从最新K线开始向前遍历，找到信号后立即执行
- 比较相邻K线的EMA值来检测穿越

### 5.3 多重过滤
- 信号强度过滤
- 成交量过滤
- 技术指标过滤（RSI、MACD、KDJ）
- 趋势确认
- 持仓限制

### 5.4 风险控制
- 止损止盈
- 早期止损
- 趋势反转退出
- 最大持仓数限制

---

## 六、数据流

```
数据库 (trading_strategies)
    ↓
_load_strategies()
    ↓
execute_strategy()
    ↓
数据库 (kline_data) → 获取K线数据
    ↓
_execute_symbol_strategy()
    ↓
计算技术指标 → 检测信号 → 执行交易
    ↓
交易引擎 (futures_engine) → 执行真实交易
    ↓
数据库 (strategy_trade_records) → 保存交易记录
    ↓
数据库 (strategy_execution_results) → 保存执行结果
```

---

## 七、注意事项

1. **时间处理**：
   - 数据库中的时间都是UTC时间
   - 显示时转换为本地时间（UTC+8）
   - 计算和比较时使用UTC时间

2. **信号检测**：
   - 必须比较相邻两个K线的EMA值才能检测穿越
   - 不能只检查当前K线的EMA值

3. **价格使用**：
   - 检测信号时使用K线的指标值
   - 执行交易时使用实时价格

4. **持仓管理**：
   - 从交易引擎获取真实持仓
   - 执行交易后更新持仓列表（用于后续逻辑）

5. **错误处理**：
   - 每个步骤都有异常处理
   - 错误会记录到日志，但不会中断整个流程

