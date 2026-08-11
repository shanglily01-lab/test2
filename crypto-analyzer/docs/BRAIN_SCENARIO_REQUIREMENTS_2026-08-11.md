# 超级大脑场景化交易需求（批准版，2026-08-11）

## 决策结论

已批准：

- 新增市场场景状态机作为 BRAIN 第一层判断。
- 开仓必须先判 Regime，再判 Playbook。
- A1 只在多头或强单币多头场景开，禁止在转空/瀑布场景继续惯性开多。
- A2 只在空头趋势或强单币空头场景小仓开。
- C1 默认影子，只在空头趋势/瀑布强确认下极小仓试点。
- TRANSITION 趋势切换期禁止旧方向新开。
- 计划到期不得无脑强平，必须按当前场景和持仓 thesis 重判。

未批准：

- 暂不增加“日内方向熔断”（例如 A1 单日亏损超过某阈值自动暂停 LONG）。后续如需加入，另行确认。

## 1. Regime 枚举

| Regime | 含义 | 默认姿态 |
|---|---|---|
| `BULL_TREND` | Big4 明确偏多，市场量价健康 | 主做 A1 |
| `BEAR_TREND` | Big4 明确偏空，市场量价健康 | 允许 A2，小仓 C1 |
| `CRASH_DOWN` | 急跌/瀑布，波动与成交放大 | 禁止 A1，允许强空小仓 |
| `PANIC_REBOUND` | 急跌后恐慌反弹 | 禁止追空，B1/C2 先影子 |
| `RANGE_CHOP` | 高波动震荡，方向反复 | 少开、轻仓、快出 |
| `LOW_VOL_NO_TRADE` | 低波动低量无趋势 | 不开仓 |
| `TOKEN_DIVERGENCE` | Big4 中性，但单币强趋势 | 小仓顺单币强方向 |
| `TRANSITION` | 大环境方向切换期 | 禁止旧方向新开 |

## 2. 开仓规则

开仓顺序必须为：

```text
Regime 判定
  -> Playbook 是否在该 Regime 允许表中
  -> 方向是否允许
  -> 1h/15m 是否支持
  -> 胜率/edge/confirmed
  -> 仓位倍率
  -> execution_mode
  -> 限价单
```

### 2.1 BULL_TREND

- 允许：A1。
- 谨慎允许：C3，仅影子或后续确认。
- 禁止：A2、B2、C1、C4。
- A1 正常仓，限价接回踩。

### 2.2 BEAR_TREND

- 允许：A2。
- 谨慎允许：C1 小仓。
- 禁止：A1。
- A2 保持 0.35-0.50 仓；C1 保持 0.20-0.35 仓。

### 2.3 CRASH_DOWN

- 禁止：A1、B1 第一时间抄底。
- 允许：强 C1 / 强 A2 小仓。
- 必须具备 1h/15m 同空，且有 `crash_spike` / `break_support` / `volume_expand_down` 等确认。
- 持仓时间短，不能使用普通 6h hold。

### 2.4 PANIC_REBOUND

- 禁止：继续追空。
- B1/C2 先影子观察，实开需另行确认。
- 不恢复 A1，除非 1h 结构已修复。

### 2.5 RANGE_CHOP

- 允许：高 edge A1/A2。
- 禁止：C1/C3 追突破。
- 仓位 0.25-0.50。
- 只用限价，超时取消。

### 2.6 LOW_VOL_NO_TRADE

- 禁止所有新开仓。
- 只落库影子机会。

### 2.7 TOKEN_DIVERGENCE

- Big4 中性，但单币 1h/15m 同向且强。
- 强多允许 A1 小仓。
- 强空允许 A2 或强 C1 小仓。
- 仓位 0.25-0.50。

### 2.8 TRANSITION

- 禁止旧方向新开。
- LONG -> FLAT/SHORT 过渡：禁止 A1。
- SHORT -> FLAT/LONG 过渡：禁止追空。
- 新方向只允许小仓确认。

## 3. 持仓规则

持仓监控每次需要重判：

- 当前 Regime 是否变化；
- 持仓方向是否仍被 Regime 允许；
- 原 Playbook thesis 是否仍成立；
- 15m 是否结构破坏；
- 是否有盈利应保护；
- 是否需要缩短计划持仓。

动作枚举：

| 动作 | 含义 |
|---|---|
| `HOLD` | 继续持有 |
| `TIGHTEN` | 收紧 trail/缩短到期 |
| `EXIT` | thesis 失效，主动平 |
| `FORCE_EXIT` | 熔断、硬 SL、极端反向 |

## 4. 平仓规则

平仓优先级：

1. 交易安全与异常保护。
2. 美元熔断。
3. Playbook 快速失败。
4. Regime 反向。
5. 硬 SL/TP。
6. trail lock。
7. 计划到期重判。
8. DeepSeek 持仓顾问复核。

计划到期时不得无脑强平：

- 若 Regime 与持仓方向一致，且 15m thesis 仍成立，可延长或继续持有。
- 若 Regime 中性但持仓没有盈利进展，应平仓。
- 若 Regime 反向或 Playbook thesis 失效，应平仓。

## 5. 明确不做

本轮不做：

- 日内方向熔断；
- 自动根据单日亏损暂停某方向；
- 分批减仓；
- 实盘同步；
- 全量恢复 C1/B1/B2/B3/C3/C4 实开。

## 6. 第一阶段落地范围

本轮实现：

- 新增 `brain_market_regime.py`；
- orchestrator 接入 Regime；
- BRAIN opportunity 落库 reason 中带 Regime；
- A1 在 `BEAR_TREND` / `CRASH_DOWN` / `TRANSITION` 禁止；
- A2/C1 只在允许场景小仓；
- `LOW_VOL_NO_TRADE` 禁止开仓；
- 计划到期改为 BRAIN 专属重判。

