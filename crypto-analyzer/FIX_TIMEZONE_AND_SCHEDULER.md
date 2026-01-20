# 修复时区问题和Scheduler停止运行

## 🚨 当前问题确认

根据 `verify_timezone_issue.py` 的诊断结果：

1. **MySQL服务器时区**: UTC+8 (应该改为UTC+0)
2. **真实数据延迟**: 24.1分钟 (最近10分钟没有新数据)
3. **Scheduler状态**: 虽然日志显示在运行，但**没有写入数据到数据库**

## 🔧 修复步骤

### 第一步: 修改MySQL服务器时区为UTC (立即执行)

```bash
# SSH到服务器
ssh ec2-user@13.212.252.171

# 修改MySQL配置文件
sudo nano /etc/my.cnf
# 或
sudo nano /etc/mysql/my.cnf
# 或
sudo nano /etc/mysql/mysql.conf.d/mysqld.cnf
```

**添加以下内容到 `[mysqld]` 部分**:

```ini
[mysqld]
default-time-zone='+00:00'
```

**重启MySQL服务**:

```bash
# 方式1: systemctl
sudo systemctl restart mysql
# 或
sudo systemctl restart mysqld

# 方式2: service命令
sudo service mysql restart
# 或
sudo service mysqld restart
```

**验证时区修改**:

```bash
mysql -h localhost -u admin -p'Tonny@1000' -e "SELECT NOW(), UTC_TIMESTAMP(), @@global.time_zone, @@session.time_zone;"
```

**预期输出** (NOW和UTC_TIMESTAMP应该相同):
```
+---------------------+---------------------+--------------------+---------------------+
| NOW()               | UTC_TIMESTAMP()     | @@global.time_zone | @@session.time_zone |
+---------------------+---------------------+--------------------+---------------------+
| 2026-01-20 06:30:00 | 2026-01-20 06:30:00 | +00:00             | +00:00              |
+---------------------+---------------------+--------------------+---------------------+
```

### 第二步: 检查并重启Scheduler服务

```bash
# 1. 检查scheduler进程
ps aux | grep scheduler.py

# 2. 如果有进程,查看其进程ID和启动时间
ps aux | grep scheduler.py | grep -v grep

# 3. 检查scheduler日志最后100行
tail -100 logs/scheduler.log

# 4. 检查日志最新时间
tail -1 logs/scheduler.log

# 5. 如果scheduler在运行但不写入数据,杀掉并重启
kill $(ps aux | grep 'scheduler.py' | grep -v grep | awk '{print $2}')

# 6. 等待进程结束
sleep 2

# 7. 启动新的scheduler
cd /home/ec2-user/crypto-analyzer  # 或你的实际路径
nohup python3 app/scheduler.py > logs/scheduler.log 2>&1 &

# 8. 验证启动成功
ps aux | grep scheduler.py

# 9. 监控日志
tail -f logs/scheduler.log
```

**预期日志** (应该立即看到):
```
[HH:MM:SS] 首次数据采集开始...
[HH:MM:SS] 开始采集币安合约数据...
  ✓ 合约数据采集完成: 成功 45/45, 失败 0, 耗时 3.9秒
```

### 第三步: 修复代码中的时区问题

#### 3.1 修复 scheduler.py 中的 datetime.now()

**问题行: Line 392**

```python
# 修改前 ❌
'timestamp': datetime.now()

# 修改后 ✅
'timestamp': datetime.utcnow()
```

**完整修复**:

```bash
cd /home/ec2-user/crypto-analyzer
git pull origin master  # 获取最新代码后

# 在本地修改后推送,或在服务器上直接修改
```

#### 3.2 修复 check_actual_data.py

将所有 `NOW()` 改为 `UTC_TIMESTAMP()`:

```python
# 示例:
# 修改前
TIMESTAMPDIFF(MINUTE, MAX(timestamp), NOW()) as minutes_ago

# 修改后
TIMESTAMPDIFF(MINUTE, MAX(timestamp), UTC_TIMESTAMP()) as minutes_ago
```

#### 3.3 修复 smart_trader_service.py 中的 DATA_STALE 检查

需要查找所有 DATA_STALE 检查并使用 UTC_TIMESTAMP()。

### 第四步: 验证修复效果

#### 4.1 等待1-2分钟后运行诊断

```bash
# 在本地运行
python verify_timezone_issue.py
```

**预期输出**:
```
✅ 系统运行正常!
   - 数据延迟: 10秒 (<1分钟)
   - Scheduler正在正常采集和写入数据
```

#### 4.2 在服务器上检查数据库

```bash
mysql -h localhost -u admin -p'Tonny@1000' -D binance-data -e "
SELECT
    exchange,
    COUNT(*) as records,
    MAX(timestamp) as latest,
    TIMESTAMPDIFF(SECOND, MAX(timestamp), UTC_TIMESTAMP()) as delay_sec
FROM price_data
WHERE timestamp >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 5 MINUTE)
GROUP BY exchange;
"
```

**预期输出**:
```
+-----------------+---------+---------------------+-----------+
| exchange        | records | latest              | delay_sec |
+-----------------+---------+---------------------+-----------+
| binance_futures |    1350 | 2026-01-20 06:35:10 |         5 |
| binance         |    1320 | 2026-01-20 06:35:08 |         7 |
+-----------------+---------+---------------------+-----------+
```

delay_sec 应该 <15秒

### 第五步: 监控稳定性 (10分钟)

```bash
# 持续监控scheduler日志
tail -f logs/scheduler.log | grep "合约数据采集完成"

# 在另一个终端,每30秒检查一次数据库
watch -n 30 'mysql -h localhost -u admin -p"Tonny@1000" -D binance-data -e "SELECT exchange, COUNT(*) as cnt, MAX(timestamp), TIMESTAMPDIFF(SECOND, MAX(timestamp), UTC_TIMESTAMP()) as delay FROM price_data WHERE timestamp >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 MINUTE) GROUP BY exchange"'
```

## 🎯 问题根因分析

### 为什么Scheduler显示在运行但不写入数据?

可能原因 (按概率排序):

1. **数据库连接失败** (最可能)
   - 网络问题
   - 连接池耗尽
   - 权限问题
   - MySQL服务器负载过高

2. **异常被静默捕获**
   - db_service.save_*() 返回False但scheduler没有记录
   - SQLAlchemyError被捕获但只打印到debug级别

3. **事务未提交**
   - session.commit() 失败但没有抛出异常
   - 自动提交被禁用

4. **Scheduler进程僵死**
   - 进程存在但主循环卡住
   - 某个任务阻塞了整个调度器

### 如何确认根因?

```bash
# 1. 检查scheduler日志中的ERROR
grep -i error logs/scheduler.log | tail -50

# 2. 检查数据库连接错误
grep -i "database\|connection\|mysql\|sqlalchemy" logs/scheduler.log | tail -50

# 3. 检查最后一次成功写入的时间
mysql -h localhost -u admin -p'Tonny@1000' -D binance-data -e "
SELECT exchange, MAX(timestamp), COUNT(*)
FROM price_data
GROUP BY exchange;
"

# 4. 检查scheduler进程状态
ps -p $(pgrep -f scheduler.py) -o pid,ppid,cmd,%cpu,%mem,etime,stat

# 5. 检查数据库连接数
mysql -h localhost -u admin -p'Tonny@1000' -e "SHOW PROCESSLIST;" | grep binance-data | wc -l
```

## 📋 完整修复清单

- [ ] 修改MySQL服务器时区为UTC+0
- [ ] 重启MySQL服务
- [ ] 验证时区修改成功
- [ ] 停止当前scheduler进程
- [ ] 启动新的scheduler进程
- [ ] 验证scheduler开始写入数据
- [ ] 修复scheduler.py Line 392: datetime.now() → datetime.utcnow()
- [ ] 修复check_actual_data.py: NOW() → UTC_TIMESTAMP()
- [ ] 测试并提交代码
- [ ] 在服务器上拉取最新代码
- [ ] 重启scheduler服务
- [ ] 监控10分钟确认稳定

## 🔍 故障排查命令速查

```bash
# 快速诊断一键脚本
cat > /tmp/quick_diag.sh << 'EOF'
#!/bin/bash
echo "=== Scheduler进程 ==="
ps aux | grep scheduler.py | grep -v grep

echo -e "\n=== Scheduler最新日志 (最近10行) ==="
tail -10 ~/crypto-analyzer/logs/scheduler.log

echo -e "\n=== 数据库最新数据 ==="
mysql -h localhost -u admin -p'Tonny@1000' -D binance-data -e "
SELECT
    exchange,
    MAX(timestamp) as latest,
    TIMESTAMPDIFF(SECOND, MAX(timestamp), UTC_TIMESTAMP()) as delay_sec
FROM price_data
GROUP BY exchange;
"

echo -e "\n=== MySQL时区 ==="
mysql -h localhost -u admin -p'Tonny@1000' -e "SELECT NOW(), UTC_TIMESTAMP();"
EOF

chmod +x /tmp/quick_diag.sh
/tmp/quick_diag.sh
```

## 预期修复效果

修复完成后:

1. ✅ MySQL时区统一为UTC+0
2. ✅ Scheduler正常写入数据
3. ✅ 数据延迟 <15秒
4. ✅ 无DATA_STALE误报
5. ✅ 所有时间戳使用UTC

