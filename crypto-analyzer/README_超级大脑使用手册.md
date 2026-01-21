# 超级大脑使用手册

## 🎯 简单说明

超级大脑已经完成了全方位自适应优化，包括：
- ✅ 信号权重自动优化（12个组件）
- ✅ 止盈止损动态调整（全局+每个交易对）
- ✅ 动态仓位分配（根据表现调整）
- ✅ 市场观察和状态管理（6小时分析）

**你只需要运行主服务，其他都是自动的！**

## 🚀 如何使用

### 1. 启动服务

```bash
# SSH到服务器
ssh user@13.212.252.171

# 进入项目目录
cd /root/crypto-analyzer  # 替换为你的实际路径

# 拉取最新代码
git pull

# 启动服务
nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &

# 查看日志
tail -f logs/smart_trader.log
```

### 2. 每天查看表现

```bash
# 运行每日分析脚本
python3 analyze_smart_brain_2days.py
```

就这么简单！

## 📊 系统自动做什么

`smart_trader_service.py` 已经集成了所有优化功能，运行后会自动：

1. **加载所有配置**
   - 信号权重配置（从 signal_scoring_weights 表）
   - 每个交易对的止盈止损（从 symbol_risk_params 表）
   - 每个交易对的仓位倍数（从 symbol_risk_params 表）

2. **实时交易决策**
   - 分析市场信号
   - 记录 signal_components（每个信号的贡献）
   - 根据配置自动开仓、止盈、止损

3. **数据记录**
   - 所有交易记录到 futures_positions 表
   - 包含 entry_score 和 signal_components 字段
   - 供后续优化分析使用

## 🔄 如何优化配置

### 方法1: 手动优化（推荐）

当你觉得需要优化时，手动运行：

```bash
# 优化信号权重（建议每3-7天运行一次）
python3 safe_weight_optimizer.py

# 优化止盈止损和仓位（建议每7-14天运行一次）
python3 run_advanced_optimization.py

# 优化后重启服务加载新配置
pkill -f smart_trader_service.py
sleep 2
nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &
```

### 方法2: 设置定时自动优化（可选）

如果你想完全自动化，可以设置cron任务：

```bash
crontab -e

# 添加以下任务
# 每天凌晨2点优化权重
0 2 * * * cd /root/crypto-analyzer && python3 safe_weight_optimizer.py >> logs/weight_optimizer.log 2>&1

# 每天凌晨2:05重启服务
5 2 * * * pkill -f smart_trader_service.py && sleep 2 && cd /root/crypto-analyzer && nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &

# 每周日凌晨3点优化止盈止损
0 3 * * 0 cd /root/crypto-analyzer && echo "y" | python3 run_advanced_optimization.py >> logs/advanced_optimizer.log 2>&1
```

## 📈 查看表现

### 每日分析

```bash
# 查看最近2天的交易表现
python3 analyze_smart_brain_2days.py
```

输出示例：
```
╔══════════════════════════════════════════════════════════╗
║        超级大脑交易表现分析 (最近2天)                    ║
╚══════════════════════════════════════════════════════════╝

📊 总体统计
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
总交易数         : 632
盈利交易         : 164 (25.9%)
亏损交易         : 468 (74.1%)
总盈利           : $1,234.56
总亏损           : -$3,200.78
净盈亏           : -$1,966.22

💎 表现最好的交易对 (Top 10)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
XMR/USDT    : 18笔 | 55.6%胜率 | +$142.35
UNI/USDT    : 8笔  | 75.0%胜率 | +$90.12
ETC/USDT    : 9笔  | 66.7%胜率 | +$63.45
...
```

### 实时监控

```bash
# 查看实时交易
tail -f logs/smart_trader.log | grep -E "开仓|平仓"

# 查看所有日志
tail -f logs/smart_trader.log
```

## 🎯 性能目标

| 时间段 | 胜率目标 | 盈亏目标 | 当前状态 |
|--------|---------|---------|---------|
| 第1周  | 30%     | 日亏损<$50 | 25.9%胜率 |
| 第1月  | 35%     | 盈亏平衡  | 需优化 |
| 第3月  | 40%     | 月盈利$500+ | 目标 |

## 🔍 验证部署

运行验证脚本检查所有配置是否正确：

```bash
python3 verify_deployment.py
```

如果看到 "🎉 所有检查通过！" 说明一切正常。

## 📊 数据库查询

### 快速查看最新交易

```sql
-- 登录数据库
mysql -h 13.212.252.171 -u binance -pSHbin@110 binance-data

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
```

## ⚠️ 常见问题

### Q: signal_components显示NULL怎么办？

A: 确保已经拉取最新代码并重启服务：
```bash
git pull
pkill -f smart_trader_service.py
nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &
```

### Q: 胜率一直很低怎么办？

A: 运行优化脚本：
```bash
python3 safe_weight_optimizer.py
python3 run_advanced_optimization.py
# 然后重启服务
```

### Q: 如何知道优化有没有生效？

A: 查看日志中的配置加载信息：
```bash
tail -100 logs/smart_trader.log | grep -E "权重|止盈|止损|倍数"
```

应该看到类似：
```
[INFO] 📊 加载信号权重: position_low=23, momentum_down_3pct=16, ...
[INFO] 📊 加载交易对风险参数: XMR/USDT TP=7.5% SL=3.0% 倍数=1.5x
```

## 📚 核心文件说明

### 必须运行的
- `smart_trader_service.py` - 主交易服务（唯一需要一直运行的）

### 每天查看的
- `analyze_smart_brain_2days.py` - 每日分析脚本

### 定期优化的（可选）
- `safe_weight_optimizer.py` - 信号权重优化（建议每3-7天）
- `run_advanced_optimization.py` - 止盈止损优化（建议每7-14天）

### 验证工具
- `verify_deployment.py` - 部署验证脚本

### 其他可选脚本
- `run_market_observer.py` - 市场观察（可选，用于收集市场数据）
- `run_market_regime_analysis.py` - 市场状态分析（可选）

## 🎊 总结

**超级大脑使用就这么简单：**

1. 启动服务：`nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &`
2. 每天查看：`python3 analyze_smart_brain_2days.py`
3. 定期优化（可选）：`python3 safe_weight_optimizer.py` + 重启服务

其他都是自动的！系统会自己学习和进化！🚀

---

*版本: v3.0*
*更新时间: 2026-01-21*
