# 订单取消原因说明

## 概述
系统中的订单取消原因（`cancellation_reason`）字段用于记录订单被取消的具体原因，帮助用户了解为什么某个订单没有执行。

## 取消原因类型

### 1. `manual` - 手动取消
**触发场景**: 用户在前端界面点击"撤销"按钮手动取消订单

**相关代码**:
- `app/api/futures_api.py` line 606 - API 默认 `reason='manual'`

**显示文案**: "手动取消"

---

### 2. `timeout` - 超时取消
**触发场景**: 限价单等待时间超过设定的超时时长（如30分钟），且当前价格偏离限价超过1%，为避免追高/杀低而取消

**详细逻辑**:
- 模拟盘: 在 `futures_limit_order_executor.py` 中检查
- 实盘: 在 `live_order_monitor.py` 中检查
- 超时阈值: 策略配置中的 `limitOrderTimeoutMinutes`（默认30分钟）
- 价格偏离阈值: 1%（硬编码）

**示例日志**:
```
⏰ 限价单超时取消: DOGE/USDT SHORT 价格偏离超过1% (1.23%), 限价=0.147, 当前=0.145
```

**相关代码**:
- `app/services/futures_limit_order_executor.py` line 420-428
- `app/services/live_order_monitor.py` line 481-487

**显示文案**: "超时取消 - 价格偏离过大"

---

### 3. `trend_reversal` - 趋势反转取消
**触发场景**: 限价单挂单期间，市场趋势发生反转（如EMA交叉反向、信号消失等），策略决定取消订单

**详细逻辑**:
- 检查策略配置中的 `cancelLimitOrderOnTrendReversal` 是否启用
- 调用 `_should_cancel_order_by_trend()` 检查趋势反转条件:
  - EMA快线与慢线交叉方向改变
  - 买入/卖出信号消失
  - 成交量不足

**示例日志**:
```
🔄 趋势反转取消限价单: DOGE/USDT SHORT - EMA交叉反向
```

**相关代码**:
- `app/services/futures_limit_order_executor.py` line 363-376
- `app/services/live_order_monitor.py` line 215-219

**显示文案**: "趋势反转取消 - {具体原因}"

---

### 4. `strategy_signal` - 策略信号取消
**触发场景**: 策略运行过程中，根据新的信号判断当前挂单不再符合策略逻辑，主动取消

**使用方式**:
- 通过 API 调用 `DELETE /orders/{order_id}?reason=strategy_signal`

**相关代码**:
- `app/api/futures_api.py` line 606-652

**显示文案**: "策略信号取消"

---

### 5. `risk_control` - 风控取消
**触发场景**: 系统风控检测到异常情况（如账户风险过高、持仓过多等），自动取消订单

**使用方式**:
- 通过 API 调用 `DELETE /orders/{order_id}?reason=risk_control`

**显示文案**: "风控取消"

---

### 6. `system` - 系统取消
**触发场景**: 系统维护、异常处理、或其他系统级操作导致的订单取消

**使用方式**:
- 通过 API 调用 `DELETE /orders/{order_id}?reason=system`

**显示文案**: "系统取消"

---

### 7. `expired` - 订单过期
**触发场景**: 订单有效期到期后自动取消（如果有设置订单有效期）

**使用方式**:
- 通过 API 调用 `DELETE /orders/{order_id}?reason=expired`

**显示文案**: "订单过期"

---

## 数据库字段

### `futures_orders` 表
```sql
cancellation_reason VARCHAR(50)  -- 取消原因
canceled_at TIMESTAMP            -- 取消时间
```

### `live_futures_positions` 表
- 实盘仓位也会同步记录取消原因
- `status` 字段会设置为对应的状态（如 `TIMEOUT_PRICE_DEVIATION`）

---

## 前端显示映射

建议在前端使用以下中文映射：

```javascript
const CANCELLATION_REASON_MAP = {
  'manual': '手动取消',
  'timeout': '超时取消',
  'trend_reversal': '趋势反转',
  'strategy_signal': '策略信号',
  'risk_control': '风控取消',
  'system': '系统取消',
  'expired': '订单过期',
  null: '未知',
  '': '未知'
};
```

---

## 常见问题

### Q: 为什么有的订单显示"未知"？
A: 这是旧数据的历史问题。2025-12-10 之前的代码在取消订单时没有设置 `cancellation_reason` 字段。现在已修复，新的取消订单都会正确记录原因。

### Q: `timeout` 和 `expired` 有什么区别？
A:
- `timeout`: 限价单等待超时且价格偏离过大，系统主动取消
- `expired`: 订单本身设置了有效期，到期后自动取消（目前系统暂未实现此功能）

### Q: 趋势反转取消的具体判断条件是什么？
A: 参见 `futures_limit_order_executor.py` 中的 `_should_cancel_order_by_trend()` 方法，主要包括:
1. EMA交叉反向（快线从上穿变为下穿，或反之）
2. 信号强度不足（volume_signal_strength < 0）
3. 成交量低于阈值

---

## 技术细节

### 模拟盘取消流程
1. `futures_limit_order_executor.py` 每轮扫描所有 PENDING 订单
2. 检查趋势反转 → 取消并设置 `cancellation_reason='trend_reversal'`
3. 检查超时 → 取消并设置 `cancellation_reason='timeout'`
4. 更新 `futures_orders` 表的 `status='CANCELLED'` 和 `canceled_at`

### 实盘取消流程
1. `live_order_monitor.py` 扫描 `live_futures_positions` 表的 PENDING 订单
2. 检查趋势反转 → 调用币安 API 取消订单 → 更新数据库
3. 检查超时 → 调用币安 API 取消订单 → 发送 Telegram 通知
4. 更新 `live_futures_positions` 表的 `status` 和 `futures_orders` 表的 `cancellation_reason`

### API 取消流程
1. 前端调用 `DELETE /orders/{order_id}?reason=manual`
2. 后端更新模拟盘订单状态
3. 如果有对应的实盘订单，同步取消
4. 释放冻结的保证金
5. 返回成功响应

---

## 修改历史

- **2025-12-10**: 修复模拟盘限价单取消时未设置 `cancellation_reason` 的问题
- **2025-12-10**: 实盘监控添加 `cancellation_reason` 参数支持
- **2025-12-10**: 创建本文档
