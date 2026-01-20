# 🚨 紧急部署: 修复时区问题 (commit 4844835)

## 问题确认

根据诊断，有两个问题:

1. ✅ **时区问题**: MySQL服务器使用UTC+8，导致DATA_STALE误报8小时延迟
2. ❌ **Scheduler停止写入**: 虽然进程在运行，但最近24分钟没有数据写入数据库

## 快速修复步骤 (10分钟)

### 1. SSH到服务器

```bash
ssh ec2-user@13.212.252.171
cd ~/crypto-analyzer  # 或你的实际路径
```

### 2. 拉取最新代码

```bash
git pull origin master
```

**预期输出**:
```
From github.com:shanglily01-lab/test2
   cb94e9b..4844835  master     -> origin/master
Updating cb94e9b..4844835
Fast-forward
 app/scheduler.py        | 1 +
 check_actual_data.py    | ...
 (新文件: verify_timezone_issue.py, FIX_TIMEZONE_AND_SCHEDULER.md)
```

### 3. 检查Scheduler状态

```bash
# 查看进程
ps aux | grep scheduler.py | grep -v grep

# 查看最新日志
tail -20 logs/scheduler.log

# 查看进程运行时间
ps -p $(pgrep -f scheduler.py) -o etime,cmd
```

### 4. 重启Scheduler (关键!)

```bash
# 杀掉旧进程
kill $(pgrep -f scheduler.py)

# 等待进程结束
sleep 3

# 启动新进程
nohup python3 app/scheduler.py > logs/scheduler.log 2>&1 &

# 验证启动
ps aux | grep scheduler.py | grep -v grep

# 监控日志 (应该立即看到数据采集)
tail -f logs/scheduler.log
```

**预期日志** (立即出现):
```
[HH:MM:SS] 首次数据采集开始...
[HH:MM:SS] 开始采集币安合约数据...
  ✓ 合约数据采集完成: 成功 45/45, 失败 0, 耗时 3.9秒
```

### 5. 修改MySQL时区 (可选但强烈推荐)

```bash
# 编辑MySQL配置
sudo nano /etc/my.cnf
# 或
sudo nano /etc/mysql/my.cnf
```

**添加到 [mysqld] 部分**:
```ini
[mysqld]
default-time-zone='+00:00'
```

**重启MySQL**:
```bash
sudo systemctl restart mysqld
# 或
sudo service mysqld restart
```

**验证**:
```bash
mysql -h localhost -u admin -p'Tonny@1000' -e "SELECT NOW(), UTC_TIMESTAMP();"
```

NOW() 和 UTC_TIMESTAMP() 应该相同。

### 6. 验证修复 (重要!)

等待1-2分钟后，检查数据库:

```bash
mysql -h localhost -u admin -p'Tonny@1000' -D binance-data -e "
SELECT
    exchange,
    MAX(timestamp) as latest,
    TIMESTAMPDIFF(SECOND, MAX(timestamp), UTC_TIMESTAMP()) as delay_sec
FROM price_data
GROUP BY exchange;
"
```

**预期结果**:
```
+-----------------+---------------------+-----------+
| exchange        | latest              | delay_sec |
+-----------------+---------------------+-----------+
| binance_futures | 2026-01-20 06:45:10 |         5 |
| binance         | 2026-01-20 06:45:08 |         7 |
+-----------------+---------------------+-----------+
```

`delay_sec` 应该 **<15秒** ✅

### 7. 持续监控 (5分钟)

```bash
# 监控scheduler日志
tail -f logs/scheduler.log | grep "合约数据采集完成"

# 每30秒检查数据库 (在另一个终端)
watch -n 30 'mysql -h localhost -u admin -p"Tonny@1000" -D binance-data -e "SELECT exchange, COUNT(*) cnt, MAX(timestamp), TIMESTAMPDIFF(SECOND, MAX(timestamp), UTC_TIMESTAMP()) delay FROM price_data WHERE timestamp >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 MINUTE) GROUP BY exchange"'
```

## 快速诊断命令

如果问题持续存在:

```bash
# 一键诊断脚本
python3 verify_timezone_issue.py
```

这个脚本会显示:
- MySQL时区设置
- Python时区设置
- 数据库数据新鲜度
- Scheduler是否在写入数据

## 常见问题

### Q1: Scheduler重启后还是没有数据?

**检查日志错误**:
```bash
grep -i error logs/scheduler.log | tail -20
grep -i "database\|connection\|mysql" logs/scheduler.log | tail -20
```

**检查数据库连接**:
```bash
mysql -h localhost -u admin -p'Tonny@1000' -D binance-data -e "SHOW PROCESSLIST;"
```

### Q2: 还是看到DATA_STALE警告?

运行诊断:
```bash
python3 verify_timezone_issue.py
```

如果显示 "数据延迟 <1分钟" 但还有警告，说明 `smart_trader_service.py` 也需要重启:

```bash
# 查找进程
ps aux | grep smart_trader

# 重启
kill $(pgrep -f smart_trader)
nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &
```

### Q3: MySQL时区修改需要重启吗?

**需要!** 修改配置后必须重启MySQL服务才能生效。

## 回滚方案

如果出现问题:

```bash
cd ~/crypto-analyzer
git reset --hard cb94e9b  # 回到上一个版本
kill $(pgrep -f scheduler.py)
nohup python3 app/scheduler.py > logs/scheduler.log 2>&1 &
```

## ✅ 成功标志

修复成功后，你应该看到:

1. ✅ Scheduler日志每10秒显示 "✓ 合约数据采集完成"
2. ✅ 数据库查询显示 delay_sec <15秒
3. ✅ verify_timezone_issue.py 显示 "✅ 系统运行正常"
4. ✅ Smart trader 不再有 DATA_STALE 警告

## 预计时间

- 代码拉取: 30秒
- 重启scheduler: 30秒
- MySQL时区修改 (可选): 3-5分钟
- 验证: 2-3分钟

**总计: 约5-10分钟**

## 技术细节

**修改内容**:
- `app/scheduler.py` Line 392: `datetime.now()` → `datetime.utcnow()`
- `check_actual_data.py`: 所有 `NOW()` → `UTC_TIMESTAMP()`

**根本原因**:
- Scheduler保存数据时使用本地时间 (UTC+8)
- 数据库查询时也使用NOW() (UTC+8)
- 但服务器日志是UTC+0
- 导致时间比较混乱

**解决方案**:
- 统一使用UTC时间
- MySQL服务器也设置为UTC+0

---

**Commit**: 4844835
**部署日期**: 2026-01-20
**优先级**: 🔴 紧急 (系统当前无法正常写入数据)
