# 合约与现货账户分离方案

**日期**: 2026-01-23
**目的**: 解决资金冻结余额错误问题，分离现货和合约账户管理

---

## 背景问题

### 原有架构问题

1. **账户混用**: `paper_trading_accounts` 同时用于现货和合约交易
2. **资金管理BUG**:
   - 开仓时: ❌ 未冻结资金 (没有 `UPDATE frozen_balance`)
   - 平仓时: ✅ 解冻资金 (`frozen_balance -= margin`)
   - 结果: `frozen_balance` 累计变成负数

### 数据异常

```
账户 ID=2 (合约账户):
- 可用余额: $146,339.44
- 冻结余额: -$86,200.00  ❌ (错误)
- 当前持仓: 56个，总保证金 $13,400

差异: -$86,200 - $13,400 = -$99,600
```

**原因**: 历史上平仓了约99,600/200 = 498笔订单，每次平仓都解冻了保证金，但开仓时从未冻结过。

---

## 解决方案

### 1. 创建专用合约账户表

创建新表 `futures_trading_accounts`，专门用于合约交易：

```sql
CREATE TABLE futures_trading_accounts (
    id INT PRIMARY KEY,
    account_name VARCHAR(100),
    current_balance DECIMAL(20,2),   -- 可用余额
    frozen_balance DECIMAL(20,2),    -- 冻结余额（持仓保证金）
    total_equity DECIMAL(20,2),      -- 总权益
    -- ... 其他字段
);
```

### 2. 数据迁移

从 `paper_trading_accounts` 迁移 account_id=2 到新表：

```bash
python fix_futures_account_frozen_balance.py
```

**迁移结果**:
- ✅ 创建 `futures_trading_accounts` 表
- ✅ 迁移 account_id=2 数据
- ✅ 修复 `frozen_balance` = $13,400 (根据当前持仓重新计算)

### 3. 代码修复

#### 开仓逻辑 (添加冻结资金)

**位置**: `smart_trader_service.py:604-620`

```python
# 插入持仓记录
cursor.execute("""
    INSERT INTO futures_positions (...)
    VALUES (...)
""")

# 🆕 冻结资金 (开仓时扣除可用余额，增加冻结余额)
cursor.execute("""
    UPDATE futures_trading_accounts
    SET current_balance = current_balance - %s,
        frozen_balance = frozen_balance + %s,
        updated_at = NOW()
    WHERE id = %s
""", (margin, margin, self.account_id))
```

#### 平仓逻辑 (保持解冻资金)

**位置**: `smart_trader_service.py:887-898` (5处相同逻辑)

```python
# 解冻资金，返还本金和盈亏
cursor.execute("""
    UPDATE futures_trading_accounts
    SET current_balance = current_balance + %s + %s,  -- 本金 + 盈亏
        frozen_balance = frozen_balance - %s,         -- 解冻保证金
        realized_pnl = realized_pnl + %s,
        total_trades = total_trades + 1,
        winning_trades = winning_trades + IF(%s > 0, 1, 0),
        losing_trades = losing_trades + IF(%s < 0, 1, 0),
        updated_at = NOW()
    WHERE id = %s
""", (margin, realized_pnl, margin, realized_pnl, realized_pnl, realized_pnl, self.account_id))
```

#### 全局替换

所有 `UPDATE paper_trading_accounts` → `UPDATE futures_trading_accounts` (10处)

---

## 资金流转示例

### 场景: 开仓 BTC/USDT LONG

**初始状态**:
```
current_balance: $10,000
frozen_balance: $0
```

**开仓 (保证金 $200)**:
```sql
UPDATE futures_trading_accounts
SET current_balance = current_balance - 200,  -- $10,000 → $9,800
    frozen_balance = frozen_balance + 200     -- $0 → $200
```

**结果**:
```
current_balance: $9,800  (可用)
frozen_balance: $200     (冻结)
total_equity: $10,000    (不变)
```

### 场景: 平仓，盈利 $50

```sql
UPDATE futures_trading_accounts
SET current_balance = current_balance + 200 + 50,  -- $9,800 → $10,050
    frozen_balance = frozen_balance - 200,         -- $200 → $0
    realized_pnl = realized_pnl + 50
```

**结果**:
```
current_balance: $10,050  (可用，包含盈利)
frozen_balance: $0        (全部解冻)
total_equity: $10,050     (增加$50)
realized_pnl: $50
```

---

## 后续规范

### 账户使用规范

| 表名 | 用途 | Account Type |
|------|------|--------------|
| `paper_trading_accounts` | 现货模拟交易 | `spot` |
| `futures_trading_accounts` | 合约模拟交易 | `futures` |

### 关联关系

```
futures_trading_accounts (id=2)
    ├── futures_positions (account_id=2)
    ├── futures_orders (account_id=2)
    └── futures_trades (account_id=2)

paper_trading_accounts (id=1)
    └── spot_positions (account_id=1)
        └── spot_orders (account_id=1)
```

### 代码规范

1. **开仓时必须**:
   - ✅ 插入 `futures_positions`
   - ✅ 冻结资金 (`current_balance -= margin`, `frozen_balance += margin`)

2. **平仓时必须**:
   - ✅ 更新 `futures_positions` (status='closed')
   - ✅ 解冻资金 (`current_balance += margin + pnl`, `frozen_balance -= margin`)
   - ✅ 更新统计 (realized_pnl, total_trades, winning_trades, etc.)

---

## 验证方法

### 1. 检查冻结余额是否正确

```sql
SELECT
    a.id,
    a.account_name,
    a.frozen_balance as '账户冻结余额',
    COALESCE(SUM(p.margin), 0) as '实际持仓保证金',
    a.frozen_balance - COALESCE(SUM(p.margin), 0) as '差异'
FROM futures_trading_accounts a
LEFT JOIN futures_positions p ON p.account_id = a.id AND p.status = 'open'
WHERE a.id = 2
GROUP BY a.id;
```

**预期**: 差异应该是 0

### 2. 检查总权益计算

```sql
SELECT
    a.id,
    a.current_balance + a.frozen_balance as '可用+冻结',
    a.total_equity as '总权益',
    (a.current_balance + a.frozen_balance) - a.total_equity as '差异(未实现盈亏)'
FROM futures_trading_accounts a
WHERE a.id = 2;
```

**公式**: `total_equity = current_balance + frozen_balance + unrealized_pnl`

---

## 部署清单

- [x] 创建 `futures_trading_accounts` 表
- [x] 迁移 account_id=2 数据
- [x] 修复 `frozen_balance` 初始值
- [x] 更新 `smart_trader_service.py` 开仓逻辑 (添加冻结)
- [x] 更新所有 `paper_trading_accounts` → `futures_trading_accounts`
- [ ] 重启服务
- [ ] 验证新开仓是否正确冻结资金
- [ ] 验证平仓是否正确解冻资金
- [ ] 监控 `frozen_balance` 是否始终等于持仓保证金总和

---

## 相关文件

- `create_futures_accounts_table.sql` - 建表SQL
- `fix_futures_account_frozen_balance.py` - 数据迁移脚本
- `smart_trader_service.py` - 合约交易服务 (已更新)
- `docs/futures_database_schema.md` - 数据库表结构文档

---

**修改时间**: 2026-01-23
**修改人**: Claude Sonnet 4.5
**影响范围**: 合约账户资金管理
**向后兼容**: 否 (需要重启服务)
**风险等级**: 高 (涉及资金计算)
