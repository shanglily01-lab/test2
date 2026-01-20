# 盈亏计算问题分析报告

## 问题概述

用户发现止盈平仓后,实际 `realized_pnl` 比预期少了 2-3 USDT:

| 交易对 | 方向 | 价格变化 | 预期 PnL | 实际 PnL | 差异 |
|--------|------|----------|----------|----------|------|
| XMR/USDT | 做多 | +2.11% | 42.20 USDT | 39.18 USDT | **-2.20 USDT** |
| CHZ/USDT | 做空 | +2.09% | 41.72 USDT | 39.22 USDT | **-1.71 USDT** |

## 问题根源

### 1. 服务架构分析

系统中有**两个独立服务**操作同一数据库:

```
┌─────────────────────────────┐
│  smart_trader_service.py    │  ← 今天早上更新的服务
│  (主交易服务)                │
└─────────────────────────────┘

┌─────────────────────────────┐
│  stop_loss_monitor.py       │  ← 止损监控服务 (定时轮询)
│  └─> futures_trading_engine │
└─────────────────────────────┘
```

**XMR 和 CHZ 这两笔订单是被 `stop_loss_monitor.py` 平仓的,而不是 `smart_trader_service.py`。**

### 2. 代码问题定位

在 [`app/trading/stop_loss_monitor.py`](app/trading/stop_loss_monitor.py) 中:

```python
# Line 628-632 (修复前)
result = self.engine.close_position(
    position_id=position_id,
    reason='take_profit',
    close_price=take_profit_price  # ❌ 使用预设的止盈价格
)
```

当止盈触发时,代码传入了 `close_price=take_profit_price`,导致:
1. 使用**预设的止盈价格**计算 PnL
2. 而不是使用**实际的市场价格**

### 3. 实际数据验证

#### XMR/USDT LONG 订单

```
开仓价格:         606.30766667 USDT
预设止盈价:       618.43382000 USDT  ← 代码使用这个价格
实际市场价:       619.10000000 USDT  ← 应该使用这个价格
持仓数量:         3.29865530

使用止盈价计算:
  价格差: 618.43382 - 606.30766667 = 12.12615333
  PnL (无手续费): 12.12615333 × 3.29865530 = 40.00 USDT
  手续费 (0.04%): 0.82 USDT
  实际 PnL: 40.00 - 0.82 = 39.18 USDT ✅ 匹配数据库

使用市场价计算:
  价格差: 619.10 - 606.30766667 = 12.79233333
  PnL (无手续费): 12.79233333 × 3.29865530 = 42.20 USDT
  手续费 (0.04%): 0.82 USDT
  应得 PnL: 42.20 - 0.82 = 41.38 USDT

损失: 41.38 - 39.18 = 2.20 USDT (5.3%)
```

#### CHZ/USDT SHORT 订单

```
开仓价格:         0.05700914 USDT
预设止盈价:       0.05586896 USDT  ← 代码使用这个价格
实际市场价:       0.05582000 USDT  ← 应该使用这个价格
持仓数量:         35082.09385372

使用止盈价计算:
  价格差: 0.05700914 - 0.05586896 = 0.00114018
  PnL (无手续费): 0.00114018 × 35082.09385372 = 40.00 USDT
  手续费 (0.04%): 0.78 USDT
  实际 PnL: 40.00 - 0.78 = 39.22 USDT ✅ 匹配数据库

使用市场价计算:
  价格差: 0.05700914 - 0.05582000 = 0.00118914
  PnL (无手续费): 0.00118914 × 35082.09385372 = 41.72 USDT
  手续费 (0.04%): 0.78 USDT
  应得 PnL: 41.72 - 0.78 = 40.93 USDT

损失: 40.93 - 39.22 = 1.71 USDT (4.2%)
```

### 4. 为什么会出现这个问题?

止盈触发后,价格**继续向有利方向移动**:

```
XMR/USDT LONG:
止盈价 618.43 触发 ──> 价格继续上涨到 619.10
                    ↑
                    损失了 0.67 USDT/单位 的额外盈利

CHZ/USDT SHORT:
止盈价 0.05586896 触发 ──> 价格继续下跌到 0.05582
                         ↑
                         损失了 0.00004896 USDT/单位 的额外盈利
```

## 修复方案

### 代码修改

文件: [`app/trading/stop_loss_monitor.py`](app/trading/stop_loss_monitor.py)

```diff
# 止损平仓 (Line 608-612)
result = self.engine.close_position(
    position_id=position_id,
-   reason=stop_type,
-   close_price=actual_stop_price  # 使用实际触发的止损价格平仓
+   reason=stop_type
+   # 不传入 close_price,使用实时市场价格平仓,避免损失额外盈利
)

# 止盈平仓 (Line 628-632)
result = self.engine.close_position(
    position_id=position_id,
-   reason='take_profit',
-   close_price=take_profit_price  # 使用止盈价格平仓
+   reason='take_profit'
+   # 不传入 close_price,使用实时市场价格平仓,获取最大盈利
)
```

### 修复后的行为

1. **止盈触发时**:
   - 检测到价格 ≥ 止盈价格,触发平仓
   - 调用 `close_position()` **不传入** `close_price` 参数
   - `futures_trading_engine.py` 使用**实时市场价格**平仓
   - 获取最大盈利 ✅

2. **止损触发时**:
   - 检测到价格 ≤ 止损价格,触发平仓
   - 调用 `close_position()` **不传入** `close_price` 参数
   - 使用实时市场价格平仓
   - 如果价格继续下跌,避免更大亏损 ✅

## 后续建议

### 1. 服务器部署

由于修改的是 `stop_loss_monitor.py`,需要检查服务器上是否有运行该监控服务:

```bash
# 查找止损监控进程
ps aux | grep stop_loss_monitor

# 如果有运行,需要重启该服务
# (具体重启命令取决于服务器配置)
```

### 2. 数据库历史记录

之前使用预设价格平仓的记录**无法修复**,因为实际的市场价格已经无法追溯。

但可以识别受影响的订单:

```sql
-- 查找可能受影响的订单 (止盈/止损平仓,且 mark_price ≠ take_profit_price)
SELECT
    id,
    symbol,
    position_side,
    entry_price,
    take_profit_price,
    mark_price,
    realized_pnl,
    notes,
    close_time
FROM futures_positions
WHERE status = 'closed'
  AND notes IN ('止盈', '止损')
  AND ABS(mark_price - take_profit_price) > 0.0001
  AND close_time >= '2026-01-01'
ORDER BY close_time DESC;
```

### 3. 监控验证

修复部署后,观察新的平仓记录:
- `mark_price` 应该是实时市场价格
- `realized_pnl` 应该基于 `mark_price` 计算
- 而不是基于 `take_profit_price/stop_loss_price`

## 总结

- **问题**: 止盈/止损平仓时使用预设价格而非实时市场价格
- **影响**: 损失 2-5% 的额外盈利
- **根源**: `stop_loss_monitor.py` 传入 `close_price` 参数
- **修复**: 移除参数传递,使用实时价格平仓
- **状态**: 已提交代码 (commit `235f04e`), 等待服务器部署

---
Generated at: 2026-01-20
Commit: 235f04e
