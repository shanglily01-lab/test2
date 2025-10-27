# Hyperliquid 聪明钱包监控升级指南

## 🎯 升级内容

已成功实施 **Hyperliquid 聪明钱包分级监控**，将监控能力从 **1个配置地址** 提升到 **8000+个数据库钱包**！

## ✅ 已完成的改动

### 1. 数据库服务增强
- ✅ 新增 `get_monitored_wallets_by_priority()` 方法
- ✅ 支持按 PnL、ROI、交易活跃度排序获取钱包

### 2. 采集器升级
- ✅ `monitor_all_addresses()` 支持从数据库读取钱包
- ✅ 支持5种优先级模式：high, medium, low, all, config

### 3. 调度器优化
- ✅ 高优先级钱包：每5分钟监控200个
- ✅ 中优先级钱包：每1小时监控500个
- ✅ 全量扫描：每6小时监控8000+个

### 4. 测试工具
- ✅ 创建 `test_hyperliquid_priority.py` 验证功能
- ✅ 自动检测钱包数量、预估效率、给出建议

## 📋 使用步骤

### 第1步：运行测试脚本

```bash
cd /home/tonny/code/test2/crypto-analyzer
python test_hyperliquid_priority.py
```

**检查内容**：
- 数据库中有多少监控钱包？
- 高/中/低优先级分别有多少？
- 预估监控耗时和数据量

### 第2步：确认配置

检查 `config.yaml` 中的配置：

```yaml
hyperliquid:
  enabled: true  # 确保启用
  min_trade_usd: 10000  # 建议从50K降低到10K
```

### 第3步：重启系统

```bash
# 停止当前运行
Ctrl+C

# 重新启动
python run.py
```

### 第4步：观察日志

启动后你会看到类似日志：

```
✓ Hyperliquid 高优先级钱包 (200个) - 每 5 分钟
✓ Hyperliquid 中优先级钱包 (500个) - 每 1 小时
✓ Hyperliquid 全量扫描 (8000+个) - 每 6 小时

[10:05:00] 开始监控 Hyperliquid 聪明钱包 (优先级: high)...
从数据库加载 200 个高优先级地址 (PnL>10K, ROI>50%, 7天内活跃)
开始监控 200 个地址, 回溯 1 小时
  进度: 50/200 个地址已监控
  进度: 100/200 个地址已监控
  进度: 150/200 个地址已监控
  进度: 200/200 个地址已监控
监控完成: 200 个地址
  本次监控: 200 个地址
  发现 45 笔交易, 120 个持仓
```

## 📊 预期效果

### 数据量提升

| 指标 | 升级前 | 升级后 | 提升 |
|------|--------|--------|------|
| 监控钱包数 | 1个 | 8000+个 | 8000倍 |
| 高优先级实时性 | - | 5分钟 | 新增 |
| 每日交易数据 | ~50条 | 2000-5000条 | 40-100倍 |

### 监控策略 (假设8000个钱包)

| 任务 | 频率 | 钱包数 | 监控耗时 |
|------|------|--------|---------|
| 高优先级 | 5分钟 | 200 | ~3分钟 |
| 中优先级 | 1小时 | 500 | ~8分钟 |
| 全量扫描 | 6小时 | 8000 | ~2小时 |

## ⚠️ 注意事项

### 1. 如果数据库钱包不足

如果测试脚本显示钱包数量很少（<100个）：

```bash
# 检查数据库
mysql -u root -p'Shang@2019' -D 'binance-data' -e "
SELECT COUNT(*) FROM hyperliquid_monitored_wallets WHERE is_monitoring=1;
"
```

如果钱包太少，需要先运行 Hyperliquid 排行榜采集：
```bash
# 运行排行榜采集，自动发现优秀交易者
python -c "from app.scheduler import UnifiedDataScheduler; import asyncio; s = UnifiedDataScheduler(); asyncio.run(s.collect_hyperliquid_leaderboard())"
```

### 2. 如果没有高优先级钱包

如果测试显示高优先级钱包为0，可以降低阈值：

修改 `hyperliquid_collector.py:376,386`：
```python
# 高优先级: 从 PnL>10K, ROI>50% 降低到 PnL>5K, ROI>30%
db_wallets = hyperliquid_db.get_monitored_wallets_by_priority(
    min_pnl=5000,   # 从 10000 改为 5000
    min_roi=30,     # 从 50 改为 30
    days_active=7,
    limit=200
)
```

### 3. 如果监控耗时过长

如果全量扫描超过2小时，可以：

**方案A: 减少高优先级钱包数量**
```python
# scheduler.py:942
limit=100  # 从200改为100
```

**方案B: 降低监控频率**
```python
# scheduler.py:942
schedule.every(10).minutes.do(  # 从5分钟改为10分钟
```

**方案C: 提高优先级阈值**
```python
# 只监控最顶尖的交易者
min_pnl=20000,  # 提高到20K
min_roi=80      # 提高到80%
```

### 4. 如果需要暂时禁用

如果想回到只监控配置文件钱包的模式：

修改 `scheduler.py:943`：
```python
lambda: asyncio.run(self.monitor_hyperliquid_wallets(priority='config'))
```

## 📈 优化建议

### 短期优化（立即可做）

1. **降低交易阈值**：`min_trade_usd: 50000` → `10000`
   - 当前$50K阈值可能过滤掉大量有价值的交易
   - 降低到$10K可以捕获更多信号

2. **观察数据质量**：运行24小时后检查
   - 高优先级钱包是否产生有价值的交易？
   - 数据量是否达到预期？

### 中期优化（1-2周后）

1. **根据实际效果调整优先级分组**
   - 分析哪些钱包产生了最有利可图的信号
   - 动态调整 PnL 和 ROI 阈值

2. **优化监控频率**
   - 如果监控耗时过长，降低频率或减少钱包数
   - 如果监控很快完成，可以提高频率

### 长期优化（考虑扩展）

如果需要更高实时性：
- **Websocket 订阅**: Hyperliquid 支持 websocket，可以实时推送交易
- **并发监控**: 使用异步并发提高监控速度

## 🔍 监控指标

系统会自动保存以下数据到数据库：

1. **交易记录** (`hyperliquid_wallet_trades`)
   - 币种、方向（多/空）、价格、数量
   - 交易时间、已实现盈亏

2. **持仓快照** (`hyperliquid_wallet_positions`)
   - 当前持仓、未实现盈亏
   - 入场价格、标记价格

3. **钱包统计** (`hyperliquid_monitored_wallets`)
   - 最后检查时间、交易时间
   - 检查次数累计

## 📞 问题排查

### 问题1: 日志显示"从配置文件加载 1 个地址"

**原因**: `hyperliquid_db` 未正确传递或数据库连接失败

**解决**: 检查数据库配置和连接

### 问题2: 监控耗时过长

**原因**: 钱包数量太多

**解决**: 减少钱包数量或提高阈值

### 问题3: 数据量没有明显增加

**原因**: 钱包不活跃或阈值太高

**解决**:
1. 降低 `min_trade_usd` 到 10K
2. 检查高优先级钱包是否真的活跃
3. 确认是否在交易活跃时段

## 📚 相关文档

- [test_hyperliquid_priority.py](test_hyperliquid_priority.py) - 测试验证脚本
- [hyperliquid_db.py](app/database/hyperliquid_db.py) - 数据库操作
- [hyperliquid_collector.py](app/collectors/hyperliquid_collector.py) - 数据采集
- [scheduler.py](app/scheduler.py) - 调度配置

## 🚀 快速开始

```bash
# 1. 测试验证
python test_hyperliquid_priority.py

# 2. 如果钱包不足，先采集排行榜
python -m app.collectors.hyperliquid_leaderboard

# 3. 确认配置启用
# config.yaml: hyperliquid.enabled: true

# 4. 重启系统
python run.py

# 5. 观察日志
# 看到 "从数据库加载 XXX 个高优先级地址" 就表示成功了
```

---

**升级完成！开始享受8000+钱包的实时监控吧！** 🚀
