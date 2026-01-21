# 数据库字段快速参考卡片

⚡ 快速查找常用表的字段名,避免字段错误

---

## 🔥 最常用的字段

```python
# futures_positions (持仓表)
position_side          # ❌ 不是 side
signal_components      # JSON文本
entry_score           # 入场得分

# signal_scoring_weights (权重表)
signal_component      # ❌ 不是 component_name
weight_long          # ❌ 不是单一的 weight
weight_short         # ❌ 不是单一的 weight
last_adjusted        # ❌ 不是 last_updated

# adaptive_params (参数表)
param_key            # ❌ 不是 param_name
updated_at           # ❌ 不是 last_updated

# optimization_history (优化历史)
optimized_at         # ❌ 不是 timestamp
target_name          # ❌ 不是 adjustments_made
param_name           # ❌ 不是 total_adjusted
reason               # ❌ 不是 notes
```

---

## 📋 核心表字段速查

### futures_positions
```sql
SELECT
    symbol,
    position_side,      -- LONG/SHORT
    entry_price,
    entry_score,        -- 入场评分
    signal_components,  -- JSON: {"component": weight}
    realized_pnl,
    unrealized_pnl,
    status,             -- open/closed
    source,             -- smart_trader/manual
    open_time,
    close_time
FROM futures_positions
WHERE source = 'smart_trader'
```

### signal_scoring_weights
```sql
SELECT
    signal_component,   -- 信号组件名
    weight_long,        -- 做多权重
    weight_short,       -- 做空权重
    base_weight,        -- 基础权重
    performance_score,  -- 性能得分
    last_adjusted,      -- 最后调整时间
    is_active
FROM signal_scoring_weights
WHERE is_active = 1
```

### signal_component_performance
```sql
SELECT
    component_name,     -- 注意这里是 component_name 不是 signal_component
    position_side,
    total_orders,
    win_orders,
    win_rate,
    total_pnl,
    avg_pnl,
    last_analyzed
FROM signal_component_performance
```

### adaptive_params
```sql
SELECT
    param_key,          -- 参数键 (不是 param_name)
    param_value,
    param_type,         -- global/symbol/signal
    description,
    updated_at,         -- 更新时间 (不是 last_updated)
    updated_by
FROM adaptive_params
WHERE param_type = 'global'
```

### optimization_history
```sql
SELECT
    optimized_at,       -- 优化时间 (不是 timestamp)
    optimization_type,  -- symbol_risk/weight/signal
    target_name,        -- 目标名称 (不是 adjustments_made)
    param_name,         -- 参数名 (不是 total_adjusted)
    old_value,
    new_value,
    change_amount,
    win_rate,
    reason              -- 原因 (不是 notes)
FROM optimization_history
ORDER BY optimized_at DESC
```

### symbol_risk_params
```sql
SELECT
    symbol,
    long_take_profit_pct,
    long_stop_loss_pct,
    short_take_profit_pct,
    short_stop_loss_pct,
    position_multiplier,
    win_rate,
    total_trades,
    total_pnl,
    last_optimized,     -- 最后优化时间 (不是 last_updated)
    is_active
FROM symbol_risk_params
WHERE is_active = 1
ORDER BY total_pnl DESC
```

### signal_position_multipliers
```sql
SELECT
    component_name,     -- 信号组件名
    position_side,      -- LONG/SHORT
    position_multiplier,
    win_rate,
    total_trades,
    total_pnl,
    last_analyzed,
    is_active
FROM signal_position_multipliers
WHERE is_active = 1
```

---

## ⚠️ 常见错误对照表

| ❌ 错误写法 | ✅ 正确写法 | 表名 |
|-----------|-----------|------|
| `side` | `position_side` | futures_positions |
| `component_name` | `signal_component` | signal_scoring_weights |
| `weight` | `weight_long`, `weight_short` | signal_scoring_weights |
| `last_updated` | `last_adjusted` | signal_scoring_weights |
| `param_name` | `param_key` | adaptive_params |
| `last_updated` | `updated_at` | adaptive_params |
| `timestamp` | `optimized_at` | optimization_history |
| `notes` | `reason` | optimization_history |
| `last_updated` | `last_optimized` | symbol_risk_params |

---

## 🎯 重要提醒

### 不同表中相同概念的不同字段名:

**"组件名称"**:
- signal_scoring_weights → `signal_component`
- signal_component_performance → `component_name`
- signal_position_multipliers → `component_name`

**"时间戳"**:
- 通用更新 → `updated_at`
- 权重调整 → `last_adjusted`
- 参数优化 → `last_optimized`
- 优化执行 → `optimized_at`
- 性能分析 → `last_analyzed`

---

## 📚 详细文档

需要更多信息请查看:
- **DATABASE_SCHEMA_REFERENCE.md** - 完整的表结构参考
- **FIELD_VERIFICATION_REPORT.md** - 详细的字段验证报告
- **DEPLOYMENT_FIXES_SUMMARY.md** - 部署修复总结

---

**最后更新**: 2026-01-21
