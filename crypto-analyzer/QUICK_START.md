# 🚀 超级大脑快速启动指南

## 📋 一键部署（复制粘贴即可）

### 步骤1: 远程服务器部署

```bash
# SSH登录
ssh user@13.212.252.171

# 进入项目目录（替换为你的实际路径）
cd /root/crypto-analyzer  # 或你的实际路径

# 拉取最新代码
git pull origin master

# 停止旧服务
pkill -f smart_trader_service.py
sleep 3

# 重启服务
nohup python3 smart_trader_service.py > logs/smart_trader_$(date +%Y%m%d_%H%M%S).log 2>&1 &

# 查看日志（按Ctrl+C退出）
tail -f logs/smart_trader_*.log
```

### 步骤2: 验证部署

```bash
# 运行验证脚本
python3 verify_deployment.py

# 如果看到 "🎉 所有检查通过！超级大脑已完全激活！"，说明部署成功
```

### 步骤3: 设置定时任务

```bash
# 编辑crontab
crontab -e

# 复制粘贴以下内容（记得修改路径）
# 将 /root/crypto-analyzer 替换为你的实际路径

# 市场观察 - 每5分钟
*/5 * * * * cd /root/crypto-analyzer && python3 run_market_observer.py >> logs/market_observer.log 2>&1

# 市场状态 - 每6小时
0 */6 * * * cd /root/crypto-analyzer && python3 run_market_regime_analysis.py >> logs/regime_analysis.log 2>&1

# 权重优化 - 每天凌晨2点
0 2 * * * cd /root/crypto-analyzer && python3 safe_weight_optimizer.py >> logs/weight_optimizer.log 2>&1

# 重启服务 - 每天凌晨2:05
5 2 * * * pkill -f smart_trader_service.py && sleep 2 && cd /root/crypto-analyzer && nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &

# 高级优化 - 每3天凌晨3点
0 3 */3 * * cd /root/crypto-analyzer && echo "y" | python3 run_advanced_optimization.py >> logs/advanced_optimizer.log 2>&1

# 重启服务 - 每3天凌晨3:10
10 3 */3 * * pkill -f smart_trader_service.py && sleep 2 && cd /root/crypto-analyzer && nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &

# 每日报告 - 每天早上8点
0 8 * * * cd /root/crypto-analyzer && python3 analyze_smart_brain_2days.py > logs/daily_report_$(date +\%Y\%m\%d).txt 2>&1

# 保存并退出（vim: 按ESC，输入:wq，按回车）
```

## 📊 日常监控命令

### 每天早上检查（9:00）

```bash
# 1. 查看昨天的表现
python3 analyze_smart_brain_2days.py

# 2. 查看权重优化日志
ls -lt logs/weight_optimization/adjustment_summary_*.txt | head -1 | xargs cat

# 3. 查看市场观察
tail -20 logs/market_report_*.txt | tail -20
```

### 实时监控

```bash
# 监控交易活动
tail -f logs/smart_trader_*.log | grep -E "开仓|平仓"

# 监控所有活动
tail -f logs/smart_trader_*.log
```

### 检查定时任务是否运行

```bash
# 查看cron任务列表
crontab -l

# 查看权重优化日志
tail -50 logs/weight_optimizer.log

# 查看市场观察日志
tail -50 logs/market_observer.log

# 查看高级优化日志
tail -50 logs/advanced_optimizer.log
```

## 🔍 数据库查询

### 快速查询最新数据

```bash
# 登录MySQL
mysql -h 13.212.252.171 -u binance -p binance-data

# 或者一行命令
mysql -h 13.212.252.171 -u binance -pSHbin@110 binance-data
```

```sql
-- 查看最新10笔交易
SELECT
    symbol, side, entry_score, signal_components,
    open_price, close_price, realized_pnl, open_time
FROM futures_positions
WHERE source = 'smart_trader'
ORDER BY open_time DESC
LIMIT 10;

-- 查看今天的胜率
SELECT
    COUNT(*) as total,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100 as win_rate,
    SUM(realized_pnl) as total_pnl
FROM futures_positions
WHERE source = 'smart_trader'
    AND status = 'closed'
    AND DATE(open_time) = CURDATE();

-- 查看各交易对表现
SELECT
    symbol,
    COUNT(*) as trades,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100 as win_rate,
    SUM(realized_pnl) as total_pnl,
    AVG(realized_pnl) as avg_pnl
FROM futures_positions
WHERE source = 'smart_trader'
    AND status = 'closed'
    AND open_time >= DATE_SUB(NOW(), INTERVAL 2 DAY)
GROUP BY symbol
ORDER BY total_pnl DESC
LIMIT 20;

-- 查看当前市场状态
SELECT * FROM market_regime_states
ORDER BY timestamp DESC
LIMIT 1;
```

## ⚠️ 常见问题

### Q1: signal_components 显示为 NULL

**原因**: 服务还在使用旧代码

**解决**:
```bash
cd /root/crypto-analyzer
git pull
pkill -f smart_trader_service.py
sleep 3
nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &
```

### Q2: 市场观察没有数据

**原因**: cron任务未运行或脚本有错误

**解决**:
```bash
# 手动运行一次
python3 run_market_observer.py

# 检查是否有错误
# 如果成功，设置cron任务
crontab -e
# 添加: */5 * * * * cd /root/crypto-analyzer && python3 run_market_observer.py >> logs/market_observer.log 2>&1
```

### Q3: 权重优化失败

**原因**: 数据不足或数据库连接问题

**解决**:
```bash
# 查看详细日志
tail -100 logs/weight_optimizer.log

# 查看错误文件
ls logs/weight_optimization/ERROR_*.txt

# 手动运行测试
python3 safe_weight_optimizer.py
```

### Q4: 服务意外停止

**原因**: 内存不足、异常退出等

**解决**:
```bash
# 查看最后的日志
tail -100 logs/smart_trader_*.log

# 重启服务
nohup python3 smart_trader_service.py > logs/smart_trader_$(date +%Y%m%d_%H%M%S).log 2>&1 &

# 验证运行中
ps aux | grep smart_trader_service.py
```

### Q5: 定时任务不执行

**原因**: cron服务未启动或路径错误

**解决**:
```bash
# 检查cron服务状态
systemctl status cron  # 或 crond

# 如果未运行，启动它
systemctl start cron

# 检查cron日志
grep CRON /var/log/syslog  # Debian/Ubuntu
# 或
tail -f /var/log/cron  # CentOS/RedHat

# 确保路径正确
crontab -l  # 查看当前任务，检查路径是否正确
```

## 📈 性能目标

### 第1周目标（2026-01-28前）
- [x] 部署所有优化
- [ ] 胜率提升到 30%+
- [ ] 日均亏损 < $50
- [ ] 优秀交易对盈利增加 30%+

### 第1月目标（2026-02-21前）
- [ ] 胜率稳定在 35%+
- [ ] 月度盈亏平衡
- [ ] 淘汰5-10个差劲交易对

### 第3月目标（2026-04-21前）
- [ ] 胜率达到 40%+
- [ ] 月度盈利 $500+
- [ ] 完全自适应运行

## 🎯 成功标志

当你看到这些日志，说明系统完全激活：

```
[INFO] 📊 加载信号权重: position_low=23, momentum_down_3pct=16, volatility_high=5, ...
[INFO] 📊 加载交易对风险参数: XMR/USDT TP=7.5% SL=3.0% 倍数=1.5x
[INFO] 📊 市场状态: BULL_MARKET | 强度: 76.5 | 倾向: LONG
[INFO] 🎯 开仓成功: BTC/USDT LONG
[INFO] 📊 entry_score: 28, 调整后: 23 (市场加成-5)
[INFO] 📊 signal_components: {"position_low": 23, "momentum_down_3pct": 16, "volatility_high": 5}
[INFO] 📊 止盈: 5.0%, 止损: -2.0%
```

## 📚 完整文档

- [FINAL_DEPLOYMENT_CHECKLIST.md](FINAL_DEPLOYMENT_CHECKLIST.md) - 详细部署清单
- [README_FINAL_SUMMARY.md](README_FINAL_SUMMARY.md) - 完整优化总结
- [MARKET_REGIME_INTEGRATION_GUIDE.md](MARKET_REGIME_INTEGRATION_GUIDE.md) - 市场状态集成
- [MARKET_OBSERVER_INTEGRATION.md](MARKET_OBSERVER_INTEGRATION.md) - 市场观察说明
- [AGGRESSIVE_OPTIMIZATION_SCHEDULE.md](AGGRESSIVE_OPTIMIZATION_SCHEDULE.md) - 优化频率分析

## 🆘 需要帮助？

如果遇到问题：

1. 运行 `python3 verify_deployment.py` 查看哪里有问题
2. 查看对应的日志文件
3. 参考上面的常见问题解决方案
4. 检查数据库连接是否正常

---

**准备好了吗？一起开启智能交易新时代！** 🚀

*版本: v3.0 - Full Adaptive System*
*更新时间: 2026-01-21*
