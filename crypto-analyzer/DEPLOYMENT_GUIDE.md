# 部署指南 - Scheduler 性能优化

## 📦 本次更新内容

### Commit 1: `235f04e` - 修复止盈/止损平仓问题
- **问题**: 止盈/止损使用预设价格而非实时市场价格平仓
- **影响**: 每笔订单损失2-5%的额外盈利
- **修复**: 移除 `close_price` 参数,使用实时市场价格
- **文件**: `app/trading/stop_loss_monitor.py`

### Commit 2: `085fbd9` - 优化 Scheduler 数据采集性能
- **问题**: 任务执行时间(19.5秒) >> 调度间隔(5秒),导致任务堆积
- **优化**:
  1. 减少延迟: 0.5秒 → 0.1秒
  2. 增加间隔: 5秒 → 10秒
  3. 添加监控: 记录执行时间,超过8秒发出警告
- **效果**: 采集速度提升5倍 (19.5秒 → 3.9秒)
- **文件**: `app/scheduler.py`

## 🚀 服务器部署步骤

### 1. 检查当前运行的服务

```bash
# SSH 到服务器
ssh ec2-user@your-server-ip

# 检查正在运行的服务
ps aux | grep -E "scheduler|stop_loss_monitor|smart_trader"
```

### 2. 更新代码

```bash
cd /path/to/crypto-analyzer
git pull origin master
```

**预期输出**:
```
From github.com:shanglily01-lab/test2
   235f04e..085fbd9  master     -> origin/master
Updating 235f04e..085fbd9
Fast-forward
 app/scheduler.py              | 15 +++--
 app/trading/stop_loss_monitor.py | 4 +-
 PNL_ISSUE_ANALYSIS.md         | 250 ++++++++
 SCHEDULER_OPTIMIZATION.md     | 243 ++++++++
```

### 3. 重启受影响的服务

#### A. 重启 Scheduler (必须)

```bash
# 查找 scheduler 进程
ps aux | grep "python.*scheduler.py"

# 假设 PID 是 123456
kill 123456

# 等待进程结束
sleep 2

# 重启服务
nohup python3 app/scheduler.py > logs/scheduler.log 2>&1 &

# 验证启动成功
tail -f logs/scheduler.log
```

**预期日志**:
```
[HH:MM:SS] 开始采集币安合约数据...
  ✓ 合约数据采集完成: 成功 39/39, 失败 0, 耗时 3.9秒
```

如果看到 **耗时 <5秒**,说明优化生效! ✅

#### B. 重启止损监控服务 (如果在运行)

```bash
# 查找 stop_loss_monitor 进程
ps aux | grep "stop_loss_monitor"

# 如果有进程在运行,重启它
kill <PID>
nohup python3 app/trading/stop_loss_monitor.py > logs/stop_loss_monitor.log 2>&1 &
```

#### C. Smart Trader Service (如果在运行)

```bash
# 查找 smart_trader_service 进程
ps aux | grep "smart_trader_service"

# 重启 (如果需要)
kill <PID>
nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &
```

### 4. 验证优化效果

#### 监控 Scheduler 日志 (10分钟)

```bash
tail -f logs/scheduler.log | grep "合约数据采集完成"
```

**预期输出**:
```
[14:20:35] 开始采集币安合约数据...
  ✓ 合约数据采集完成: 成功 39/39, 失败 0, 耗时 3.8秒
[14:20:45] 开始采集币安合约数据...
  ✓ 合约数据采集完成: 成功 39/39, 失败 0, 耗时 4.1秒
[14:20:55] 开始采集币安合约数据...
  ✓ 合约数据采集完成: 成功 39/39, 失败 0, 耗时 3.9秒
```

**关键指标**:
- ✅ **耗时 <5秒**: 优化成功
- ⚠️  **耗时 5-8秒**: 可接受,继续观察
- ❌ **耗时 >8秒**: 出现警告,需要进一步优化

#### 检查数据新鲜度

运行诊断脚本 (在服务器上):

```bash
# 创建诊断脚本 (如果还没有)
cat > check_data_freshness.py << 'EOF'
import pymysql
from datetime import datetime

db_config = {
    'host': 'localhost',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data',
    'charset': 'utf8mb4'
}

conn = pymysql.connect(**db_config)
cursor = conn.cursor(pymysql.cursors.DictCursor)

# 检查最近1分钟的价格数据
cursor.execute("""
    SELECT
        COUNT(DISTINCT symbol) as symbol_count,
        MAX(timestamp) as latest_timestamp,
        TIMESTAMPDIFF(SECOND, MAX(timestamp), NOW()) as seconds_ago
    FROM price_data
    WHERE exchange = 'binance_futures'
    AND timestamp >= DATE_SUB(NOW(), INTERVAL 1 MINUTE)
""")

result = cursor.fetchone()

print(f"最近1分钟的数据:")
print(f"  交易对数: {result['symbol_count']}")
print(f"  最新数据: {result['seconds_ago']}秒前")

if result['seconds_ago'] < 15:
    print(f"  ✅ 数据非常新鲜 (<15秒)")
elif result['seconds_ago'] < 60:
    print(f"  ⚠️  数据略有延迟 (15-60秒)")
else:
    print(f"  ❌ 数据延迟严重 (>60秒)")

cursor.close()
conn.close()
EOF

python3 check_data_freshness.py
```

### 5. 监控告警

如果日志中出现以下警告:

```
⚠️  合约数据采集耗时过长: 12.5秒 (预期 <8秒)
```

**可能原因**:
1. 网络延迟增加
2. API 响应变慢
3. 服务器负载过高

**解决方案**:
- 检查网络连接: `ping api.binance.com`
- 检查服务器负载: `top`, `htop`
- 如果持续超时,考虑进一步增加间隔到20秒

## 📊 性能对比

| 指标 | 优化前 | 优化后 | 改善 |
|------|--------|--------|------|
| 单次采集耗时 | 19.5秒 | 3.9秒 | **5倍** |
| 采集间隔 | 5秒 | 10秒 | - |
| 任务堆积 | ❌ 严重 | ✅ 无 | - |
| 数据新鲜度 | ⚠️  延迟 | ✅ 及时 | - |

## 🔧 故障排查

### 问题1: 耗时仍然很长 (>10秒)

**检查**:
```bash
# 查看实际延迟设置
grep "await asyncio.sleep" app/scheduler.py | grep -A 2 -B 2 "0.1"
```

**应该看到**:
```python
await asyncio.sleep(0.1)
```

如果还是 `0.5`,说明代码没有更新成功。

### 问题2: 数据采集频率降低

这是预期的!从5秒降到10秒,但数据质量更高,不会堆积延迟。

如果需要更高频率,可以在确认耗时稳定<5秒后,改回5秒间隔:

```python
# app/scheduler.py Line 969
schedule.every(5).seconds.do(  # 改回5秒
```

### 问题3: 止盈止损 PnL 仍然不准确

**检查** `stop_loss_monitor.py` 是否重启:

```bash
ps aux | grep stop_loss_monitor
# 如果没有进程,说明没有运行该服务
# 检查是否在 main.py 或其他地方启动
```

**验证修复**:
等待新的止盈/止损订单,查看数据库中的 `realized_pnl` 和 `mark_price` 是否一致。

## 📝 后续优化计划

如果 3.9秒 的性能仍不满足需求,可以实施并发采集方案 (详见 `SCHEDULER_OPTIMIZATION.md` 方案C):

- **并发采集**: 将耗时降低到 1-2秒
- **支持5秒高频**: 采集间隔可恢复到5秒甚至更短
- **代码改动**: 需要重构 `collect_binance_futures_data` 方法

## ✅ 部署检查清单

- [ ] SSH 到服务器
- [ ] 运行 `git pull origin master`
- [ ] 重启 `scheduler.py` 服务
- [ ] 重启 `stop_loss_monitor.py` (如果在运行)
- [ ] 监控日志 10 分钟,确认耗时 <5秒
- [ ] 运行数据新鲜度检查
- [ ] 等待新订单验证 PnL 计算正确

---

## 🚨 常见问题排查

### 问题: K线数据过时 (DATA_STALE 警告)

**症状**:
```
[DATA_STALE] XXX/USDT K线数据过时! 最新K线时间: 13.9分钟前
```

**原因**: scheduler 服务未运行或采集失败

**诊断步骤**: 详见 [check_scheduler_status.md](check_scheduler_status.md)

**快速检查**:
```bash
# 1. 检查 scheduler 是否在运行
ps aux | grep scheduler.py

# 2. 如果没有进程,启动它
cd /path/to/crypto-analyzer
nohup python3 app/scheduler.py > logs/scheduler.log 2>&1 &

# 3. 监控日志
tail -f logs/scheduler.log | grep "合约数据采集完成"
```

**预期恢复时间**: 1-2分钟后数据应该更新

---

**部署时间**: 预计 10-15 分钟
**停机时间**: 约 5-10 秒 (重启服务期间)
**回滚方案**: `git reset --hard 235f04e` 然后重启服务

## 📝 更新日志

### 2026-01-20
- `f6415e5` - 修复 quantity 未定义错误
- `085fbd9` - 优化 scheduler 性能 (5倍速度提升)
- `235f04e` - 修复止盈止损价格问题
