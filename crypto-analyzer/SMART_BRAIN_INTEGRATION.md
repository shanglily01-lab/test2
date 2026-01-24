# 超级大脑集成指南

## 概述

本文档说明如何将超级大脑优化组件（SmartEntryExecutor 和 SmartExitOptimizer）集成到现有交易流程中。

## 架构概览

```
┌─────────────────┐
│ SmartDecisionBrain│ ← 信号生成（评分、决策）
└────────┬────────┘
         │ 生成信号
         ▼
┌─────────────────┐
│SmartEntryExecutor│ ← 智能分批建仓（30分钟，30%/30%/40%）
└────────┬────────┘
         │ 开仓完成
         ▼
┌─────────────────┐
│SmartExitOptimizer│ ← 智能分层平仓（实时监控）
└────────┬────────┘
         │ 平仓完成
         ▼
    数据库记录
```

## 组件说明

### 1. 现有组件

- **SmartDecisionBrain** (`app/services/smart_decision_brain.py`)
  - 基于K线多维度分析的交易决策系统
  - 输出：交易信号（direction, score, trade_params）

- **RealtimePositionMonitor** (`app/services/realtime_position_monitor.py`)
  - 实时持仓监控服务（WebSocket价格推送）
  - 当前功能：移动止盈/硬止损监控

- **FuturesTradingEngine** (`app/trading/futures_trading_engine.py`)
  - 模拟合约交易引擎
  - 功能：开仓、平仓、价格查询

### 2. 新增组件

- **PriceSampler** (`app/services/price_sampler.py`)
  - 实时价格采样器（滚动5分钟窗口）
  - 功能：建立价格基线、动态评估入场价格

- **SmartEntryExecutor** (`app/services/smart_entry_executor.py`)
  - 智能分批建仓执行器
  - 功能：30分钟内完成30%/30%/40%分批建仓

- **SmartExitOptimizer** (`app/services/smart_exit_optimizer.py`)
  - 智能平仓优化器
  - 功能：分层平仓逻辑（基于盈利百分比和回撤）

## 集成步骤

### 步骤1: 数据库升级

执行数据库迁移脚本：

```bash
mysql -u root -p binance-data < app/database/smart_brain_schema.sql
```

新增字段：
- `batch_plan` - 分批建仓计划
- `batch_filled` - 已完成批次记录
- `entry_signal_time` - 信号发出时间
- `avg_entry_price` - 平均入场价
- `planned_close_time` - 计划平仓时间
- `close_extended` - 是否延长平仓时间
- `extended_close_time` - 延长后的平仓时间
- `max_profit_pct` - 最高盈利百分比
- `max_profit_price` - 最高盈利时的价格
- `max_profit_time` - 达到最高盈利的时间

### 步骤2: 修改现有开仓流程

**原始流程**（需要修改）：

```python
# 在信号生成服务中（如strategy_executor）
signal = smart_brain.should_trade(symbol)

if signal['decision']:
    # 当前：直接一次性开仓
    position = futures_engine.open_position(
        symbol=symbol,
        direction=signal['direction'],
        ...
    )
```

**新流程**（集成SmartEntryExecutor）：

```python
from app.services.smart_entry_executor import SmartEntryExecutor
from app.services.binance_ws_price import get_ws_price_service

# 初始化智能建仓执行器（全局初始化一次）
price_service = get_ws_price_service()
smart_entry = SmartEntryExecutor(
    db_config=db_config,
    live_engine=futures_engine,
    price_service=price_service
)

# 信号生成
signal = smart_brain.should_trade(symbol)

if signal['decision']:
    # 新：使用智能分批建仓
    entry_result = await smart_entry.execute_entry({
        'symbol': symbol,
        'direction': signal['direction'],
        'total_margin': 400,  # USDT保证金
        'leverage': 10,  # 杠杆倍数
        'strategy_id': strategy_id,
        'trade_params': signal['trade_params']
    })

    if entry_result['success']:
        position_id = entry_result['position_id']
        logger.info(f"✅ 智能建仓完成: {symbol} | 平均价格: {entry_result['avg_price']}")
    else:
        logger.error(f"❌ 智能建仓失败: {entry_result['error']}")
```

### 步骤3: 修改现有平仓监控流程

**原始流程**（需要修改）：

```python
# 在RealtimePositionMonitor中
# 当前：只有移动止盈和硬止损逻辑
async def _check_position(self, position, current_price):
    # 检查移动止盈
    # 检查硬止损
    pass
```

**新流程**（集成SmartExitOptimizer）：

有两个集成选项：

#### **选项A: 完全替换现有监控逻辑**

```python
# 在RealtimePositionMonitor初始化时
from app.services.smart_exit_optimizer import SmartExitOptimizer

class RealtimePositionMonitor:
    def __init__(self, db_config, strategy_executor=None, fallback_callback=None):
        # ...现有初始化代码...

        # 新增：初始化智能平仓优化器
        self.smart_exit = SmartExitOptimizer(
            db_config=db_config,
            live_engine=strategy_executor.live_engine if strategy_executor else None,
            price_service=self.ws_service
        )

    async def start(self, account_id: int = 2):
        # ...现有启动代码...

        # 加载所有开放持仓后，启动智能平仓监控
        positions = self.load_open_positions(account_id)

        for position in positions:
            # 为每个持仓启动智能平仓监控
            await self.smart_exit.start_monitoring_position(position['id'])

        logger.info(f"✅ 智能平仓优化器已启动，监控 {len(positions)} 个持仓")
```

#### **选项B: 双轨并行（推荐用于过渡期）**

```python
class RealtimePositionMonitor:
    def __init__(self, db_config, strategy_executor=None, fallback_callback=None):
        # ...现有代码...
        self.smart_exit = SmartExitOptimizer(
            db_config=db_config,
            live_engine=strategy_executor.live_engine if strategy_executor else None,
            price_service=self.ws_service
        )

        # 配置项：是否启用智能平仓优化器
        self.enable_smart_exit = True  # 可以通过配置文件控制

    async def _check_position(self, position, current_price):
        """检查单个持仓的止盈止损"""

        # 检查是否由智能平仓优化器管理
        if self.enable_smart_exit and position.get('batch_plan'):
            # 这个持仓由智能建仓生成，使用智能平仓优化器
            # 不执行原有逻辑，由SmartExitOptimizer独立监控
            return

        # 原有逻辑（移动止盈/硬止损）
        # ...现有代码...
```

### 步骤4: 服务启动集成

修改主服务启动脚本（如 `main.py` 或交易服务入口）：

```python
import asyncio
from app.services.smart_entry_executor import SmartEntryExecutor
from app.services.smart_exit_optimizer import SmartExitOptimizer
from app.services.binance_ws_price import get_ws_price_service
from app.services.realtime_position_monitor import init_realtime_monitor

async def main():
    # 1. 初始化WebSocket价格服务
    price_service = get_ws_price_service()

    # 2. 初始化智能建仓执行器（全局单例）
    smart_entry = SmartEntryExecutor(
        db_config=db_config,
        live_engine=futures_engine,
        price_service=price_service
    )

    # 3. 初始化实时监控服务（自动集成智能平仓优化器）
    monitor = init_realtime_monitor(
        db_config=db_config,
        strategy_executor=strategy_executor,
        fallback_callback=None
    )

    # 4. 启动监控服务
    await monitor.start(account_id=2)

    logger.info("✅ 超级大脑交易系统已启动")

    # 5. 保持运行
    while True:
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
```

## 配置说明

### config.yaml 新增配置项

```yaml
# 超级大脑优化配置
smart_brain:
  # 智能建仓配置
  entry:
    enabled: true  # 是否启用智能建仓
    batch_ratios: [0.3, 0.3, 0.4]  # 分批比例
    time_window_minutes: 30  # 建仓时间窗口
    sampling_window_seconds: 300  # 价格采样窗口（5分钟）
    sampling_interval_seconds: 10  # 采样间隔

  # 智能平仓配置
  exit:
    enabled: true  # 是否启用智能平仓
    high_profit_threshold: 3.0  # 高盈利阈值（%）
    high_profit_drawback: 0.5  # 高盈利回撤止盈（%）
    mid_profit_threshold: 1.0  # 中盈利阈值（%）
    mid_profit_drawback: 0.4  # 中盈利回撤止盈（%）
    low_profit_target: 1.0  # 低盈利目标（%）
    micro_loss_threshold: -0.5  # 微亏损阈值（%）
    extension_minutes: 30  # 延长时间（分钟）
```

## 测试建议

### 单元测试

```python
# tests/test_smart_entry_executor.py
import pytest
from app.services.smart_entry_executor import SmartEntryExecutor

@pytest.mark.asyncio
async def test_entry_execution():
    # 测试智能建仓流程
    signal = {
        'symbol': 'BTC/USDT',
        'direction': 'LONG',
        'total_margin': 400,
        'leverage': 10
    }

    result = await smart_entry.execute_entry(signal)
    assert result['success'] == True
    assert len(result['plan']['batches']) == 3
    assert all(b['filled'] for b in result['plan']['batches'])
```

### 模拟测试

1. **小仓位测试**：使用最小保证金（如50 USDT）测试完整流程
2. **监控日志**：观察建仓价格评分、平仓触发条件
3. **对比测试**：同时运行原有逻辑和新逻辑，对比结果

## 回滚方案

如果需要回滚到原有逻辑：

```python
# 方案1: 配置文件控制
smart_brain:
  entry:
    enabled: false  # 禁用智能建仓，使用原有一次性开仓
  exit:
    enabled: false  # 禁用智能平仓，使用原有移动止盈

# 方案2: 代码回滚
# 将 RealtimePositionMonitor 中的 enable_smart_exit 设为 False
```

## 监控指标

需要监控的关键指标：

1. **建仓效率**
   - 平均建仓用时（是否在30分钟内完成）
   - 平均入场价格与信号价格的偏差
   - 各批次价格评分分布

2. **平仓效果**
   - 各层级平仓触发次数（高盈利/中盈利/低盈利/微亏损）
   - 延长平仓触发次数
   - 平均持仓时间

3. **整体表现**
   - 胜率变化
   - 平均盈利变化
   - 最大回撤变化

## 常见问题

### Q1: WebSocket价格服务断线怎么办？

SmartExitOptimizer 继承了 RealtimePositionMonitor 的降级机制，会自动切换到REST API轮询模式。

### Q2: 建仓过程中出现异常怎么办？

SmartEntryExecutor 有超时强制建仓机制：
- 第1批：15分钟超时强制建仓
- 第2批：20分钟超时强制建仓
- 第3批：28分钟超时强制建仓

确保30分钟内完成所有建仓。

### Q3: 如何查看智能建仓的详细过程？

查看数据库 `futures_positions` 表的 `batch_plan` 和 `batch_filled` 字段：

```sql
SELECT
    symbol,
    direction,
    batch_plan,
    batch_filled,
    avg_entry_price,
    entry_signal_time
FROM futures_positions
WHERE id = ?;
```

### Q4: 智能平仓和原有移动止盈会冲突吗？

推荐使用"选项B：双轨并行"集成方案，通过 `batch_plan` 字段区分：
- 有 `batch_plan`：由智能平仓优化器管理
- 无 `batch_plan`：由原有移动止盈管理

## 性能优化建议

1. **数据库连接池**：SmartExitOptimizer 已使用连接池（pool_size=3）
2. **异步并发**：所有监控任务使用 `asyncio.create_task` 并发运行
3. **缓存优化**：价格基线计算使用滚动窗口，避免重复计算

## 下一步计划

1. **实盘小规模测试**（1-2周）
   - 选择3-5个币种
   - 使用最小仓位
   - 收集性能数据

2. **参数优化**
   - 根据实盘数据调整分批比例
   - 优化价格评分权重
   - 调整平仓阈值

3. **功能扩展**
   - 支持做空方向的智能建仓
   - 增加市场波动率自适应
   - 集成风险控制模块
