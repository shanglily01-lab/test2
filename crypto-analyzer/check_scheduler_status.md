# Scheduler 状态诊断

## 🚨 当前问题

从日志看到大量警告:
```
[DATA_STALE] K线数据过时! 最新K线时间: 13.9分钟前
```

这说明 **scheduler 数据采集服务可能没有正常运行**。

## 📋 诊断步骤

### 1. 检查 scheduler 进程是否在运行

```bash
ps aux | grep "scheduler.py"
```

**预期结果**:
```
ec2-user  123456  0.5  1.2  scheduler.py
```

**如果没有进程**:
- scheduler 服务已停止
- 需要重启服务

### 2. 检查 scheduler 日志

```bash
# 查看最新日志
tail -100 logs/scheduler.log

# 查看实时日志
tail -f logs/scheduler.log
```

**关键信息**:
- 是否有 "开始采集币安合约数据" 的日志?
- 是否有错误信息?
- 最后一次采集是什么时候?

### 3. 检查数据库中的最新数据

运行诊断脚本:

```bash
python3 << 'EOF'
import pymysql
from datetime import datetime

conn = pymysql.connect(
    host='localhost',
    user='admin',
    password='Tonny@1000',
    database='binance-data'
)
cursor = conn.cursor()

# 检查最新K线数据
cursor.execute("""
    SELECT
        exchange,
        MAX(timestamp) as latest,
        TIMESTAMPDIFF(MINUTE, MAX(timestamp), NOW()) as minutes_ago
    FROM kline_data
    WHERE timeframe = '1m'
    GROUP BY exchange
""")

print("K线数据新鲜度:")
for row in cursor.fetchall():
    print(f"  {row[0]:20s}: {row[2]:3d}分钟前")

cursor.close()
conn.close()
EOF
```

**预期结果** (如果 scheduler 正常):
```
K线数据新鲜度:
  binance_futures     :   0分钟前  # 或最多1-2分钟
```

**如果显示 10+ 分钟**:
- scheduler 没有运行,或
- 采集任务失败

## 🔧 解决方案

### 方案A: Scheduler 服务未运行

如果 `ps aux | grep scheduler` 没有结果:

```bash
# 1. 进入项目目录
cd /path/to/crypto-analyzer

# 2. 确认代码是最新的
git pull origin master

# 3. 启动 scheduler
nohup python3 app/scheduler.py > logs/scheduler.log 2>&1 &

# 4. 验证启动成功
ps aux | grep scheduler.py

# 5. 监控日志
tail -f logs/scheduler.log
```

**预期日志** (启动后立即采集):
```
[HH:MM:SS] 首次数据采集开始...
[HH:MM:SS] 开始采集币安合约数据...
  ✓ 合约数据采集完成: 成功 39/39, 失败 0, 耗时 3.9秒
```

### 方案B: Scheduler 运行但采集失败

如果进程存在但数据过时:

```bash
# 1. 查看日志中的错误
grep -E "ERROR|WARNING|失败" logs/scheduler.log | tail -50

# 2. 检查是否是 API 限流
grep "429\|rate limit\|Too Many Requests" logs/scheduler.log | tail -20

# 3. 检查网络连接
ping api.binance.com
curl -I https://fapi.binance.com/fapi/v1/ping
```

**常见错误**:

#### 错误1: API 限流
```
429 Too Many Requests
```
**解决**: 增加延迟或减少频率

#### 错误2: 网络问题
```
Connection timeout
ConnectionError
```
**解决**: 检查服务器网络,防火墙设置

#### 错误3: 数据库连接问题
```
Can't connect to MySQL server
Too many connections
```
**解决**: 检查数据库服务,增加连接池大小

### 方案C: 代码未更新

```bash
# 检查当前代码版本
cd /path/to/crypto-analyzer
git log -1 --oneline app/scheduler.py

# 应该看到:
# 085fbd9 perf: 优化scheduler数据采集性能

# 如果不是这个 commit,需要更新:
git pull origin master

# 重启服务
ps aux | grep scheduler.py | awk '{print $2}' | xargs kill
nohup python3 app/scheduler.py > logs/scheduler.log 2>&1 &
```

## 📊 验证修复

### 1. 等待1分钟后检查数据

```bash
python3 << 'EOF'
import pymysql
conn = pymysql.connect(host='localhost', user='admin', password='Tonny@1000', database='binance-data')
cursor = conn.cursor()

cursor.execute("""
    SELECT COUNT(*), MAX(timestamp), TIMESTAMPDIFF(SECOND, MAX(timestamp), NOW())
    FROM kline_data
    WHERE timeframe = '1m' AND exchange = 'binance_futures'
    AND timestamp >= DATE_SUB(NOW(), INTERVAL 2 MINUTE)
""")

count, latest, seconds_ago = cursor.fetchone()
print(f"最近2分钟的K线记录: {count}条")
print(f"最新数据: {seconds_ago}秒前")

if seconds_ago < 20:
    print("✅ 数据采集正常!")
else:
    print("❌ 数据仍然过时,需要继续排查")

cursor.close()
conn.close()
EOF
```

### 2. 监控 smart_trader_service 日志

```bash
# 应该不再看到 DATA_STALE 警告
tail -f logs/smart_trader.log | grep -v "DATA_STALE"
```

## 🎯 关键检查点

- [ ] scheduler 进程正在运行
- [ ] scheduler 日志显示正常采集 (耗时 <5秒)
- [ ] 数据库中的K线数据 <1分钟前
- [ ] smart_trader 不再报 DATA_STALE 警告
- [ ] 代码版本是最新的 (commit 085fbd9 或更新)

## 💡 临时解决方案

如果无法立即修复 scheduler,可以临时调整 smart_trader 的数据新鲜度阈值:

```bash
# 找到 smart_trader_service.py 中的新鲜度检查
grep -n "DATA_STALE" smart_trader_service.py

# 临时将阈值从 10分钟改为 20分钟
# 但这只是权宜之计,根本问题还是要修复 scheduler
```

**不推荐这样做**,因为使用过时数据会导致交易决策不准确。

## 📞 需要帮助?

如果按照上述步骤仍无法解决:

1. 提供 scheduler 日志的最后100行:
   ```bash
   tail -100 logs/scheduler.log
   ```

2. 提供进程状态:
   ```bash
   ps aux | grep -E "scheduler|smart_trader"
   ```

3. 提供数据库状态:
   ```bash
   # 运行上面的数据新鲜度检查脚本
   ```
