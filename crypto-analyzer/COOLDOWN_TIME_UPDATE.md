# 冷却时间优化更新

**更新时间**: 2026-01-31
**修改内容**: 将平仓后冷却时间从1小时调整为15分钟

---

## 修改原因

用户要求将冷却时间从1小时缩短到15分钟，以提高交易频率和捕捉更多交易机会。

### 当前问题
- 冷却时间1小时(60分钟)过长
- 错过了很多15-60分钟内的反向交易机会
- 对于4-6小时的短线交易，1小时冷却占比过大(16-25%)

---

## 修改详情

### 1. U本位合约服务 (smart_trader_service.py)

**修改位置**:
- Line 1040: `cooldown_minutes=60` → `cooldown_minutes=15`
- Line 1834: 函数默认参数 `60` → `15`
- Line 2529: `cooldown_minutes=60` → `cooldown_minutes=15`
- Line 1041: 日志信息 "平仓后1小时冷却期内" → "平仓后15分钟冷却期内"
- Line 1838: 注释 "默认冷却期1小时" → "默认冷却期15分钟"

**函数签名变更**:
```python
# 修改前
def check_recent_close(self, symbol: str, side: str, cooldown_minutes: int = 60):
    """默认冷却期1小时,避免反复开平造成频繁交易"""

# 修改后
def check_recent_close(self, symbol: str, side: str, cooldown_minutes: int = 15):
    """默认冷却期15分钟,避免反复开平造成频繁交易"""
```

---

### 2. 币本位合约服务 (coin_futures_trader_service.py)

**修改位置**:
- Line 1045: `cooldown_minutes=60` → `cooldown_minutes=15`
- Line 1839: 函数默认参数 `60` → `15`
- Line 2534: `cooldown_minutes=60` → `cooldown_minutes=15`
- Line 1044: 日志信息 "平仓后冷却期内(1小时)" → "平仓后冷却期内(15分钟)"
- Line 1843: 注释 "默认冷却期1小时" → "默认冷却期15分钟"

**修改一致**: 与U本位合约保持相同的冷却时间

---

## 影响分析

### 正面影响

1. **提高交易频率**
   - 冷却时间从60分钟降低到15分钟
   - 可以更快地重新开仓
   - 捕捉更多短期波动机会

2. **适配短线策略**
   - 4-6小时持仓周期下，15分钟冷却更合理
   - 冷却期占比从16-25%降低到4-6%

3. **减少机会损失**
   - 之前1小时内的反向信号无法交易
   - 现在15-60分钟的机会可以捕捉

### 潜在风险

1. **可能增加频繁交易**
   - 同一交易对同一方向可能在短时间内多次开平仓
   - 需要监控是否出现反复开平的情况

2. **手续费成本**
   - 交易频率提高可能导致手续费增加
   - 需要确保额外交易的盈利覆盖手续费

3. **信号质量要求更高**
   - 冷却时间短，对信号准确性要求更高
   - 需要配合之前的信号权重优化

---

## 对比分析

### 1小时冷却 vs 15分钟冷却

| 场景 | 1小时冷却 | 15分钟冷却 | 改进 |
|------|----------|-----------|------|
| **快速反转** | ❌ 错过 | ✅ 捕捉 | 可以交易15-60分钟内的反转 |
| **短期震荡** | ❌ 错过 | ✅ 捕捉 | 可以交易震荡行情的多次机会 |
| **4H持仓周期** | 冷却占25% | 冷却占6% | 冷却期占比大幅降低 |
| **6H持仓周期** | 冷却占16% | 冷却占4% | 更合理的冷却比例 |

### 示例场景

**场景1: 快速反转**
```
10:00 - 做多BTC开仓
11:30 - 止损平仓(亏损)
11:45 - 出现做空信号

旧规则: 需要等到12:00才能做空 (错过15分钟)
新规则: 11:45立即可以做空 ✅
```

**场景2: 震荡行情**
```
09:00 - 做空ETH开仓
10:30 - 止盈平仓(盈利)
10:45 - 出现做多信号

旧规则: 需要等到11:30才能做多 (错过45分钟)
新规则: 10:45立即可以做多 ✅
```

---

## 配合其他优化

本次冷却时间优化与之前的优化协同作用:

### 1. 信号权重优化
- 降低了短期噪音信号权重(volatility_high, momentum_down_3pct)
- 提高了可靠信号权重(consecutive_bull/bear)
- 配合短冷却时间,可以快速捕捉高质量信号

### 2. 1D信号降权
- 1D信号从30分降到3分
- 更关注1H短期趋势
- 短冷却时间允许快速跟随1H趋势变化

### 3. LONG信号提升
- LONG信号权重大幅提升
- 可以更快地在上涨行情中做多
- 15分钟冷却允许快速从SHORT转LONG

---

## 监控指标

优化后需要监控以下指标:

### 1. 交易频率
```sql
-- 检查平均每日交易次数
SELECT
    DATE(open_time) as trade_date,
    COUNT(*) as trades_per_day,
    COUNT(DISTINCT symbol) as unique_symbols
FROM futures_positions
WHERE open_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY DATE(open_time)
ORDER BY trade_date DESC;
```

**预期**: 每日交易次数从150-200增加到200-300

### 2. 冷却期内信号
```sql
-- 检查有多少信号被冷却期拒绝
SELECT COUNT(*) as rejected_by_cooldown
FROM signal_logs
WHERE reject_reason LIKE '%冷却期%'
AND log_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR);
```

**预期**: 被冷却期拒绝的信号减少70%以上

### 3. 反复开平检测
```sql
-- 检查同一交易对是否频繁开平
SELECT
    symbol,
    position_side,
    COUNT(*) as trade_count,
    AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)) as avg_duration_min
FROM futures_positions
WHERE close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
AND status = 'CLOSED'
GROUP BY symbol, position_side
HAVING trade_count > 5
ORDER BY trade_count DESC;
```

**预期**: 没有单个交易对在24H内交易超过10次

---

## 回滚方案

如果15分钟冷却导致问题,可以回滚到30分钟作为折中:

```python
# 回滚到30分钟
cooldown_minutes=30  # 折中方案

# 或完全回滚到1小时
cooldown_minutes=60  # 原始设置
```

---

## 实施步骤

1. ✅ 修改 smart_trader_service.py
2. ✅ 修改 coin_futures_trader_service.py
3. ⏳ 重启交易服务
4. ⏳ 监控24H交易数据
5. ⏳ 评估效果,必要时调整

---

## 预期效果

**优化前** (1小时冷却):
```
24H交易: 150-200笔
冷却期拒绝: ~50次/天
错过机会: 较多短期反转信号
```

**优化后** (15分钟冷却):
```
24H交易: 200-300笔 (增加30-50%)
冷却期拒绝: ~15次/天 (减少70%)
错过机会: 减少,可捕捉更多短期机会
```

---

**更新完成时间**: 2026-01-31
**建议监控期**: 24-48小时
**下次评估**: 2026-02-01
