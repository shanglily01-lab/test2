# 禁用币本位合约服务指南

## 🔴 为什么要禁用?

币本位合约服务目前存在严重问题:

1. **没有K线数据**: `futures_klines` 表中没有 `/USD` 交易对数据
2. **无法获取价格**: REST API 和 WebSocket 均无法获取价格
3. **分批建仓失败**: 后台任务持续运行并报错
4. **大量错误日志**: 每10秒一次价格获取失败

### 典型错误日志

```
XTZ/USD REST API获取失败: 'price'
XTZ/USD 所有价格获取方法均失败
❌ [BATCH_ENTRY_CALLBACK_ERROR] XTZ/USD SHORT | float division by zero
```

---

## ⚡ 快速禁用步骤

### 步骤1: 停止币本位服务

```bash
pm2 stop coin_futures_trader
pm2 delete coin_futures_trader
```

验证:
```bash
pm2 list
# 应该看不到 coin_futures_trader
```

### 步骤2: 关闭异常持仓

运行清理脚本:
```bash
python stop_coin_futures.py
```

或手动关闭:
```sql
UPDATE futures_positions
SET status = 'closed',
    close_time = NOW(),
    realized_pnl = IFNULL(unrealized_pnl, 0),
    notes = CONCAT(IFNULL(notes, ''), ' | 币本位服务停用,系统自动关闭')
WHERE account_id = 3
AND status = 'open';
```

### 步骤3: 禁用配置 (可选)

编辑 `config.yaml`,注释掉币本位配置:

```yaml
# 暂时禁用币本位合约
# coin_futures_symbols:
# - BTCUSD_PERP
# - ETHUSD_PERP
# - ADAUSD_PERP
# - DOTUSD_PERP
# ...
```

### 步骤4: 重启U本位服务

```bash
pm2 restart smart_trader
pm2 logs smart_trader --lines 50
```

验证日志:
- ✅ 不应再出现 `/USD` 交易对的错误
- ✅ 只应该处理 `/USDT` 交易对

---

## 📊 当前状态检查

### 检查币本位持仓

```bash
python check_wrong_positions.py
```

### 检查进程状态

```bash
pm2 list
pm2 logs coin_futures_trader --lines 20  # 应该显示已停止
pm2 logs smart_trader --lines 20         # 应该正常运行
```

---

## 🔄 如果将来要启用币本位

需要完成以下准备工作:

### 1. 添加数据采集

需要创建 `coin_futures_collector.py` 或修改现有采集器:

```python
# 采集币本位交易对的K线数据
symbols = ['BTC/USD', 'ETH/USD', 'ADA/USD', 'DOT/USD', ...]

# 写入 futures_klines 表
# symbol格式: ADA/USD (不是 ADAUSD_PERP)
```

### 2. 配置价格订阅

确保 WebSocket 服务能订阅币本位交易对:

```python
# 币安币本位合约 WebSocket
# wss://dstream.binance.com/ws/adausd_perp@ticker
```

### 3. 验证数据完整性

```sql
-- 检查K线数据
SELECT symbol, COUNT(*) as klines, MAX(open_time) as latest
FROM futures_klines
WHERE symbol LIKE '%/USD'
GROUP BY symbol;

-- 应该看到数据且latest是最近的时间
```

### 4. 小规模测试

- 只启用1-2个交易对
- 使用小仓位测试
- 监控日志确认无错误
- 验证价格获取正常

---

## ⚠️ 重要提醒

**在数据采集完善之前,不要启用币本位服务!**

当前系统适合专注做好U本位合约:
- ✅ 有完整的K线数据
- ✅ 价格获取稳定
- ✅ 信号分析准确
- ✅ 风险可控

---

## 📝 禁用清单

- [ ] `pm2 stop coin_futures_trader`
- [ ] `pm2 delete coin_futures_trader`
- [ ] 运行 `python stop_coin_futures.py` 清理持仓
- [ ] 注释 `config.yaml` 中的 `coin_futures_symbols` (可选)
- [ ] `pm2 restart smart_trader`
- [ ] 验证日志无 `/USD` 错误
- [ ] 运行 `python check_wrong_positions.py` 确认无异常持仓

---

**完成禁用后,系统应该恢复正常,不再产生币本位相关错误日志。**
