# K线数据回补指南

当 scheduler 中断导致数据缺失时，使用此脚本补采集历史数据。

## 快速使用

### 1. 回补今天凌晨0点到13点的数据

```bash
cd /home/tonny/code/test2/crypto-analyzer

python scripts/backfill_kline_data.py \
  --start "2025-10-28 00:00:00" \
  --end "2025-10-28 13:00:00"
```

### 2. 同时回补K线和价格数据

```bash
python scripts/backfill_kline_data.py \
  --start "2025-10-28 00:00:00" \
  --end "2025-10-28 13:00:00" \
  --include-prices
```

### 3. 自定义时间周期

```bash
# 只回补 1分钟 和 5分钟 K线
python scripts/backfill_kline_data.py \
  --start "2025-10-28 00:00:00" \
  --end "2025-10-28 13:00:00" \
  --timeframes "1m,5m"
```

---

## 参数说明

| 参数 | 必填 | 说明 | 示例 |
|------|------|------|------|
| `--start` | 是 | 开始时间 | `"2025-10-28 00:00:00"` |
| `--end` | 是 | 结束时间 | `"2025-10-28 13:00:00"` |
| `--timeframes` | 否 | 时间周期（逗号分隔） | `"1m,5m,1h"` (默认) |
| `--include-prices` | 否 | 同时回补价格数据表 | 添加此参数启用 |

---

## 支持的时间周期

- `1m` - 1分钟K线
- `5m` - 5分钟K线
- `15m` - 15分钟K线
- `1h` - 1小时K线
- `4h` - 4小时K线
- `1d` - 1天K线

---

## 数据采集说明

### 交易所优先级

1. **Binance** (优先) - 数据最全面
2. **Gate.io** (备用) - Binance 失败时使用

### 币种范围

自动从 `config.yaml` 读取 `symbols` 列表，默认包括：
- BTC/USDT
- ETH/USDT
- BNB/USDT
- SOL/USDT
- 等等...

### 数据保存

- **K线数据** → `kline_data` 表
- **价格数据** → `price_data` 表（使用 `--include-prices` 时）

---

## 常见场景

### 场景1：scheduler 运行12小时后停止

```bash
# 假设现在是 2025-10-28 14:00
# scheduler 在昨晚 22:00 停止

python scripts/backfill_kline_data.py \
  --start "2025-10-27 22:00:00" \
  --end "2025-10-28 14:00:00" \
  --include-prices
```

### 场景2：只回补缺失的1小时K线

```bash
python scripts/backfill_kline_data.py \
  --start "2025-10-28 08:00:00" \
  --end "2025-10-28 10:00:00" \
  --timeframes "1h"
```

### 场景3：回补整个昨天的数据

```bash
python scripts/backfill_kline_data.py \
  --start "2025-10-27 00:00:00" \
  --end "2025-10-27 23:59:59" \
  --include-prices
```

---

## 运行示例

```bash
$ python scripts/backfill_kline_data.py --start "2025-10-28 00:00:00" --end "2025-10-28 13:00:00"

================================================================================
开始回补K线数据
时间范围: 2025-10-28 00:00:00 ~ 2025-10-28 13:00:00
币种数量: 15
时间周期: 1m, 5m, 1h
交易所: binance, gate
================================================================================

📊 回补 1m K线数据...
  ✓ [binance] BTC/USDT (1m): 保存 780 条K线
  ✓ [binance] ETH/USDT (1m): 保存 780 条K线
  ✓ [binance] BNB/USDT (1m): 保存 780 条K线
  ...

📊 回补 5m K线数据...
  ✓ [binance] BTC/USDT (5m): 保存 156 条K线
  ✓ [binance] ETH/USDT (5m): 保存 156 条K线
  ...

📊 回补 1h K线数据...
  ✓ [binance] BTC/USDT (1h): 保存 13 条K线
  ✓ [binance] ETH/USDT (1h): 保存 13 条K线
  ...

================================================================================
✅ K线数据回补完成
总保存: 14235 条, 错误: 0 次
================================================================================

🎉 所有数据回补完成！
```

---

## 注意事项

### 1. API限流

- 脚本已内置延迟 (0.2秒/请求)
- 如遇到限流错误，等待几分钟后重试

### 2. 数据去重

- 使用 `ON DUPLICATE KEY UPDATE` 机制
- 重复运行不会产生重复数据

### 3. 时间范围限制

- Binance 单次最多返回 1000 条K线
- 如需回补超大时间范围，分多次运行

**示例：回补整月数据**
```bash
# 分10天一批
python scripts/backfill_kline_data.py --start "2025-10-01 00:00:00" --end "2025-10-10 23:59:59"
python scripts/backfill_kline_data.py --start "2025-10-11 00:00:00" --end "2025-10-20 23:59:59"
python scripts/backfill_kline_data.py --start "2025-10-21 00:00:00" --end "2025-10-28 23:59:59"
```

### 4. 数据库连接

- 确保 `config.yaml` 中的数据库配置正确
- Windows 本地数据库需在 Windows 环境运行脚本

---

## 验证数据完整性

### 查看K线数据

```sql
-- 检查某个币种某个时间段的数据
SELECT
    symbol,
    timeframe,
    COUNT(*) as count,
    MIN(timestamp) as first_time,
    MAX(timestamp) as last_time
FROM kline_data
WHERE symbol = 'BTC/USDT'
  AND timeframe = '1m'
  AND timestamp BETWEEN '2025-10-28 00:00:00' AND '2025-10-28 13:00:00'
GROUP BY symbol, timeframe;
```

### 查找数据缺口

```sql
-- 查看数据时间分布
SELECT
    DATE_FORMAT(timestamp, '%Y-%m-%d %H:00:00') as hour,
    COUNT(*) as count
FROM kline_data
WHERE symbol = 'BTC/USDT'
  AND timeframe = '1m'
  AND timestamp BETWEEN '2025-10-28 00:00:00' AND '2025-10-28 13:00:00'
GROUP BY hour
ORDER BY hour;
```

---

## 故障排查

### 问题1：`ModuleNotFoundError`

```bash
# 确保在项目根目录运行
cd /home/tonny/code/test2/crypto-analyzer
python scripts/backfill_kline_data.py ...
```

### 问题2：数据库连接失败

```bash
# 检查数据库配置
cat config.yaml | grep -A 10 "database:"

# 测试数据库连接
mysql -h <host> -u <user> -p<password> <database>
```

### 问题3：API访问失败

- 检查网络连接
- 确认 Binance/Gate.io 是否可访问
- 查看 API 密钥是否正确（公开接口无需密钥）

### 问题4：时间格式错误

```bash
# 正确格式（带双引号）
--start "2025-10-28 00:00:00"

# 错误格式
--start 2025-10-28 00:00:00  # ❌ 缺少引号
--start "2025-10-28"         # ❌ 缺少时间部分
```

---

## 自动化建议

### Cron 定时任务（每天自动回补）

```bash
# 每天凌晨 3 点回补前一天的数据
0 3 * * * cd /home/tonny/code/test2/crypto-analyzer && python scripts/backfill_kline_data.py --start "$(date -d 'yesterday' '+\%Y-\%m-\%d 00:00:00')" --end "$(date -d 'yesterday' '+\%Y-\%m-\%d 23:59:59')" >> /var/log/backfill.log 2>&1
```

### Windows 计划任务

创建批处理文件 `backfill_today.bat`:

```batch
@echo off
cd C:\path\to\crypto-analyzer
python scripts/backfill_kline_data.py --start "2025-10-28 00:00:00" --end "2025-10-28 13:00:00"
pause
```

---

## 相关文档

- [DATA_COLLECTION_FREQUENCY.md](../DATA_COLLECTION_FREQUENCY.md) - 数据采集频率说明
- [scheduler.py](../app/scheduler.py) - 定时采集任务
- [config.yaml](../config.yaml) - 系统配置

---

**最后更新**: 2025-10-28
**脚本路径**: `scripts/backfill_kline_data.py`