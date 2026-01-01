# RSI信号开仓功能说明

> 版本: 1.2
> 更新日期: 2026-01-01
> 实现文件: `app/services/strategy_executor_v2.py`

## 功能概述

RSI信号是一种**独立的开仓机制**，与现有的金叉/死叉信号并存，互不干扰。

### 核心逻辑

- **做多信号**: 当15M RSI < 阈值(默认30) 且 EMA强度连续上升时开多
- **做空信号**: 当15M RSI > 阈值(默认75) 且 EMA强度连续下降时开空

### 关键特点

1. **独立机制**: 不受金叉/死叉逻辑影响，是额外的开仓信号源
2. **并存使用**: 金叉/死叉信号继续保留，RSI信号作为补充
3. **趋势确认**: 需要EMA强度连续2-3根K线保持同一方向变化
4. **强度限制**:
   - 做多时当前EMA强度必须 > `minEmaStrengthLong`
   - 做空时当前EMA强度必须 < `maxEmaStrengthShort`
5. **⚡ 市价单开仓**: RSI信号使用市价单立即成交，不等待回调（v1.1新增）
6. **🛡️ 反转平仓豁免**: RSI信号开的仓位不受反转平仓影响，由止损/止盈管理（v1.2新增）

---

## 配置说明

在策略配置的 `config` JSON 中添加 `rsiSignal` 字段：

```json
{
  "rsiSignal": {
    "enabled": true,
    "longThreshold": 30,
    "shortThreshold": 75,
    "emaStrengthTrendBars": 2,
    "minEmaStrengthLong": 0.15,
    "maxEmaStrengthShort": 1.0
  }
}
```

### 参数详解

| 参数 | 类型 | 默认值 | 说明 |
|-----|------|--------|------|
| `enabled` | boolean | false | 是否启用RSI信号功能 |
| `longThreshold` | number | 30 | 做多RSI阈值（RSI < 此值视为超卖） |
| `shortThreshold` | number | 75 | 做空RSI阈值（RSI > 此值视为超买） |
| `emaStrengthTrendBars` | number | 2 | 连续趋势K线数（2或3） |
| `minEmaStrengthLong` | number | 0.15 | 做多最小EMA强度（%） |
| `maxEmaStrengthShort` | number | 1.0 | 做空最大EMA强度（%） |

---

## 信号判断流程

### 做多(LONG)信号

```
1. RSI(15M) < longThreshold (默认30)
   ↓
2. 检查EMA强度是否连续上升
   - 计算最近N根K线的EMA强度：|EMA9 - EMA26| / EMA26 * 100
   - 验证：强度[-2] > 强度[-3] (如果N=2)
   ↓
3. 当前EMA强度 >= minEmaStrengthLong (默认0.15%)
   ↓
4. 触发做多信号
```

### 做空(SHORT)信号

```
1. RSI(15M) > shortThreshold (默认75)
   ↓
2. 检查EMA强度是否连续下降
   - 计算最近N根K线的EMA强度：|EMA9 - EMA26| / EMA26 * 100
   - 验证：强度[-2] < 强度[-3] (如果N=2)
   ↓
3. 当前EMA强度 <= maxEmaStrengthShort (默认1.0%)
   ↓
4. 触发做空信号
```

---

## 实现细节

### 1. 信号检测方法

位置: `strategy_executor_v2.py:1023`

```python
def check_rsi_signal(self, symbol: str, ema_data: Dict, strategy: Dict) -> Tuple[Optional[str], str]:
    """
    检测RSI极值+EMA强度变化信号（独立开仓机制）

    Returns:
        (信号方向 'long'/'short'/None, 信号描述)
    """
```

### 2. 集成到主执行循环

位置: `strategy_executor_v2.py:3607-3627`

执行顺序:
1. 金叉/死叉信号检测
2. 连续趋势信号检测
3. 持续趋势开仓检测
4. 震荡反向信号检测
5. **RSI信号检测** ← 新增
6. 限价单信号检测

### 3. 信号处理特点

- **跳过技术指标过滤器**: RSI信号是独立机制，不受RSI过滤器、MACD过滤器等限制
- **仍需开仓冷却检查**: 防止频繁开仓
- **信号类型**: `'rsi_signal'`
- **入场原因格式**: `"rsi_signal: RSI超卖(29.3<30) + EMA强度上升(0.12% > 0.18%, 当前0.18%>=0.15%)"`

---

## 示例配置

### 激进策略

```json
{
  "rsiSignal": {
    "enabled": true,
    "longThreshold": 35,
    "shortThreshold": 70,
    "emaStrengthTrendBars": 2,
    "minEmaStrengthLong": 0.10,
    "maxEmaStrengthShort": 1.5
  }
}
```

适用场景: 捕捉更多机会，适合波动较大的币种

### 保守策略

```json
{
  "rsiSignal": {
    "enabled": true,
    "longThreshold": 25,
    "shortThreshold": 80,
    "emaStrengthTrendBars": 3,
    "minEmaStrengthLong": 0.20,
    "maxEmaStrengthShort": 0.8
  }
}
```

适用场景: 只在极端超买/超卖时开仓，减少误判

### 禁用RSI信号

```json
{
  "rsiSignal": {
    "enabled": false
  }
}
```

或者完全不配置 `rsiSignal` 字段，默认禁用。

---

## 调试日志

启用RSI信号后，日志示例：

```
[BTC/USDT] RSI信号: RSI超卖(28.5<30) + EMA强度上升(0.12% > 0.15% > 0.18%, 当前0.18%>=0.15%)
✅ RSI信号匹配方向: signal=long
🚀 准备执行RSI信号开仓: rsi_signal: RSI超卖(28.5<30) + EMA强度上升(...)
📊 RSI信号开仓结果: {'success': True, 'position_id': 12345}
```

未触发时:
```
[ETH/USDT] RSI信号: RSI(45.2)在正常区间[30, 75]
[SOL/USDT] RSI信号: RSI超卖(28.1<30)但EMA强度未连续上升
[BNB/USDT] RSI信号: RSI超买(76.3>75)但EMA强度过高(1.25%>1.0%)
```

---

## 注意事项

1. **数据要求**: 需要至少20根15M K线数据用于RSI计算
2. **EMA强度计算**: 使用已收盘K线（klines[-2], klines[-3]）避免未确认数据
3. **信号优先级**: RSI信号在震荡反向信号之后、限价单信号之前检测
4. **不适用场景**: 震荡行情、趋势不明确时RSI信号可能频繁触发，建议配合其他过滤器
5. **与金叉/死叉并存**: 两种信号独立触发，可能在不同时机开仓
6. **🛡️ 反转平仓豁免**: RSI信号开的仓位不受金叉/死叉反转平仓影响，由止损/止盈管理

---

## 数据库字段

RSI信号开仓的持仓记录:

| 字段 | 示例值 |
|-----|--------|
| `source` | 'strategy' |
| `entry_signal_type` | 'rsi_signal' |
| `entry_reason` | 'rsi_signal: RSI超卖(28.5<30) + EMA强度上升(0.12% > 0.18%, 当前0.18%>=0.15%)' |

查询RSI信号开仓的持仓:

```sql
SELECT * FROM futures_positions
WHERE entry_signal_type = 'rsi_signal'
ORDER BY created_at DESC;
```

---

## 性能测试建议

1. **回测验证**: 在模拟盘先启用RSI信号，观察1-2周表现
2. **参数调优**: 根据不同币种调整阈值，避免过度交易
3. **风险控制**: 建议配合 `entryCooldown` 限制同方向最大仓位数
4. **监控指标**:
   - RSI信号触发频率
   - RSI信号开仓胜率
   - RSI信号开仓平均盈亏

---

## FAQ

**Q: RSI信号会替代金叉/死叉信号吗？**
A: 不会。RSI信号是额外的开仓机会，与金叉/死叉并存。

**Q: RSI信号是否受RSI过滤器影响？**
A: 不受影响。RSI信号是独立机制，跳过所有技术指标过滤器。

**Q: 为什么做多和做空的EMA强度要求不同？**
A: 做多时需要一定强度避免震荡，做空时需要限制强度避免追高。可根据实际情况调整。

**Q: 可以只启用做多或只启用做空吗？**
A: 可以通过调整阈值实现，例如设置 `shortThreshold: 100` 将永不触发做空信号。

**Q: 建议的 `emaStrengthTrendBars` 值是多少？**
A: 推荐2-3。值越大越保守，但可能错过快速趋势；值越小越激进，但可能增加误判。

**Q: RSI信号开的仓位会被反转信号平掉吗？**
A: 不会。RSI信号是"逆势抄底/追顶"策略，短期趋势反转是正常的。系统会跳过反转平仓检查，由止损/止盈来管理风险。

---

## 更新日志

- **2026-01-01 v1.2**: RSI信号开的仓位豁免反转平仓，避免被趋势反转过早平掉
- **2026-01-01 v1.1**: RSI信号改为市价单开仓，提高成交率
- **2026-01-01 v1.0**: 初始版本，实现RSI极值+EMA强度趋势信号检测
