# 数据库更新说明

## 更新内容
添加 `strategy_trade_records` 表用于保存策略执行的交易记录（包括测试和实盘）。

## 执行步骤

### 方法1: 使用完整SQL脚本（推荐）
直接执行 `update_database.sql` 文件中的所有SQL语句。

### 方法2: 分步执行
按照 `update_database_step_by_step.sql` 中的步骤逐步执行。

### 方法3: 使用Python脚本
```bash
python scripts/create_strategy_trade_records_table.py
python scripts/fix_strategy_id_type.py
```

## SQL脚本说明

### 1. update_database.sql
完整的SQL脚本，包含：
- 创建 `strategy_trade_records` 表（如果不存在）
- 修改 `strategy_id` 字段类型为 BIGINT（如果表已存在）

### 2. update_database_step_by_step.sql
分步执行的SQL脚本，包含详细的检查步骤。

## 表结构说明

### strategy_trade_records 表
- **策略信息**: strategy_id (BIGINT), strategy_name, account_id
- **交易信息**: symbol, action, direction, position_side
- **价格和数量**: entry_price, exit_price, quantity, leverage
- **金额信息**: margin, total_value, fee, realized_pnl
- **关联信息**: position_id, order_id, signal_id
- **其他**: reason (交易原因), trade_time, created_at

### 重要字段说明
- `account_id = 0`: 表示测试交易记录
- `account_id > 0`: 表示实盘交易记录
- `strategy_id`: 使用 BIGINT 类型以支持更大的ID值

## 注意事项
1. 执行前请备份数据库
2. 如果表已存在，ALTER TABLE 语句会修改字段类型，不会丢失数据
3. 确保数据库用户有 CREATE TABLE 和 ALTER TABLE 权限


