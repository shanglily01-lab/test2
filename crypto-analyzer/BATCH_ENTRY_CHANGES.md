# 分批建仓逻辑改进 - 多个独立持仓方案

## 📝 改动说明

**日期**: 2026-02-21
**改动**: 移除"同一交易对同方向只能1个持仓"的限制

## 🎯 改动原因

### 旧方案的问题
1. **盈亏计算复杂**: 分批平仓需要累加 `realized_pnl`，容易出错
2. **数据一致性**: 频繁UPDATE同一条记录，可能导致数据不一致
3. **无法追溯**: 无法查看每批次的独立盈亏
4. **逻辑复杂**: 需要维护分批平仓的状态和逻辑

### 新方案的优势
1. **逻辑清晰**: 每批建仓都是独立持仓记录
2. **盈亏独立**: 每个持仓独立计算，不会重复扣除
3. **易于追溯**: 可以清晰看到每批次的盈亏
4. **简化代码**: 不需要分批平仓逻辑

## 🔧 具体改动

### 1. smart_trader_service.py (U本位合约)
```python
# 旧代码（已注释）
# if self.has_position(symbol, new_side):
#     logger.info(f"[SKIP] {symbol} {new_side}方向已有持仓")
#     continue

# 新逻辑：允许多个同方向持仓
```

### 2. smart_entry_executor.py (分批建仓执行器)
```python
# 旧代码（已注释）：检查是否已有相同方向持仓
# existing = cursor.fetchone()
# if existing:
#     return existing_id  # 返回已存在的持仓ID

# 新逻辑：每次都创建新的持仓记录
```

### 3. coin_futures_trader_service.py (币本位合约)
```python
# 同样移除了持仓重复检查
```

## 📊 示例对比

### 旧方案：单个持仓 + 分批平仓
```
开仓:
  Position ID: 1001
  ├─ 第1批: 30个币 @ 100 USDT (保证金 120)
  ├─ 第2批: 30个币 @ 99 USDT  → UPDATE quantity=60, margin=238.8
  └─ 第3批: 40个币 @ 98 USDT  → UPDATE quantity=100, margin=395.6

平仓:
  ├─ 第1次: 50个币 @ 102  → realized_pnl += 150
  └─ 第2次: 50个币 @ 103  → realized_pnl += 200  (累加)

问题: realized_pnl需要累加，容易出错！
```

### 新方案：多个独立持仓
```
开仓:
  Position ID: 1001 → 30个币 @ 100 USDT (保证金 120)
  Position ID: 1002 → 30个币 @ 99 USDT  (保证金 118.8)
  Position ID: 1003 → 40个币 @ 98 USDT  (保证金 156.8)

平仓:
  Position 1001 → 30个币 @ 102 → realized_pnl = 60 ✓
  Position 1002 → 30个币 @ 103 → realized_pnl = 120 ✓
  Position 1003 → 40个币 @ 101 → realized_pnl = 120 ✓

优势: 每个持仓盈亏独立，逻辑清晰！
```

## ⚠️ 风控建议

### 1. 限制总持仓数量
建议添加检查：同一交易对同方向最多允许 N 个持仓（例如3个）

```python
# 建议添加的风控
cursor.execute("""
    SELECT COUNT(*) as count
    FROM futures_positions
    WHERE symbol = %s AND position_side = %s
    AND status IN ('open', 'building')
    AND account_id = %s
""", (symbol, direction, account_id))

if count >= 3:  # 最多3个同方向持仓
    logger.warning(f"⚠️ {symbol} {direction}方向已有{count}个持仓，达到上限")
    return
```

### 2. 限制总保证金占用
确保总保证金占用不超过账户余额的一定比例：

```python
# 获取当前总冻结保证金
cursor.execute("""
    SELECT frozen_balance, current_balance
    FROM futures_trading_accounts
    WHERE id = %s
""", (account_id,))

account = cursor.fetchone()
max_frozen = account['current_balance'] * 0.8  # 最多占用80%

if account['frozen_balance'] + new_margin > max_frozen:
    logger.warning(f"⚠️ 保证金占用超限")
    return
```

### 3. 分批建仓策略调整
现在每批建仓都是独立持仓，建议：
- 每批的止盈止损独立设置
- 每批的数量根据市场情况动态调整
- 每批的超时时间可以不同

## 🔄 数据迁移

无需数据迁移！此改动向后兼容：
- ✅ 已有的单个持仓记录继续正常工作
- ✅ 已有的分批平仓逻辑继续有效
- ✅ 新开仓会创建多个独立持仓

## 📈 预期效果

### 盈亏计算
- ✅ 不再有重复扣除的问题
- ✅ 每个持仓的盈亏清晰可见
- ✅ 总盈亏 = SUM(所有closed持仓的realized_pnl)

### 平仓逻辑
- ✅ 每个持仓独立判断平仓条件
- ✅ 不需要分批平仓逻辑
- ✅ 满足条件立即平仓

### 数据追溯
- ✅ 可以查看每批次的入场价格
- ✅ 可以查看每批次的盈亏
- ✅ 可以统计哪批次的胜率更高

## 🧪 测试建议

1. **模拟盘测试**: 先在模拟盘测试多个同方向持仓
2. **盈亏验证**: 检查每个持仓的盈亏是否独立计算
3. **风控验证**: 确保总保证金占用在可控范围
4. **极端情况**: 测试同时开3个以上持仓的情况

## 📞 联系

如有问题，请检查：
- 账户余额是否正确更新
- 持仓记录是否独立创建
- 盈亏计算是否正确

使用 `check_pnl_issue.py` 脚本验证盈亏计算的正确性。
