# 策略执行器优化更新 (2026-01-04)

## 更新概述

本次更新主要解决了以下问题：
1. 频繁开平仓导致的手续费浪费
2. 开仓即平仓的问题（持仓时间过短）
3. 小额波动导致的频繁止损
4. 代码重复和维护性问题

---

## 1. 修复限价单开仓冷却绕过问题

### 问题描述
限价单信号 `check_limit_entry_signal` 检查冷却时只看 CANCELLED/EXPIRED 订单，不检查最近平仓的持仓。导致平仓后1.8分钟就能重新开仓，违反了30分钟冷却要求。

### 修复方案
在 `check_limit_entry_signal` 方法中添加统一的冷却检查：

```python
# 检查平仓后的冷却时间（使用统一的开仓冷却检查）
in_cooldown, cooldown_msg = self.check_entry_cooldown(symbol, direction, strategy, strategy_id)
if in_cooldown:
    return None, f"限价单{cooldown_msg}"
```

**影响**：
- 限价单信号也会遵守30分钟开仓冷却
- 防止频繁开平仓

**提交**: `fix: 修复开仓冷却检查逻辑，添加真正的时间冷却`

---

## 2. 添加15分钟最小持仓时间保护

### 问题描述
- ZEC开仓后9秒就被移动止盈平仓
- DASH开仓后6秒就被EMA差值收窄止盈平仓
- 没有给持仓足够的发展空间

### 修复方案

#### A. 移动止盈保护
```python
def check_trailing_stop(self, position: Dict, current_price: float):
    if callback_pct >= self.TRAILING_CALLBACK:
        # 添加最小持仓时间保护（15分钟）
        satisfied, duration = self.check_min_holding_duration(position, 15)
        if not satisfied:
            logger.debug(f"{symbol} 移动止盈被跳过: 持仓时长{duration:.1f}分钟 < 15分钟")
            return False, "", updates
```

#### B. EMA差值收窄止盈保护
```python
def check_ema_diff_take_profit(self, position: Dict, ema_data: Dict, current_pnl_pct: float):
    # EMA差值收窄止盈
    if ema_diff_pct < threshold:
        satisfied, duration = self.check_min_holding_duration(position, 15)
        if not satisfied:
            return False, ""

    # EMA方向反转止盈
    if not ema_supports_position and current_pnl_pct >= min_profit_pct:
        satisfied, duration = self.check_min_holding_duration(position, 15)
        if not satisfied:
            return False, ""
```

**影响**：
- 所有止盈机制（移动止盈、EMA差值收窄、EMA方向反转）都必须等待15分钟
- 给予持仓足够的发展时间
- 避免开仓即平仓

**提交**:
- `fix: 为移动止盈添加15分钟最小持仓时间保护`
- `fix: 为EMA差值收窄止盈添加15分钟最小持仓时间保护`

---

## 3. 提高EMA止盈最小盈利要求

### 问题描述
- `minProfitPct: 0.3%` 过低
- 手续费（开仓0.05% + 平仓0.05% = 0.1%）占比过大
- 频繁的0.3%盈利平仓导致实际盈利很少

### 修复方案
```python
min_profit_pct = ema_diff_tp.get('minProfitPct', 1.5)  # 从0.3%提高到1.5%
```

**新的止盈逻辑**：
1. **盈利 < 1.5%**：持仓继续，让利润成长
2. **盈利 ≥ 1.5%**：
   - 移动止盈激活（回撤0.5%时平仓）
   - EMA差值 < 0.5%时可止盈
   - EMA方向反转时可止盈

**配合移动止盈**：
- 移动止盈激活阈值：1.5%
- 回撤平仓阈值：0.5%
- 两者协同工作，避免过早止盈

**提交**: `fix: 提高EMA止盈最小盈利要求从0.3%到1.5%`

---

## 4. 添加亏损阈值保护

### 问题描述
在任何亏损情况下都会检查EMA平仓条件，导致小额亏损（如-0.5%）也可能被平掉，没有给亏损仓位翻盘的机会。

### 修复方案
```python
min_loss_pct = ema_diff_tp.get('minLossPct', -0.8)  # 最小亏损要求，默认-0.8%

# 检查是否达到触发条件：盈利 >= 1.5% 或 亏损 <= -0.8%
# -0.8% ~ 1.5% 之间不触发任何平仓逻辑，给仓位发展空间
if min_loss_pct <= current_pnl_pct < min_profit_pct:
    return False, ""
```

**新的触发逻辑**：
1. **盈利 ≥ 1.5%**：检查止盈条件（趋势减弱等）
2. **-0.8% ~ 1.5% 之间**：不触发平仓，给仓位发展空间
3. **亏损 ≤ -0.8%**：检查止损条件（趋势反转等）
4. **亏损 ≤ -2.5%**：硬止损（账户资金-12.5%）

**杠杆换算**：
- -0.8% × 5倍杠杆 = 账户资金-4%
- -2.5% × 5倍杠杆 = 账户资金-12.5%

**提交**: `fix: 为EMA止盈添加亏损阈值保护，避免小额波动频繁平仓`

---

## 5. 代码重构：统一保护机制

### 问题描述
最小持仓时间检查代码在3个地方重复，每处都有13行相同的时间计算逻辑，难以维护。

### 重构方案

#### A. 新增统一方法
```python
def check_min_holding_duration(self, position: Dict, min_minutes: int = 15) -> Tuple[bool, float]:
    """
    检查是否满足最小持仓时间要求

    Returns:
        (是否满足要求, 已持仓分钟数)
    """
    open_time = position.get('open_time')
    if not open_time:
        return True, 0

    now = self.get_local_time()
    if isinstance(open_time, datetime):
        duration_minutes = (now - open_time).total_seconds() / 60
        return duration_minutes >= min_minutes, duration_minutes

    return True, 0
```

#### B. 统一调用
```python
# 移动止盈
satisfied, duration = self.check_min_holding_duration(position, 15)
if not satisfied:
    logger.debug(f"{symbol} 移动止盈被跳过: 持仓时长{duration:.1f}分钟 < 15分钟")
    return False, "", updates

# EMA差值收窄止盈
satisfied, duration = self.check_min_holding_duration(position, 15)
if not satisfied:
    logger.debug(f"{symbol} EMA差值收窄止盈被跳过: 持仓时长{duration:.1f}分钟 < 15分钟")
    return False, ""

# EMA方向反转止盈
satisfied, duration = self.check_min_holding_duration(position, 15)
if not satisfied:
    logger.debug(f"{symbol} EMA方向反转止盈被跳过: 持仓时长{duration:.1f}分钟 < 15分钟")
    return False, ""
```

**优化效果**：
- 删除39行重复代码
- 统一为3行方法调用
- 提高可维护性和一致性

**提交**: `refactor: 统一最小持仓时间保护机制，消除重复代码`

---

## 6. 移除表现不佳的交易对

### 移除币种
- **AVAX/USDT**: 近3日亏损 -19.09 USDT
- **DASH/USDT**: 频繁开仓即平仓
- **XTZ/USDT**: 表现不佳

### 新增币种
- ATOM/USDT
- MATIC/USDT
- DYDX/USDT
- COMP/USDT
- CRV/USDT
- LDO/USDT
- KPEPE/USDT
- RNDR/USDT
- KSHIB/USDT
- PUMP/USDT

**总计**: 35个交易对

**提交**:
- `config: 移除表现不佳的交易对`
- `config: 添加10个新交易对`

---

## 参数对比表

| 参数 | 修改前 | 修改后 | 说明 |
|------|--------|--------|------|
| EMA止盈最小盈利 | 0.3% | 1.5% | 避免频繁小盈利平仓 |
| EMA止盈亏损触发 | 无限制 | -0.8% | 小亏损不触发EMA平仓 |
| 最小持仓时间 | 仅部分止盈有 | 统一15分钟 | 所有止盈统一保护 |
| 开仓冷却检查 | 仅市价单 | 市价单+限价单 | 限价单也遵守冷却 |

---

## 平仓逻辑优先级（更新后）

```
0. 硬止损（-2.5%）
   └── 强制平仓，不受任何保护

1. 冷却期保护（开仓后15分钟内）
   ├── 跳过：移动止损
   ├── 跳过：5M信号智能止损
   ├── 跳过：金叉/死叉反转
   └── 保留：硬止损、最大止盈

2. 最小持仓时间保护（开仓后15分钟内）
   ├── 跳过：移动止盈
   ├── 跳过：EMA差值收窄止盈
   └── 跳过：EMA方向反转止盈

3. 盈亏区间保护（-0.8% ~ 1.5%）
   ├── 跳过：EMA差值收窄止盈
   └── 跳过：EMA方向反转止盈

4. 正常止盈（满足条件后）
   ├── 最大止盈（≥8%）
   ├── 移动止盈（1.5%激活，回撤0.5%）
   ├── EMA差值收窄（盈利≥1.5%且差值<0.5%）
   └── EMA方向反转（盈利≥1.5%且方向反转）
```

---

## 影响评估

### 正面影响
1. **减少手续费损耗**：避免频繁0.3%的小盈利平仓
2. **提高实际盈利**：扣除手续费后仍有1.4%实际盈利
3. **给予持仓发展空间**：15分钟+盈亏区间双重保护
4. **代码更易维护**：统一的保护机制方法
5. **防止冷却绕过**：限价单也遵守30分钟冷却

### 可能的风险
1. **错过部分止盈机会**：小盈利（<1.5%）可能回吐
2. **小亏损继续扩大**：-0.5%的亏损可能变成-2%
3. **持仓时间变长**：15分钟保护可能延迟平仓

### 风险缓解
1. 移动止盈在1.5%激活，保护大盈利不回吐
2. 硬止损在-2.5%强制平仓，限制最大亏损
3. 15分钟保护只影响止盈，不影响止损

---

## 建议的后续优化

### 1. 可配置化参数
将硬编码的参数改为策略配置：
```json
{
  "protections": {
    "minHoldingMinutes": 15,      // 最小持仓时间
    "minProfitPct": 1.5,          // EMA止盈最小盈利
    "minLossPct": -0.8,           // EMA止盈亏损触发
    "entryCooldownMinutes": 30     // 开仓冷却时间
  }
}
```

### 2. 持仓数量查询优化
提取统一的 `get_position_counts()` 方法，减少重复数据库查询。

### 3. 冷却管理器
创建独立的 `CooldownManager` 类，统一管理所有冷却机制。

### 4. 反转开仓强度对齐
将反转开仓的强度要求从0.05%降低到0.01%，与金叉/死叉保持一致。

---

## 测试建议

### 1. 回测验证
- 使用历史数据回测新参数下的表现
- 对比修改前后的盈亏曲线
- 重点关注持仓时长分布

### 2. 实盘观察
- 观察前3天的交易表现
- 统计持仓时长是否增加
- 监控手续费占比是否下降

### 3. 关键指标
- **平均持仓时长**：应该 > 15分钟
- **开仓频率**：应该降低（冷却保护）
- **盈利分布**：小盈利（<1.5%）应该减少
- **手续费占比**：应该下降

---

## 相关提交

```bash
7b34b2d fix: 为EMA差值收窄止盈添加15分钟最小持仓时间保护
20d335a fix: 为移动止盈添加15分钟最小持仓时间保护
73359e8 fix: 修复开仓冷却检查逻辑，添加真正的时间冷却
cb7d4d7 fix: 提高EMA止盈最小盈利要求从0.3%到1.5%
d645a60 fix: 为EMA止盈添加亏损阈值保护，避免小额波动频繁平仓
79e5bdd refactor: 统一最小持仓时间保护机制，消除重复代码
d13678f config: 移除表现不佳的交易对
a2d3cf7 config: 添加10个新交易对
```

---

*文档日期: 2026-01-04*
*对应版本: v2.1*
