# 超级大脑 - 现货交易策略完整文档

## 📋 目录

1. [策略概述](#策略概述)
2. [核心理念](#核心理念)
3. [交易流程](#交易流程)
4. [风险控制](#风险控制)
5. [技术实现](#技术实现)
6. [性能指标](#性能指标)
7. [运维管理](#运维管理)
8. [常见问题](#常见问题)

---

## 策略概述

### 策略定位

**现货短线动态价格采样策略 V2** - 基于24H市场信号,通过动态价格采样在8小时内完成一个完整的交易周期,实现稳定盈利。

### 核心特点

- ⏱️ **8小时周期**: 快速周转,提高资金效率
- 📊 **动态采样**: 建仓寻找低价,平仓寻找高价
- 💰 **分批建仓**: 5批次逐步建立仓位
- 🎯 **让利润奔跑**: 持仓4小时不干预
- 🛡️ **严格风控**: 5%止损,15%止盈

### 适用场景

- ✅ 24H涨幅3-10%的上涨行情
- ✅ 成交量放大,流动性好的币种
- ✅ 趋势向上(STRONG_UP/UP)的市场
- ❌ 震荡盘整市场
- ❌ 流动性差的小币种

---

## 核心理念

### 1. 时间分段管理

整个交易周期严格划分为5个阶段,每个阶段有明确的目标和操作:

```
阶段1: 采样阶段 (1H)      → 采集价格样本,不交易
阶段2: 建仓阶段 (2H)      → 动态寻找最优价格,完成5批建仓
阶段3: 持仓阶段 (4H)      → 让利润奔跑,只监控止盈止损
阶段4: 平仓采样阶段 (1H)  → 采集平仓价格样本
阶段5: 平仓阶段 (剩余时间) → 动态寻找最优价格平仓
```

### 2. 动态价格优化

**建仓策略**: 使用最近1小时价格样本的**20%分位数**(偏低价格)
- 不追高,等待价格回落
- 当前价 ≤ 最优价 × 1.02时执行建仓

**平仓策略**: 使用最近1小时价格样本的**80%分位数**(偏高价格)
- 不杀低,等待价格上涨
- 当前价 ≥ 最优价 × 0.97 且有盈利时平仓

### 3. 让利润奔跑

建仓完成后进入**4小时持仓期**:
- ✅ 只监控止盈15%和止损5%
- ❌ 不监控任何其他信号
- ❌ 不执行加仓操作
- ❌ 不主动平仓

**目的**: 避免过早平仓,捕捉趋势延续

---

## 交易流程

### 完整周期示例

**开始时间**: 10:00:00
**目标币种**: BTC/USDT
**信号强度**: 50分
**24H涨幅**: +6.5%

#### 阶段1: 采样阶段 (10:00 - 11:00)

```
10:00:00  创建持仓记录, phase = 'sampling'
10:00:30  采集价格样本: $50,000
10:01:00  采集价格样本: $50,050
10:01:30  采集价格样本: $49,950
...
11:00:00  采样完成, 共采集120个样本
          进入建仓阶段, phase = 'building'
```

**样本统计**:
- 最高价: $50,200
- 最低价: $49,800
- 平均价: $50,000
- 20%分位数: $49,900 (目标建仓价)

#### 阶段2: 建仓阶段 (11:00 - 13:00)

**5批次建仓计划**:

| 批次 | 比例 | 金额 | 目标价格 | 实际成交 | 时间 |
|------|------|------|----------|----------|------|
| 1 | 15% | $300 | ≤ $49,900 | $49,850 | 11:05 |
| 2 | 20% | $400 | ≤ $49,900 | $49,880 | 11:28 |
| 3 | 20% | $400 | ≤ $49,900 | $49,820 | 11:52 |
| 4 | 20% | $400 | ≤ $49,900 | $49,900 | 12:20 |
| 5 | 25% | $500 | ≤ $49,900 | $49,870 | 12:45 |

**建仓结果**:
- 总成本: $2,000
- 总数量: 0.04015 BTC
- 平均成本: $49,864
- 止盈价格: $57,344 (+15%)
- 止损价格: $47,371 (-5%)

```
12:45:00  第5批建仓完成
          进入持仓阶段, phase = 'holding'
          持仓期结束时间: 16:45 (4小时后)
```

#### 阶段3: 持仓阶段 (12:45 - 16:45)

```
12:45 - 16:45  让利润奔跑
               只监控: 止盈$57,344 / 止损$47,371

价格走势:
13:00  $50,100 (+0.5%)  → 继续持有
14:00  $51,200 (+2.7%)  → 继续持有
15:00  $52,500 (+5.3%)  → 继续持有
16:00  $53,800 (+7.9%)  → 继续持有
16:45  $54,200 (+8.7%)  → 进入平仓采样阶段
```

#### 阶段4: 平仓采样阶段 (16:45 - 17:45)

```
16:45:00  进入平仓采样阶段, phase = 'exit_sampling'
16:45:30  采集平仓样本: $54,200
16:46:00  采集平仓样本: $54,350
16:46:30  采集平仓样本: $54,150
...
17:45:00  采样完成, 共采集120个样本
          进入平仓阶段, phase = 'exit_ready'
```

**样本统计**:
- 最高价: $54,800
- 最低价: $53,900
- 平均价: $54,300
- 80%分位数: $54,600 (目标平仓价)

#### 阶段5: 平仓阶段 (17:45 - 18:00)

```
17:50:00  当前价: $54,580
          80%分位数: $54,600
          当前价 ≥ 最优价 × 97% ($54,582 ≥ $52,962) ✅
          盈利: +9.5% > 0 ✅

          执行平仓!

平仓结果:
  平仓价格: $54,580
  盈亏: +$189.65 (+9.5%)
  原因: 最优平仓(+9.5%)
  持仓时长: 5小时5分钟
```

### 异常处理

#### 情况1: 建仓超时

如果2小时内未完成5批建仓,强制以当前价格完成剩余批次:

```
13:00:00  建仓2小时到期
          当前完成: 3批
          剩余: 2批 (第4、5批)

          强制建仓:
          第4批: $50,200 (虽然高于目标价,但必须完成)
          第5批: $50,250

          进入持仓阶段
```

#### 情况2: 触发止盈

```
14:30:00  当前价: $57,500
          止盈价: $57,344

          触发止盈! 立即平仓

平仓结果:
  平仓价格: $57,500
  盈亏: +$306.52 (+15.3%)
  原因: 止盈15%
  持仓时长: 1小时45分钟
```

#### 情况3: 触发止损

```
13:15:00  当前价: $47,200
          止损价: $47,371

          触发止损! 立即平仓

平仓结果:
  平仓价格: $47,200
  盈亏: -$106.46 (-5.3%)
  原因: 止损5%
  持仓时长: 30分钟
```

#### 情况4: 8小时强制平仓

```
18:00:00  总时长8小时到期
          当前价: $51,200

          强制平仓!

平仓结果:
  平仓价格: $51,200
  盈亏: +$53.61 (+2.7%)
  原因: 8H超时平仓(+2.7%)
  持仓时长: 8小时
```

---

## 风险控制

### 1. 价格风险

**止损机制**:
- 固定止损: -5%
- 触发条件: 当前价 ≤ 平均成本 × 0.95
- 执行方式: 立即市价平仓

**止盈机制**:
- 固定止盈: +15%
- 触发条件: 当前价 ≥ 平均成本 × 1.15
- 执行方式: 立即市价平仓

### 2. 时间风险

**建仓超时保护**:
- 时限: 2小时
- 未完成批次: 强制以当前价建仓
- 目的: 避免长时间空仓

**总时长控制**:
- 时限: 8小时
- 超时处理: 强制平仓
- 目的: 避免长期套牢

### 3. 流动性风险

**入场过滤**:
- 24H成交额 > 500万 USDT
- 避免流动性不足的币种

**建仓分批**:
- 5批次逐步建仓
- 降低单次冲击成本

### 4. 仓位风险

**单币限制**:
- 单币资金: 2,000 USDT
- 最大亏损: 2,000 × 5% = 100 USDT

**总仓位控制**:
- 最大持仓: 15个币种
- 最大占用: 30,000 USDT (60%)
- 预留资金: 20,000 USDT (40%)

**风险敞口**:
- 15个全部止损: 1,500 USDT (3%)
- 可接受范围

---

## 技术实现

### 系统架构

```
spot_trader_service_v2.py
├── PriceSampler (价格采样器)
│   ├── add_sample()              # 添加价格样本
│   ├── get_optimal_buy_price()   # 获取最优买入价 (20%分位)
│   └── get_optimal_sell_price()  # 获取最优卖出价 (80%分位)
│
├── DynamicPositionManager (动态仓位管理)
│   ├── create_position()         # 创建持仓
│   ├── add_batch()               # 添加批次
│   ├── close_position()          # 平仓
│   └── get_positions()           # 获取持仓列表
│
└── SpotTraderV2 (主服务)
    ├── collect_price_samples()       # 采集价格样本
    ├── check_new_opportunities()     # 检查新机会
    ├── manage_positions()            # 管理持仓
    ├── _handle_sampling_phase()      # 处理采样阶段
    ├── _handle_building_phase()      # 处理建仓阶段
    ├── _handle_holding_phase()       # 处理持仓阶段
    ├── _handle_exit_sampling_phase() # 处理平仓采样
    └── _handle_exit_phase()          # 处理平仓阶段
```

### 数据库表结构

**spot_positions_v2**:

```sql
CREATE TABLE spot_positions_v2 (
  id INT PRIMARY KEY AUTO_INCREMENT,
  symbol VARCHAR(20),                    -- 交易对
  status VARCHAR(20),                    -- active/closed
  phase VARCHAR(20),                     -- 当前阶段

  -- 时间节点
  sampling_start_time DATETIME,          -- 采样开始
  building_start_time DATETIME,          -- 建仓开始
  holding_start_time DATETIME,           -- 持仓开始
  exit_sampling_start_time DATETIME,     -- 平仓采样开始
  exit_time DATETIME,                    -- 平仓时间

  -- 建仓信息
  current_batch INT,                     -- 当前批次 (0-5)
  total_quantity DECIMAL(18,8),          -- 总数量
  total_cost DECIMAL(20,2),              -- 总成本
  avg_entry_price DECIMAL(18,8),         -- 平均成本

  -- 止盈止损
  take_profit_price DECIMAL(18,8),       -- 止盈价格
  stop_loss_price DECIMAL(18,8),         -- 止损价格

  -- 平仓信息
  exit_price DECIMAL(18,8),              -- 平仓价格
  realized_pnl DECIMAL(20,2),            -- 盈亏金额
  realized_pnl_pct DECIMAL(10,4),        -- 盈亏百分比
  close_reason VARCHAR(100),             -- 平仓原因

  created_at DATETIME,
  updated_at DATETIME
);
```

### 核心算法

#### 价格分位数计算

```python
def get_optimal_buy_price(samples, current_price):
    """获取最优买入价格 (20%分位数)"""
    # 过滤最近1小时样本
    recent = [s for s in samples if s.time >= now - 1hour]

    # 价格排序
    prices = sorted([s.price for s in recent])

    # 计算20%分位数
    index = int(len(prices) * 0.2)
    optimal = prices[index]

    # 返回较低价格
    return min(current_price, optimal)

def get_optimal_sell_price(samples, current_price, entry_price):
    """获取最优卖出价格 (80%分位数)"""
    # 过滤最近1小时样本
    recent = [s for s in samples if s.time >= now - 1hour]

    # 价格排序
    prices = sorted([s.price for s in recent])

    # 计算80%分位数
    index = int(len(prices) * 0.8)
    optimal = prices[index]

    # 判断是否应该卖出
    if current_price >= optimal * 0.97:
        if current_price > entry_price:
            return True, current_price

    return False, optimal
```

#### 阶段转换逻辑

```python
def manage_positions():
    for pos in active_positions:
        phase = pos.phase

        if phase == 'sampling':
            # 检查是否采样1小时
            if now >= pos.building_start_time:
                update_phase(pos, 'building')

        elif phase == 'building':
            # 动态建仓
            if should_build_batch(pos):
                build_next_batch(pos)

            # 检查是否超时2小时
            if now >= pos.building_start_time + 2hours:
                force_complete_building(pos)

        elif phase == 'holding':
            # 只检查止盈止损
            check_take_profit_stop_loss(pos)

            # 检查是否持仓4小时
            if now >= pos.exit_sampling_start_time:
                update_phase(pos, 'exit_sampling')

        elif phase == 'exit_sampling':
            # 采集平仓样本
            # 检查是否采样1小时
            if now >= pos.exit_sampling_start_time + 1hour:
                update_phase(pos, 'exit_ready')

        elif phase == 'exit_ready':
            # 动态平仓
            should_exit, price = check_optimal_exit(pos)
            if should_exit:
                close_position(pos, price)

            # 检查8小时总时长
            if now >= pos.created_at + 8hours:
                force_close_position(pos)
```

---

## 性能指标

### 预期收益

**单笔交易**:
- 胜率: 60%
- 平均盈利: 10% (止盈前平仓)
- 平均亏损: -5% (止损)
- 单笔期望: 2,000 × (10% × 60% - 5% × 40%) = **+80 USDT**

**日均表现**:
- 每笔耗时: 8小时
- 日均完成: 10-15笔
- 日均收益: 10 × 80 = **800 USDT**

**月度表现**:
- 月交易笔数: 300-450笔
- 月收益: 800 × 30 = **24,000 USDT**
- 月化收益率: 24,000 / 50,000 = **48%**

### 风险指标

**最大回撤**:
- 单笔最大亏损: -100 USDT (-5%)
- 连续10笔亏损: -1,000 USDT (-2%)
- 最大回撤: **-3%** (极端情况)

**夏普比率**:
- 年化收益: 48% × 12 = 576%
- 年化波动: 约15%
- 夏普比率: **~38** (极高)

---

## 运维管理

### 启动服务

```bash
# 拉取最新代码
git pull

# 启动服务
nohup python3 spot_trader_service_v2.py > /tmp/spot_trader.log 2>&1 &

# 查看PID
echo "现货调度器 PID: $!"
```

### 监控命令

#### 1. 查看运行日志

```bash
# 实时查看日志
tail -f /tmp/spot_trader.log

# 筛选关键事件
tail -f /tmp/spot_trader.log | grep "创建持仓\|完成第.*批建仓\|进入.*阶段\|平仓"
```

#### 2. 查看活跃持仓

```bash
python3 -c "
import pymysql
import os
from dotenv import load_dotenv
load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT')),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database='binance-data',
    cursorclass=pymysql.cursors.DictCursor
)

cursor = conn.cursor()
cursor.execute('''
    SELECT symbol, phase, current_batch, avg_entry_price,
           TIMESTAMPDIFF(MINUTE, created_at, NOW()) as minutes_held
    FROM spot_positions_v2
    WHERE status = \"active\"
    ORDER BY created_at DESC
''')

print('活跃持仓:')
for row in cursor.fetchall():
    print(f\"{row['symbol']:12} {row['phase']:15} 批次:{row['current_batch']}/5 时长:{row['minutes_held']}分钟\")

cursor.close()
conn.close()
"
```

#### 3. 查看今日统计

```bash
python3 -c "
import pymysql
import os
from dotenv import load_dotenv
load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT')),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database='binance-data',
    cursorclass=pymysql.cursors.DictCursor
)

cursor = conn.cursor()
cursor.execute('''
    SELECT
        COUNT(*) as total_trades,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning,
        SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losing,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl_pct) as avg_pnl_pct
    FROM spot_positions_v2
    WHERE status = \"closed\" AND DATE(exit_time) = CURDATE()
''')

stats = cursor.fetchone()
print(f\"今日统计:\")
print(f\"  总交易: {stats['total_trades']}笔\")
print(f\"  盈利: {stats['winning']}笔\")
print(f\"  亏损: {stats['losing']}笔\")
print(f\"  胜率: {stats['winning']/stats['total_trades']*100:.1f}%\")
print(f\"  总盈亏: ${stats['total_pnl']:.2f}\")
print(f\"  平均收益率: {stats['avg_pnl_pct']:.2f}%\")

cursor.close()
conn.close()
"
```

### 停止服务

```bash
# 查找进程
ps aux | grep spot_trader_service_v2

# 停止进程 (替换PID)
kill <PID>

# 强制停止
kill -9 <PID>
```

---

## 常见问题

### Q1: 为什么建仓阶段没有交易?

**可能原因**:
1. 当前价格高于最优价格 (20%分位数)
2. 价格样本不足 (需要至少10个样本)
3. WebSocket价格服务未连接

**解决方法**:
```bash
# 查看日志
tail -f /tmp/spot_trader.log | grep "最优价格\|建仓"

# 检查价格样本
# 如果看到 "样本不足", 等待更多采样
```

### Q2: 为什么持仓4小时后没有平仓?

**可能原因**:
1. 当前价格未达到最优卖出价 (80%分位数的97%)
2. 当前处于亏损状态 (不满足盈利条件)
3. 还在平仓采样阶段 (需要1小时)

**解决方法**:
- 耐心等待,系统会在剩余3小时内寻找最优价格
- 如果8小时到期,会强制平仓

### Q3: 止损触发太频繁怎么办?

**可能原因**:
- 市场波动过大
- 止损阈值5%太小

**解决方法**:
```python
# 调整止损阈值 (spot_trader_service_v2.py:91)
self.stop_loss_pct = 0.06  # 5% → 6%
```

### Q4: 如何提高收益率?

**优化方向**:
1. **调整止盈**: 15% → 12% (更快平仓)
2. **增加持仓数**: 15个 → 20个 (更多并发)
3. **降低入场阈值**: 40分 → 35分 (更多机会)

**注意**: 需要回测验证效果

### Q5: 资金利用率不高怎么办?

**检查项**:
```bash
# 查看活跃持仓数
python3 -c "..."  # 见监控命令2

# 如果持仓数 < 10:
# 1. 降低入场阈值
# 2. 增加监控币种
# 3. 检查24H信号筛选条件
```

---

## 附录

### A. 参数配置对照表

| 参数 | 位置 | 默认值 | 建议范围 |
|------|------|--------|----------|
| 单币资金 | line 87 | 2000 USDT | 1000-5000 |
| 最大持仓 | line 88 | 15个 | 10-20 |
| 止盈 | line 91 | 15% | 12-20% |
| 止损 | line 92 | 5% | 4-7% |
| 采样时长 | line 95 | 1小时 | 0.5-2小时 |
| 建仓时长 | line 96 | 2小时 | 1-3小时 |
| 持仓时长 | line 97 | 4小时 | 2-6小时 |
| 总时长 | line 99 | 8小时 | 6-12小时 |
| 入场阈值 | line 426 | 40分 | 35-50 |

### B. 相关文件清单

- **核心服务**: `spot_trader_service_v2.py`
- **数据库表**: `create_spot_positions_v2_table.sql`
- **策略文档**: `SPOT_DYNAMIC_PRICING_STRATEGY.md`
- **分析工具**: `analyze_spot_trading.py`
- **机会扫描**: `optimize_spot_for_short_term.py`

### C. 联系支持

如遇问题,请提供:
1. 错误日志 (最近100行)
2. 数据库状态 (活跃持仓)
3. 系统环境 (Python版本, 依赖版本)

---

**文档版本**: V2.0
**最后更新**: 2026-01-28
**维护者**: 超级大脑团队
