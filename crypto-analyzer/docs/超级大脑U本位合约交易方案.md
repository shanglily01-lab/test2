# 超级大脑 - U本位合约交易方案

> **最后更新**: 2026-02-21
> **系统版本**: V3.0 - 30H+5H双重验证 + 自适应趋势判断
> **核心特性**: Big4双重趋势验证 + 智能分批建仓 + 深V反转识别 + 智能平仓优化

---

## 📋 目录

1. [系统概述](#系统概述)
2. [核心架构](#核心架构)
3. [交易流程](#交易流程)
4. [Big4趋势判断系统](#big4趋势判断系统)
5. [信号系统](#信号系统)
6. [风险控制](#风险控制)
7. [智能建仓与平仓](#智能建仓与平仓)
8. [性能指标](#性能指标)
9. [运维管理](#运维管理)

---

## 系统概述

### 定位

**超级大脑U本位合约交易系统** 是一个具备自我学习能力的智能化期货合约交易平台，通过**30H+5H双重趋势验证**、多层信号评分、智能分批建仓和深V反转识别机制，实现稳定盈利。

### 核心特点

- 🧠 **双重趋势验证**: 30H大趋势 + 5H小趋势，避免趋势转折点逆势开仓
- 📊 **Big4市场判断**: 基于BTC/ETH/BNB/SOL的加权分析（BTC权重50%）
- 🎯 **智能分批建仓**: 3批次入场(30%-30%-40%)，K线回调确认入场
- 🛡️ **8层风控体系**: 从固定止损到智能平仓，全方位保护资金
- ⚡ **深V反转识别**: 自动识别市场底部反弹和顶部回调
- 🔄 **实时智能监控**: WebSocket价格 + K线强度检测 + 动态平仓窗口

### 适用场景

- ✅ 24小时自动化交易
- ✅ 短线趋势捕捉(3小时持仓)
- ✅ U本位合约5x杠杆
- ✅ 监控100+主流交易对

### 系统规模

- **账户ID**: 2 (U本位专用)
- **核心代码**: 8000+行
- **主要模块**: 7个
- **扫描频率**: Big4每15分钟，信号每5分钟，平仓每秒

---

## 核心架构

### 系统组件

```
┌─────────────────────────────────────────────────────────────┐
│                  超级大脑U本位合约交易系统 V3.0                │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ Big4趋势检测  │  │ 价格推送服务  │  │ 信号生成器   │     │
│  │ 30H+5H验证   │→ │ WebSocket    │→ │ 多维度评分   │     │
│  │ 深V反转识别  │  │ Price Cache  │  │ 55分阈值     │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│         ↓                                    ↓              │
│  ┌──────────────┐                    ┌──────────────┐     │
│  │ 分批建仓管理  │                    │ 信号过滤系统  │     │
│  │ 3批独立持仓  │←──────────────→   │ 黑名单+分级  │     │
│  │ K线回调确认  │                    │ 防护机制     │     │
│  └──────────────┘                    └──────────────┘     │
│                                             ↓              │
│                                      ┌──────────────┐     │
│                                      │ 智能交易服务  │     │
│                                      │ Smart Trader │     │
│                                      │ 3000+行代码  │     │
│                                      └──────────────┘     │
│                                             ↓              │
│         ┌───────────────────────────────────────┐         │
│         │          持仓监控系统                   │         │
│         │  • 8层风控检查                         │         │
│         │  • 实时盈亏监控                        │         │
│         │  • Big4紧急干预                       │         │
│         └───────────────────────────────────────┘         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 技术栈

- **语言**: Python 3.9+
- **框架**: FastAPI (异步Web框架)
- **数据库**: MySQL 8.0
- **交易所**: Binance Futures API (fapi.binance.com)
- **实时通讯**: WebSocket
- **调度**: schedule库 + asyncio

---

## 交易流程

### 完整交易生命周期

```
1. Big4趋势判断阶段 (15分钟一次，缓存1小时)
   ├─ 1H 30根K线分析 (大趋势)
   ├─ 1H 5根K线分析 (小趋势)
   ├─ 趋势修正逻辑:
   │  • 30H下跌+5H上涨 → NEUTRAL
   │  • 30H上涨+5H下跌 → NEUTRAL
   │  • 趋势一致 → 保持
   ├─ 深V反转识别:
   │  • 72H持续下跌 ≥ 3%
   │  • 24H加速下跌 ≥ 1.5%
   │  • 1H长下影线 ≥ 2%
   │  • 触发反弹窗口45分钟
   └─ 输出: BULLISH/BEARISH/NEUTRAL + 强度0-100

2. 信号产生阶段 (5分钟一次)
   ├─ K线数据加载 (1D/1H/15M)
   ├─ 多维度信号评分
   ├─ 综合评分 (阈值55分)
   └─ Big4过滤和仓位调整

3. 信号过滤阶段
   ├─ Big4方向过滤 (强度≥60直接跳过逆势)
   ├─ 黑名单检查
   ├─ 深V反转保护 (45分钟内禁止逆向开仓)
   └─ 仓位数量限制

4. 智能建仓阶段 (K线回调策略)
   ├─ 启动K线回调监控
   ├─ Batch 1 (30%): 15M阶段 (0-30分钟)
   │  • 等待15M反向K线
   │  • 创建独立持仓记录
   ├─ Batch 2 (30%): 5M阶段 (30-60分钟)
   │  • 等待5M反向K线
   │  • 创建独立持仓记录
   └─ Batch 3 (40%): 5M阶段 (需间隔5分钟)
      • 等待第2批完成≥5分钟
      • 创建独立持仓记录

5. 持仓监控阶段 (8层检查)
   ├─ 优先级1: 固定止损 (2%)
   ├─ 优先级2: 固定止盈 (6%)
   ├─ 优先级3: K线反转 + 亏损>1%
   ├─ 优先级4: 智能顶底识别
   ├─ 优先级5: 动态超时检查
   ├─ 优先级6: 分阶段超时 (1H-3H)
   ├─ 优先级7: 3小时绝对超时
   └─ 优先级8: K线强度衰减

6. 智能平仓阶段
   └─ 全部平仓 (不分批)
```

---

## Big4趋势判断系统

### 系统架构

**文件**: `app/services/big4_trend_detector.py`

**监控币种**:
```python
COIN_WEIGHTS = {
    'BTC/USDT': 0.50,  # 50% - 绝对领导者
    'ETH/USDT': 0.30,  # 30%
    'BNB/USDT': 0.10,  # 10%
    'SOL/USDT': 0.10   # 10%
}
```

### 30H+5H双重验证逻辑

**核心改进** (2026-02-12):

```
对每个币种 (BTC/ETH/BNB/SOL):

  ┌─ 1H K线 30根分析 (大趋势) ─┐
  │  ├─ 阳线≥18根 → BULL      │
  │  ├─ 阴线≥18根 → BEAR      │
  │  ├─ 阳线≥16根 → 中等多头  │
  │  ├─ 阴线≥16根 → 中等空头  │
  │  └─ 否则 → NEUTRAL        │
  └─────────────────────────┘
           ↓
  ┌─ 1H K线 5根分析 (小趋势) ─┐
  │  ├─ 阳线≥3根 → BULL       │
  │  ├─ 阴线≥3根 → BEAR       │
  │  └─ 否则 → NEUTRAL        │
  └─────────────────────────┘
           ↓
  ┌─ 趋势修正规则 ─────────┐
  │                          │
  │  IF 30H=BEAR AND 5H=BULL:
  │     修正为 NEUTRAL
  │     → 大趋势下跌但短期反弹
  │                          │
  │  IF 30H=BULL AND 5H=BEAR:
  │     修正为 NEUTRAL
  │     → 大趋势上涨但短期回调
  │                          │
  │  IF 30H=NEUTRAL:
  │     跟随5H小趋势
  │                          │
  │  ELSE (30H与5H一致):
  │     保持30H大趋势
  │                          │
  └─────────────────────────┘
```

### 深V反转识别机制 (2026-02-13新增)

#### 触底反弹策略 🚀

**设计理念**:
市场在持续下跌后出现深V反转时，往往是强力做多机会。系统通过多维度检测识别这种反转信号，并在45分钟窗口内禁止做空，避免逆势亏损。

**四重检测条件**:

```python
# 条件1: 72H持续下跌 (确认下跌趋势)
if price_change_72h <= -3%:
    condition_1 = True  # 长期下跌确立

# 条件2: 24H加速下跌 (识别恐慌杀跌)
if price_change_24h <= -1.5%:
    condition_2 = True  # 近期加速下跌

# 条件3: 1H长下影线 (捕捉抄底资金)
if lower_shadow_1h >= 2%:
    condition_3 = True  # 出现强力支撑
    # 下影线长度 = (最低价 - 收盘价) / 最低价

# 条件4: 价格反弹确认 (确认反转开始)
if current_price > lowest_price_4h:
    condition_4 = True  # 价格已开始反弹

# 触底反弹触发
if all([condition_1, condition_2, condition_3, condition_4]):
    trigger_bottom_bounce()
```

**触发效果**:
```python
# 1. 禁止做空45分钟
ban_short_trading = True
ban_until = current_time + timedelta(minutes=45)

# 2. 自动解除所有空头信号
cancel_all_short_signals()

# 3. 优先考虑做多机会
if long_signal and score >= 50:  # 降低阈值
    execute_long_position()
    position_size *= 1.2  # 加仓20%
```

**实际案例**:
```
场景: BTC从$90,000持续下跌至$87,000

时间线:
T-72H: BTC @ $90,000 (起点)
T-24H: BTC @ $88,500 (24H跌1.5%, 72H跌1.7%)
T-4H:  BTC @ $87,200 (4H最低点, 72H跌3.1%)
T-1H:  出现长下影线:
       最低 $87,000 → 收盘 $87,800
       下影线 = ($87,000 - $87,800) / $87,000 = 0.9%
       ❌ 未达到2%阈值，不触发

T-0H:  再次测试支撑:
       最低 $86,800 → 收盘 $88,500
       下影线 = ($86,800 - $88,500) / $86,800 = 1.96%
       价格反弹至 $88,500 (超过4H最低$87,200)

检测结果:
  ✅ 条件1: 72H跌3.6% (≥3%)
  ✅ 条件2: 24H跌1.7% (≥1.5%)
  ✅ 条件3: 1H下影线1.96% (≈2%)
  ✅ 条件4: 当前$88,500 > 4H最低$87,200

触发: 🎯 触底反弹 (45分钟保护窗口)

系统行为:
  • 禁止开空仓45分钟
  • 如有空头信号，自动取消
  • 如有做多信号(评分≥50)，立即执行
  • 做多仓位增加20%
```

#### 触顶回调策略 📉

**设计理念**:
与触底反弹对称，识别市场在持续上涨后的顶部回调信号。

**四重检测条件**:

```python
# 条件1: 72H持续上涨 (确认上涨趋势)
if price_change_72h >= 3%:
    condition_1 = True

# 条件2: 24H加速上涨 (识别追高情绪)
if price_change_24h >= 1.5%:
    condition_2 = True

# 条件3: 1H长上影线 (捕捉抛压)
if upper_shadow_1h >= 2%:
    condition_3 = True
    # 上影线长度 = (最高价 - 收盘价) / 最高价

# 条件4: 价格回调确认 (确认回调开始)
if current_price < highest_price_4h:
    condition_4 = True

# 触顶回调触发
if all([condition_1, condition_2, condition_3, condition_4]):
    trigger_top_pullback()
```

**触发效果**:
```python
# 1. 禁止做多45分钟
ban_long_trading = True
ban_until = current_time + timedelta(minutes=45)

# 2. 自动解除所有多头信号
cancel_all_long_signals()

# 3. 优先考虑做空机会
if short_signal and score >= 50:
    execute_short_position()
    position_size *= 1.2  # 加仓20%
```

**实际案例**:
```
场景: ETH从$3,200持续上涨至$3,400

时间线:
T-72H: ETH @ $3,200 (起点)
T-24H: ETH @ $3,300 (24H涨3.1%, 72H涨3.1%)
T-4H:  ETH @ $3,420 (4H最高点, 72H涨6.9%)
T-1H:  出现长上影线:
       最高 $3,450 → 收盘 $3,380
       上影线 = ($3,450 - $3,380) / $3,450 = 2.0%

检测结果:
  ✅ 条件1: 72H涨6.9% (≥3%)
  ✅ 条件2: 24H涨3.1% (≥1.5%)
  ✅ 条件3: 1H上影线2.0% (≥2%)
  ✅ 条件4: 当前$3,380 < 4H最高$3,420

触发: 🎯 触顶回调 (45分钟保护窗口)

系统行为:
  • 禁止开多仓45分钟
  • 如有多头信号，自动取消
  • 如有做空信号(评分≥50)，立即执行
  • 做空仓位增加20%
```

#### 保护窗口机制

**45分钟窗口设计**:
```python
# 为什么是45分钟？
# 1. 足够捕捉反转后的第一波行情
# 2. 避免过长导致错过趋势恢复
# 3. 对应3根15M K线，可观察确认

class ProtectionWindow:
    def __init__(self):
        self.ban_short_until = None
        self.ban_long_until = None

    def trigger_bottom_bounce(self):
        """触底反弹触发"""
        self.ban_short_until = datetime.now() + timedelta(minutes=45)
        logger.warning(f"🚀 触底反弹检测 | 禁止做空至 {self.ban_short_until}")

    def trigger_top_pullback(self):
        """触顶回调触发"""
        self.ban_long_until = datetime.now() + timedelta(minutes=45)
        logger.warning(f"📉 触顶回调检测 | 禁止做多至 {self.ban_long_until}")

    def is_short_allowed(self):
        """检查是否允许做空"""
        if self.ban_short_until and datetime.now() < self.ban_short_until:
            return False
        return True

    def is_long_allowed(self):
        """检查是否允许做多"""
        if self.ban_long_until and datetime.now() < self.ban_long_until:
            return False
        return True
```

#### 与其他策略的配合

**1. 与Big4趋势判断配合**:
```python
# 触底反弹 + Big4看多 = 强力做多信号
if bottom_bounce_triggered and big4_signal == 'BULLISH':
    position_size *= 1.5  # 加仓50%
    score_bonus += 20

# 触顶回调 + Big4看空 = 强力做空信号
if top_pullback_triggered and big4_signal == 'BEARISH':
    position_size *= 1.5
    score_bonus += 20
```

**2. 与分批建仓配合**:
```python
# 触底反弹时，加快建仓速度
if bottom_bounce_triggered:
    batch_1_timeout = 5   # 原15分钟 → 5分钟
    batch_2_timeout = 10  # 原30分钟 → 10分钟
    batch_3_timeout = 15  # 原60分钟 → 15分钟
```

**3. 与止盈止损配合**:
```python
# 触底反弹后的持仓，扩大止盈空间
if position.entry_reason == 'bottom_bounce':
    take_profit_pct = 8%  # 原6% → 8%
    stop_loss_pct = 2%    # 保持2%
```

### 开仓阈值规则

**1H K线评分** (30根):
- 阳线≥18根 → 强势40分
- 阳线≥16根 → 中等30分
- 阴线≥18根 → 空头-40分
- 阴线≥16根 → 空头-30分

**15M K线评分** (16根):
- 阳线≥11根 → 强势30分
- 阳线≥9根 → 中等20分
- 阴线≥11根 → 空头-30分
- 阴线≥9根 → 空头-20分

**5M反向加分** (3根):
- 主趋势多头 + 5M 3根阴 → +10分
- 主趋势多头 + 5M 2根阴 → +5分

**综合判断**: 加权总分 ≥ 50分 → 开仓信号

---

## 信号系统

### 开仓评分系统

**文件**: `smart_trader_service.py`

**开仓阈值**: 55分 (理论最大232分，55分≈24%强度)

**信号组合评分**:
```python
# 多维度信号组合
position_low + trend_1h_bull + volume_power_bull → 65分
position_high + trend_1h_bear + volume_power_bear → 65分
momentum_up_3pct + consecutive_bull → 25分
breakout_long + volatility_high → 30分
```

### Big4过滤规则

**顺势加分**:
```python
if (big4 == 'BULLISH' and side == 'LONG') or \
   (big4 == 'BEARISH' and side == 'SHORT'):
    bonus = min(20, signal_strength * 0.3)
    final_score += bonus
    position_multiplier = 1.2  # 仓位+20%
```

**逆势惩罚**:
```python
if big4_strength >= 60:
    skip_this_signal()  # 强势市场直接跳过逆势信号
else:
    penalty = signal_strength * 0.5
    final_score -= penalty
```

### 信号黑名单制度

| 等级 | 保证金倍数 | 说明 |
|------|----------|------|
| Level0 | 1.0 | 白名单（默认） |
| Level1 | 0.75 | 警告：降25% |
| Level2 | 0.875 | 谨慎：降12.5% |
| Level3 | 0 | 永久禁止 |

**白名单保护**: BTC, ETH, BNB, SOL永不进入黑名单

---

## 风险控制

### 仓位管理

**单仓位配置**:
```yaml
base_margin: 400 USDT      # 基础保证金
leverage: 5x               # 杠杆倍数
stop_loss: 2%              # 止损比例
take_profit: 6%            # 止盈比例
```

**动态仓位调整**:
```python
# 1. 币种分级调整
if rating_level == 1:
    margin *= 0.75   # Level1: -25%
elif rating_level == 2:
    margin *= 0.875  # Level2: -12.5%
elif rating_level == 3:
    margin *= 0      # Level3: 禁止

# 2. Big4顺势加仓
if (big4 == 'BULLISH' and side == 'LONG') or \
   (big4 == 'BEARISH' and side == 'SHORT'):
    margin *= 1.2    # 顺势+20%
```

### 8层风控体系

```
┌─────────────────────────────────────────────┐
│ 优先级0: 最小持仓2小时限制                   │
│ • 开仓2小时内只允许止损/止盈                 │
├─────────────────────────────────────────────┤
│ 优先级1: 固定止损 (2%)                      │
│ • 无延迟，立即市价平仓                      │
├─────────────────────────────────────────────┤
│ 优先级2: 固定止盈 (6%)                      │
│ • 无延迟，立即市价平仓                      │
├─────────────────────────────────────────────┤
│ 优先级3: K线强度衰减 + 亏损反转              │
│ • 亏损>1% AND K线方向反转                   │
├─────────────────────────────────────────────┤
│ 优先级4: 智能顶底识别                       │
│ • LONG顶部: 盈利≥2% + 强烈看空             │
│ • SHORT底部: 盈利≥2% + 强烈看多            │
├─────────────────────────────────────────────┤
│ 优先级5: 动态超时检查                       │
│ • 检查timeout_at字段                        │
├─────────────────────────────────────────────┤
│ 优先级6: 分阶段超时 (1H/2H/3H)              │
│ • 1H: -2.5%亏损 → 平仓                     │
│ • 2H: -2.0%亏损 → 平仓                     │
│ • 3H: -1.5%亏损 → 平仓                     │
├─────────────────────────────────────────────┤
│ 优先级7: 3小时绝对强制平仓                  │
│ • max_hold_minutes = 180分钟               │
├─────────────────────────────────────────────┤
│ 优先级8: K线强度衰减检查                    │
│ • 每15分钟检查一次                          │
└─────────────────────────────────────────────┘
```

---

## 智能建仓与平仓

### 分批建仓 (BatchPositionManager + KlinePullbackEntryExecutor)

**文件**:
- `app/services/batch_position_manager.py`
- `app/services/kline_pullback_entry_executor.py`

**3批配置**:
```yaml
batch_1: 30%  # 15M阶段 (0-30分钟)
batch_2: 30%  # 5M阶段 (30-60分钟)
batch_3: 40%  # 5M阶段 (需间隔≥5分钟)
```

**K线回调策略**:
```python
阶段1 (0-30分钟): 监控15M K线
  • 做多: 等待1根15M阴线 (close < open)
  • 做空: 等待1根15M阳线 (close > open)
  • 触发: 执行第1批建仓 (30%)

阶段2 (30-60分钟): 监控5M K线
  • 做多: 等待1根5M阴线
  • 做空: 等待1根5M阳线
  • 触发: 执行第2批建仓 (30%)

阶段3 (第2批后≥5分钟):
  • 间隔检查: 距第2批≥5分钟
  • 触发: 执行第3批建仓 (40%)
```

**每批独立持仓**:
```python
# 每批都创建独立的 position_id
position_1 = create_position(batch_num=0, ratio=0.3)  # ID: 1001
position_2 = create_position(batch_num=1, ratio=0.3)  # ID: 1002
position_3 = create_position(batch_num=2, ratio=0.4)  # ID: 1003
```

### 智能平仓

**平仓策略**: 全部平仓 (不分批)

```python
def close_position(position_id):
    """
    平仓时一次性平掉全部持仓
    • 不使用分批平仓逻辑
    • realized_pnl 直接赋值
    • 释放全部保证金
    """
```

**智能监控策略**:

虽然平仓不再分批，但监控策略更加精细化，从开仓开始就持续监控盈亏情况。

#### 1. 基础监控规则

```
时间线监控:
├─ 0-30分钟: 仅监控止损(2%)和止盈(6%)
├─ 30分钟后: 启动智能监控系统
└─ 接近3小时: 启动最优价格评估体系
```

#### 2. 亏损监控策略 (30分钟后启动)

**策略A - 2%亏损快速止损**:
```python
if unrealized_pnl_pct <= -2%:
    # 连续观察2根5M K线
    watch_5m_candles(count=2)

    if not improved_in_2_candles:
        # 没有好转，立即平仓
        close_position_immediately()
        reason = "亏损2%+5M K线无好转"
```

**实际案例**:
```
场景: LONG BTC @ $88,000，当前价格 $86,240 (亏损2.0%)

T+35分钟: 检测到亏损2.0%，启动5M监控
T+40分钟: 观察第1根5M K线
   - Open: $86,240 → Close: $86,180
   - 继续下跌，未好转
T+45分钟: 观察第2根5M K线
   - Open: $86,180 → Close: $86,100
   - 继续下跌，确认无好转

✅ 触发平仓: 2根5M K线均未好转，立即市价平仓
```

**策略B - 1%亏损谨慎观察**:
```python
if unrealized_pnl_pct <= -1%:
    # 连续观察2根15M K线
    watch_15m_candles(count=2)

    if not sustainably_improved:
        # 没有持续好转，平仓
        close_position()
        reason = "亏损1%+15M K线无持续好转"
```

**实际案例**:
```
场景: SHORT ETH @ $3,200，当前价格 $3,232 (亏损1.0%)

T+40分钟: 检测到亏损1.0%，启动15M监控
T+55分钟: 观察第1根15M K线
   - Open: $3,232 → Close: $3,228
   - 稍有好转(回落4 USDT)
T+70分钟: 观察第2根15M K线
   - Open: $3,228 → Close: $3,235
   - 反弹回升，确认无持续好转

✅ 触发平仓: 虽然第1根好转，但第2根反弹，无持续好转
```

#### 3. 盈利监控策略

**策略C - 小额盈利自由发育**:
```python
if 0% < unrealized_pnl_pct < 1%:
    # 任由其发育，不进行干预
    monitor_only()
    reason = "小额盈利，观察阶段"
```

**策略D - 2%盈利回撤保护**:
```python
if unrealized_pnl_pct >= 2%:
    # 记录最高盈利点
    max_profit = max(max_profit, unrealized_pnl_pct)

    # 计算回撤比例
    drawdown = max_profit - unrealized_pnl_pct

    if drawdown >= 0.5%:
        # 回撤超过0.5%，立即止盈
        close_position_immediately()
        reason = "盈利2%+回撤0.5%触发止盈"
```

**实际案例**:
```
场景: LONG BNB @ $600，持续上涨

T+45分钟: $612 (盈利2.0%)，记录max_profit=2.0%
T+60分钟: $615 (盈利2.5%)，更新max_profit=2.5%
T+75分钟: $618 (盈利3.0%)，更新max_profit=3.0%
T+80分钟: $615 (盈利2.5%)，回撤0.5%

✅ 触发止盈:
   - 当前盈利2.5% (≥2%)
   - 回撤 = 3.0% - 2.5% = 0.5% (≥0.5%)
   - 立即市价平仓锁定利润
```

#### 4. 最优价格评估体系 (接近3小时时启动)

**时间窗口**: 持仓时间超过2.5小时 (150分钟) 时启动

```python
if hold_minutes >= 150:
    # 启动最优价格评估
    optimal_price_finder.start()

    # 评估策略
    price_samples = []
    for i in range(30):  # 30分钟采样
        price_samples.append(current_price)

    # 寻找最优平仓价格
    if current_pnl > 0:
        # 盈利状态: 寻找局部高点
        if is_local_peak(current_price, price_samples):
            close_position()
            reason = "最优价格评估-局部高点"
    else:
        # 亏损状态: 寻找局部低点（止损价附近）
        if is_relative_recovery(current_price, price_samples):
            close_position()
            reason = "最优价格评估-相对回升"
```

**实际案例 - 盈利场景**:
```
场景: LONG SOL @ $100，持仓2.5小时，当前盈利1.8%

T+150分钟: 启动最优价格评估系统
   - 采样价格: [$101.5, $101.7, $101.8, $101.9, $102.0, $101.9]
   - 当前价格 $102.0 为最近15分钟局部高点
   - 下一根K线 $101.9 开始回落

✅ 触发平仓: 识别局部高点 $102.0，执行平仓
```

**实际案例 - 亏损场景**:
```
场景: SHORT ADA @ $0.50，持仓2.5小时，当前亏损1.2%

T+150分钟: 启动最优价格评估系统
   - 采样价格: [$0.506, $0.507, $0.506, $0.505, $0.504]
   - 当前价格 $0.504 出现回落（相对回升）
   - 判断为短期最优价格

✅ 触发平仓: 识别相对回升点 $0.504，减少亏损
```

#### 5. 监控优先级

```
监控策略执行优先级:

优先级0: 固定止损 (2%) - 立即平仓
优先级1: 固定止盈 (6%) - 立即平仓
优先级2: 亏损2% + 2根5M无好转 - 快速平仓
优先级3: 盈利2% + 回撤0.5% - 锁定利润
优先级4: 亏损1% + 2根15M无持续好转 - 谨慎平仓
优先级5: 最优价格评估 (150分钟后) - 智能择时
优先级6: 3小时绝对超时 (180分钟) - 强制平仓
```

#### 6. 代码实现示例

```python
class IntelligentCloseMonitor:
    """智能平仓监控器"""

    def __init__(self, position):
        self.position = position
        self.max_profit = 0
        self.candle_5m_buffer = []
        self.candle_15m_buffer = []
        self.price_samples = []

    def check_close_conditions(self):
        """检查平仓条件"""
        pnl_pct = self.position.unrealized_pnl_pct
        hold_minutes = self.get_hold_minutes()

        # 前30分钟仅检查止损止盈
        if hold_minutes < 30:
            if pnl_pct <= -2:
                return "固定止损", True
            if pnl_pct >= 6:
                return "固定止盈", True
            return None, False

        # 30分钟后启动智能监控

        # 策略A: 亏损2% + 5M监控
        if pnl_pct <= -2:
            if self.check_5m_no_improvement():
                return "亏损2%+5M无好转", True

        # 策略D: 盈利2% + 回撤0.5%
        if pnl_pct >= 2:
            self.max_profit = max(self.max_profit, pnl_pct)
            drawdown = self.max_profit - pnl_pct
            if drawdown >= 0.5:
                return "盈利2%+回撤0.5%", True

        # 策略B: 亏损1% + 15M监控
        if pnl_pct <= -1:
            if self.check_15m_no_sustained_improvement():
                return "亏损1%+15M无持续好转", True

        # 策略: 最优价格评估 (150分钟后)
        if hold_minutes >= 150:
            if self.find_optimal_price():
                return "最优价格评估", True

        # 绝对超时
        if hold_minutes >= 180:
            return "3小时绝对超时", True

        return None, False

    def check_5m_no_improvement(self):
        """检查2根5M K线是否无好转"""
        if len(self.candle_5m_buffer) < 2:
            return False

        candle_1, candle_2 = self.candle_5m_buffer[-2:]

        # 判断是否持续恶化或无明显好转
        if self.position.position_side == 'LONG':
            # 多仓: 期待价格上涨
            if candle_2.close <= candle_1.close:
                return True  # 继续下跌或横盘
        else:
            # 空仓: 期待价格下跌
            if candle_2.close >= candle_1.close:
                return True  # 继续上涨或横盘

        return False

    def check_15m_no_sustained_improvement(self):
        """检查2根15M K线是否无持续好转"""
        if len(self.candle_15m_buffer) < 2:
            return False

        candle_1, candle_2 = self.candle_15m_buffer[-2:]

        # 判断是否持续好转
        if self.position.position_side == 'LONG':
            # 第1根好转但第2根反转
            if candle_1.close > candle_1.open and candle_2.close < candle_1.close:
                return True
        else:
            if candle_1.close < candle_1.open and candle_2.close > candle_1.close:
                return True

        return False

    def find_optimal_price(self):
        """寻找最优平仓价格"""
        if len(self.price_samples) < 10:
            return False

        current_price = self.price_samples[-1]
        recent_prices = self.price_samples[-30:]  # 最近30分钟

        if self.position.unrealized_pnl_pct > 0:
            # 盈利: 寻找局部高点
            if current_price >= max(recent_prices[-10:]):
                return True
        else:
            # 亏损: 寻找相对回升
            if current_price <= min(recent_prices[-10:]):
                return True

        return False
```

---

## 性能指标

### 关键绩效指标

**盈利指标**:
- 胜率 (Win Rate): >55%
- 盈亏比 (P/L Ratio): >2.0
- 利润因子 (Profit Factor): >1.5

**风险指标**:
- 最大回撤 (Max Drawdown): <15%
- 夏普比率 (Sharpe Ratio): >1.5

**交易指标**:
- 平均持仓时间: 3小时
- 平均入场评分: >55分
- 日均交易数: 10-20笔

---

## 运维管理

### 启动服务

```bash
# 直接运行
python smart_trader_service.py

# 后台运行
nohup python smart_trader_service.py > logs/smart_trader.log 2>&1 &
```

### 日志监控

**关键日志标签**:
```
[BIG4-MARKET]    - Big4市场趋势判断
[BIG4-CORRECT]   - 趋势修正逻辑
[BIG4-DEEPV]     - 深V反转识别
[KLINE_PULLBACK] - K线回调建仓
[BATCH-ENTRY]    - 分批建仓执行
[BLACKLIST]      - 信号黑名单过滤
```

### 常用命令

```bash
# 查看当前持仓
mysql -e "SELECT symbol, position_side, unrealized_pnl_pct, status
FROM futures_positions WHERE account_id=2 AND status='open';"

# 查看24H盈亏
mysql -e "SELECT SUM(realized_pnl) as total_pnl, COUNT(*) as trades
FROM futures_positions WHERE account_id=2 AND
close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR);"

# 查看Big4状态
mysql -e "SELECT * FROM big4_trend_signals
ORDER BY checked_at DESC LIMIT 1;"
```

---

## 总结

超级大脑U本位合约交易系统 V3.0 通过以下核心机制实现稳定盈利:

1. ✅ **30H+5H双重趋势验证** - 避免趋势转折点逆势开仓
2. ✅ **Big4权重判断** - BTC 50% / ETH 30% / BNB 10% / SOL 10%
3. ✅ **深V反转识别** - 45分钟保护窗口，防止市场急转直下时逆向开仓
4. ✅ **智能分批建仓** - 3批K线回调确认，每批独立持仓
5. ✅ **8层风控体系** - 从固定止损到智能平仓全覆盖
6. ✅ **开仓阈值55分** - 确保只交易高质量信号

**关键参数**:
- 开仓阈值: 55分
- 分批比例: 30% / 30% / 40%
- 止损止盈: 2% / 6%
- 最大持仓: 3小时
- 杠杆倍数: 5倍

---

**文档版本**: V3.0
**最后更新**: 2026-02-21
**维护者**: 超级大脑开发团队
