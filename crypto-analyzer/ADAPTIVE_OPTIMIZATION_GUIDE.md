# 自适应优化系统使用指南

## 🧠 概述

自适应优化器是超级大脑的**自我学习和自我优化模块**，能够：

1. **自动识别问题交易对** - 根据实盘表现动态加入黑名单
2. **自动识别问题信号** - 发现哪些信号类型+方向组合表现差
3. **生成优化建议** - 针对问题提供具体的优化方案
4. **自动应用优化** - 可选自动更新配置文件

## 📊 优化器能力

### 1. 交易对黑名单管理

**自动识别条件**：
- 单个交易对亏损超过 **-$20**
- 胜率低于 **10%**
- 最少需要 **5笔订单**样本

**示例输出**：
```
🚫 建议加入黑名单的交易对
  • VIRTUAL/USDT  - 亏损$-573.24, 胜率0.0%
  • WIF/USDT      - 亏损$-397.69, 胜率22.2%
  • NIGHT/USDT    - 亏损$-249.81, 胜率10.0%
```

### 2. 信号性能分析

**检测维度**：
- 信号类型 (SMART_BRAIN_15/20/30/45/60)
- 交易方向 (LONG/SHORT)
- 订单数量、胜率、总盈亏
- 平均持仓时间

**问题信号识别**：
- 信号+方向亏损超过 **-$100**
- 高严重性: 亏损超过 **-$500**

**示例输出**：
```
🔴 SMART_BRAIN_20 - LONG
  订单数: 60
  胜率: 8.3%
  总盈亏: $-1026.91
  平均持仓: 24分钟
  建议: 增加最小持仓时间到120分钟; 放宽止损到4%; 降低仓位到50%或暂时禁用
```

### 3. 智能建议生成

针对不同问题提供具体建议：

**做多(LONG)信号问题**：
- 持仓时间<90分钟 → 增加最小持仓时间到120分钟
- 胜率<15% → 放宽止损到4%
- 亏损>$500 → 降低仓位到50%或暂时禁用

**做空(SHORT)信号问题**：
- 亏损>$100 → 检查信号逻辑

**高分信号问题**：
- 高分信号亏损严重 → 降低该分数信号的权重

## 🚀 使用方法

### 方法1: 手动运行（推荐用于测试）

```bash
cd /path/to/crypto-analyzer
python test_optimizer.py
```

查看报告后，可以选择是否应用优化。

### 方法2: 作为模块调用

```python
from app.services.adaptive_optimizer import AdaptiveOptimizer

# 创建优化器
optimizer = AdaptiveOptimizer(db_config)

# 生成24小时报告
report = optimizer.generate_optimization_report(hours=24)

# 打印报告
optimizer.print_report(report)

# 自动应用优化（会更新config.yaml）
results = optimizer.apply_optimizations(report, auto_apply=True)
```

### 方法3: 定时任务（生产环境推荐）

在 `smart_trader_service.py` 或 `scheduler.py` 中添加定时任务：

```python
# 每天凌晨2点运行优化
schedule.every().day.at("02:00").do(
    lambda: run_adaptive_optimization()
)

def run_adaptive_optimization():
    """运行自适应优化"""
    try:
        optimizer = AdaptiveOptimizer(db_config)
        report = optimizer.generate_optimization_report(hours=24)

        # 检查是否有高严重性问题
        high_severity_count = report['summary']['high_severity_issues']

        if high_severity_count > 0:
            logger.warning(f"🔴 发现{high_severity_count}个高严重性问题!")
            optimizer.print_report(report)

            # 发送通知
            send_telegram_notification(
                f"⚠️ 超级大脑发现{high_severity_count}个高严重性问题，请查看日志"
            )

        # 自动应用黑名单优化
        results = optimizer.apply_optimizations(report, auto_apply=True)

        if results['blacklist_added']:
            logger.info(f"✅ 自动添加{len(results['blacklist_added'])}个交易对到黑名单")
            # 重启智能交易服务以应用新配置
            # restart_smart_trader()

    except Exception as e:
        logger.error(f"❌ 自适应优化失败: {e}")
```

## 📝 配置参数

可以在初始化时自定义阈值：

```python
optimizer = AdaptiveOptimizer(db_config)

# 自定义阈值
optimizer.thresholds = {
    'min_orders_for_analysis': 10,       # 最少10笔订单才分析
    'blacklist_loss_threshold': -50,     # 亏损超过50才加入黑名单
    'blacklist_win_rate_threshold': 0.05, # 胜率低于5%才加入黑名单
    'signal_direction_loss_threshold': -200,  # 信号亏损超过200才报告
    'long_stop_loss_multiplier': 3.0,    # 做多止损放宽3倍
    'min_holding_time_long': 180,        # 做多最小持仓3小时
}
```

## 🎯 最佳实践

### 1. 初期（前1周）

- **每天手动运行** 查看报告
- **不要自动应用** 优化，先观察建议是否合理
- **记录每次优化** 的效果

### 2. 稳定期（1周后）

- **每天凌晨自动运行**
- **自动应用黑名单** 优化
- **手动审核信号** 优化建议
- **设置Telegram通知** 有高严重性问题时告警

### 3. 优化策略

**快速止血**:
```python
# 发现高严重性问题时立即应用
if report['summary']['high_severity_issues'] > 0:
    results = optimizer.apply_optimizations(report, auto_apply=True)
    # 立即重启服务
    restart_services()
```

**渐进优化**:
```python
# 先观察3天，确认问题持续存在再应用
if check_problem_persists(days=3):
    results = optimizer.apply_optimizations(report, auto_apply=True)
```

## 📈 效果监控

### 监控指标

1. **黑名单效果**
   - 新增黑名单交易对数量
   - 黑名单交易对的历史亏损
   - 移除黑名单后的盈利改善

2. **信号优化效果**
   - 优化前后胜率对比
   - 优化前后盈亏对比
   - 优化前后平均持仓时间

### 监控脚本

```bash
# 查看最近优化记录
grep "自适应优化" logs/smart_trader.log | tail -20

# 查看黑名单变化
git diff config.yaml | grep blacklist -A 10
```

## 🛠️ 故障排查

### 问题1: 优化器运行失败

**检查**:
```bash
python test_optimizer.py
```

**常见原因**:
- 数据库连接失败
- 数据不足（需要至少5笔订单）
- 配置文件权限问题

### 问题2: 黑名单未生效

**检查**:
```bash
# 1. 确认黑名单已更新
cat config.yaml | grep -A 10 blacklist

# 2. 确认服务已重启
ps aux | grep smart_trader

# 3. 查看日志确认黑名单检查
tail -f logs/smart_trader.log | grep "黑名单"
```

### 问题3: 优化建议不合理

**调整阈值**:
```python
# 提高分析门槛
optimizer.thresholds['min_orders_for_analysis'] = 10
optimizer.thresholds['blacklist_loss_threshold'] = -50
```

## 📊 实际案例

### 案例1: 2026-01-20首次运行

**发现问题**:
- 12个交易对需要加入黑名单
- 3个高严重性信号问题（LONG方向）
- SMART_BRAIN_20 LONG亏损$-1026.91

**应用优化**:
```python
results = optimizer.apply_optimizations(report, auto_apply=True)
# 新增黑名单: 12个
# 预期效果: 净利润提升 $1000+
```

**后续监控**:
- 24小时后观察新订单
- 确认黑名单交易对不再开仓
- 观察LONG订单是否减少

## 🔮 未来扩展

### 计划功能

1. **自动调整止损**
   - 根据波动率动态调整
   - 不同交易对不同止损

2. **自动调整仓位**
   - 表现好的信号增加仓位
   - 表现差的信号降低仓位

3. **信号权重自适应**
   - 动态调整各信号的评分权重
   - 学习最优权重组合

4. **时间窗口优化**
   - 识别最佳交易时间段
   - 避开高风险时间段

5. **交易对动态评级**
   - A级: 表现最好，优先交易
   - B级: 表现一般，正常交易
   - C级: 表现较差，降低仓位
   - D级: 加入黑名单

## 📚 相关文档

- [BLACKLIST_UPDATE_2026-01-20.md](BLACKLIST_UPDATE_2026-01-20.md) - 黑名单更新记录
- [analyze_long_failures.py](analyze_long_failures.py) - 做多失败分析
- [config.yaml](config.yaml) - 配置文件

---

**更新时间**: 2026-01-20
**版本**: 1.0
**状态**: ✅ 已测试，可用于生产环境
