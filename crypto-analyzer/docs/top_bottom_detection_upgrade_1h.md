# 顶底识别升级：从15m切换到1h K线

## 问题背景

用户反馈：使用15m K线做顶底识别时，**假信号太多**，导致：
- 回落2-3%就平仓，但有些是**盈利-0.6%**（亏损就平了）
- 没有吃到足够的利润就提前平仓
- 例如：`TOP_DETECTED(高点回落1.5%,盈利-0.6%)`

## 根本原因

15分钟K线太敏感：
- 回落阈值只需 **1.0%** 就触发
- 只检查最近10根15m K线（2.5小时窗口）
- 容易被短期波动误导

## 解决方案：升级到1h K线

### 核心改进

| 项目 | 15m K线（旧） | 1h K线（新） | 改进原因 |
|------|--------------|-------------|----------|
| **时间窗口** | 最近10根（2.5小时） | 最近12根（12小时） | 更长的观察周期，避免短期噪音 |
| **回落/反弹阈值** | 1.0% | **1.5%** | 提高阈值，减少假信号 |
| **K线确认数量** | 最近3根，至少2根收阴 | 最近4根，至少**3根**收阴 | 更严格的趋势确认 |
| **成交量确认** | ❌ 无 | ✅ **新增**：成交量需放大1.2倍 | 确认是真实的趋势反转 |
| **影线判断** | 上影线 > 实体2倍 | 上影线 > 实体**1.5倍** | 更宽松的影线判断（1h级别影线更重要） |

### 修改的代码位置

**文件**: `smart_trader_service.py`

**函数**: `check_top_bottom()` (第631-693行)

### 详细逻辑对比

#### 做多持仓 - 顶部识别

**修改前 (15m K线)**:
```python
# 1. 最近10根15m K线（2.5小时）
klines_15m = self.brain.load_klines(symbol, '15m', 30)
recent_10 = klines_15m[-10:]

# 2. 回落阈值 1.0%
if is_peak and pullback_pct >= 1.0 and (bearish_count >= 2 or long_upper_shadow >= 2):
    return True, f"TOP_DETECTED(...)"
```

**修改后 (1h K线)**:
```python
# 1. 最近12根1h K线（12小时）
klines_1h = self.brain.load_klines(symbol, '1h', 48)
recent_12 = klines_1h[-12:]

# 2. 回落阈值 1.5%，确认至少3根收阴
if is_peak and pullback_pct >= 1.5 and (bearish_count >= 3 or long_upper_shadow >= 2):
    return True, f"TOP_DETECTED(...)"
```

#### 做空持仓 - 底部识别

**修改前 (15m K线)**:
```python
# 1. 最近10根15m K线（2.5小时）
recent_10 = klines_15m[-10:]

# 2. 反弹阈值 1.0%
if is_bottom and bounce_pct >= 1.0 and (bullish_count >= 2 or long_lower_shadow >= 2):
    return True, f"BOTTOM_DETECTED(...)"
```

**修改后 (1h K线)**:
```python
# 1. 最近12根1h K线（12小时）
recent_12 = klines_1h[-12:]

# 2. 反弹阈值 1.5%，确认至少3根收阳
if is_bottom and bounce_pct >= 1.5 and (bullish_count >= 3 or long_lower_shadow >= 2):
    return True, f"BOTTOM_DETECTED(...)"
```

### 新增成交量确认逻辑

```python
# 最近3根1h K线的平均成交量 vs 前21根的平均成交量
avg_volume_24h = sum(k['volume'] for k in recent_24[:21]) / 21
recent_3_volume = sum(k['volume'] for k in klines_1h[-3:]) / 3
volume_surge = recent_3_volume > avg_volume_24h * 1.2  # 成交量放大20%
```

目前成交量确认作为辅助指标计算，但未强制要求（暂时允许数据不足时通过）。后续可根据实盘效果调整。

## 预期效果

### 正面影响 ✅

1. **减少假信号**：
   - 回落/反弹需要达到 **1.5%**（从1.0%提高）
   - 需要 **3根** 1h K线确认（从2根15m提高）
   - 更长的观察窗口（12小时 vs 2.5小时）

2. **提高盈利质量**：
   - 避免在盈利-0.6%时就平仓
   - 让利润充分运行后再平仓
   - 减少不必要的交易次数

3. **更稳健的判断**：
   - 1h K线过滤掉短期噪音
   - 成交量确认增加可靠性
   - 趋势确认更充分

### 潜在影响 ⚠️

1. **平仓延迟**：
   - 可能错过最佳平仓点（顶部回落1.5%才平仓）
   - 如果反转很快，可能多回撤0.5%的利润

2. **持仓时间增加**：
   - 从15m切换到1h，识别信号延迟约45分钟
   - 持仓时间可能从2-3小时增加到4-6小时

3. **需要更多1h K线数据**：
   - 需要至少24根1h K线（24小时数据）
   - 如果数据不足会返回 `False`

## 部署步骤

### 1. 验证1h K线数据充足性

```bash
mysql -h 13.212.252.171 -u admin -p binance-data -e "
SELECT
    symbol,
    COUNT(*) as kline_count,
    MAX(timestamp) as latest_time,
    TIMESTAMPDIFF(HOUR, MIN(timestamp), MAX(timestamp)) as hours_coverage
FROM kline_data
WHERE exchange = 'binance_futures'
AND timeframe = '1h'
GROUP BY symbol
HAVING kline_count < 48
ORDER BY kline_count ASC
LIMIT 10;
"
```

**预期结果**：所有交易对应该有 **≥48根** 1h K线数据。

如果有交易对数据不足，需要先回填：
```bash
cd /root/crypto-analyzer
python backfill_futures_klines.py
```

### 2. 部署新代码

```bash
cd /root/crypto-analyzer
git pull
```

**预期拉取到的commit**: 包含 `smart_trader_service.py` 的 `check_top_bottom()` 函数修改。

### 3. 重启超级大脑服务

```bash
pkill -f smart_trader_service.py
nohup python smart_trader_service.py > logs/smart_trader.log 2>&1 &
```

### 4. 验证日志

```bash
tail -f logs/smart_trader.log
```

观察是否有 `TOP_DETECTED` 或 `BOTTOM_DETECTED` 日志出现，如果出现，检查回落/反弹百分比应该 **≥1.5%**。

## 验证效果

### 方法1: 查看数据库记录

等待新的平仓交易产生后（约2-4小时），运行：

```bash
python check_close_reason.py
```

检查新的 `TOP_DETECTED` / `BOTTOM_DETECTED` 记录：
- 回落/反弹百分比应该 **≥1.5%**（不再是1.0%）
- 盈利百分比应该更高（减少亏损平仓的情况）

### 方法2: 对比历史数据

查询最近24小时的顶底识别平仓记录：

```sql
SELECT
    symbol,
    position_side,
    notes,
    realized_pnl,
    close_time
FROM futures_positions
WHERE status = 'closed'
AND notes LIKE '%DETECTED%'
AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
ORDER BY close_time DESC;
```

**改进前的典型记录**:
```
TOP_DETECTED(高点回落1.5%,盈利-0.6%)  # 亏损就平了
TOP_DETECTED(高点回落2.1%,盈利-0.2%)  # 小亏就平了
TOP_DETECTED(高点回落2.3%,盈利+0.2%)  # 小赚就平了
```

**改进后的预期记录**:
```
TOP_DETECTED(高点回落1.8%,盈利+0.5%)  # 有盈利才平仓
TOP_DETECTED(高点回落2.5%,盈利+1.2%)  # 利润更充分
TOP_DETECTED(高点回落1.6%,盈利+0.3%)  # 减少亏损平仓
```

## 回滚方案

如果效果不理想（例如错过太多顶部导致利润回吐），可以回滚到15m K线版本：

```bash
cd /root/crypto-analyzer
git log --oneline | head -5  # 查看最近的commits
git reset --hard <上一个commit>  # 回滚到修改前的版本
pkill -f smart_trader_service.py
nohup python smart_trader_service.py > logs/smart_trader.log 2>&1 &
```

或者可以考虑折中方案：
- 保持1h K线，但降低阈值到 **1.2%**
- 或者改为混合策略：15m预警 + 1h确认

## 后续优化方向

根据实盘效果，可以考虑：

1. **动态阈值**：根据波动率调整回落/反弹阈值
   - 高波动币种（如DOGE）：阈值提高到2.0%
   - 低波动币种（如BTC）：阈值保持1.5%

2. **混合策略**：
   - 15m K线快速预警（回落1.0%时标记）
   - 1h K线最终确认（回落1.5%且1h确认才平仓）

3. **分批平仓**：
   - 回落1.5%时平仓50%
   - 回落2.5%时平仓剩余50%

4. **成交量强制确认**：
   - 目前成交量确认计算但不强制
   - 可以改为：成交量未放大时，阈值提高到2.0%

---

**修改时间**: 2026-01-23
**修改原因**: 用户反馈15m K线假信号太多
**主要改进**: 切换到1h K线，阈值从1.0%提高到1.5%，确认从2根提高到3根
**预期效果**: 减少假信号，提高盈利质量，避免亏损平仓
