# 模拟合约交易数据库表结构

> 数据库: binance-data
> 更新日期: 2026-01-26
>
> **重要更新**:
> - 2026-01-26: 添加每日复盘系统相关表（复盘报告、机会详情、信号分析、参数调整）
> - 2026-01-26: 添加现货交易系统表（spot_positions）
> - 2026-01-22: 添加超级大脑相关表结构（信号评分、组件性能分析）
> - 2026-01-22: 更新 futures_positions 新增字段（entry_score, signal_components）
> - 2026-01-16: 新增盈利保护平仓原因代码（profit_protect_*）
> - 2026-01-16: 新增智能渐进止损平仓原因代码
> - 2026-01-15: 新增V3趋势质量平仓原因代码
> - 2026-01-15: 新增RSI相关字段说明
> - 2026-01-15: 更新平仓原因代码列表

--服务端的数据库
database: binance-data
host:13.212.252.171
port:3306
user:admin
password:Tonny@1000

## 表关系概览

```
paper_trading_accounts (账户)
    ├── futures_positions (持仓) ──┬── futures_orders (订单)
    │                              └── futures_trades (成交)
    ├── paper_trading_balance_history (余额历史)
    ├── trading_strategies (策略配置)
    ├── 超级大脑信号系统
    │   ├── ema_signals (EMA信号)
    │   ├── paper_trading_signal_executions (信号执行记录)
    │   ├── signal_blacklist (信号黑名单)
    │   ├── signal_component_performance (组件性能)
    │   ├── signal_position_multipliers (仓位倍数)
    │   └── signal_scoring_weights (评分权重)
    ├── 每日复盘系统 ⚡ 新增 2026-01-26
    │   ├── daily_review_reports (复盘报告主表)
    │   ├── daily_review_opportunities (机会详情表)
    │   ├── daily_review_signal_analysis (信号分析表)
    │   └── parameter_adjustments (参数调整历史)
    └── 现货交易系统 ⚡ 新增 2026-01-26
        └── spot_positions (现货持仓表)
```

---

## 1. paper_trading_accounts (模拟交易账户)

账户主表，管理模拟交易资金和统计数据。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int(11) | 主键，账户ID |
| user_id | int(11) | 用户ID，默认1 |
| account_name | varchar(100) | 账户名称 |
| account_type | varchar(20) | 账户类型：spot/futures |
| initial_balance | decimal(20,2) | 初始余额，默认10000 |
| current_balance | decimal(20,2) | 当前可用余额 |
| frozen_balance | decimal(20,2) | 冻结余额（持仓保证金） |
| total_equity | decimal(20,2) | 总权益 = 可用 + 冻结 + 未实现盈亏 |
| total_profit_loss | decimal(20,2) | 总盈亏 |
| total_profit_loss_pct | decimal(10,4) | 总盈亏百分比 |
| realized_pnl | decimal(20,2) | 已实现盈亏 |
| unrealized_pnl | decimal(20,2) | 未实现盈亏 |
| total_trades | int(11) | 总交易次数 |
| winning_trades | int(11) | 盈利次数 |
| losing_trades | int(11) | 亏损次数 |
| win_rate | decimal(5,2) | 胜率 |
| max_balance | decimal(20,2) | 历史最高余额 |
| max_drawdown | decimal(20,2) | 最大回撤金额 |
| max_drawdown_pct | decimal(10,4) | 最大回撤百分比 |
| strategy_name | varchar(100) | 策略名称 |
| auto_trading | tinyint(1) | 是否自动交易 |
| max_position_size | decimal(5,2) | 最大仓位比例 |
| stop_loss_pct | decimal(5,2) | 止损百分比 |
| take_profit_pct | decimal(5,2) | 止盈百分比 |
| max_daily_loss | decimal(20,2) | 每日最大亏损限制 |
| status | varchar(20) | 状态：active/inactive |
| is_default | tinyint(1) | 是否默认账户 |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

---

## 2. futures_positions (合约持仓)

记录每笔合约持仓的详细信息。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int(11) | 主键，持仓ID |
| account_id | int(11) | 关联账户ID |
| user_id | int(11) | 用户ID |
| symbol | varchar(20) | 交易对，如 BTC/USDT |
| position_side | varchar(10) | 持仓方向：LONG/SHORT |
| leverage | int(11) | 杠杆倍数 |
| quantity | decimal(18,8) | 持仓数量 |
| notional_value | decimal(20,2) | 名义价值（合约价值） |
| margin | decimal(20,2) | 保证金 |
| entry_price | decimal(18,8) | 入场价格 |
| mark_price | decimal(18,8) | 标记价格（最新价） |
| liquidation_price | decimal(18,8) | 强平价格 |
| unrealized_pnl | decimal(20,2) | 未实现盈亏 |
| unrealized_pnl_pct | decimal(10,4) | 未实现盈亏百分比 |
| realized_pnl | decimal(20,2) | 已实现盈亏（平仓后） |
| stop_loss_price | decimal(18,8) | 止损价格 |
| take_profit_price | decimal(18,8) | 止盈价格 |
| stop_loss_pct | decimal(5,2) | 止损百分比 |
| take_profit_pct | decimal(5,2) | 止盈百分比 |
| entry_ema_diff | decimal(18,8) | 入场时EMA差值 |
| total_funding_fee | decimal(20,8) | 累计资金费率 |
| open_time | datetime | 开仓时间 |
| last_update_time | datetime | 最后更新时间 |
| close_time | datetime | 平仓时间 |
| holding_hours | int(11) | 持仓小时数 |
| status | varchar(20) | 状态：open/closed |
| source | varchar(50) | 来源：manual/strategy/signal |
| signal_id | int(11) | 关联信号ID |
| strategy_id | bigint(20) | 关联策略ID |
| notes | text | 备注（平仓原因等） |
| max_profit_pct | decimal(10,4) | 最大浮盈百分比 |
| max_profit_price | decimal(18,8) | 最大浮盈时价格 |
| trailing_stop_activated | tinyint(1) | 移动止盈是否激活 |
| trailing_stop_price | decimal(18,8) | 移动止盈触发价 |
| entry_signal_type | varchar(50) | 入场信号类型，如 SMART_BRAIN_75 |
| entry_score | int(11) | 入场信号评分（0-100） ⚡ 新增 2026-01-22 |
| signal_components | text | 信号组成部分JSON ⚡ 新增 2026-01-22 |
| entry_reason | varchar(500) | 入场原因 |
| live_position_id | int(11) | 关联实盘持仓ID |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

---

## 3. futures_orders (合约订单)

记录所有合约订单（开仓/平仓）。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | bigint(20) | 主键 |
| account_id | int(11) | 关联账户ID |
| user_id | int(11) | 用户ID |
| strategy_id | bigint(11) | 关联策略ID |
| timeout_minutes | int(11) | 订单超时时间（分钟） |
| order_id | varchar(50) | 订单ID，如 FUT-XXXX |
| position_id | int(11) | 关联持仓ID |
| symbol | varchar(20) | 交易对 |
| side | varchar(20) | 方向：OPEN_LONG/OPEN_SHORT/CLOSE_LONG/CLOSE_SHORT |
| order_type | varchar(20) | 订单类型：MARKET/LIMIT |
| leverage | int(11) | 杠杆倍数 |
| price | decimal(18,8) | 订单价格 |
| quantity | decimal(18,8) | 订单数量 |
| executed_quantity | decimal(18,8) | 已成交数量 |
| margin | decimal(20,2) | 保证金 |
| total_value | decimal(20,2) | 订单总价值 |
| executed_value | decimal(20,2) | 已成交价值 |
| fee | decimal(20,8) | 手续费 |
| fee_rate | decimal(10,6) | 手续费率，默认0.0004 |
| status | varchar(20) | 状态：PENDING/FILLED/CANCELED |
| avg_fill_price | decimal(18,8) | 成交均价 |
| fill_time | datetime | 成交时间 |
| stop_price | decimal(18,8) | 触发价格（止损/止盈单） |
| stop_loss_price | decimal(18,8) | 止损价格 |
| take_profit_price | decimal(18,8) | 止盈价格 |
| order_source | varchar(500) | 订单来源：manual/strategy |
| signal_id | int(11) | 关联信号ID |
| realized_pnl | decimal(20,2) | 已实现盈亏（平仓订单） |
| pnl_pct | decimal(10,4) | 盈亏百分比 |
| notes | text | 备注（平仓原因） |
| cancellation_reason | varchar(100) | 取消原因 |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |
| canceled_at | datetime | 取消时间 |

---

## 4. futures_trades (合约成交)

记录每笔订单的成交明细。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | bigint(20) | 主键 |
| account_id | int(11) | 关联账户ID |
| order_id | varchar(50) | 关联订单ID |
| position_id | int(11) | 关联持仓ID |
| trade_id | varchar(50) | 成交ID，如 T-XXXX |
| symbol | varchar(20) | 交易对 |
| side | varchar(20) | 方向：OPEN_LONG/CLOSE_SHORT等 |
| price | decimal(18,8) | 成交价格 |
| quantity | decimal(18,8) | 成交数量 |
| notional_value | decimal(20,2) | 成交价值 |
| leverage | int(11) | 杠杆倍数 |
| margin | decimal(20,2) | 保证金 |
| fee | decimal(20,8) | 手续费 |
| fee_rate | decimal(10,6) | 手续费率 |
| realized_pnl | decimal(20,2) | 已实现盈亏（平仓） |
| pnl_pct | decimal(10,4) | 盈亏百分比 |
| roi | decimal(10,4) | 投资回报率 |
| entry_price | decimal(18,8) | 入场价格 |
| trade_time | datetime | 成交时间 |
| created_at | datetime | 创建时间 |

---

## 5. paper_trading_balance_history (余额历史)

记录账户余额变动历史，用于绘制权益曲线。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | bigint(20) | 主键 |
| account_id | int(11) | 关联账户ID |
| balance | decimal(20,2) | 当前余额 |
| frozen_balance | decimal(20,2) | 冻结余额 |
| total_equity | decimal(20,2) | 总权益 |
| realized_pnl | decimal(20,2) | 已实现盈亏 |
| unrealized_pnl | decimal(20,2) | 未实现盈亏 |
| total_pnl | decimal(20,2) | 总盈亏 |
| total_pnl_pct | decimal(10,4) | 总盈亏百分比 |
| change_type | varchar(50) | 变动类型：open/close/funding等 |
| change_amount | decimal(20,2) | 变动金额 |
| related_order_id | varchar(50) | 关联订单ID |
| notes | text | 备注 |
| snapshot_time | datetime | 快照时间 |
| created_at | datetime | 创建时间 |

---


---

## 常用查询示例

### 查询账户持仓
```sql
SELECT * FROM futures_positions
WHERE account_id = 2 AND status = 'open';
```

### 查询今日交易
```sql
SELECT * FROM futures_trades
WHERE account_id = 2 AND DATE(trade_time) = CURDATE();
```

### 查询策略盈亏统计
```sql
SELECT
    strategy_id,
    COUNT(*) as total_trades,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
    SUM(realized_pnl) as total_pnl
FROM futures_positions
WHERE status = 'closed' AND strategy_id IS NOT NULL
GROUP BY strategy_id;
```

### 查询挂单
```sql
SELECT * FROM futures_orders
WHERE account_id = 2 AND status = 'PENDING';
```

---

## 平仓原因代码 (notes字段)

### 标准平仓原因

| 代码 | 中文说明 | 触发条件 |
|------|----------|---------|
| hard_stop_loss | 硬止损 | 价格变化≥5.0%（保证金亏损50%） |
| trailing_stop_loss | 移动止损 | 触及动态调整的止损价 |
| max_take_profit | 最大止盈 | 盈利≥8.0%（已废弃） |
| trailing_take_profit | 移动止盈 | 盈利≥3.4%激活，回撤≥0.3%触发 |
| ema_diff_narrowing_tp | EMA差值收窄止盈 | EMA差值<0.5%且盈利≥1.5% |
| death_cross_reversal | 死叉反转平仓 | 持多仓时15M EMA死叉 |
| golden_cross_reversal | 金叉反转平仓 | 持空仓时15M EMA金叉 |
| 5m_death_cross_sl | 5分钟死叉止损 | 做多亏损+5M EMA死叉 |
| 5m_golden_cross_sl | 5分钟金叉止损 | 做空亏损+5M EMA金叉 |
| trend_weakening | 趋势减弱平仓 | EMA差值连续减弱 |
| manual | 手动平仓 | 用户手动操作 |
| liquidation | 强制平仓 | 触及强平价 |
| emergency_stop | 紧急停止 | 短时间多次硬止损触发 |

### 智能渐进止损平仓原因 ⚡ 新增 (2026-01-16)

| 代码 | 中文说明 | 触发条件 |
|------|----------|---------|
| progressive_sl_0.5pct | 渐进止损-层级1 | 亏损-0.5%到-1.0% + 5M+15M都反转 |
| progressive_sl_1pct | 渐进止损-层级2 | 亏损-1.0%到-2.0% + 15M+1H都反转 |
| progressive_sl_2pct | 渐进止损-层级3 | 亏损-2.0%到-3.0% + 15M反转或趋势减弱 |
| progressive_sl_3pct | 渐进止损-层级4 | 亏损>-3.0%，立即止损 |

### 盈利保护平仓原因 ⚡ 新增 2026-01-16

通用盈利保护机制，适用于所有策略。根据盈利幅度和趋势质量动态锁定利润。

| 代码 | 中文说明 | 触发条件 |
|------|----------|---------|
| profit_protect_reversal | 盈利保护-趋势反转 | 任何盈利 + 15M EMA反转（死叉/金叉） |
| profit_protect_weak | 盈利保护-趋势显著减弱 | 盈利<1.0% + 趋势强度<入场时30% |
| profit_protect_2pct | 盈利保护-大盈利锁定 | 盈利≥2.0% + 趋势强度<入场时70% |

### V3策略专属平仓原因 ⚡ 新增

| 代码 | 中文说明 | 触发条件 |
|------|----------|---------|
| v3_trend_collapse | V3趋势崩溃 | 趋势质量分数<30，立即平仓 |
| v3_trend_critical | V3趋势危险 | 趋势质量分数30-40且盈利<0.5% |
| v3_trend_weak | V3趋势减弱 | 趋势质量分数40-60且盈利<1.0% |

### 取消订单原因 (futures_orders.cancellation_reason)

| 代码 | 中文说明 | 触发场景 |
|------|----------|---------|
| validation_failed | 自检未通过 | pendingValidation检查失败 |
| trend_reversal | 趋势转向 | 检测到反向EMA交叉 |
| rsi_filter | RSI过滤 ⚡ 新增 | RSI超买(>80)或超卖(<20) |
| reversal_warning | 反转预警 | EMA9斜率突变 |
| timeout | 超时取消 | 超过2小时未成交 |
| position_exists | 持仓已存在 | 同方向已有持仓 |
| ema_diff_small | EMA差值过小 | 成交时EMA差值不足 |
| execution_failed | 执行失败 | 开仓时发生错误 |

### 数据格式

**英文格式** (推荐):
```
reason_code|param1:value|param2:value
```

**示例**:
```
trailing_take_profit|max:3.5%|cb:1.2%
hard_stop_loss|pnl:-5.02%
v3_trend_collapse|score:25|ema_diff:0.35%|ratio:0.16
progressive_sl_1pct|loss:1.25%|reason:multi_timeframe_reversed
progressive_sl_2pct|loss:2.45%|reason:15m_reversed
progressive_sl_3pct|loss:3.15%|reason:severe_loss
profit_protect_reversal|profit:1.85%|reason:15m_death_cross
profit_protect_weak|profit:0.65%|reason:trend_weak_30pct
profit_protect_2pct|profit:2.35%|reason:trend_weak_70pct
```

**中文格式** (兼容):
```
手动平仓
移动止损
硬止损平仓(亏损5.02% >= 5%)
```

**混合格式**:
```
close_reason: hard_stop_loss|pnl:-5.02%
```

---

## 7. ema_signals (EMA信号表) ⚡ 超级大脑

存储EMA交叉信号数据，用于超级大脑决策分析。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int(11) | 主键 |
| symbol | varchar(20) | 交易对 |
| timeframe | varchar(10) | 时间周期，如 5m, 15m, 1h |
| signal_type | varchar(10) | 信号类型：LONG/SHORT |
| signal_strength | varchar(20) | 信号强度：weak/medium/strong |
| timestamp | datetime | 信号时间 |
| price | decimal(20,8) | 信号价格 |
| short_ema | decimal(20,8) | 短期EMA值 |
| long_ema | decimal(20,8) | 长期EMA值 |
| ema_config | varchar(50) | EMA配置，如 9-21 |
| volume_ratio | decimal(10,2) | 成交量比率 |
| volume_type | varchar(10) | 成交量类型 |
| price_change_pct | decimal(10,4) | 价格变化百分比 |
| ema_distance_pct | decimal(10,4) | EMA距离百分比 |
| created_at | timestamp | 创建时间 |

---

## 8. paper_trading_signal_executions (信号执行记录) ⚡ 超级大脑

记录每个交易信号的执行情况和决策过程。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int(11) | 主键 |
| account_id | int(11) | 账户ID |
| signal_id | int(11) | 关联信号ID |
| symbol | varchar(20) | 交易对 |
| signal_type | varchar(20) | 信号类型 |
| signal_strength | varchar(20) | 信号强度 |
| confidence_score | decimal(5,2) | 置信度分数（0-100） |
| is_executed | tinyint(1) | 是否已执行 |
| execution_status | varchar(20) | 执行状态：success/failed/skipped |
| order_id | varchar(50) | 关联订单ID |
| decision | varchar(20) | 决策结果：open/skip |
| decision_reason | text | 决策原因（为何执行/跳过） |
| execution_price | decimal(18,8) | 执行价格 |
| execution_quantity | decimal(18,8) | 执行数量 |
| execution_amount | decimal(20,2) | 执行金额 |
| signal_time | datetime | 信号时间 |
| execution_time | datetime | 执行时间 |
| created_at | datetime | 创建时间 |

---

## 9. signal_blacklist (信号黑名单) ⚡ 超级大脑

存储表现不佳的信号组合，用于过滤低质量信号。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int(11) | 主键 |
| signal_type | varchar(50) | 信号类型 |
| position_side | varchar(10) | 持仓方向：LONG/SHORT |
| reason | varchar(255) | 加入黑名单原因 |
| total_loss | decimal(15,2) | 累计亏损 |
| win_rate | decimal(5,4) | 胜率 |
| order_count | int(11) | 订单数量 |
| created_at | timestamp | 创建时间 |
| updated_at | timestamp | 更新时间 |
| is_active | tinyint(1) | 是否激活 |
| notes | text | 备注 |

---

## 10. signal_component_performance (信号组件性能) ⚡ 超级大脑

分析各个信号组件的表现，用于动态调整权重。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int(11) | 主键 |
| component_name | varchar(50) | 组件名称，如 ema_golden_cross |
| position_side | varchar(10) | 方向：LONG/SHORT |
| total_orders | int(11) | 总订单数 |
| win_orders | int(11) | 盈利订单数 |
| total_pnl | decimal(15,2) | 累计盈亏 |
| avg_pnl | decimal(10,2) | 平均盈亏 |
| win_rate | decimal(5,4) | 胜率 |
| contribution_score | decimal(5,2) | 贡献分数 |
| last_analyzed | timestamp | 最后分析时间 |
| updated_at | timestamp | 更新时间 |

---

## 11. signal_position_multipliers (仓位倍数表) ⚡ 超级大脑

根据组件表现动态调整开仓倍数。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int(11) | 主键 |
| component_name | varchar(50) | 组件名称 |
| position_side | varchar(10) | 方向：LONG/SHORT |
| position_multiplier | decimal(5,2) | 仓位倍数，默认1.00 |
| total_trades | int(11) | 交易次数 |
| win_rate | decimal(5,4) | 胜率 |
| avg_pnl | decimal(10,2) | 平均盈亏 |
| total_pnl | decimal(15,2) | 累计盈亏 |
| last_analyzed | timestamp | 最后分析时间 |
| adjustment_count | int(11) | 调整次数 |
| is_active | tinyint(1) | 是否激活 |
| created_at | timestamp | 创建时间 |
| updated_at | timestamp | 更新时间 |

---

## 12. signal_scoring_weights (信号评分权重) ⚡ 超级大脑

存储各信号组件的评分权重，用于计算综合信号分数。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int(11) | 主键 |
| signal_component | varchar(50) | 信号组件，唯一键 |
| weight_long | decimal(5,2) | 做多权重 |
| weight_short | decimal(5,2) | 做空权重 |
| base_weight | decimal(5,2) | 基础权重 |
| performance_score | decimal(5,2) | 性能分数 |
| last_adjusted | timestamp | 最后调整时间 |
| adjustment_count | int(11) | 调整次数 |
| description | varchar(255) | 组件描述 |
| is_active | tinyint(1) | 是否激活 |
| updated_at | timestamp | 更新时间 |

---

## 超级大脑工作流程

1. **信号采集**: `ema_signals` 表记录各时间周期的EMA交叉信号
2. **信号评分**: 根据 `signal_scoring_weights` 计算综合分数
3. **性能分析**: `signal_component_performance` 分析各组件表现
4. **黑名单过滤**: `signal_blacklist` 过滤低质量信号
5. **仓位调整**: `signal_position_multipliers` 根据表现调整仓位
6. **执行记录**: `paper_trading_signal_executions` 记录执行过程
7. **持仓管理**: 将评分和组件信息存入 `futures_positions` 的 `entry_score` 和 `signal_components` 字段

---

## 13. daily_review_reports (每日复盘报告主表) ⚡ 新增 2026-01-26

存储每日复盘报告的汇总信息和完整JSON数据。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int(11) | 主键 |
| date | date | 复盘日期，唯一索引 |
| report_json | mediumtext | 完整报告JSON数据 |
| total_opportunities | int(11) | 总机会数 |
| captured_count | int(11) | 已捕获机会数 |
| missed_count | int(11) | 错过机会数 |
| capture_rate | float | 捕获率（百分比） |
| created_at | timestamp | 创建时间 |
| updated_at | timestamp | 更新时间 |

**索引**:
- UNIQUE KEY `unique_date` (date)
- INDEX `idx_date` (date)
- INDEX `idx_capture_rate` (capture_rate)

**用途**:
- 按日期查询复盘报告
- 追踪捕获率趋势
- 存储完整复盘分析结果

---

## 14. daily_review_opportunities (机会详情表) ⚡ 新增 2026-01-26

存储每个识别到的大行情机会的详细信息。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int(11) | 主键 |
| review_date | date | 复盘日期 |
| symbol | varchar(20) | 交易对，如 BTC/USDT |
| timeframe | varchar(10) | 时间周期：5m/15m/1h |
| move_type | varchar(10) | 机会类型：pump(上涨)/dump(下跌) |
| start_time | datetime | 机会开始时间 |
| end_time | datetime | 机会结束时间 |
| price_change_pct | float | 价格变化百分比 |
| volume_ratio | float | 成交量倍数 |
| captured | boolean | 是否被系统捕获 |
| capture_delay_minutes | int(11) | 捕获延迟（分钟），NULL表示未捕获 |
| signal_type | varchar(50) | 捕获信号类型（已捕获时） |
| position_pnl_pct | float | 实际持仓盈亏百分比 |
| miss_reason | text | 错过原因（未捕获时） |
| created_at | timestamp | 创建时间 |

**索引**:
- INDEX `idx_review_date` (review_date)
- INDEX `idx_symbol` (symbol)
- INDEX `idx_captured` (captured)
- INDEX `idx_timeframe` (timeframe)

**用途**:
- 分析不同交易对的捕获表现
- 统计各时间周期的机会分布
- 追踪错过原因分布
- 评估实际盈亏效果

---

## 15. daily_review_signal_analysis (信号分析表) ⚡ 新增 2026-01-26

存储每个信号类型的详细分析数据和评分。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int(11) | 主键 |
| review_date | date | 复盘日期 |
| signal_type | varchar(50) | 信号类型，如 BOTTOM_REVERSAL_LONG |
| total_trades | int(11) | 总交易笔数 |
| win_trades | int(11) | 盈利笔数 |
| loss_trades | int(11) | 亏损笔数 |
| win_rate | float | 胜率（百分比） |
| avg_pnl | float | 平均盈亏（百分比） |
| best_trade | float | 最佳交易盈亏（百分比） |
| worst_trade | float | 最差交易盈亏（百分比） |
| long_trades | int(11) | 做多笔数 |
| short_trades | int(11) | 做空笔数 |
| avg_holding_minutes | float | 平均持仓时长（分钟） |
| captured_opportunities | int(11) | 捕获的大行情机会数 |
| rating | varchar(20) | 评级：优秀/良好/一般/较差 |
| score | int(11) | 综合评分（0-100） |
| created_at | timestamp | 创建时间 |

**索引**:
- UNIQUE KEY `unique_review_signal` (review_date, signal_type)
- INDEX `idx_review_date` (review_date)
- INDEX `idx_score` (score)

**评分机制** (总分100):
- 胜率权重 50%: ≥60%得50分，≥50%得30分，≥40%得10分
- 平均盈亏权重 30%: ≥1.5%得30分，≥0.5%得20分，≥0%得10分
- 捕获机会权重 20%: ≥5个得20分，≥3个得10分，≥1个得5分

**评级标准**:
- 🌟优秀: ≥80分
- ✅良好: 60-79分
- ⚠️一般: 40-59分
- ❌较差: <40分

**用途**:
- 对比不同信号的表现
- 识别最佳和最差信号
- 追踪信号评分变化趋势
- 优化信号权重配置

---

## 16. parameter_adjustments (参数调整历史表) ⚡ 新增 2026-01-26

存储自动优化系统的参数调整记录。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int(11) | 主键 |
| adjustment_date | timestamp | 调整时间，默认当前时间 |
| param_group | varchar(100) | 参数组，如 signal_thresholds |
| param_name | varchar(100) | 参数名，如 BOTTOM_REVERSAL_LONG.min_score |
| old_value | varchar(100) | 旧值 |
| new_value | varchar(100) | 新值 |
| reason | text | 调整原因说明 |
| applied | boolean | 是否已应用，默认TRUE |

**索引**:
- INDEX `idx_adjustment_date` (adjustment_date)
- INDEX `idx_param_group` (param_group)

**用途**:
- 追踪参数优化历史
- 评估优化效果
- 回滚不当的参数调整
- 分析参数变化趋势

---

## 17. spot_positions (现货持仓表) ⚡ 新增 2026-01-26

存储现货交易系统的持仓信息。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int(11) | 主键 |
| symbol | varchar(20) | 交易对，如 BTC/USDT |
| entry_price | decimal(20,8) | 首次买入价格 |
| avg_entry_price | decimal(20,8) | 平均成本价 |
| quantity | decimal(20,8) | 持仓数量 |
| total_cost | decimal(20,4) | 总成本（USDT） |
| current_batch | int(11) | 当前批次（1-5），默认1 |
| take_profit_price | decimal(20,8) | 止盈价格 |
| stop_loss_price | decimal(20,8) | 止损价格 |
| exit_price | decimal(20,8) | 平仓价格（平仓后） |
| pnl | decimal(20,4) | 盈亏金额（USDT） |
| pnl_pct | decimal(10,6) | 盈亏百分比 |
| close_reason | varchar(50) | 平仓原因：止盈/止损/手动 |
| signal_strength | decimal(5,2) | 开仓信号强度（0-100） |
| signal_details | text | 信号详情 |
| status | varchar(20) | 状态：active/closed，默认active |
| created_at | timestamp | 创建时间 |
| updated_at | timestamp | 更新时间 |
| closed_at | timestamp | 平仓时间，NULL表示未平仓 |

**索引**:
- INDEX `idx_symbol` (symbol)
- INDEX `idx_status` (status)
- INDEX `idx_created` (created_at)
- INDEX `idx_pnl` (pnl_pct)

**批次建仓比例**:
- 批次1: 15%（底部反转信号可增至19.5%）
- 批次2: 15%
- 批次3: 25%
- 批次4: 25%
- 批次5: 20%

**止盈止损**:
- 止盈: 相对成本价 +30%
- 止损: 相对成本价 -15%

**用途**:
- 管理现货持仓
- 追踪分批建仓进度
- 统计现货交易盈亏
- 分析信号表现

---

## 每日复盘系统工作流程

1. **机会识别**: 扫描历史K线数据，识别大行情机会（pump/dump）
2. **捕获检测**: 对比实际交易记录，判断是否捕获机会
3. **信号分析**: 统计各信号类型的交易表现和评分
4. **报告生成**: 汇总分析结果，存入 `daily_review_reports`
5. **详情存储**: 机会详情存入 `daily_review_opportunities`
6. **信号评估**: 信号评分存入 `daily_review_signal_analysis`
7. **参数优化**: 根据复盘结果调整参数，记录到 `parameter_adjustments`

---

## 现货交易系统特点

1. **底部反转策略**: 专注捕捉触底反弹机会
2. **仅做多**: 现货只能做多，无爆仓风险
3. **分批建仓**: 5批渐进式买入，降低成本
4. **激进抄底**: 底部反转信号首批加仓30%
5. **宽松止损**: 15%止损空间，可承受更大波动

---

## 复盘系统查询示例

### 查询最近7天捕获率趋势
```sql
SELECT
    date,
    total_opportunities,
    captured_count,
    capture_rate
FROM daily_review_reports
WHERE date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
ORDER BY date DESC;
```

### 查询今日错过的机会
```sql
SELECT
    symbol,
    timeframe,
    move_type,
    price_change_pct,
    miss_reason,
    start_time
FROM daily_review_opportunities
WHERE review_date = CURDATE()
AND captured = FALSE
ORDER BY ABS(price_change_pct) DESC
LIMIT 10;
```

### 查询信号评分排名
```sql
SELECT
    signal_type,
    rating,
    score,
    win_rate,
    avg_pnl,
    total_trades
FROM daily_review_signal_analysis
WHERE review_date = CURDATE()
ORDER BY score DESC;
```

### 统计各交易对捕获表现
```sql
SELECT
    symbol,
    COUNT(*) as total,
    SUM(CASE WHEN captured = TRUE THEN 1 ELSE 0 END) as captured,
    ROUND(AVG(CASE WHEN captured = TRUE THEN 1 ELSE 0 END) * 100, 2) as rate
FROM daily_review_opportunities
WHERE review_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
GROUP BY symbol
ORDER BY rate DESC;
```

### 查询参数调整历史
```sql
SELECT
    adjustment_date,
    param_name,
    old_value,
    new_value,
    reason
FROM parameter_adjustments
WHERE adjustment_date >= DATE_SUB(NOW(), INTERVAL 7 DAY)
ORDER BY adjustment_date DESC;
```

### 查询现货活跃持仓
```sql
SELECT
    symbol,
    entry_price,
    avg_entry_price,
    quantity,
    current_batch,
    signal_strength,
    created_at
FROM spot_positions
WHERE status = 'active'
ORDER BY signal_strength DESC;
```
