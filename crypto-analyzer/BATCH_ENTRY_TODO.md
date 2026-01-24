# 分批建仓功能待办事项

## ⚠️ 当前状态：已禁用

分批建仓和智能平仓功能已暂时禁用（config.yaml 中 `enabled: false`），因为核心开仓逻辑还未实现。

---

## ✅ 已完成的工作

### 1. 基础设施
- [x] 数据库迁移（smart_brain_schema.sql）
- [x] 配置文件结构（config.yaml 中 batch_entry 和 smart_exit）
- [x] SmartEntryExecutor 基础框架
- [x] SmartExitOptimizer 完整实现
- [x] PriceSampler 价格采样器

### 2. 集成工作
- [x] 导入执行器到 smart_trader_service.py
- [x] 事件循环引用修复
- [x] 信号验证逻辑提前
- [x] 平仓逻辑分离（batch_plan 持仓 vs 普通持仓）

### 3. 文档
- [x] SMART_BRAIN_REQUIREMENTS.md - 需求文档
- [x] SMART_BRAIN_INTEGRATION.md - 集成指南
- [x] BATCH_ENTRY_INTEGRATION_PLAN.md - 集成计划
- [x] BATCH_ENTRY_TESTING_GUIDE.md - 测试指南

---

## ❌ 待完成的核心功能

### 🔴 关键任务：实现 SmartEntryExecutor 的实际开仓逻辑

**文件**: `app/services/smart_entry_executor.py`

#### 需要实现的方法

##### 1. `_execute_batch()` - 执行单批次开仓

**当前状态**：
```python
# TODO: 调用实际开仓逻辑
# await self.live_engine.open_position(...)
```

**需要实现**：
```python
async def _execute_batch(self, plan: Dict, batch_num: int, price: Decimal, reason: str):
    """执行单批次开仓（实际开仓逻辑）"""
    batch = plan['batches'][batch_num]
    symbol = plan['symbol']
    direction = plan['direction']

    # 1. 计算这一批次的实际数量
    # 2. 调用交易引擎开仓（或直接插入数据库模拟）
    # 3. 记录批次信息
    # 4. 如果是最后一批，创建完整的持仓记录
```

##### 2. `_create_position_record()` - 创建持仓记录

**需要实现**：
```python
async def _create_position_record(self, plan: Dict, signal: Dict) -> int:
    """
    创建分批建仓的持仓记录

    需要插入的字段：
    - batch_plan: JSON（分批计划）
    - batch_filled: JSON（已完成批次）
    - entry_signal_time: 信号发出时间
    - avg_entry_price: 加权平均入场价
    - planned_close_time: 计划平仓时间（基于entry_score）
    - 其他常规字段

    Returns:
        position_id: 持仓ID
    """
```

##### 3. `_freeze_margin()` - 冻结保证金

**需要实现**：
```python
async def _freeze_margin(self, total_margin: float, account_id: int):
    """
    冻结保证金

    UPDATE futures_trading_accounts
    SET current_balance = current_balance - %s,
        frozen_balance = frozen_balance + %s
    WHERE id = %s
    """
```

##### 4. 完善 `execute_entry()` 返回值

**当前**：返回模拟数据

**需要**：返回真实的持仓ID和数据
```python
return {
    'success': True,
    'position_id': position_id,  # 实际的持仓ID
    'avg_price': avg_price,      # 实际的平均价格
    'total_quantity': total_qty, # 实际的总数量
    'plan': plan                 # 完整的建仓计划
}
```

---

## 📋 详细实现步骤

### 步骤1: 实现批次开仓逻辑

**问题**：如何开仓？
- 选项A：调用交易所API（真实交易）
- 选项B：调用模拟引擎（FuturesTradingEngine）
- 选项C：直接操作数据库（最简单，用于测试）

**推荐**：先用选项C（直接数据库），验证逻辑正确后再切换到实际引擎

**代码位置**：`app/services/smart_entry_executor.py:359`

```python
async def _execute_batch(self, plan: Dict, batch_num: int, price: Decimal, reason: str):
    """执行单批次开仓"""
    import pymysql

    batch = plan['batches'][batch_num]
    symbol = plan['symbol']
    direction = plan['direction']

    # 计算这一批的保证金和数量
    batch_margin = plan['total_margin'] * batch['ratio']
    batch_quantity = (batch_margin * plan['leverage']) / float(price)

    # 记录批次信息
    batch['filled'] = True
    batch['price'] = float(price)
    batch['time'] = datetime.now()
    batch['margin'] = batch_margin
    batch['quantity'] = batch_quantity

    logger.info(
        f"✅ {symbol} 第{batch_num+1}批建仓完成 | "
        f"价格: ${price:.4f} | "
        f"数量: {batch_quantity:.2f} | "
        f"保证金: ${batch_margin:.0f} | "
        f"原因: {reason}"
    )
```

### 步骤2: 实现持仓记录创建

**何时创建**：第3批完成后

**代码位置**：在 `execute_entry()` 末尾，检测到所有批次完成后

```python
# 在 execute_entry() 中，所有批次完成后
if all(b['filled'] for b in plan['batches']):
    # 创建持仓记录
    position_id = await self._create_position_record(plan, signal)

    # 返回结果
    return {
        'success': True,
        'position_id': position_id,
        'avg_price': self._calculate_avg_price(plan),
        'total_quantity': sum(b['quantity'] for b in plan['batches']),
        'plan': plan
    }
```

**实现 `_create_position_record()`**：

```python
async def _create_position_record(self, plan: Dict, signal: Dict) -> int:
    """创建分批建仓持仓记录"""
    import pymysql
    import json

    conn = pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    try:
        symbol = plan['symbol']
        direction = plan['direction']

        # 计算汇总数据
        total_quantity = sum(b['quantity'] for b in plan['batches'])
        avg_price = self._calculate_avg_price(plan)
        total_margin = sum(b['margin'] for b in plan['batches'])

        # 准备 batch_plan 和 batch_filled JSON
        batch_plan_json = json.dumps({
            'batches': [
                {
                    'ratio': b['ratio'],
                    'target_price': None,  # 可以存储目标价格
                    'timeout_minutes': [15, 20, 28][i]
                }
                for i, b in enumerate(plan['batches'])
            ]
        })

        batch_filled_json = json.dumps({
            'batches': [
                {
                    'ratio': b['ratio'],
                    'price': b['price'],
                    'time': b['time'].isoformat(),
                    'margin': b['margin'],
                    'quantity': b['quantity']
                }
                for b in plan['batches']
            ]
        })

        # 计算计划平仓时间（基于 entry_score）
        entry_score = signal.get('trade_params', {}).get('entry_score', 30)
        if entry_score >= 45:
            max_hold_minutes = 360  # 6小时
        elif entry_score >= 30:
            max_hold_minutes = 240  # 4小时
        else:
            max_hold_minutes = 120  # 2小时

        from datetime import datetime, timedelta
        planned_close_time = datetime.now() + timedelta(minutes=max_hold_minutes)

        # 计算止损止盈价格
        adaptive_params = signal.get('trade_params', {}).get('adaptive_params', {})
        stop_loss_pct = adaptive_params.get('stop_loss_pct', 0.03)
        take_profit_pct = adaptive_params.get('take_profit_pct', 0.02)

        if direction == 'LONG':
            stop_loss = avg_price * (1 - stop_loss_pct)
            take_profit = avg_price * (1 + take_profit_pct)
        else:  # SHORT
            stop_loss = avg_price * (1 + stop_loss_pct)
            take_profit = avg_price * (1 - take_profit_pct)

        # 插入持仓记录
        cursor.execute("""
            INSERT INTO futures_positions
            (account_id, symbol, position_side, quantity, entry_price, avg_entry_price,
             leverage, notional_value, margin, open_time, stop_loss_price, take_profit_price,
             entry_signal_type, entry_score, signal_components,
             batch_plan, batch_filled, entry_signal_time, planned_close_time,
             source, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, 'smart_trader_batch', 'open', NOW(), NOW())
        """, (
            signal.get('account_id', 2),
            symbol,
            direction,
            total_quantity,
            avg_price,  # entry_price 使用平均价
            avg_price,  # avg_entry_price
            signal.get('leverage', 5),
            total_quantity * avg_price,  # notional_value
            total_margin,
            stop_loss,
            take_profit,
            signal.get('trade_params', {}).get('signal_combination_key', 'batch_entry'),
            entry_score,
            json.dumps(signal.get('trade_params', {}).get('signal_components', {})),
            batch_plan_json,
            batch_filled_json,
            plan['signal_time'],
            planned_close_time
        ))

        position_id = cursor.lastrowid

        # 冻结保证金
        cursor.execute("""
            UPDATE futures_trading_accounts
            SET current_balance = current_balance - %s,
                frozen_balance = frozen_balance + %s,
                updated_at = NOW()
            WHERE id = %s
        """, (total_margin, total_margin, signal.get('account_id', 2)))

        conn.commit()

        logger.info(
            f"✅ 持仓记录已创建: ID={position_id} | "
            f"{symbol} {direction} | "
            f"数量: {total_quantity:.2f} | "
            f"平均价: ${avg_price:.4f}"
        )

        return position_id

    except Exception as e:
        conn.rollback()
        logger.error(f"创建持仓记录失败: {e}")
        raise
    finally:
        cursor.close()
        conn.close()
```

### 步骤3: 修改 `execute_entry()` 调用实际开仓

在 `execute_entry()` 末尾，检测到所有批次完成后：

```python
# 在 execute_entry() 的最后
finally:
    # 停止采样器
    sampling_task.cancel()

# 检查是否所有批次都完成
if all(b['filled'] for b in plan['batches']):
    # 创建持仓记录
    try:
        position_id = await self._create_position_record(plan, signal)

        return {
            'success': True,
            'position_id': position_id,
            'avg_price': self._calculate_avg_price(plan),
            'total_quantity': sum(b.get('quantity', 0) for b in plan['batches']),
            'plan': plan
        }
    except Exception as e:
        logger.error(f"创建持仓记录失败: {e}")
        return {
            'success': False,
            'error': f'创建持仓记录失败: {e}'
        }
else:
    logger.error(f"{symbol} 建仓未完成，部分批次失败")
    return {
        'success': False,
        'error': '建仓未完成'
    }
```

---

## 🧪 测试计划

### 测试1: 离线测试（不开仓）
1. 修改 `_execute_batch()` 只打印日志，不实际开仓
2. 启用分批建仓 `enabled: true`
3. 观察日志，确认价格采样、批次判断逻辑正确

### 测试2: 数据库测试
1. 实现 `_create_position_record()`
2. 完成一次完整建仓流程
3. 检查数据库 `futures_positions` 表是否正确插入记录
4. 检查 `batch_plan` 和 `batch_filled` JSON 字段

### 测试3: 智能平仓测试
1. 手动创建一个分批建仓持仓记录
2. 启用智能平仓 `enabled: true`
3. 观察智能平仓监控是否正常工作
4. 验证分层平仓逻辑

### 测试4: 小仓位实盘测试
1. 使用最小保证金（50 USDT）
2. 白名单测试单个币种
3. 观察完整流程

---

## 📊 验证清单

### 开仓验证
- [ ] 价格采样正常（5分钟窗口）
- [ ] 第1批在合适价格建仓
- [ ] 第2批在合适价格建仓
- [ ] 第3批在合适价格建仓
- [ ] 30分钟内完成所有建仓
- [ ] 平均价格优于直接开仓

### 数据库验证
- [ ] `batch_plan` JSON 正确
- [ ] `batch_filled` JSON 正确
- [ ] `avg_entry_price` 正确
- [ ] `planned_close_time` 正确
- [ ] `entry_signal_time` 正确
- [ ] 保证金正确冻结

### 平仓验证
- [ ] 智能平仓监控启动
- [ ] 高盈利回撤止盈触发
- [ ] 中盈利回撤止盈触发
- [ ] 低盈利快速止盈触发
- [ ] 盈亏平衡点捕捉
- [ ] 微亏损延长时间

---

## 🚀 启用步骤

完成上述实现和测试后：

1. 修改 `config.yaml`:
   ```yaml
   batch_entry:
     enabled: true
     whitelist_symbols: ['BTC/USDT']  # 先测试单个币种
   smart_exit:
     enabled: true
   ```

2. 重启服务

3. 观察日志

4. 验证数据库

5. 逐步扩大到多币种

---

## 📝 注意事项

1. **事务处理**：创建持仓和冻结保证金必须在同一事务中
2. **异常处理**：任何批次失败都要能够回滚
3. **日志记录**：每个关键步骤都要有详细日志
4. **性能考虑**：30分钟内可能同时有多个分批建仓任务
5. **幂等性**：避免重复创建持仓记录

---

## 🔗 相关文件

- `app/services/smart_entry_executor.py` - 需要修改
- `app/services/smart_exit_optimizer.py` - 已完成
- `app/services/price_sampler.py` - 已完成
- `smart_trader_service.py` - 集成代码
- `config.yaml` - 配置文件
- `app/database/smart_brain_schema.sql` - 数据库schema

---

最后更新: 2026-01-24
