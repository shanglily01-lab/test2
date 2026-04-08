# U本位合约开仓逻辑全解析

> 基于代码实现整理，2026-04-08 更新
> 覆盖范围：主策略 / BTC动量 / 预测器 / U本位破位，共4种策略

---

## 目录

0. [开关层级（总开关与子开关）](#0-开关层级总开关与子开关)
1. [系统全貌](#1-系统全貌)
2. [主循环时序](#2-主循环时序)
3. [全局过滤器（开仓前置条件）](#3-全局过滤器)
4. [Big4趋势检测](#4-big4趋势检测)
5. [V1评分系统（14信号组件）](#5-v1评分系统)
6. [V2协同过滤](#6-v2协同过滤)
7. [主策略（SmartTrader）完整开仓流程](#7-主策略完整开仓流程)
8. [BTC动量策略](#8-btc动量策略)
9. [预测器策略](#9-预测器策略)
10. [U本位破位策略](#10-u本位破位策略)
11. [平仓逻辑（SmartExitOptimizer）](#11-平仓逻辑)
12. [关键参数速查表](#12-关键参数速查表)
13. [开关一览](#13-开关一览)

---

## 0. 开关层级（总开关与子开关）

**总开关** `u_futures_trading_enabled`：关了 → 下面全部不能新开仓；开了 → 再看各自子开关。

**子开关**（总开关打开后才看）：

- `predictor_enabled` → 预测神器  
- `btc_momentum_enabled` → BTC 动量  
- `u_coin_style_enabled` → U本位破位  
- `signal_confirmation_enabled` / `trend_following_enabled` → 主策略里的信号确认 / 趋势跟随（可只开其一）

实盘下单另看 `live_trading_enabled`。

---

## 1. 系统全貌

### 四种策略并行运行，共享 account_id=2

| 策略 | 文件 | source字段 | 扫描间隔 | 子开关 | Big4过滤 |
|------|------|-----------|---------|--------|----------|
| 主策略（信号确认/趋势跟随） | `smart_trader_service.py` | `signal_confirm` / `trend_follow` | 300秒 | 见 §0 | **是** |
| BTC动量跟随 | `app/services/btc_momentum_trader.py` | `BTC_MOMENTUM` | 每分钟 | `btc_momentum_enabled` | **否** |
| 市场预测器（预测神器） | `app/services/market_predictor.py` | `PREDICTOR` | 每6小时 | `predictor_enabled` | **否** |
| U本位破位 | `u_coin_style_trader_service.py` | `u_coin_style` | 300秒 | `u_coin_style_enabled` | **否** |

支线（预测/动量/破位）不走主策略的 Big4 门控；主策略 Big4=NEUTRAL 时本身不开仓，不等于支线停。U 破位扫描池同主策略 `config.yaml` 的 `symbols`。

### 核心账户参数

```
account_id         = 2           # 所有4种策略共用
leverage           = 5           # 5倍杠杆（固定）
默认保证金          = 400 U
黑名单L1保证金       = 100 U（rating_level=1）
黑名单L2保证金       = 50 U（rating_level=2）
rating_level=3     → 永久禁止，不扫描
```

---

## 2. 主循环时序

`smart_trader_service.py` 每 **300秒** 执行一轮，顺序如下：

```
① 实盘对账          每5分钟   检查持仓与交易所是否一致，修复不一致
② 黑名单重载        每5分钟   重新加载 trading_symbol_rating + config.yaml 白名单
③ 配置重载          每30分钟  重新加载 signal_scoring_weights + adaptive_params
④ 开关检查          每轮      u_futures_trading_enabled = 0 → 跳过本轮
⑤ 盈利熔断          每4小时   过去6H盈利 > 1000U → 自动禁止开仓
⑥ 亏损熔断          每30分钟  过去3H亏损 > 300U → 自动禁止开仓
⑦ Big4趋势检测      每轮      获取 overall_signal / signal_strength / emergency_intervention
⑧ 信号扫描          每轮      遍历白名单，调用 analyze()，生成机会列表
⑨ 羊群防护          每轮      LONG机会 > 60% → 阈值+5；SHORT > 60% → 全部放行
⑩ 开仓执行          每轮      调用 open_position()，写DB，实盘同步
⑪ 平仓检查          每轮      SmartExitOptimizer 检查所有 open 持仓
```

---

## 3. 全局过滤器

> **适用范围：** 本节及后文「`analyze()` 内过滤」「执行层 Big4 强度」等，主要针对 **主策略** `smart_trader_service.py`。**预测神器**、**BTC 动量**、**U本位破位**不走本节所述 Big4 门控（见第 8、9、10 节）。

以下条件任一不满足，**整轮或单个币种**直接跳过：

### 3.1 交易开关（整轮级别）

| 开关 | 说明 | 触发禁止的条件 |
|------|------|-------------|
| `u_futures_trading_enabled` | **U本位总开关**（预测/动量/破位/主策略共通，见 [§0](#0-开关层级总开关与子开关)） | 手动或熔断后自动设为 0 → 上述全部停新开仓 |
| `allow_long` | 允许做多 | 手动关闭 |
| `allow_short` | 允许做空 | 手动关闭 |

### 3.2 熔断（整轮级别）

| 类型 | 检测窗口 | 阈值 | 检测频率 | 效果 |
|------|---------|------|---------|------|
| 盈利熔断 | 过去6小时 | 盈利 > 1000U | 每4小时 | 自动将 `u_futures_trading_enabled` 设为 0 |
| 亏损熔断 | 过去3小时 | 亏损 > 300U | 每30分钟 | 自动将 `u_futures_trading_enabled` 设为 0 |

### 3.3 持仓数量（整轮级别）

```
当前持仓数 >= max_positions（默认50，从 system_settings 读取）→ 跳过本轮扫描
```

### 3.4 Big4紧急干预（整轮级别）

来自 `emergency_intervention` 表（见第4节）：

```
block_long = True  → 本轮所有LONG信号被丢弃
block_short = True → 本轮所有SHORT信号被丢弃
两者同时 = True    → 冲突自动解除，全部放行（2026-04-08修复）
同类型封锁 > 6小时 → 不再续期，强制解封（2026-04-08修复）
```

### 3.5 单币种级别过滤（在 analyze() 内）

| 过滤条件 | 处理 |
|---------|------|
| `rating_level = 3` | 跳过，不分析 |
| 已有同向仓位 | 跳过（防重复） |
| 评分 > 150 分 | 拒绝（防追涨杀跌） |
| 信号数 < 2 | 拒绝 |
| 信号方向矛盾 | 拒绝（如做多但含空头信号） |
| 信号黑名单命中 | 拒绝 |
| V2方向冲突 + V1未达阈值+25分 | 拒绝 |
| TOP50过滤（实盘模式） | `live_trading_enabled=1` 时，不在 TOP50 内则拒绝 |

---

## 4. Big4趋势检测

### 4.1 权重配置

```
BTC/USDT  → 50%
ETH/USDT  → 30%
BNB/USDT  → 10%
SOL/USDT  → 10%
```

### 4.2 信号分类及对主策略的影响

| Big4信号 | 触发条件 | 主策略行为 | 开仓阈值 |
|---------|---------|----------|---------|
| `STRONG_BULLISH` | 强多权重 ≥ 60% | 信号确认多头，**禁止做空** | LONG ≥ 55分 |
| `BULLISH` | 总多权重 ≥ 60% | 趋势跟随多头 | LONG ≥ 20分 |
| `NEUTRAL` | 多空均衡 | **禁止开仓** | — |
| `BEARISH` | 总空权重 ≥ 60% | 趋势跟随空头 | SHORT ≥ 20分 |
| `STRONG_BEARISH` | 强空权重 ≥ 60% | 信号确认空头，**禁止做多** | SHORT ≥ 55分 |

> **策略模式标记（source字段细分）：**
> - STRONG_BULLISH / STRONG_BEARISH → `source = 'signal_confirm'`
> - BULLISH / BEARISH → `source = 'trend_follow'`

### 4.3 紧急干预（emergency_intervention）

**触发条件（4H窗口检测）：**

```
触底反转（BOTTOM_BOUNCE）：
  - 4H内最高→最低跌幅 ≤ -5%
  - 当前价从最低点回升 > 0%
  - 效果：block_short = True（禁止做空2小时）

触顶回调（TOP_REVERSAL）：
  - 4H内最低→最高涨幅 ≥ 5%
  - 当前价从最高点回落 < 0%
  - 效果：block_long = True（禁止做多2小时）
```

**冲突与解除规则（2026-04-08修复）：**

```
情形1：同一4H窗口同时检测到触底+触顶
  → 逻辑矛盾，双重封锁自动解除，不写DB记录

情形2：DB存量记录 block_long=True AND block_short=True
  → 自动解除双重封锁，恢复正常交易

情形3：同类型封锁累计超过 6 小时
  → 不再续期，强制解封（MAX_BLOCK_TOTAL_HOURS = 6）
```

---

## 5. V1评分系统

### 5.1 14个信号组件（权重来自 DB `signal_scoring_weights` 表）

| 信号组件 | 触发条件 | LONG权重 | SHORT权重 |
|---------|---------|---------|----------|
| `position_low` | 价格在100根K线区间 < 30% | 11 | 0 |
| `position_mid` | 价格在100根K线区间 30~70% | 10 | 30 |
| `position_high` | 价格在100根K线区间 > 70% | 0 | 30（注1） |
| `position_24h_low` | 价格在24H区间 < 30% | 12 | 0 |
| `position_24h_high` | 价格在24H区间 > 70% | 0 | 21 |
| `momentum_up_3pct` | 24H涨幅 > 3% | 15 | 0 |
| `momentum_down_3pct` | 24H跌幅 < -3% | 0 | 24 |
| `trend_1h_bull` | 1H近30根阳线占比 > 60% | 11 | 0 |
| `trend_1h_bear` | 1H近30根阳线占比 < 40% | 0 | 30 |
| `consecutive_bull` | 连续看涨K线 | 15 | 0 |
| `consecutive_bear` | 连续看跌K线 | 0 | 26 |
| `volume_power_bull` | 1H+15M净量能均为多头（双重确认） | 25 | 0 |
| `volume_power_bear` | 1H+15M净量能均为空头（双重确认） | 0 | 30 |
| `volume_power_1h_bull` | 仅1H净量能为多头 | 25 | 0 |
| `volume_power_1h_bear` | 仅1H净量能为空头 | 0 | 30 |
| `breakout_long` | 高位+量能+1H净量多头（追涨） | 30 | 0 |
| `breakdown_short` | 低位+量能+1H净量空头（追空） | 0 | 30 |
| `volatility_high` | 波动率较高 | 10 | 30 |
| `volume_power_12x_bull` | 15M量能 ≥ 12倍均值，多头 | 15 | 0 |
| `volume_power_12x_bear` | 15M量能 ≥ 12倍均值，空头 | 0 | 27 |
| `big4_strong_bull_cont` | Big4=STRONG_BULLISH + 24H涨幅>2% + 价格>50% | 15 | 0 |

> **注1**：`position_high` 做多被信号清洗阶段强制过滤（高位禁止做多）

**理论满分：**
- LONG：约 179 分（`trend_1d_bull` 已移除，对应20分权重为死重量）
- SHORT：约 328 分

### 5.2 开仓资格判断

```python
# STRONG信号 → 信号确认模式
STRONG_BULLISH：long_score >= 55  → side='LONG'
STRONG_BEARISH：short_score >= 55 → side='SHORT'

# 普通信号 → 趋势跟随模式
BULLISH：long_score >= 20   → side='LONG'
BEARISH：short_score >= 20  → side='SHORT'

# 两方向都满足时，选分数更高的方向
# NEUTRAL：不开仓
```

### 5.3 信号清洗与验证

```
① 清洗：只保留与最终方向一致的信号组件（多头信号只留做多时）
② 最小信号数：清洗后 < 2 个有效信号 → 拒绝
③ position_mid要求：含此信号则至少需要3个信号才放行
④ 1H时间框架一致性：
   - 做多但含 trend_1h_bear → 拒绝
   - 做空但含 trend_1h_bull → 拒绝
⑤ 评分上限：score > 150 → 拒绝（防追涨杀跌）
```

### 5.4 羊群行为防护（扫描后）

```
若 LONG机会 / 总机会 > 60%：
  → 所有LONG信号的阈值额外 +5分（趋势跟随：25分；信号确认：60分）
  → 目的：过滤低质量跟风信号

若 SHORT机会 / 总机会 > 60%：
  → 全部放行（恐慌做空被视为趋势力量）
```

---

## 6. V2协同过滤

基于 `coin_kline_scores` 表的 5 分钟滚动评分。

### 6.1 过滤逻辑

```
① 数据新鲜度检查：
   updated_at 超过 15 分钟 → 忽略V2过滤，直接通过

② 方向一致性检查：
   V2方向 与 V1方向 相同 → 通过
   V2方向 与 V1方向 相反 AND V2强度 < 20 → 视为噪音，通过
   V2方向 与 V1方向 相反 AND V2强度 ≥ 20 → 触发软阻断：
     V1分数 ≥ (当前模式阈值 + 25) → 谨慎放行（V2滞后5-15分钟）
     V1分数 < (当前模式阈值 + 25) → 拒绝
```

**软阻断门槛计算（2026-04-08修复）：**

| 策略模式 | 当前阈值 | 软阻断门槛 |
|---------|---------|----------|
| signal_confirm | 55 | 80 分 |
| trend_follow | 20 | 45 分 |

---

## 7. 主策略完整开仓流程

```
open_position(opp) 被调用

    ├─ 验证 symbol 格式（禁止/USDT:USDT格式进入此函数）
    ├─ 检查已有持仓（has_position → 防重复，防对冲）
    ├─ 检查 TOP50（live_trading_enabled=1 时）
    ├─ 检查 rating_level（=3 → 拒绝）
    │
    ├─ 【价格采样建仓（SmartEntryExecutor）】
    │   ├─ 15分钟内采样，寻找最优入场价
    │   ├─ 一次性下单（非分批）
    │   └─ 采样失败 → fallback 到立即开仓
    │
    ├─ 计算开仓参数：
    │   ├─ margin = 400U（正常）或 100U（L1）或 50U（L2）
    │   ├─ quantity = margin * leverage / entry_price
    │   ├─ stop_loss_price = entry * (1 ∓ SL%)
    │   └─ take_profit_price = entry * (1 ± TP%)
    │
    ├─ 写入 futures_positions 表：
    │   ├─ account_id = 2
    │   ├─ source = 'signal_confirm' 或 'trend_follow'
    │   ├─ status = 'open'
    │   ├─ max_hold_minutes = max_hold_hours * 60（从DB读取）
    │   └─ planned_close_time = NOW() + max_hold_minutes
    │
    └─ 实盘同步：
        live_trading_enabled=1 → 调用 BinanceFuturesEngine.open_position()
        live_trading_enabled=0 → 仅记录到DB（模拟盘）
```

**止损止盈参数来源（按优先级）：**

```
1. system_settings.stop_loss_pct / take_profit_pct（每次读取，实时生效）
2. adaptive_params 表（LONG/SHORT方向分别配置）
3. 硬编码默认值：SL=3%, TP=2%（兜底）
```

---

## 8. BTC动量策略

**文件：** `app/services/btc_momentum_trader.py`

**Big4：** 本策略**不经过**主策略的 Big4 过滤（无 `overall_signal`/强度门控、无 `analyze()`）。触发与下单仅依赖 BTC 涨跌幅窗口、冷却、`btc_momentum_enabled` 与 `u_futures_trading_enabled` 等。

### 触发条件

```
检测BTC过去 [15, 30, 45, 60] 分钟内的涨跌幅
任意窗口 |涨跌幅| ≥ 1.5% → 触发
4小时内只触发一次（COOLDOWN = 4H，从 system_settings.max_hold_hours 读取）
```

### 开仓逻辑

```
触发方向 = BTC涨 → 做多；BTC跌 → 做空

对 TOP50 所有交易对：
  已有同向仓位 → 保留不动
  已有反向仓位 → 先平反向，再开同向
  无仓位       → 直接开仓

参数：
  margin   = 模拟盘400U / 实盘100U（每账号最多5个实盘仓位）
  leverage = 5
  SL       = 2%（从 system_settings 读取）
  TP       = 6%（从 system_settings 读取）
  source   = 'BTC_MOMENTUM'
  planned_close_time = NOW() + max_hold_hours
```

### 实盘条件

```
live_trading_enabled = 1
AND symbol in TOP50
AND 当前该API账号实盘仓位 < 5
→ 调用 BinanceFuturesEngine 真实下单
```

---

## 9. 预测器策略

**文件：** `app/services/market_predictor.py`

**Big4：** 预测神器**不经过**主策略的 Big4 过滤；`run_all()` 仅检查 `predictor_enabled`、`u_futures_trading_enabled` 及评级黑名单等，按 K 线模型算置信度并下单，与 Big4 `NEUTRAL`/强度无关。

### 两类单子

| 类型 | 存储位置 | confidence 要求 | 保证金 | 最大单数 |
|------|---------|---------------|--------|---------|
| 虚拟单（回测验证） | `prediction_backtest` | ≥ 40% | — | 不限 |
| 真实模拟单 | `futures_positions` | ≥ 70% | 100U×5 | 5单 |

### 真实模拟单参数

```
source   = 'PREDICTOR'
margin   = 100U（L1黑名单已修复，commit 1467980）
leverage = 5
SL/TP    = 2% / 6%（从 system_settings 读取）
planned_close_time = 开仓后 6H（由 max_hold_hours 控制）
```

### 触发频率

每6小时运行一次，从最新市场数据生成预测。

---

## 10. U本位破位策略

**文件：** `u_coin_style_trader_service.py`

**Big4：** 与主策略独立，**不**做 Big4 `NEUTRAL` 整轮跳过、紧急干预拦截、方向过滤或 Big4 动态保证金；评分仅用 K 线组件 + `BreakoutSystem` 破位加权。

### 核心参数

```
account_id   = 2（与主策略共享持仓池）
MAX_POSITIONS = 20
THRESHOLD    = 60（开仓基础阈值）
LEVERAGE     = 5
SCAN_INTERVAL = 300秒
开关          = system_settings.u_coin_style_enabled
```

### 独立权重

使用 `signal_scoring_weights WHERE strategy_type='u_coin_style'`，与主策略默认权重完全隔离，14个组件独立配置。

### 保证金分级

```
rating_level = 0 → 400U
rating_level = 1 → 100U
rating_level = 2 → 50U
rating_level = 3 → 禁止
```

### 特殊机制

```
BreakoutSystem：价格/结构破位检测，对信号加权评分，强平反向持仓（非 Big4 市场门控）
扫描池：`config.yaml` 的 `symbols`（排除 rating≥3）；实盘条件：live_trading_enabled=1 AND 当前 API 实盘仓位 < 5
熔断：3小时亏损 > 300U → 自动停止
持仓池与主策略/BTC动量/预测器共享：has_position 不过滤 source（防重复+防对冲）
source = 'u_coin_style'
```

---

## 11. 平仓逻辑

**SmartExitOptimizer** 每轮循环检查所有 open 持仓，按以下优先级顺序执行：

| 优先级 | 条件 | 动作 |
|-------|------|------|
| 1 | 固定止损命中 | 立即平仓（止损） |
| 2 | 固定止盈命中 | 立即平仓（止盈） |
| 3 | 15M连续3根逆向K线 | 趋势反转，平仓 |
| 4 | V2信号反向 + 当前亏损 | 果断止损（防沉没成本） |
| 5 | `timeout_at` 到期 | 动态超时平仓 |
| 6 | `planned_close_time` 前30分钟 | 智能平仓（盈则平，亏则等） |
| 7 | 持仓时长 ≥ `max_hold_minutes` | 强制全部平仓 |
| 8 | 1H K线强度衰减 | 分批平仓 |

> **移动止盈（追踪止盈）：目前已关闭**
> 原因：0.5%回撤阈值过于敏感，插针频繁触发误平。现依赖固定SL/TP。

**max_hold_minutes 来源：**
```
max_hold_hours（从 system_settings 读取，范围 3~8H）× 60 = max_hold_minutes
修改后无需重启，下一个被检查的仓位即时生效
```

---

## 12. 关键参数速查表

| 参数 | 值 | 来源 | 实时生效 |
|------|-----|------|---------|
| 杠杆 | 5x | 硬编码 | 否 |
| 默认保证金 | 400U | 硬编码 | 否 |
| L1保证金 | 100U | 硬编码 | 否 |
| L2保证金 | 50U | 硬编码 | 否 |
| 最大持仓数 | 50（默认） | system_settings.max_positions | 是 |
| 扫描间隔 | 300秒 | 硬编码 | 否 |
| 信号确认阈值 | 55分 | 硬编码 | 否 |
| 趋势跟随阈值 | 20分 | 硬编码 | 否 |
| 评分上限 | 150分 | 硬编码 | 否 |
| LONG理论满分 | ~179分 | 权重表 | 30分钟重载 |
| SHORT理论满分 | ~328分 | 权重表 | 30分钟重载 |
| V2数据过时阈值 | 15分钟 | 硬编码 | 否 |
| V2软阻断偏移 | +25分 | 硬编码 | 否 |
| 止损% | 2~3%（默认3%） | system_settings / adaptive_params | 是 |
| 止盈% | 2~6%（默认2%） | system_settings / adaptive_params | 是 |
| 最大持仓时长 | 3~8小时 | system_settings.max_hold_hours | 是 |
| Big4封锁单次时长 | 2小时 | 硬编码（BLOCK_DURATION_HOURS） | 否 |
| Big4封锁最大时长 | 6小时 | 硬编码（MAX_BLOCK_TOTAL_HOURS） | 否 |
| Big4触底检测阈值 | -5% | 硬编码（BOTTOM_DROP_THRESHOLD） | 否 |
| Big4触顶检测阈值 | +5% | 硬编码（TOP_RISE_THRESHOLD） | 否 |
| 盈利熔断阈值 | 1000U / 6H | 硬编码 | 否 |
| 亏损熔断阈值 | 300U / 3H | 硬编码 | 否 |
| BTC动量触发阈值 | 1.5% | 硬编码 | 否 |
| BTC动量检测窗口 | 15/30/45/60分钟 | 硬编码 | 否 |
| BTC动量冷却时间 | 由 max_hold_hours 决定 | system_settings | 是 |
| 预测器置信度门槛 | 虚拟≥40%，模拟≥70% | 硬编码 | 否 |
| 羊群防护触发 | LONG/SHORT > 60% | 硬编码 | 否 |
| 羊群防护加分 | +5分 | 硬编码 | 否 |

---

## 13. 开关一览

所有开关在 `system_settings` 表。先看总开关 `u_futures_trading_enabled`，再看 §0 各子项。

| setting_key | 说明 | 值格式 |
|-------------|------|-------|
| `u_futures_trading_enabled` | **U本位总开关**（关则预测/动量/破位/主策略均不新开仓） | '1'/'0' |
| `signal_confirmation_enabled` | 主策略·信号确认路径（STRONG Big4） | '1'/'0' |
| `trend_following_enabled` | 主策略·趋势跟随路径（BULLISH/BEARISH Big4） | '1'/'0' |
| `live_trading_enabled` | 实盘同步总开关 | '1'/'0' |
| `allow_long` | 允许做多 | '1'/'0' |
| `allow_short` | 允许做空 | '1'/'0' |
| `big4_filter_enabled` | Big4过滤器开关 | 'true'/'false' |
| `btc_momentum_enabled` | BTC动量（须总开关同开） | '1'/'0' |
| `predictor_enabled` | 预测神器（须总开关同开） | '1'/'0' |
| `u_coin_style_enabled` | U本位破位（须总开关同开） | '1'/'0' |
| `max_positions` | 最大持仓数 | 数字字符串 |
| `max_hold_hours` | 最大持仓时长（小时） | 数字字符串 |
| `stop_loss_pct` | 全局止损% | 数字字符串 |
| `take_profit_pct` | 全局止盈% | 数字字符串 |

> 所有 system_settings 修改**无需重启**，下次循环自动读取生效。
> 熔断触发后 `u_futures_trading_enabled` 被自动改为 '0'，**需手动改回 '1'** 才能恢复。
