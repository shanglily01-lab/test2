# 部署指南 - 2026-01-23

## 概述

本次更新包含以下关键改进：
1. 全局UTC+0时区标准化
2. 多时间周期K线采集 (5m, 15m, 1h, 1d)
3. 历史K线数据回填
4. 黑名单100U小仓位交易策略
5. 历史交易数据清理

## 部署步骤

### 1. 更新代码

```bash
cd /root/crypto-analyzer
git pull
```

预期输出：更新到最新的commit（包含backfill_futures_klines.py和cleanup_old_trades.py）

### 2. 停止相关服务

```bash
# 停止快速采集服务
pkill -f fast_collector_service.py

# 停止超级大脑交易服务
pkill -f smart_trader_service.py
```

### 3. 修复历史数据时区（如果尚未执行）

#### 3.1 修复futures_positions的close_time

```bash
cd /root/crypto-analyzer
python fix_close_time_to_utc.py
```

当提示确认时，输入 `yes`

预期结果：
- 修改2652条记录的close_time（减去8小时）
- 最早记录时间应该在2026-01-20左右

#### 3.2 修复futures_trades的时间字段

```bash
python fix_futures_trades_time.py
```

当提示确认时，输入 `yes`

预期结果：
- 修改4416条trade_time记录
- 修改4416条created_at记录

### 4. 清理1.20之前的历史数据（可选）

```bash
python cleanup_old_trades.py
```

当提示确认时，输入 `yes`

预期结果：
- 删除futures_positions中2026-01-20之前的记录
- 删除futures_trades中2026-01-20之前的记录
- 剩余记录时间都在2026-01-20之后

### 5. 回填历史K线数据

**重要：这是必须步骤，否则超级大脑将因数据不足而无法做决策**

```bash
python backfill_futures_klines.py
```

预期结果：
- 回填1h K线：约100条/交易对 × N个交易对
- 回填1d K线：约50条/交易对 × N个交易对
- 总计数千条历史K线数据

时间：预计需要1-2分钟（有API限流延迟）

### 6. 验证数据

```bash
# 验证K线数据是否充足
mysql -h localhost -u admin -p binance-data -e "
SELECT
    timeframe,
    COUNT(*) as total_records,
    COUNT(DISTINCT symbol) as symbols_count,
    MIN(timestamp) as earliest_time,
    MAX(timestamp) as latest_time
FROM kline_data
WHERE exchange = 'binance_futures'
GROUP BY timeframe
ORDER BY
    CASE timeframe
        WHEN '5m' THEN 1
        WHEN '15m' THEN 2
        WHEN '1h' THEN 3
        WHEN '1d' THEN 4
    END;
"
```

预期输出示例：
```
+-----------+---------------+---------------+---------------------+---------------------+
| timeframe | total_records | symbols_count | earliest_time       | latest_time         |
+-----------+---------------+---------------+---------------------+---------------------+
| 5m        |           50  |            50 | 2026-01-23 08:00:00 | 2026-01-23 08:55:00 |
| 15m       |           50  |            50 | 2026-01-23 08:00:00 | 2026-01-23 08:45:00 |
| 1h        |         5000  |            50 | 2026-01-19 12:00:00 | 2026-01-23 08:00:00 |
| 1d        |         2500  |            50 | 2025-12-04 00:00:00 | 2026-01-23 00:00:00 |
+-----------+---------------+---------------+---------------------+---------------------+
```

关键检查点：
- 1h K线应该有约100条/交易对（4天历史）
- 1d K线应该有约50条/交易对（50天历史）
- 所有时间戳应该是UTC+0

### 7. 重启服务

#### 7.1 启动快速采集服务

```bash
cd /root/crypto-analyzer
nohup python fast_collector_service.py > logs/fast_collector.log 2>&1 &
```

检查日志：
```bash
tail -f logs/fast_collector.log
```

预期看到：
```
开始快速数据采集周期
采集周期: 5m, 15m, 1h, 1d K线数据
采集 5m K线数据 (每个交易对1条)...
采集 15m K线数据 (每个交易对1条)...
采集 1h K线数据 (每个交易对100条)...
采集 1d K线数据 (每个交易对50条)...
✓ 采集周期完成
```

#### 7.2 启动超级大脑交易服务

```bash
nohup python smart_trader_service.py > logs/smart_trader.log 2>&1 &
```

检查日志：
```bash
tail -f logs/smart_trader.log
```

预期看到：
```
超级大脑自动交易服务启动
黑名单交易对仓位: 100 USDT
正常交易对仓位: 400 USDT
4小时超时平仓检查（使用UTC+0）
```

### 8. 验证部署结果

#### 8.1 检查时区是否正确

查看最新开仓记录：
```bash
mysql -h localhost -u admin -p binance-data -e "
SELECT
    symbol,
    position_side,
    entry_price,
    created_at,
    TIMESTAMPDIFF(HOUR, created_at, NOW()) as hours_ago
FROM futures_positions
WHERE status = 'open'
ORDER BY created_at DESC
LIMIT 5;
"
```

关键：`created_at` 应该显示UTC+0时间，`hours_ago`应该准确显示距今小时数

#### 8.2 检查黑名单策略

查看黑名单交易对的仓位大小：
```bash
mysql -h localhost -u admin -p binance-data -e "
SELECT
    symbol,
    position_side,
    quantity,
    entry_price,
    quantity * entry_price as position_value_usdt
FROM futures_positions
WHERE status = 'open'
AND symbol IN (SELECT symbol FROM futures_trades WHERE symbol IN ('DOGE/USDT', 'SHIB/USDT'))
ORDER BY created_at DESC;
"
```

黑名单交易对的仓位价值应该约为100 USDT，正常交易对约为400 USDT

#### 8.3 检查K线数据新鲜度

```bash
mysql -h localhost -u admin -p binance-data -e "
SELECT
    timeframe,
    COUNT(*) as records,
    MAX(timestamp) as latest_time,
    TIMESTAMPDIFF(MINUTE, MAX(timestamp), NOW()) as minutes_ago
FROM kline_data
WHERE exchange = 'binance_futures'
GROUP BY timeframe;
"
```

- 5m K线应该在5分钟内
- 15m K线应该在15分钟内
- 1h K线应该在60分钟内
- 1d K线应该在24小时内

## 预期影响

### 正面影响
1. ✅ 时区统一，4小时超时平仓准确触发
2. ✅ 多时间周期K线，超级大脑决策更准确
3. ✅ 历史数据充足，避免"数据不足"错误
4. ✅ 黑名单小仓位策略，降低风险

### 潜在风险
1. ⚠️ 首次回填可能触发币安API限流（已添加延迟保护）
2. ⚠️ 数据库写入量增加（1h/1d K线每5分钟更新）
3. ⚠️ 历史时间数据已修改，旧的时间戳-8小时

## 回滚方案

如果部署出现问题：

```bash
# 1. 停止所有服务
pkill -f fast_collector_service.py
pkill -f smart_trader_service.py

# 2. 回滚代码到上一个版本
cd /root/crypto-analyzer
git reset --hard 2f1a69b  # 回滚到时区修复之前的版本

# 3. 重启服务
nohup python fast_collector_service.py > logs/fast_collector.log 2>&1 &
nohup python smart_trader_service.py > logs/smart_trader.log 2>&1 &
```

## 联系支持

如有问题，请提供以下信息：
- 部署到哪一步出错
- 完整的错误日志
- 数据库验证查询的输出

---

**部署人员：** _____________
**部署时间：** _____________
**验证结果：** ☐ 通过  ☐ 失败（原因：_______________）
