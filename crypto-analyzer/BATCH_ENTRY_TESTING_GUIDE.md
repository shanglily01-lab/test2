# 分批建仓功能测试指南

## ✅ 集成完成情况

### 已完成项目

1. ✅ **数据库迁移**
   - 执行了 `app/database/smart_brain_schema.sql`
   - 添加了 batch_plan, batch_filled, avg_entry_price, planned_close_time 等字段

2. ✅ **配置文件**
   - 在 `config.yaml` 中添加了 `batch_entry` 和 `smart_exit` 配置
   - 初始状态为 `enabled: false`（安全启动）

3. ✅ **代码集成**
   - 在 `smart_trader_service.py` 中导入了 SmartEntryExecutor 和 SmartExitOptimizer
   - 在 `__init__()` 中初始化了智能建仓和平仓执行器
   - 修改了 `open_position()` 函数，支持分批建仓逻辑
   - 添加了 `_open_position_with_batch()` 异步方法
   - 添加了 `_start_smart_exit_monitoring()` 启动智能平仓监控
   - 在 `async_main()` 中启动智能平仓监控

---

## 测试流程

### 阶段1: 语法检查（必须）

在启动服务前，先检查代码是否有语法错误：

```bash
cd /path/to/crypto-analyzer
python3 -m py_compile smart_trader_service.py
```

如果没有输出，说明语法正确。如果有错误，请先修复。

---

### 阶段2: 单币种小仓位测试（1-2天）

#### 步骤1: 修改配置启用分批建仓

编辑 `config.yaml`，找到 `batch_entry` 部分，修改为：

```yaml
  # 智能分批建仓配置
  batch_entry:
    enabled: true  # 启用分批建仓
    whitelist_symbols: ['BTC/USDT']  # 只测试BTC
    batch_ratios: [0.3, 0.3, 0.4]  # 分批比例：30%, 30%, 40%
    time_window_minutes: 30  # 建仓时间窗口（分钟）
    sampling_window_seconds: 300  # 价格采样窗口（5分钟）
    sampling_interval_seconds: 10  # 采样间隔（10秒）
  # 智能平仓优化配置
  smart_exit:
    enabled: true  # 启用智能平仓
    high_profit_threshold: 3.0  # 高盈利阈值（%）
    high_profit_drawback: 0.5  # 高盈利回撤止盈（%）
    mid_profit_threshold: 1.0  # 中盈利阈值（%）
    mid_profit_drawback: 0.4  # 中盈利回撤止盈（%）
    low_profit_target: 1.0  # 低盈利目标（%）
    micro_loss_threshold: -0.5  # 微亏损阈值（%）
    extension_minutes: 30  # 延长时间（分钟）
```

#### 步骤2: 重启服务

```bash
# 停止现有服务
pkill -f smart_trader_service.py

# 重启服务
cd /path/to/crypto-analyzer
nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &

# 查看日志
tail -f logs/smart_trader.log
```

#### 步骤3: 观察日志

**启动时应该看到**：
```
✅ 智能分批建仓执行器已启动
✅ 智能平仓优化器已启动
✅ 智能平仓监控已启动，监控 X 个持仓
```

**当BTC/USDT有交易信号时应该看到**：
```
[BATCH_ENTRY] BTC/USDT LONG 使用智能分批建仓
[PRICE_SAMPLER] BTC/USDT 开始价格采样（5分钟窗口）
[BATCH_1] BTC/USDT LONG 第1批(30%) 等待价格...
[BATCH_1] BTC/USDT LONG 第1批价格评分: XX分, 执行建仓
[BATCH_2] BTC/USDT LONG 第2批(30%) 等待价格...
...
✅ [BATCH_ENTRY_COMPLETE] BTC/USDT LONG | 持仓ID: XXX | 平均价格: $XX.XXXX
✅ [SMART_EXIT] 已启动智能平仓监控: 持仓XXX
```

**智能平仓时应该看到**：
```
🚨 触发平仓条件: 持仓XXX BTC/USDT LONG | 高盈利回撤止盈(盈利3.2%, 最高3.5%, 回撤0.5%)
🔴 执行平仓: 持仓XXX BTC/USDT LONG | 价格$XX.XXX
✅ 平仓成功: 持仓XXX
```

#### 步骤4: 验证数据库

检查分批建仓记录：

```bash
mysql -h 13.212.252.171 -u admin -p'Tonny@1000' binance-data -e "
SELECT
    id, symbol, position_side,
    avg_entry_price, entry_price,
    batch_plan, batch_filled,
    entry_signal_time, planned_close_time,
    max_profit_pct, close_reason
FROM futures_positions
WHERE batch_plan IS NOT NULL
ORDER BY id DESC
LIMIT 5;
"
```

**预期结果**：
- `batch_plan` 字段包含 3 个批次的计划 JSON
- `batch_filled` 字段包含 3 个批次的完成记录 JSON
- `avg_entry_price` 是加权平均入场价
- `entry_signal_time` 是信号发出时间
- `planned_close_time` 是计划平仓时间
- `max_profit_pct` 记录了最高盈利百分比
- `close_reason` 显示平仓原因（如"高盈利回撤止盈"）

---

### 阶段3: 多币种测试（3-5天）

如果单币种测试通过，扩大测试范围：

```yaml
  batch_entry:
    enabled: true
    whitelist_symbols: ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
    batch_ratios: [0.3, 0.3, 0.4]
    time_window_minutes: 30
```

---

### 阶段4: 全面启用（7天后）

如果多币种测试稳定，启用所有币种：

```yaml
  batch_entry:
    enabled: true
    whitelist_symbols: []  # 空=全部启用
    batch_ratios: [0.3, 0.3, 0.4]
    time_window_minutes: 30
```

---

## 监控指标

### 建仓效率

```sql
-- 分批建仓成功率（今日）
SELECT
    COUNT(*) as total_batch_positions,
    SUM(CASE WHEN JSON_LENGTH(batch_filled) = 3 THEN 1 ELSE 0 END) as completed_batches,
    ROUND(SUM(CASE WHEN JSON_LENGTH(batch_filled) = 3 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as completion_rate_pct
FROM futures_positions
WHERE batch_plan IS NOT NULL
AND DATE(open_time) = CURDATE();
```

### 平均入场价格优势

```sql
-- 对比分批建仓和一次性开仓的平均价格（今日）
SELECT
    symbol,
    COUNT(*) as trade_count,
    AVG(CASE WHEN batch_plan IS NOT NULL THEN avg_entry_price END) as avg_batch_entry,
    AVG(CASE WHEN batch_plan IS NULL THEN entry_price END) as avg_direct_entry,
    ROUND((AVG(CASE WHEN batch_plan IS NULL THEN entry_price END) -
           AVG(CASE WHEN batch_plan IS NOT NULL THEN avg_entry_price END)) /
           AVG(CASE WHEN batch_plan IS NOT NULL THEN avg_entry_price END) * 100, 4) as price_advantage_pct
FROM futures_positions
WHERE DATE(open_time) = CURDATE()
GROUP BY symbol
HAVING COUNT(*) >= 2;
```

### 平仓层级分布

```sql
-- 各层级平仓触发次数（今日）
SELECT
    CASE
        WHEN close_reason LIKE '%高盈利回撤%' THEN '高盈利回撤(≥3%)'
        WHEN close_reason LIKE '%中盈利回撤%' THEN '中盈利回撤(1-3%)'
        WHEN close_reason LIKE '%低盈利快速%' THEN '低盈利快速(0-1%)'
        WHEN close_reason LIKE '%盈亏平衡%' THEN '捕捉盈亏平衡'
        WHEN close_reason LIKE '%延长时间%' THEN '延长时间到期'
        WHEN close_reason LIKE '%计划平仓%' THEN '计划平仓时间'
        ELSE '其他'
    END as close_type,
    COUNT(*) as count,
    ROUND(AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)), 2) as avg_hold_minutes,
    ROUND(AVG((close_price - entry_price) / entry_price * 100), 2) as avg_profit_pct
FROM futures_positions
WHERE status = 'closed'
AND batch_plan IS NOT NULL
AND DATE(close_time) = CURDATE()
GROUP BY close_type
ORDER BY count DESC;
```

### 整体表现对比

```sql
-- 分批建仓 vs 一次性开仓 胜率对比（最近7天）
SELECT
    CASE
        WHEN batch_plan IS NOT NULL THEN '分批建仓'
        ELSE '一次性开仓'
    END as entry_type,
    COUNT(*) as total_trades,
    SUM(CASE WHEN (close_price - entry_price) / entry_price > 0 THEN 1 ELSE 0 END) as winning_trades,
    ROUND(SUM(CASE WHEN (close_price - entry_price) / entry_price > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate_pct,
    ROUND(AVG((close_price - entry_price) / entry_price * 100), 2) as avg_profit_pct,
    ROUND(AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)), 2) as avg_hold_minutes
FROM futures_positions
WHERE status = 'closed'
AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY entry_type;
```

---

## 常见问题排查

### Q1: 服务启动时报错 "No module named 'app.services.smart_entry_executor'"

**原因**: 文件路径不正确或文件不存在

**解决**:
```bash
ls -la app/services/smart_entry_executor.py
ls -la app/services/smart_exit_optimizer.py
ls -la app/services/price_sampler.py
```

确保这三个文件都存在。

---

### Q2: 启动日志显示 "⚠️ 智能分批建仓未启用"

**原因**: config.yaml 中的 `enabled: false`

**解决**: 检查 config.yaml 中的配置是否正确设置为 `enabled: true`

---

### Q3: 分批建仓失败，降级到一次性开仓

**原因**: 可能是价格采样失败、WebSocket连接问题等

**排查**:
1. 检查 WebSocket 价格服务是否正常：
   ```
   grep "WebSocket 价格服务" logs/smart_trader.log
   ```

2. 检查具体错误信息：
   ```
   grep "BATCH_ENTRY_ERROR" logs/smart_trader.log
   ```

---

### Q4: 分批建仓只完成了1批或2批就超时了

**原因**: 价格波动不大，没有触发第2/3批的价格条件

**说明**: 这是正常的，SmartEntryExecutor 有超时强制建仓机制：
- 第1批：15分钟超时
- 第2批：20分钟超时
- 第3批：28分钟超时

超时后会强制按市价建仓，确保30分钟内完成所有建仓。

---

### Q5: 智能平仓监控没有启动

**原因**: 可能是异步任务启动失败

**排查**:
```bash
grep "启动智能平仓监控" logs/smart_trader.log
```

如果没有看到相关日志，检查代码中的 `_start_smart_exit_monitoring()` 是否被调用。

---

## 回滚方案

如果出现严重问题，立即回滚：

### 方案1: 禁用分批建仓（推荐）

编辑 `config.yaml`:

```yaml
  batch_entry:
    enabled: false  # 禁用分批建仓
  smart_exit:
    enabled: false  # 禁用智能平仓
```

重启服务：

```bash
pkill -f smart_trader_service.py
nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &
```

### 方案2: 回滚代码（极端情况）

如果配置禁用无效，可以使用git回滚代码：

```bash
git log --oneline -5  # 查看最近5次提交
git revert <commit_hash>  # 回滚到指定提交
```

---

## 性能优化建议

1. **数据库连接池**: SmartExitOptimizer 已使用连接池（pool_size=3），无需额外配置

2. **异步并发**: 所有监控任务使用 `asyncio.create_task` 并发运行，性能良好

3. **缓存优化**: PriceSampler 使用滚动窗口，避免重复计算

4. **日志级别**: 如果觉得日志太多，可以调整日志级别：
   ```python
   # 在 smart_trader_service.py 顶部修改
   logger.add(
       sys.stderr,
       level="WARNING"  # 改为WARNING减少日志输出
   )
   ```

---

## 下一步计划

1. **参数优化**: 根据实盘数据调整分批比例、价格评分权重、平仓阈值

2. **功能扩展**:
   - 支持做空方向的智能建仓
   - 增加市场波动率自适应
   - 集成风险控制模块

3. **监控面板**: 开发专门的分批建仓监控面板（可选）

---

## 联系支持

如果遇到问题，请提供以下信息：

1. 错误日志（最近100行）：
   ```bash
   tail -100 logs/smart_trader.log
   ```

2. 配置文件（batch_entry 和 smart_exit 部分）

3. 数据库查询结果（分批建仓记录）

4. 服务运行环境信息：
   ```bash
   python3 --version
   mysql --version
   uname -a
   ```
