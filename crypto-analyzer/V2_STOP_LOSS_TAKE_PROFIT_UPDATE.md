# V2策略止盈止损功能添加

## 问题描述
用户发现V2 K线回调策略在开仓时没有设置止盈止损，这可能导致风险控制不足。

## 解决方案
为V2策略的KlinePullbackEntryExecutor添加完整的止盈止损功能，与一次性开仓逻辑保持一致。

## 修改文件

### 1. `app/services/kline_pullback_entry_executor.py`

#### 修改1: 增强初始化参数
**位置**: `__init__` 方法 (第24行)

**变更**:
- 添加 `brain` 参数 - 智能大脑，用于获取自适应止盈止损参数
- 添加 `opt_config` 参数 - 优化配置，用于获取波动率配置

```python
def __init__(self, db_config: dict, live_engine, price_service, account_id=None, brain=None, opt_config=None):
    # ...
    self.brain = brain if brain else getattr(live_engine, 'brain', None)
    self.opt_config = opt_config if opt_config else getattr(live_engine, 'opt_config', None)
```

#### 修改2: 添加止盈止损计算方法
**位置**: 第54行之前

**新增方法**: `_calculate_stop_take_prices()`

**功能**:
1. 根据方向获取自适应参数 (brain.adaptive_long / adaptive_short)
2. 使用波动率自适应止损 (复用live_engine的方法)
3. 使用波动率配置计算动态止盈
4. 回退到自适应参数（如果波动率配置不可用）
5. 返回止损/止盈价格和百分比

**核心逻辑**:
```python
# 止损计算
base_stop_loss_pct = adaptive_params.get('stop_loss_pct', 0.03)
stop_loss_pct = self.live_engine.calculate_volatility_adjusted_stop_loss(...)

# 止盈计算 (优先使用波动率配置)
if volatility_profile:
    if direction == 'LONG':
        take_profit_pct = volatility_profile['long_fixed_tp_pct']
    else:
        take_profit_pct = volatility_profile['short_fixed_tp_pct']
else:
    take_profit_pct = adaptive_params.get('take_profit_pct', 0.02)

# 价格计算
if direction == 'LONG':
    stop_loss_price = current_price * (1 - stop_loss_pct)
    take_profit_price = current_price * (1 + take_profit_pct)
else:  # SHORT
    stop_loss_price = current_price * (1 + stop_loss_pct)
    take_profit_price = current_price * (1 - take_profit_pct)
```

#### 修改3: 创建持仓时计算并保存止盈止损
**位置**: `execute_entry` 方法，创建持仓记录之前 (第184行)

**变更**:
- 在创建持仓前获取当前价格
- 调用 `_calculate_stop_take_prices()` 计算止盈止损
- INSERT语句中使用计算的值替代原来的None

**之前**:
```python
None,  # stop_loss_price
None,  # take_profit_price
None,  # stop_loss_pct
None,  # take_profit_pct
```

**之后**:
```python
stop_loss_price,   # 计算的止损价格
take_profit_price, # 计算的止盈价格
stop_loss_pct,     # 计算的止损百分比
take_profit_pct,   # 计算的止盈百分比
```

#### 修改4: 第1批建仓时更新止盈止损
**位置**: 第1批建仓完成时的UPDATE语句 (第585行)

**变更**:
- 基于第1批实际成交价格重新计算止盈止损
- UPDATE语句中添加止盈止损字段

```python
# 重新计算止盈止损（基于第1批实际成交价格）
stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct = \
    self._calculate_stop_take_prices(symbol, direction, float(entry_price), signal_components)

# UPDATE语句添加字段
UPDATE futures_positions
SET quantity = %s,
    entry_price = %s,
    avg_entry_price = %s,
    notional_value = %s,
    margin = %s,
    open_time = %s,
    batch_filled = %s,
    stop_loss_price = %s,      # 新增
    take_profit_price = %s,    # 新增
    stop_loss_pct = %s,        # 新增
    take_profit_pct = %s,      # 新增
    updated_at = NOW()
WHERE id = %s
```

#### 修改5: 后续批次建仓时更新止盈止损
**位置**: `_update_position_after_batch` 方法 (第651行)

**变更**:
- 基于新的平均价格重新计算止盈止损
- UPDATE语句中添加止盈止损字段

```python
# 重新计算止盈止损（基于新的平均价格）
stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct = \
    self._calculate_stop_take_prices(symbol, direction, avg_price, signal_components)

# UPDATE语句添加字段
UPDATE futures_positions
SET entry_price = %s,
    quantity = %s,
    margin = %s,
    batch_filled = %s,
    stop_loss_price = %s,      # 新增
    take_profit_price = %s,    # 新增
    stop_loss_pct = %s,        # 新增
    take_profit_pct = %s,      # 新增
WHERE id = %s
```

### 2. `coin_futures_trader_service.py`

#### 修改: 初始化V2执行器时传入brain和opt_config
**位置**: 第1276行

**变更**:
```python
self.smart_entry_executor = KlinePullbackEntryExecutor(
    db_config=self.db_config,
    live_engine=self,
    price_service=self.ws_service,
    account_id=self.account_id,
    brain=self.brain,           # 新增: 传入智能大脑
    opt_config=self.opt_config  # 新增: 传入优化配置
)
logger.info("✅ 智能分批建仓执行器已启动 (V2 K线回调策略 + 止盈止损)")  # 更新日志
```

## 止盈止损计算逻辑

### 止损计算
1. 基础止损: 从brain.adaptive_long/short获取 `stop_loss_pct` (默认3%)
2. 波动率调整: 使用 `calculate_volatility_adjusted_stop_loss()` 根据信号组成调整
3. 方向计算:
   - LONG: `stop_loss_price = current_price × (1 - stop_loss_pct)`
   - SHORT: `stop_loss_price = current_price × (1 + stop_loss_pct)`

### 止盈计算
1. 优先使用波动率配置:
   - LONG: `volatility_profile['long_fixed_tp_pct']` (基于15M阳线统计)
   - SHORT: `volatility_profile['short_fixed_tp_pct']` (基于15M阴线统计)
2. 回退到自适应参数: `adaptive_params['take_profit_pct']` (默认2%)
3. 方向计算:
   - LONG: `take_profit_price = current_price × (1 + take_profit_pct)`
   - SHORT: `take_profit_price = current_price × (1 - take_profit_pct)`

## 特性

✅ **动态更新**: 每批建仓后，止盈止损基于新的平均价格重新计算
✅ **波动率自适应**: 根据交易对的历史波动率调整止盈目标
✅ **信号组成感知**: 止损根据信号强度进行波动率调整
✅ **与V1一致**: 使用与一次性开仓相同的计算逻辑

## 测试建议

1. **查看新建仓位的止盈止损**:
   ```sql
   SELECT symbol, position_side, entry_price,
          stop_loss_price, stop_loss_pct,
          take_profit_price, take_profit_pct,
          status
   FROM futures_positions
   WHERE entry_signal_type = 'kline_pullback_v2'
   AND status IN ('building', 'open')
   ORDER BY created_at DESC
   LIMIT 10;
   ```

2. **验证止盈止损更新**:
   - 观察分批建仓过程中，每批完成后止盈止损价格的变化
   - 确认最终止盈止损基于平均入场价格

3. **对比V1和V2的止盈止损**:
   - 检查两种策略是否使用相同的计算逻辑
   - 验证波动率配置是否正确应用

## 注意事项

⚠️ **SmartExitOptimizer**:
- V2策略的止盈止损会被写入数据库
- SmartExitOptimizer会读取这些值并管理实际平仓
- 如果止盈止损为None，优化器会使用默认逻辑

⚠️ **重启服务生效**:
- 修改代码后需要重启币本位交易服务
- 已有的building状态持仓会在下次更新时刷新止盈止损

⚠️ **配置依赖**:
- 需要brain和opt_config正确初始化
- 如果这些配置缺失，会回退到默认值或由优化器管理

## 影响范围

- ✅ 新建的V2策略持仓会有止盈止损
- ✅ 分批建仓过程中会动态更新止盈止损
- ✅ 与SmartExitOptimizer兼容
- ⚠️ 已有的building持仓需等待下次批次更新才会设置止盈止损

## 完成时间
2026-02-20
