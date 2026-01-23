# 平仓原因显示修复 - 部署说明

## 问题根因

**数据库层面**: `smart_trader_service.py` 的平仓逻辑没有将 `close_reason` 写入 `futures_positions.notes` 字段，导致所有记录的 `notes` 字段为 `NULL`。

**前端层面**: 前端和API已经正确实现了 `TOP_DETECTED`/`BOTTOM_DETECTED` 的解析，但因为数据库字段为空，所以显示"未知"。

## 修复内容

### 1. 后端数据写入修复
**文件**: `smart_trader_service.py`

**第一处**: 止盈止损平仓 (第790-796行)
```python
# 修复前
cursor.execute("""
    UPDATE futures_positions
    SET status = 'closed', mark_price = %s,
        realized_pnl = %s,
        close_time = NOW(), updated_at = NOW()
    WHERE id = %s
""", (current_price, realized_pnl, pos_id))

# 修复后
cursor.execute("""
    UPDATE futures_positions
    SET status = 'closed', mark_price = %s,
        realized_pnl = %s,
        notes = %s,
        close_time = NOW(), updated_at = NOW()
    WHERE id = %s
""", (current_price, realized_pnl, close_reason, pos_id))
```

**第二处**: 超时平仓 (第944-950行)
```python
# 修复前
cursor.execute("""
    UPDATE futures_positions
    SET status = 'closed', mark_price = %s,
        realized_pnl = %s,
        close_time = NOW(), updated_at = NOW()
    WHERE id = %s
""", (current_price, realized_pnl, pos_id))

# 修复后
close_reason = f"TIMEOUT_4H(持仓{hours_old}小时)"
cursor.execute("""
    UPDATE futures_positions
    SET status = 'closed', mark_price = %s,
        realized_pnl = %s,
        notes = %s,
        close_time = NOW(), updated_at = NOW()
    WHERE id = %s
""", (current_price, realized_pnl, close_reason, pos_id))
```

### 2. 前端解析已完成
**文件**: `templates/futures_trading.html` (已修复)
**文件**: `app/api/futures_review_api.py` (已修复)

支持的平仓原因格式：
- `TOP_DETECTED(高点回落1.4%,盈利-0.4%)` → 智能顶部识别
- `BOTTOM_DETECTED(低点反弹1.8%,盈利+1.1%)` → 智能底部识别
- `STOP_LOSS` → 止损
- `TAKE_PROFIT` → 固定止盈
- `TIMEOUT_4H(持仓12小时)` → 超时平仓

## 部署步骤

### 1. 拉取最新代码
```bash
cd /root/crypto-analyzer
git pull
```

预期输出：更新到 commit `002c956`

### 2. 重启超级大脑交易服务
```bash
# 停止现有服务
pkill -f smart_trader_service.py

# 确认已停止
ps aux | grep smart_trader_service.py

# 重新启动
nohup python smart_trader_service.py > logs/smart_trader.log 2>&1 &

# 查看启动日志
tail -f logs/smart_trader.log
```

预期看到：
```
超级大脑自动交易服务启动
黑名单交易对仓位: 100 USDT
正常交易对仓位: 400 USDT
```

### 3. 可选: 重启FastAPI服务 (如果想立即生效前端解析)
```bash
# 停止现有服务
pkill -f "python app/main.py"

# 重新启动
cd /root/crypto-analyzer
nohup python app/main.py > logs/fastapi.log 2>&1 &

# 查看启动日志
tail -f logs/fastapi.log
```

预期看到：
```
✅ 复盘合约API路由已注册
启动FastAPI服务器...
```

## 验证

### 1. 等待新的平仓交易
修复只对**新产生的平仓记录**生效，历史的 NULL 记录无法回溯。

### 2. 检查数据库
```bash
python check_close_reason.py
```

预期：新的平仓记录 `notes` 字段不再为 `None`，而是显示具体原因如：
```
平仓原因(notes): 'TOP_DETECTED(高点回落1.4%,盈利-0.4%)'
平仓原因(notes): 'BOTTOM_DETECTED(低点反弹2.1%,盈利+1.0%)'
平仓原因(notes): 'STOP_LOSS'
平仓原因(notes): 'TIMEOUT_4H(持仓5小时)'
```

### 3. 检查前端显示

**合约交易页面** (http://13.212.252.171:9020/futures)
- 查看"最近成交"表格的"平仓原因"列
- 应显示: `[智能顶部识别] 高点回落1.4%,盈利-0.4%`

**复盘24H页面** (http://13.212.252.171:9020/futures-review)
- 查看交易记录的"平仓原因"列
- 应显示: `智能顶部识别(高点回落1.4%,盈利-0.4%)`

## 注意事项

1. **历史记录**: 修复前的记录 `notes` 为 `NULL`，仍会显示"未知"，这是正常的
2. **生效时间**: 需要等待新的平仓交易产生才能看到效果
3. **对冲平仓**: `hedge_loss_cut` 和 `reverse_signal` 已经在写入 `notes` (第1141、1246、1431行)，不受此bug影响

## 回滚方案

如果出现问题：
```bash
cd /root/crypto-analyzer
git reset --hard 8975b29  # 回滚到修复前的版本
pkill -f smart_trader_service.py
nohup python smart_trader_service.py > logs/smart_trader.log 2>&1 &
```

---

**修复时间**: 2026-01-23
**相关Commits**:
- `f1e0074` - 合约交易页面前端解析
- `8975b29` - 复盘24H页面API解析
- `002c956` - 超级大脑服务数据写入修复
- `2e4e686` - 超级大脑服务 futures_orders.notes 写入修复

## 补充说明 (2026-01-23 更新)

### 数据流向

1. **复盘24H页面** (`/futures-review`)
   - 查询表: `futures_positions.notes`
   - SQL: `SELECT ... notes as close_reason FROM futures_positions WHERE ...`
   - 修复: commit `002c956` (UPDATE语句添加notes字段)

2. **合约交易页面历史记录** (`/futures`)
   - 查询表: `futures_orders.notes` (通过LEFT JOIN)
   - SQL: `SELECT ... o.notes as close_reason FROM futures_trades t LEFT JOIN futures_orders o ...`
   - 修复: commit `2e4e686` (INSERT语句使用变量替代硬编码)

3. **futures_trades表**
   - 不包含平仓原因字段
   - 只记录交易执行细节(价格、数量、手续费)
   - 无需修复

### 修复对比

| 位置 | 修复前 | 修复后 |
|------|--------|--------|
| futures_positions.notes | NULL | `TOP_DETECTED(高点回落1.4%,盈利-0.4%)` |
| futures_orders.notes | `'止盈止损'` (硬编码) | `close_reason` (动态变量) |

