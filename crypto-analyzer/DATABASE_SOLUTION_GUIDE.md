# 数据库存储方案 - 解决Linux服务器无法修改config.yaml问题

## 🎯 问题

您提出的关键问题：**在Linux服务器上无法修改config.yaml**

原因：
- 文件权限问题
- 文件被锁定
- YAML文件并发写入不安全
- 服务重启后config.yaml可能被Git还原

##  ✅ 解决方案：使用数据库存储动态配置

### 架构设计

```
┌─────────────────┐
│  config.yaml    │  ← 只存储静态配置（symbols列表）
│  (只读)         │
└─────────────────┘
         ↓
┌─────────────────┐
│  MySQL数据库    │  ← 存储动态配置（黑名单、参数）
│  ├─ trading_blacklist        │  黑名单表
│  ├─ adaptive_params           │  自适应参数表
│  └─ optimization_history      │  优化历史表
└─────────────────┘
         ↓
┌─────────────────┐
│ SmartTraderService │  ← 从数据库读取配置
│ AdaptiveOptimizer  │  ← 更新数据库
└─────────────────┘
```

---

## 📊 数据库表结构

### 1. adaptive_params - 自适应参数表

```sql
CREATE TABLE adaptive_params (
    id INT AUTO_INCREMENT PRIMARY KEY,
    param_key VARCHAR(100) NOT NULL UNIQUE,    -- 参数键名
    param_value DECIMAL(10, 6) NOT NULL,       -- 参数值
    param_type VARCHAR(50) NOT NULL,           -- 参数类型: long/short
    description VARCHAR(255),                  -- 参数描述
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    updated_by VARCHAR(100) DEFAULT 'adaptive_optimizer',
    INDEX idx_param_key (param_key)
);
```

**初始数据**:
| param_key | param_value | param_type | description |
|-----------|-------------|------------|-------------|
| long_stop_loss_pct | 0.03 | long | LONG止损百分比 |
| long_take_profit_pct | 0.02 | long | LONG止盈百分比 |
| long_min_holding_minutes | 60 | long | LONG最小持仓时间 |
| long_position_size_multiplier | 1.0 | long | LONG仓位倍数 |
| short_stop_loss_pct | 0.03 | short | SHORT止损百分比 |
| short_take_profit_pct | 0.02 | short | SHORT止盈百分比 |
| short_min_holding_minutes | 60 | short | SHORT最小持仓时间 |
| short_position_size_multiplier | 1.0 | short | SHORT仓位倍数 |

### 2. trading_blacklist - 交易黑名单表

```sql
CREATE TABLE trading_blacklist (
    id INT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(50) NOT NULL UNIQUE,        -- 交易对
    reason VARCHAR(255),                       -- 加入原因
    total_loss DECIMAL(15, 2),                 -- 历史总亏损
    win_rate DECIMAL(5, 4),                    -- 胜率
    order_count INT,                           -- 订单数
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,            -- 是否激活
    INDEX idx_symbol (symbol),
    INDEX idx_is_active (is_active)
);
```

**初始数据** (从config.yaml迁移):
| symbol | reason | total_loss | is_active |
|--------|--------|------------|-----------|
| IP/USDT | 亏损 $79.34 (2笔订单, 0%胜率) | 79.34 | TRUE |
| VIRTUAL/USDT | 亏损 $35.65 (4笔订单, 0%胜率) | 35.65 | TRUE |
| LDO/USDT | 亏损 $35.88 (5笔订单, 0%胜率) | 35.88 | TRUE |
| ATOM/USDT | 亏损 $27.56 (5笔订单, 20%胜率) | 27.56 | TRUE |
| ADA/USDT | 亏损 $22.87 (6笔订单, 0%胜率) | 22.87 | TRUE |

### 3. optimization_history - 优化历史表

```sql
CREATE TABLE optimization_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    optimization_date DATE NOT NULL,           -- 优化日期
    analysis_hours INT DEFAULT 24,             -- 分析时间范围
    blacklist_added INT DEFAULT 0,             -- 新增黑名单数量
    params_updated INT DEFAULT 0,              -- 更新参数数量
    high_severity_issues INT DEFAULT 0,        -- 高严重性问题数量
    report_summary TEXT,                       -- 优化报告摘要(JSON)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_optimization_date (optimization_date)
);
```

---

## 🚀 部署步骤

### 步骤1: 创建数据库表

```bash
# 1. SSH登录服务器
ssh ec2-user@13.212.252.171

# 2. 进入项目目录
cd ~/crypto-analyzer

# 3. 拉取最新代码
git pull origin master

# 4. 导入数据库schema
mysql -h 13.212.252.171 -u admin -p'Tonny@1000' binance-data < app/database/adaptive_params_schema.sql
```

### 步骤2: 验证表创建

```bash
mysql -h 13.212.252.171 -u admin -p'Tonny@1000' -D binance-data -e "
SHOW TABLES LIKE '%adaptive%';
SHOW TABLES LIKE '%blacklist%';
"
```

**预期输出**:
```
+----------------------------------+
| Tables_in_binance-data (%adaptive%) |
+----------------------------------+
| adaptive_params                  |
| optimization_history             |
+----------------------------------+

+--------------------------------------+
| Tables_in_binance-data (%blacklist%) |
+--------------------------------------+
| trading_blacklist                    |
+--------------------------------------+
```

### 步骤3: 查看初始数据

```bash
# 查看自适应参数
mysql -h 13.212.252.171 -u admin -p'Tonny@1000' -D binance-data -e "
SELECT * FROM adaptive_params ORDER BY param_type, param_key;
"

# 查看黑名单
mysql -h 13.212.252.171 -u admin -p'Tonny@1000' -D binance-data -e "
SELECT symbol, reason, total_loss, is_active FROM trading_blacklist;
"
```

### 步骤4: 重启服务

```bash
# 重启智能交易服务
kill $(pgrep -f smart_trader_service.py)
nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &

# 查看启动日志
tail -50 logs/smart_trader.log
```

### 预期日志输出

```
2026-01-20 XX:XX:XX | INFO     | ✅ 从数据库加载配置:
2026-01-20 XX:XX:XX | INFO     |    总交易对: 50
2026-01-20 XX:XX:XX | INFO     |    数据库黑名单: 5 个
2026-01-20 XX:XX:XX | INFO     |    可交易: 45 个
2026-01-20 XX:XX:XX | INFO     |    📊 自适应参数 (从数据库):
2026-01-20 XX:XX:XX | INFO     |       LONG止损: 3.0%, 止盈: 2.0%, 最小持仓: 60分钟, 仓位倍数: 1.0
2026-01-20 XX:XX:XX | INFO     |       SHORT止损: 3.0%, 止盈: 2.0%, 最小持仓: 60分钟, 仓位倍数: 1.0
2026-01-20 XX:XX:XX | INFO     |    🚫 黑名单交易对: IP/USDT, VIRTUAL/USDT, LDO/USDT, ATOM/USDT, ADA/USDT
```

**关键检查点**:
- ✅ 看到"从数据库加载配置"
- ✅ 看到"数据库黑名单: 5 个"
- ✅ 看到"自适应参数 (从数据库)"

---

## 🔄 工作流程

### 每日凌晨2点自动优化

```
02:00:00 | 优化器分析24小时数据
   ↓
02:00:10 | 发现问题:
           - SMART_BRAIN_20 LONG 亏损$-1026.91
           - 平均持仓24分钟 (太短)
           - 胜率8.3% (太低)
   ↓
02:00:15 | 执行优化:
           1. UPDATE trading_blacklist
              SET symbol='WIF/USDT' ...

           2. UPDATE adaptive_params
              SET param_value=120
              WHERE param_key='long_min_holding_minutes'

           3. UPDATE adaptive_params
              SET param_value=0.04
              WHERE param_key='long_stop_loss_pct'
   ↓
02:00:20 | 重新加载配置:
           SELECT * FROM trading_blacklist
           SELECT * FROM adaptive_params
   ↓
02:00:25 | 立即生效
           下一笔订单使用新参数
```

### 查看优化历史

```bash
# 查看最近的优化记录
mysql -h 13.212.252.171 -u admin -p'Tonny@1000' -D binance-data -e "
SELECT
    optimization_date,
    blacklist_added,
    params_updated,
    high_severity_issues,
    created_at
FROM optimization_history
ORDER BY created_at DESC
LIMIT 10;
"
```

### 查看参数变化

```bash
# 查看LONG参数的当前值
mysql -h 13.212.252.171 -u admin -p'Tonny@1000' -D binance-data -e "
SELECT
    param_key,
    param_value,
    description,
    updated_at,
    updated_by
FROM adaptive_params
WHERE param_type = 'long'
ORDER BY updated_at DESC;
"
```

---

## 📝 手动管理

### 手动添加黑名单

```sql
INSERT INTO trading_blacklist (symbol, reason, is_active)
VALUES ('DOGE/USDT', '手动添加 - 测试', TRUE);
```

### 手动调整参数

```sql
-- 放宽LONG止损到5%
UPDATE adaptive_params
SET param_value = 0.05
WHERE param_key = 'long_stop_loss_pct';

-- 增加LONG最小持仓时间到180分钟
UPDATE adaptive_params
SET param_value = 180
WHERE param_key = 'long_min_holding_minutes';
```

### 移除黑名单

```sql
-- 软删除（推荐）
UPDATE trading_blacklist
SET is_active = FALSE
WHERE symbol = 'WIF/USDT';

-- 硬删除（慎用）
DELETE FROM trading_blacklist
WHERE symbol = 'WIF/USDT';
```

### 重置参数到默认值

```sql
UPDATE adaptive_params SET param_value = 0.03 WHERE param_key = 'long_stop_loss_pct';
UPDATE adaptive_params SET param_value = 0.02 WHERE param_key = 'long_take_profit_pct';
UPDATE adaptive_params SET param_value = 60 WHERE param_key = 'long_min_holding_minutes';
UPDATE adaptive_params SET param_value = 1.0 WHERE param_key = 'long_position_size_multiplier';
```

---

## 🎯 优势对比

### 之前方案 (config.yaml)

| 问题 | 描述 |
|------|------|
| ❌ 文件权限 | Linux服务器可能无法写入 |
| ❌ 并发安全 | 多进程修改YAML文件不安全 |
| ❌ Git冲突 | git pull可能覆盖本地修改 |
| ❌ 无历史记录 | 无法追踪参数变化历史 |
| ❌ 难以查询 | 无法SQL查询优化历史 |

### 现在方案 (数据库)

| 优势 | 描述 |
|------|------|
| ✅ 权限稳定 | 数据库权限可控 |
| ✅ 并发安全 | MySQL事务保证数据一致性 |
| ✅ 无Git冲突 | 数据库不受Git影响 |
| ✅ 完整历史 | updated_at自动记录变化时间 |
| ✅ 易于查询 | SQL查询优化历史和趋势 |
| ✅ 易于管理 | 可以用SQL脚本批量管理 |

---

## 🔍 监控和验证

### 验证黑名单生效

```bash
# 检查黑名单交易对是否还有新订单
mysql -h 13.212.252.171 -u admin -p'Tonny@1000' -D binance-data -e "
SELECT symbol, COUNT(*) as new_orders
FROM futures_positions
WHERE symbol IN (SELECT symbol FROM trading_blacklist WHERE is_active = TRUE)
AND status = 'open'
AND open_time > NOW() - INTERVAL 1 HOUR
GROUP BY symbol;
"
```

**预期结果**: 无记录（黑名单生效）

### 验证参数更新

```bash
# 查看最新LONG订单使用的参数
tail -100 logs/smart_trader.log | grep "LONG开仓成功"
```

**示例输出**:
```
[SUCCESS] BTC/USDT LONG开仓成功 | 止损: $96000.00 (-4.0%) | ... | 仓位: $200 (x0.5)
```

解释：
- `-4.0%`: 使用了数据库中的4%止损（不是默认3%）
- `x0.5`: 使用了数据库中的0.5仓位倍数（不是默认1.0）

---

## 📚 相关SQL查询

### 统计黑名单效果

```sql
-- 黑名单交易对的历史亏损统计
SELECT
    b.symbol,
    b.reason,
    b.total_loss as recorded_loss,
    COUNT(p.id) as orders_before_blacklist,
    SUM(p.realized_pnl) as actual_loss
FROM trading_blacklist b
LEFT JOIN futures_positions p ON b.symbol = p.symbol AND p.close_time < b.created_at
WHERE b.is_active = TRUE
GROUP BY b.symbol
ORDER BY actual_loss ASC;
```

### 参数调整历史

```sql
-- 查看参数调整时间线
SELECT
    param_key,
    param_value,
    updated_at,
    updated_by
FROM adaptive_params
WHERE param_key LIKE 'long_%'
ORDER BY updated_at DESC;
```

### 优化效果分析

```sql
-- 对比优化前后的订单表现
SELECT
    DATE(open_time) as date,
    position_side,
    COUNT(*) as orders,
    AVG(realized_pnl) as avg_pnl,
    AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)) as avg_hold_minutes
FROM futures_positions
WHERE status = 'closed'
AND open_time > DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY DATE(open_time), position_side
ORDER BY date DESC, position_side;
```

---

## 🎉 总结

现在系统完全不依赖config.yaml的写入权限：

✅ **黑名单**: 存储在`trading_blacklist`表，优化器自动更新
✅ **自适应参数**: 存储在`adaptive_params`表，优化器自动调整
✅ **配置加载**: SmartDecisionBrain从数据库读取，不需要修改YAML
✅ **立即生效**: reload_config()重新查询数据库，新参数立即应用
✅ **权限无忧**: 数据库权限可控，不受文件系统限制

**部署后即可自动优化，无需担心Linux文件权限问题！**

---

**创建时间**: 2026-01-20
**版本**: 1.0
**状态**: ✅ 准备就绪，等待部署
**数据库要求**: MySQL 5.7+
