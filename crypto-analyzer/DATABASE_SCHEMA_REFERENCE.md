# 数据库表结构参考手册

## 📋 超级大脑核心表

### 1. futures_positions (期货持仓表)

**主要字段**：
```
id                      INT           主键
account_id              INT           账户ID
user_id                 INT           用户ID
symbol                  VARCHAR(20)   交易对 (如: BTC/USDT)
position_side           VARCHAR(10)   持仓方向 (LONG/SHORT) ⚠️ 不是 side
leverage                INT           杠杆倍数
quantity                DECIMAL       持仓数量
notional_value          DECIMAL       名义价值
margin                  DECIMAL       保证金
entry_price             DECIMAL       开仓价格
mark_price              DECIMAL       标记价格
liquidation_price       DECIMAL       强平价格
unrealized_pnl          DECIMAL       未实现盈亏
unrealized_pnl_pct      DECIMAL       未实现盈亏百分比
realized_pnl            DECIMAL       已实现盈亏
stop_loss_price         DECIMAL       止损价格
take_profit_price       DECIMAL       止盈价格
stop_loss_pct           DECIMAL       止损百分比
take_profit_pct         DECIMAL       止盈百分比
total_funding_fee       DECIMAL       总资金费用
open_time               DATETIME      开仓时间
last_update_time        DATETIME      最后更新时间
close_time              DATETIME      平仓时间
holding_hours           INT           持仓小时数
status                  VARCHAR(20)   状态 (open/closed)
source                  VARCHAR(50)   来源 (smart_trader/manual)
signal_id               INT           信号ID
strategy_id             BIGINT        策略ID
notes                   TEXT          备注
entry_score             INT           入场得分 ⭐
signal_components       TEXT          信号组件(JSON) ⭐
entry_signal_type       VARCHAR(50)   入场信号类型
entry_reason            VARCHAR(500)  入场原因
max_profit_pct          DECIMAL       最大盈利百分比
max_profit_price        DECIMAL       最大盈利价格
trailing_stop_activated TINYINT       追踪止损是否激活
trailing_stop_price     DECIMAL       追踪止损价格
created_at              DATETIME      创建时间
updated_at              DATETIME      更新时间
```

**重要说明**：
- ⚠️ 持仓方向字段是 `position_side`，不是 `side`
- ⭐ `entry_score` 和 `signal_components` 是超级大脑新增字段
- `source = 'smart_trader'` 表示超级大脑的交易

---

### 2. signal_scoring_weights (信号评分权重表)

**字段**：
```
id                INT           主键
component_name    VARCHAR(50)   组件名称 (如: position_low, momentum_down_3pct)
position_side     VARCHAR(10)   持仓方向 (LONG/SHORT) ⚠️ 不是 side
weight            INT           权重值 (5-30)
description       TEXT          描述
last_updated      DATETIME      最后更新时间
created_at        DATETIME      创建时间
```

**12个信号组件**：
1. `position_low` - 低位建仓
2. `position_mid` - 中位建仓
3. `position_high` - 高位建仓
4. `momentum_down_3pct` - 动量下跌3%
5. `momentum_up_3pct` - 动量上涨3%
6. `trend_1h_bull` - 1小时牛市趋势
7. `trend_1h_bear` - 1小时熊市趋势
8. `trend_1d_bull` - 1天牛市趋势
9. `trend_1d_bear` - 1天熊市趋势
10. `volatility_high` - 高波动率
11. `consecutive_bull` - 连续看涨
12. `consecutive_bear` - 连续看跌

---

### 3. signal_component_performance (信号组件表现表)

**字段**：
```
id                INT           主键
component_name    VARCHAR(50)   组件名称
position_side     VARCHAR(10)   持仓方向 (LONG/SHORT)
total_orders      INT           总订单数
winning_orders    INT           盈利订单数
losing_orders     INT           亏损订单数
win_rate          DECIMAL       胜率
avg_pnl           DECIMAL       平均盈亏
total_pnl         DECIMAL       总盈亏
avg_holding_hours DECIMAL       平均持仓小时数
last_analyzed     DATETIME      最后分析时间
created_at        DATETIME      创建时间
updated_at        DATETIME      更新时间
```

---

### 4. adaptive_params (自适应参数表)

**字段**：
```
id              INT           主键
param_type      VARCHAR(50)   参数类型 (global/symbol)
param_name      VARCHAR(100)  参数名称
param_value     VARCHAR(500)  参数值
symbol          VARCHAR(20)   交易对 (NULL表示全局)
description     TEXT          描述
last_updated    DATETIME      最后更新时间
created_at      DATETIME      创建时间
```

**重要全局参数**：
- `long_take_profit_pct` - 做多止盈百分比
- `long_stop_loss_pct` - 做多止损百分比
- `short_take_profit_pct` - 做空止盈百分比
- `short_stop_loss_pct` - 做空止损百分比

---

### 5. optimization_history (优化历史表)

**字段**：
```
id                   BIGINT        主键
timestamp            DATETIME      时间戳
optimization_type    VARCHAR(50)   优化类型 (weight/tp_sl/position)
adjustments_made     TEXT          调整详情(JSON)
total_adjusted       INT           调整总数
notes                TEXT          备注
created_at           DATETIME      创建时间
```

**优化类型**：
- `weight` - 权重优化
- `tp_sl` - 止盈止损优化
- `position` - 仓位优化

---

### 6. signal_blacklist (信号黑名单表)

**字段**：
```
id              INT           主键
signal_type     VARCHAR(100)  信号类型
reason          TEXT          原因
added_at        DATETIME      添加时间
is_active       BOOLEAN       是否激活
```

---

## 🔄 可选表 (市场观察)

### 7. market_observations (市场观察表)

**字段**：
```
id                BIGINT        主键
timestamp         TIMESTAMP     时间戳
overall_trend     VARCHAR(20)   总体趋势 (bullish/bearish/neutral)
market_strength   DECIMAL       市场强度 (0-100)
bullish_count     INT           看涨数量
bearish_count     INT           看跌数量
neutral_count     INT           中性数量
btc_price         DECIMAL       BTC价格
btc_trend         VARCHAR(20)   BTC趋势
eth_price         DECIMAL       ETH价格
eth_trend         VARCHAR(20)   ETH趋势
warnings          TEXT          预警信息
created_at        TIMESTAMP     创建时间
```

---

### 8. market_regime_states (市场状态表)

**字段**：
```
id                          BIGINT        主键
timestamp                   TIMESTAMP     时间戳
regime                      VARCHAR(20)   市场状态 (bull_market/bear_market/neutral)
strength                    DECIMAL       强度 (0-100)
bias                        VARCHAR(20)   倾向 (long/short/balanced)
btc_6h_change              DECIMAL       BTC 6小时变化
eth_6h_change              DECIMAL       ETH 6小时变化
position_adjustment         DECIMAL       仓位调整倍数 (0.85-1.3)
score_threshold_adjustment  INT           分数阈值调整 (-5到+5)
observations_analyzed       INT           分析的观察数量
bullish_percentage          DECIMAL       看涨百分比
bearish_percentage          DECIMAL       看跌百分比
created_at                  TIMESTAMP     创建时间
```

---

## 📊 其他相关表

### 9. trading_blacklist (交易黑名单表)

**字段**：
```
id          INT           主键
symbol      VARCHAR(20)   交易对
reason      TEXT          原因
added_at    DATETIME      添加时间
is_active   BOOLEAN       是否激活
```

---

### 10. kline_data (K线数据表)

**字段**：
```
id              BIGINT        主键
symbol          VARCHAR(20)   交易对
interval        VARCHAR(10)   时间间隔 (1m/5m/15m/1h/4h/1d)
open_time       BIGINT        开盘时间(毫秒)
open            DECIMAL       开盘价
high            DECIMAL       最高价
low             DECIMAL       最低价
close           DECIMAL       收盘价
volume          DECIMAL       成交量
close_time      BIGINT        收盘时间(毫秒)
quote_volume    DECIMAL       成交额
trades          INT           交易笔数
```

---

## 🎯 常用查询示例

### 查询超级大脑最近交易

```sql
SELECT
    symbol, position_side, entry_score, signal_components,
    entry_price, mark_price, realized_pnl, status, open_time
FROM futures_positions
WHERE source = 'smart_trader'
ORDER BY open_time DESC
LIMIT 10;
```

### 查询信号权重配置

```sql
SELECT
    component_name, position_side, weight, last_updated
FROM signal_scoring_weights
ORDER BY weight DESC;
```

### 查询组件表现

```sql
SELECT
    component_name, position_side,
    total_orders, win_rate, avg_pnl, total_pnl
FROM signal_component_performance
WHERE total_orders > 0
ORDER BY total_pnl DESC;
```

### 查询优化历史

```sql
SELECT
    timestamp, optimization_type,
    total_adjusted, notes
FROM optimization_history
ORDER BY timestamp DESC
LIMIT 10;
```

### 查询今日交易统计

```sql
SELECT
    COUNT(*) as total_trades,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100 as win_rate,
    SUM(realized_pnl) as total_pnl
FROM futures_positions
WHERE source = 'smart_trader'
    AND status = 'closed'
    AND DATE(open_time) = CURDATE();
```

---

## ⚠️ 常见错误

### 1. 字段名错误

❌ 错误：`SELECT side FROM futures_positions`
✅ 正确：`SELECT position_side FROM futures_positions`

❌ 错误：`WHERE side = 'LONG'`
✅ 正确：`WHERE position_side = 'LONG'`

### 2. 表名错误

❌ 错误：`SELECT * FROM signal_weights`
✅ 正确：`SELECT * FROM signal_scoring_weights`

### 3. 数据类型错误

❌ 错误：`WHERE win_rate = 0.5` (win_rate是百分比，0-100)
✅ 正确：`WHERE win_rate >= 50`

---

## 📝 表关系说明

```
futures_positions (交易记录)
    └─ source = 'smart_trader'
        ├─ entry_score (来自信号评分)
        └─ signal_components (来自 signal_scoring_weights)

signal_scoring_weights (信号权重)
    └─ 被 smart_trader_service.py 加载
    └─ 被 safe_weight_optimizer.py 优化

signal_component_performance (组件表现)
    └─ 从 futures_positions 统计生成
    └─ 用于优化 signal_scoring_weights

adaptive_params (自适应参数)
    ├─ param_type = 'global' (全局止盈止损)
    └─ param_type = 'symbol' (每个交易对的配置)

optimization_history (优化历史)
    └─ 记录所有优化操作
```

---

*更新时间: 2026-01-21*
*版本: v1.0*
