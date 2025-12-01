# 信号判断流程文档

## 概述

本文档描述了 `strategy_executor.py`（实时执行）和 `strategy_test_service.py`（回测）中的信号判断完整流程。

---

## 1. 变量初始化

在信号检测开始前，初始化以下关键变量：

```python
buy_signal_triggered = False      # 是否触发买入信号
is_early_entry_signal = False     # 是否为预判信号（提前入场）
is_sustained_signal = False       # 是否为持续趋势信号
found_golden_cross = False        # 是否检测到金叉
found_death_cross = False         # 是否检测到死叉
detected_cross_type = None        # 交叉类型: 'golden' / 'death'
```

---

## 2. 信号检测阶段

### 2.1 第一优先级：EMA9/26 穿越信号

检测当前K线与前一根K线之间是否发生EMA9/26穿越：

```
金叉条件（做多）:
  - 前K线: EMA9 <= EMA26
  - 当前K线: EMA9 > EMA26

死叉条件（做空）:
  - 前K线: EMA9 >= EMA26
  - 当前K线: EMA9 < EMA26
```

**信号强度检查**：
- 计算 `ema_strength_pct = |EMA9 - EMA26| / EMA26 * 100%`
- 如果 `ema_strength_pct < min_ema_cross_strength`，信号被过滤

**触发结果**：
- `buy_signal_triggered = True`
- `detected_cross_type = 'golden'` 或 `'death'`
- `is_early_entry_signal = False`
- `is_sustained_signal = False`

---

### 2.2 第二优先级：预判入场信号（Early Entry）

**条件**：`predictive_entry = True` 且没有检测到EMA穿越

**做多预判条件**（EMA9 < EMA26，尚未金叉）：
1. EMA差距收窄到阈值以内：`|curr_diff_pct| <= early_entry_gap_threshold`
2. EMA9向上斜率检查：`ema9_slope_pct >= early_entry_slope_min_pct`
3. 价格在EMA9上方（可选）：`close > EMA9`

**做空预判条件**（EMA9 > EMA26，尚未死叉）：
1. EMA差距收窄到阈值以内：`|curr_diff_pct| <= early_entry_gap_threshold`
2. EMA9向下斜率检查：`ema9_slope_pct <= -early_entry_slope_min_pct`
3. 价格在EMA9下方（可选）：`close < EMA9`

**触发结果**：
- `buy_signal_triggered = True`
- `detected_cross_type = 'golden'` 或 `'death'`
- `is_early_entry_signal = True` ⭐
- `is_sustained_signal = False`

---

### 2.3 第三优先级：持续趋势信号（Sustained Trend）

**条件**：`sustained_trend_enabled = True` 且没有检测到其他信号

**做空持续趋势条件**（EMA9 < EMA26，已经是空头）：
1. 趋势强度在范围内：`sustained_trend_min_strength <= |diff_pct| <= sustained_trend_max_strength`
2. MA10/EMA10确认（可选）：`EMA10 < MA10`
3. 价格确认（可选）：`close < EMA9`
4. 连续K线确认（可选）：最近N根K线都保持空头状态
5. 冷却时间检查（可选）：距离上次同方向开仓超过N分钟

**做多持续趋势条件**（EMA9 > EMA26，已经是多头）：
1. 趋势强度在范围内：`sustained_trend_min_strength <= |diff_pct| <= sustained_trend_max_strength`
2. MA10/EMA10确认（可选）：`EMA10 > MA10`
3. 价格确认（可选）：`close > EMA9`
4. 连续K线确认（可选）：最近N根K线都保持多头状态
5. 冷却时间检查（可选）：距离上次同方向开仓超过N分钟

**触发结果**：
- `buy_signal_triggered = True`
- `detected_cross_type = 'golden'` 或 `'death'`
- `is_early_entry_signal = False`
- `is_sustained_signal = True` ⭐

---

## 3. 开仓条件检查

当 `buy_signal_triggered = True` 时，进入开仓条件检查：

### 3.1 重复开仓检查

1. **同一K线检查**：查询当前K线时间范围内是否已有交易记录
2. **防止重复开仓**：`prevent_duplicate_entry = True` 时，有持仓则跳过
3. **最大持仓数检查**：`len(positions) >= max_positions` 时跳过

### 3.2 方向判断

根据 `detected_cross_type` 确定交易方向：
- `'golden'` → `direction = 'long'`（做多）
- `'death'` → `direction = 'short'`（做空）

---

## 4. 过滤器检查阶段

根据信号类型，过滤器的行为有所不同：

| 过滤器 | 普通信号 | 预判信号 | 持续趋势信号 |
|--------|----------|----------|--------------|
| RSI过滤 | 正常阈值检查 | 只检查极端值(20/80) | 正常阈值检查 |
| MACD过滤 | ✅ 检查 | ❌ 跳过 | ❌ 跳过 |
| KDJ过滤 | ✅ 检查 | ❌ 跳过 | ❌ 跳过 |
| MA10/EMA10信号强度 | ✅ 检查 | ❌ 跳过 | ❌ 跳过 |
| MA10/EMA10趋势方向 | ✅ 检查 | ❌ 跳过 | ❌ 跳过 |
| 趋势持续性(trend_confirm_bars) | ✅ 检查 | ❌ 跳过 | ❌ 跳过 |

### 4.1 成交量过滤

```
做多时: buy_volume_long 条件检查
做空时: buy_volume_short 条件检查

支持格式: "<1", "1-2", ">2", 或具体数值如 "1.5"
```

### 4.2 RSI过滤

```python
if is_early_entry_signal:
    # 预判信号：只检查极端值
    做多: RSI > 80 → 过滤
    做空: RSI < 20 → 过滤
else:
    # 普通信号：正常阈值
    做多: RSI > rsi_long_max → 过滤
    做空: RSI < rsi_short_min → 过滤
```

### 4.3 MACD过滤

```python
if not is_early_entry_signal and not is_sustained_signal:
    做多: MACD柱状图 <= 0 → 过滤
    做空: MACD柱状图 >= 0 → 过滤
```

### 4.4 KDJ过滤

```python
if not is_early_entry_signal and not is_sustained_signal:
    做多: KDJ_K > kdj_long_max_k → 过滤（除非强信号）
    做空: KDJ_K < kdj_short_min_k → 过滤（除非强信号）
```

### 4.5 MA10/EMA10信号强度检查

```python
if not is_early_entry_signal and not is_sustained_signal:
    if min_ma10_cross_strength > 0:
        |EMA10 - MA10| / MA10 * 100% < min_ma10_cross_strength → 过滤
    if ma10_ema10_trend_filter:
        做多: EMA10 <= MA10 → 过滤
        做空: EMA10 >= MA10 → 过滤
```

### 4.6 趋势持续性检查

```python
if trend_confirm_bars > 0 and not is_early_entry_signal and not is_sustained_signal:
    1. 查找金叉/死叉发生的位置
    2. 检查从交叉点到当前是否持续了 trend_confirm_bars 根K线
    3. 验证趋势是否一直保持（未反转）
```

---

## 5. 执行买入

如果所有过滤器检查通过（`trend_confirm_ok = True`）：

### 5.1 计算入场价格

根据配置的价格类型计算入场价格：

**做多入场价格**（`longPrice`）：

| 选项 | 说明 | 计算公式 |
|------|------|----------|
| `market` | 市价买入 | `entry_price = realtime_price` |
| `market_minus_0_2` | 低于市价0.2%买入 | `entry_price = realtime_price * 0.998` |
| `market_minus_0_4` | 低于市价0.4%买入 | `entry_price = realtime_price * 0.996` |
| `market_minus_0_6` | 低于市价0.6%买入 | `entry_price = realtime_price * 0.994` |
| `market_minus_0_8` | 低于市价0.8%买入 | `entry_price = realtime_price * 0.992` |
| `market_minus_1` | 低于市价1%买入 | `entry_price = realtime_price * 0.99` |

**做空入场价格**（`shortPrice`）：

| 选项 | 说明 | 计算公式 |
|------|------|----------|
| `market` | 市价卖出 | `entry_price = realtime_price` |
| `market_plus_0_2` | 高于市价0.2%卖出 | `entry_price = realtime_price * 1.002` |
| `market_plus_0_4` | 高于市价0.4%卖出 | `entry_price = realtime_price * 1.004` |
| `market_plus_0_6` | 高于市价0.6%卖出 | `entry_price = realtime_price * 1.006` |
| `market_plus_0_8` | 高于市价0.8%卖出 | `entry_price = realtime_price * 1.008` |
| `market_plus_1` | 高于市价1%卖出 | `entry_price = realtime_price * 1.01` |

> **说明**：
> - 做多时使用低于市价的限价单，可以获得更好的入场价格，但可能无法成交
> - 做空时使用高于市价的限价单，可以获得更好的入场价格，但可能无法成交
> - 市价单（`market`）会立即成交，但价格可能有滑点

### 5.2 执行开仓

1. 计算仓位大小（基于账户余额和杠杆）
2. 执行开仓操作（市价单或限价单）
3. 保存交易记录到数据库
4. 更新持仓信息

---

## 6. 信号类型对比

| 特性 | EMA穿越信号 | 预判入场信号 | 持续趋势信号 |
|------|-------------|--------------|--------------|
| 触发条件 | EMA9/26发生穿越 | EMA差距收窄+斜率 | 趋势强度在范围内 |
| 入场时机 | 穿越确认后 | 穿越前提前入场 | 趋势已形成 |
| 过滤器 | 全部检查 | 仅RSI极端值 | 仅RSI正常检查 |
| 风险级别 | 中等 | 较高（可能误判） | 较低（趋势确认） |
| 适用场景 | 标准交叉策略 | 追求更好入场价 | 趋势延续策略 |

---

## 7. 流程图

```
开始
  │
  ▼
[获取K线和指标数据]
  │
  ▼
[检测EMA9/26穿越] ──────────────────┐
  │                                │
  │ 未检测到穿越                   │ 检测到穿越
  ▼                                │
[检测预判入场信号] ────────────────┤
  │                                │
  │ 未满足预判条件                 │ 满足预判条件
  ▼                                │ is_early_entry_signal=True
[检测持续趋势信号] ────────────────┤
  │                                │ is_sustained_signal=True
  │ 未满足条件                     │
  ▼                                │
[无信号，结束] ◄───────────────────┘
                                   │
                                   ▼
                      [检查开仓条件]
                           │
                           ▼
                      [确定交易方向]
                           │
                           ▼
                      [成交量过滤]
                           │
                           ▼
                      [RSI过滤]
                           │
           ┌───────────────┴───────────────┐
           │                               │
    is_early_entry=False             is_early_entry=True
    is_sustained=False               或 is_sustained=True
           │                               │
           ▼                               │
      [MACD过滤]                          │
           │                               │
           ▼                               │
      [KDJ过滤]                           │
           │                               │
           ▼                               │
      [MA10/EMA10检查]                    │
           │                               │
           ▼                               │
      [趋势持续性检查]                    │
           │                               │
           └───────────────┬───────────────┘
                           │
                           ▼
                    [所有检查通过?]
                           │
              ┌────────────┴────────────┐
              │                         │
             否                        是
              │                         │
              ▼                         ▼
         [跳过买入]               [执行买入操作]
              │                         │
              └─────────────────────────┘
                           │
                           ▼
                         结束
```

---

## 8. 配置参数说明

### 信号检测参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| buy_signal | 信号类型: ema_5m/ema_15m/ema_1h/ma_ema10 | ema_15m |
| min_ema_cross_strength | EMA穿越最小信号强度(%) | 0.1 |
| buy_directions | 允许的方向: ['long'], ['short'], ['long','short'] | ['long','short'] |

### 预判入场参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| early_entry_enabled | 是否启用预判入场 | false |
| early_entry_gap_threshold | EMA差距阈值(%) | 0.1 |
| early_entry_slope_min_pct | EMA9最小斜率(%) | 0.01 |
| early_entry_require_upward_slope | 是否要求EMA9向上/下斜率 | true |
| early_entry_require_price_above_ema | 是否要求价格在EMA9上/下方 | false |

### 持续趋势参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| sustained_trend_enabled | 是否启用持续趋势信号 | false |
| sustained_trend_min_strength | 最小趋势强度(%) | 0.15 |
| sustained_trend_max_strength | 最大趋势强度(%) | 1.0 |
| sustained_trend_min_bars | 最少连续K线数 | 2 |
| sustained_trend_require_ma10_confirm | 是否要求MA10/EMA10确认 | true |
| sustained_trend_require_price_confirm | 是否要求价格确认 | true |
| sustained_trend_cooldown_minutes | 冷却时间(分钟) | 30 |

### 过滤器参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| rsi_filter_enabled | 是否启用RSI过滤 | false |
| rsi_long_max | 做多RSI上限 | 70 |
| rsi_short_min | 做空RSI下限 | 30 |
| macd_filter_enabled | 是否启用MACD过滤 | false |
| macd_long_require_positive | 做多时要求MACD>0 | true |
| macd_short_require_negative | 做空时要求MACD<0 | true |
| kdj_filter_enabled | 是否启用KDJ过滤 | false |
| kdj_long_max_k | 做多KDJ K值上限 | 80 |
| kdj_short_min_k | 做空KDJ K值下限 | 20 |
| trend_confirm_bars | 趋势确认K线数 | 0 |

### 入场价格参数

| 参数 | 说明 | 可选值 |
|------|------|--------|
| longPrice | 做多入场价格类型 | market, market_minus_0_2, market_minus_0_4, market_minus_0_6, market_minus_0_8, market_minus_1 |
| shortPrice | 做空入场价格类型 | market, market_plus_0_2, market_plus_0_4, market_plus_0_6, market_plus_0_8, market_plus_1 |

**做多入场价格说明**：
- `market`: 市价立即成交
- `market_minus_X`: 以低于当前市价X%的价格挂限价单（X可选: 0.2, 0.4, 0.6, 0.8, 1）

**做空入场价格说明**：
- `market`: 市价立即成交
- `market_plus_X`: 以高于当前市价X%的价格挂限价单（X可选: 0.2, 0.4, 0.6, 0.8, 1）

---

## 9. 代码一致性检查清单

确保 `strategy_executor.py` 和 `strategy_test_service.py` 保持一致：

- [x] `is_sustained_signal` 变量初始化
- [x] `is_early_entry_signal` 变量初始化
- [x] MACD过滤器跳过条件: `not is_early_entry_signal and not is_sustained_signal`
- [x] KDJ过滤器跳过条件: `not is_early_entry_signal and not is_sustained_signal`
- [x] MA10/EMA10检查跳过条件: `not is_early_entry_signal and not is_sustained_signal`
- [x] 趋势持续性检查跳过条件: `not is_early_entry_signal and not is_sustained_signal`

---

*文档更新时间: 2025-12-01*
