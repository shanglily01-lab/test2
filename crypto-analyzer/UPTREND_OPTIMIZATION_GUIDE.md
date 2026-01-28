# 上涨趋势优化指南

## 当前状况

### 今日交易数据 (2026-01-28)

| 方向 | 交易数 | 胜率 | 总盈亏 | 平均盈亏/笔 |
|------|--------|------|--------|------------|
| LONG (多单) | 56笔 | 42.86% | **-$35.15** | -$0.63 |
| SHORT (空单) | 69笔 | 50.72% | **+$321.86** | +$4.66 |

**问题**: 系统做空更赚钱,但市场处于上涨趋势,应该优化多单表现。

---

## 🚀 优化方案

### 方案1: 放宽分阶段超时 (已应用 ✅)

**修改**: `app/services/smart_exit_optimizer.py`

**before**:
```python
staged_thresholds = {
    1: -0.02,   # 1小时: -2%
    2: -0.015,  # 2小时: -1.5%
    3: -0.01,   # 3小时: -1%
    4: -0.005   # 4小时: -0.5%
}
```

**after**:
```python
staged_thresholds = {
    1: -0.025,  # 1小时: -2.5% (放宽0.5%)
    2: -0.02,   # 2小时: -2.0% (放宽0.5%)
    3: -0.015,  # 3小时: -1.5% (放宽0.5%)
    4: -0.01    # 4小时: -1.0% (放宽0.5%)
}
```

**效果**:
- 给持仓更多时间反弹
- 减少过早止损
- 适合稳步上涨行情

---

### 方案2: 提升多单信号权重 (可选)

**文件**: `optimize_for_uptrend.sql`

**操作步骤**:
```bash
# 方式1: 直接执行SQL文件
mysql -h 13.212.252.171 -u admin -p binance-data < optimize_for_uptrend.sql

# 方式2: 手动执行
# 连接数据库后执行以下命令
```

**SQL内容**:
```sql
-- 1. 提升看多信号权重 (+30%)
UPDATE signal_scoring_weights
SET weight_long = weight_long * 1.3
WHERE signal_component IN (
    'position_low',          -- 低位做多
    'momentum_down_3pct',    -- 跌后反弹
    'trend_1h_bull',         -- 1H多头趋势
    'consecutive_bull',      -- 连续阳线
    'volume_power_bull',     -- 量能多头
    'volume_power_1h_bull',  -- 1H量能多头
    'breakout_long',         -- 突破做多
    'trend_1d_bull'          -- 1D多头趋势
)
AND is_active = TRUE;

-- 2. 降低看空信号权重 (-20%)
UPDATE signal_scoring_weights
SET weight_short = weight_short * 0.8
WHERE signal_component IN (
    'position_high',         -- 高位做空
    'momentum_up_3pct',      -- 涨后回调
    'trend_1h_bear',         -- 1H空头趋势
    'consecutive_bear',      -- 连续阴线
    'volume_power_bear',     -- 量能空头
    'volume_power_1h_bear',  -- 1H量能空头
    'breakdown_short',       -- 破位做空
    'trend_1d_bear'          -- 1D空头趋势
)
AND is_active = TRUE;
```

**效果**:
- 多单信号更容易触发
- 空单信号更严格
- 整体偏向做多

**示例**:
```
优化前:
- volume_power_bull: LONG权重 25分
- trend_1h_bull: LONG权重 20分
- 总分: 45分 (可能不够阈值)

优化后:
- volume_power_bull: LONG权重 32.5分 (+30%)
- trend_1h_bull: LONG权重 26分 (+30%)
- 总分: 58.5分 (更容易开仓)
```

---

### 方案3: 调整多单风险参数 (可选,更激进)

**文件**: `adjust_long_risk_params.sql`

**操作步骤**:
```bash
mysql -h 13.212.252.171 -u admin -p binance-data < adjust_long_risk_params.sql
```

**SQL内容**:
```sql
UPDATE adaptive_params
SET param_value = CASE param_key
    WHEN 'long_stop_loss_pct' THEN 0.025        -- 止损从3%降到2.5%
    WHEN 'long_take_profit_pct' THEN 0.08       -- 止盈从6%提高到8%
    WHEN 'long_position_size_multiplier' THEN 1.2  -- 仓位增加20%
    WHEN 'long_min_holding_minutes' THEN 90     -- 最小持仓时间延长到90分钟
    ELSE param_value
END
WHERE param_type = 'long';
```

**效果**:
- 止损更紧: 3% → 2.5% (快速止损小亏)
- 止盈更远: 6% → 8% (获取更大利润)
- 仓位更大: 100% → 120% (每笔多单赚更多)
- 持仓更久: 60min → 90min (给趋势更多时间)

---

## 📊 推荐执行顺序

### 保守方案 (已执行 ✅)
```
✅ 方案1: 放宽分阶段超时 (已修改代码)
```

**适用**:
- 想要改进但不想太激进
- 观察效果后再决定下一步

---

### 中等方案 (推荐)
```
✅ 方案1: 放宽分阶段超时 (已修改代码)
+ 方案2: 提升多单信号权重 (执行SQL)
```

**适用**:
- 希望明显增加多单数量
- 稳步上涨趋势已确认

**执行**:
```bash
mysql -h 13.212.252.171 -u admin -p binance-data < optimize_for_uptrend.sql
```

---

### 激进方案
```
✅ 方案1: 放宽分阶段超时 (已修改代码)
+ 方案2: 提升多单信号权重 (执行SQL)
+ 方案3: 调整多单风险参数 (执行SQL)
```

**适用**:
- 强烈看涨,想最大化收益
- 愿意承担更多风险

**执行**:
```bash
# 执行方案2
mysql -h 13.212.252.171 -u admin -p binance-data < optimize_for_uptrend.sql

# 执行方案3
mysql -h 13.212.252.171 -u admin -p binance-data < adjust_long_risk_params.sql
```

---

## ⚠️ 注意事项

### 1. 重启服务
修改代码后需要重启交易服务才能生效:
```bash
# 停止服务
pkill -f smart_trader_service.py

# 启动服务
python smart_trader_service.py &
```

### 2. 监控效果
执行优化后,关注以下指标:

**每日统计**:
```sql
SELECT
    position_side,
    COUNT(*) as count,
    ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 2) as win_rate,
    ROUND(SUM(realized_pnl), 2) as total_pnl
FROM futures_positions
WHERE status = 'closed'
  AND DATE(close_time) = CURDATE()
GROUP BY position_side;
```

**目标**:
- LONG胜率: 42% → 50%+
- LONG总盈亏: -$35 → +$100+
- LONG平均盈亏: -$0.63 → +$2+

### 3. 回滚方案

如果效果不好,可以回滚:

**回滚方案1 (代码修改)**:
```python
# 恢复原始阈值
staged_thresholds = {
    1: -0.02,
    2: -0.015,
    3: -0.01,
    4: -0.005
}
```

**回滚方案2 (SQL)**:
```sql
-- 恢复权重
UPDATE signal_scoring_weights
SET weight_long = weight_long / 1.3
WHERE signal_component IN ('position_low', 'trend_1h_bull', ...)
AND is_active = TRUE;

UPDATE signal_scoring_weights
SET weight_short = weight_short / 0.8
WHERE signal_component IN ('position_high', 'trend_1h_bear', ...)
AND is_active = TRUE;
```

**回滚方案3 (SQL)**:
```sql
-- 恢复风险参数
UPDATE adaptive_params
SET param_value = CASE param_key
    WHEN 'long_stop_loss_pct' THEN 0.03
    WHEN 'long_take_profit_pct' THEN 0.06
    WHEN 'long_position_size_multiplier' THEN 1.0
    WHEN 'long_min_holding_minutes' THEN 60
    ELSE param_value
END
WHERE param_type = 'long';
```

---

## 📈 预期效果

### 保守方案 (方案1)
```
预期改善: +10-15%
LONG胜率: 42.86% → 47%
LONG日盈亏: -$35 → -$10 或 小幅盈利
```

### 中等方案 (方案1+2)
```
预期改善: +30-40%
LONG胜率: 42.86% → 55%
LONG日盈亏: -$35 → +$80-120
多单数量: 56笔 → 70-80笔
```

### 激进方案 (方案1+2+3)
```
预期改善: +50-70%
LONG胜率: 42.86% → 60%
LONG日盈亏: -$35 → +$150-200
多单数量: 56笔 → 80-90笔
单笔盈利: -$0.63 → +$2-3
```

---

## 🎯 建议

**当前建议**: 先执行**中等方案**

1. ✅ 方案1已应用 (放宽超时)
2. 执行方案2 (提升权重)
3. 观察1-2天效果
4. 如果效果好,考虑方案3 (调整风险参数)

**执行命令**:
```bash
# 1. 执行SQL优化
mysql -h 13.212.252.171 -u admin -p binance-data < optimize_for_uptrend.sql

# 2. 重启服务
pkill -f smart_trader_service.py
python smart_trader_service.py &

# 3. 查看日志
tail -f logs/smart_trader_*.log | grep -E "\[OPEN\]|\[CLOSE\]|LONG"
```

---

## 📝 总结

- **方案1** (已完成): 放宽超时,给持仓更多时间
- **方案2** (推荐执行): 提升多单权重,增加多单数量
- **方案3** (可选): 调整风险参数,最大化收益

针对上涨趋势,建议先执行方案1+2,观察效果后再决定是否执行方案3。

**当前状态**: 方案1已生效,等待重启服务后观察效果。
