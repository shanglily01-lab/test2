# 模拟合约交易数据库表结构

> 数据库: binance-data
> 更新日期: 2025-12-24

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
    └── trading_strategies (策略配置)
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
| entry_signal_type | varchar(50) | 入场信号类型 |
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

## 6. trading_strategies (交易策略)

存储策略配置信息。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | bigint(20) | 主键，策略ID |
| user_id | int(11) | 用户ID |
| name | varchar(100) | 策略名称 |
| description | text | 策略描述 |
| account_id | int(11) | 关联账户ID |
| enabled | tinyint(1) | 是否启用 |
| market_type | varchar(10) | 市场类型：test/live |
| adaptive_regime | tinyint(1) | 自适应市场状态 |
| sync_live | tinyint(1) | 是否同步到实盘 |
| live_quantity_pct | decimal(5,2) | 实盘仓位比例 |
| config | longtext | 策略配置JSON |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

### config 字段结构示例

```json
{
  "trading_pairs": ["BTC/USDT", "ETH/USDT"],
  "timeframes": ["15m"],
  "leverage": 10,
  "quantity_pct": 5.0,
  "stop_loss_pct": 2.0,
  "take_profit_pct": 4.0,
  "max_positions": 3,
  "ema_diff_threshold": 0.05,
  "entry_conditions": {
    "ema_golden_cross": true,
    "ema_death_cross": true
  },
  "exit_conditions": {
    "hard_stop_loss": true,
    "trailing_take_profit": true,
    "ema_diff_take_profit": true
  }
}
```

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

| 代码 | 中文说明 |
|------|----------|
| hard_stop_loss | 硬止损 |
| trailing_stop_loss | 移动止损 |
| max_take_profit | 最大止盈 |
| trailing_take_profit | 移动止盈 |
| ema_diff_narrowing_tp | EMA差值收窄止盈 |
| death_cross_reversal | 死叉反转平仓 |
| golden_cross_reversal | 金叉反转平仓 |
| 5m_death_cross_sl | 5分钟死叉止损 |
| 5m_golden_cross_sl | 5分钟金叉止损 |
| manual | 手动平仓 |
| liquidation | 强制平仓 |

格式: `reason_code|param1:value|param2:value`
示例: `trailing_take_profit|max:3.5%|cb:1.2%`
