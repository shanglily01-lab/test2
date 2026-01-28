# 修复止盈缩水问题

## 问题描述

止盈触发时使用的价格和实际平仓价格不一致,导致收益缩水。

### 案例

| 交易对 | 止盈价 | 平仓价 | 损失ROI |
|--------|--------|--------|---------|
| KAIA/USDT | $0.069762 | $0.069220 | -3.93% |
| VET/USDT | $0.010293 | $0.010267 | -1.28% |
| ZKC/USDT | $0.134422 | $0.134100 | -1.22% |
| ALGO/USDT | $0.121997 | $0.121900 | -0.40% |

## 根本原因

```
检查时间点: stop_loss_monitor 使用 kline_data 缓存价格
           ↓ 价格 >= 止盈价,触发平仓
           ↓
平仓时间点: futures_trading_engine 重新调用API获取实时价格
           ↓ 价格已回落 (延迟、波动)
           ↓
实际成交: 使用回落后的价格平仓
```

时间差: 检查 → 平仓 (几百毫秒到1秒)
价格差: 由于波动、API延迟,价格可能回落 0.5%-2%

## 解决方案

### 方案 1: 传递触发价格 (推荐)

**修改**: `app/trading/stop_loss_monitor.py`

```python
# 第638-646行,修改止盈触发逻辑

# 优先级3: 检查止盈（使用持仓中保存的止盈价格）
if self.should_trigger_take_profit(position, current_price):
    take_profit_price = Decimal(str(position.get('take_profit_price', 0)))
    logger.info(f"✅ Take-profit triggered for position #{position_id} {symbol} @ {current_price:.8f} (take_profit={take_profit_price:.8f})")

    # 修改: 传入触发时的价格,避免重新获取价格导致缩水
    result = self.engine.close_position(
        position_id=position_id,
        reason='take_profit',
        close_price=current_price  # ✅ 新增: 使用触发时的价格平仓
    )

    # 同步平掉实盘仓位
    self._sync_close_live_position(position, 'take_profit')
    return {
        'position_id': position_id,
        'symbol': symbol,
        'status': 'take_profit',
        'current_price': float(current_price),
        'take_profit_price': float(take_profit_price),
        'result': result
    }
```

**优点**:
- 简单修改,只改1行代码
- 使用触发检查时的价格,保证不会缩水
- 实盘也会使用这个价格作为参考

**缺点**:
- 如果触发检查时用的是缓存价格,可能不是真实市场价
- 实盘可能无法以这个价格成交

### 方案 2: 提前止盈 + 容差

**修改**: `app/trading/stop_loss_monitor.py`

```python
# 第421-426行,修改止盈检查逻辑

if position_side == 'LONG':
    # 多头：当前价格 >= 止盈价 * 0.998 (提前0.2%触发,留出缓冲)
    buffer_price = take_profit_price * Decimal('0.998')
    if current_price >= buffer_price:
        logger.info(f"✅ Take-profit triggered for LONG position #{position['id']} {position['symbol']}: "
                  f"current={current_price:.8f}, take_profit={take_profit_price:.8f}, buffer={buffer_price:.8f}")
        return True
else:  # SHORT
    # 空头：当前价格 <= 止盈价 * 1.002 (提前0.2%触发)
    buffer_price = take_profit_price * Decimal('1.002')
    if current_price <= buffer_price:
        logger.info(f"✅ Take-profit triggered for SHORT position #{position['id']} {position['symbol']}: "
                  f"current={current_price:.8f}, take_profit={take_profit_price:.8f}, buffer={buffer_price:.8f}")
        return True
```

**优点**:
- 提前触发,留出缓冲空间
- 即使价格回落,也能达到接近止盈价的价格

**缺点**:
- 可能少赚0.2%的利润
- 需要调整buffer参数

### 方案 3: 使用限价单 (最优但复杂)

**修改**: `app/trading/futures_trading_engine.py` 和 `app/trading/binance_futures_engine.py`

```python
def close_position(self, position_id, reason='manual', close_price=None, order_type='MARKET'):
    """
    平仓

    Args:
        position_id: 持仓ID
        reason: 平仓原因
        close_price: 平仓价格
        order_type: 订单类型 ('MARKET' 或 'LIMIT')
    """
    if order_type == 'LIMIT' and close_price:
        # 使用限价单,以指定价格平仓
        # 优点: 保证成交价 >= close_price (多头) 或 <= close_price (空头)
        # 缺点: 可能不成交,需要设置超时和回退到市价单
        pass
```

**优点**:
- 最优方案,保证成交价不低于止盈价
- 符合专业交易逻辑

**缺点**:
- 实现复杂,需要处理订单状态、超时、回退
- 可能无法成交,需要监控和处理

## 推荐实施

**采用方案1 + 方案2组合:**

1. **短期**: 使用方案1,传递触发价格,立即修复缩水问题
2. **中期**: 使用方案2,提前0.2%触发,提供缓冲
3. **长期**: 实现方案3,使用限价单,专业交易

### 实施步骤

1. 修改 `app/trading/stop_loss_monitor.py` 第644行
2. 添加 `close_price=current_price` 参数
3. 测试观察效果
4. 如果效果好,保持;如果实盘滑点大,考虑方案2

## 预期效果

修复前:
- KAIA: 理论+$26.15, 实际+$7.30, 损失$18.85 (-72%)
- VET: 理论+$20.61, 实际+$15.50, 损失$5.11 (-25%)

修复后:
- 使用触发价格平仓,收益接近理论值
- 预计止盈收益提升 20%-70%
