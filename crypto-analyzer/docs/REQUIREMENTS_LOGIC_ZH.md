# 超级大脑量化交易系统 — 业务逻辑需求文档（权威版）

**版本**: v4.0  
**日期**: 2026-06-18  
**状态**: **生产逻辑唯一权威来源**（代码与本文冲突时，以本文为准改代码；改代码必须同步本文）

> 旧版 `design/需求文档.md`（v3.6）已过时，仅作历史参考。  
> AI 策略细节补充见 `docs/AI_STRATEGIES_AND_ADVISORS_ZH.md`，但**实盘同步、闸门、15m 判据以本文为准**。

---

## 0. 文档维护规范（强制）

### 0.1 适用范围

凡改动以下任一内容，**同一 PR/提交内必须更新本文对应章节**，并在 §16 变更记录追加一行：

- 模拟/实盘开平仓路径、同步时机、闸门条件  
- AI 探索/预测/顾问 prompt 门槛或判据  
- 调度频率、kill switch、进程职责  
- K 线采集分工、data_cache 刷新逻辑  
- 评级、TOP50、白名单规则  

### 0.2 禁止事项

- **禁止**在未更新本文的情况下修改 §1 所列「不可变硬规则」  
- **禁止**静默改变 `live_sync_status` 语义或回填历史模拟仓  
- **禁止**用 1h/RSI/24h 单独替代 15m 趋势定开仓方向（见 §8.3）  
- **禁止** `refresh_candidate_pool` 开头全表 DELETE  

### 0.3 代码映射

每个功能需求标注 **实现文件**；评审时对照「需求 ↔ 代码」双向检查。

---

## 1. 不可变硬规则（INVARIANTS）

| ID | 规则 | 违反后果 |
|----|------|----------|
| **INV-01** | 实盘开仓**仅**在模拟盘**该笔订单成交瞬间**且 `live_trading_enabled=1` 且闸门通过时发生 | 误开历史仓、资金损失 |
| **INV-02** | 打开 `live_trading_enabled` **不得**触发任何历史模拟持仓的实盘开仓 | 批量误开仓 |
| **INV-03** | 模拟平仓**不会**自动平实盘；须 `live_close_enabled=1` 且 source 在白名单且走 `close_position_direct` 等主动路径 | 实盘 orphan |
| **INV-04** | Big4 / 盈亏熔断**只发通知**，**不得**写 `system_settings.allow_long/allow_short/trading_enabled` | 用户失控 |
| **INV-05** | DB 配置：`get_db_config()` 裸 dict 传入 `DatabaseService` 必须 normalize，**禁止**静默 fallback `root@localhost` | 生产 1045 |
| **INV-06** | 5m/15m **仅 WS** 采集；fast_collector REST **仅** 1h/4h/1d | Binance IP ban |
| **INV-07** | `futures_positions.open_time` / 限价成交时间用 **UTC naive**（`utc_now_naive()`） | 持仓时长错乱 |
| **INV-08** | 开仓方向由 **15m 价格趋势 + 量价** 决定；1h/RSI/24h 仅辅证 | 方向误判 |

---

## 2. 进程架构

| 进程 | systemd / 入口 | 职责 |
|------|----------------|------|
| Web + API | `crypto-app-main` / `app/main.py` | FastAPI 9020、PaperLimitSync 10s 轮询、限价 executor |
| 调度 | `crypto-scheduler` / `app/scheduler.py` | data_cache、AI 探索/预测、战术、评级、情绪 |
| 主策略 | `crypto-smart-trader` / `smart_trader_service.py` | U 本位 smart_trader 扫描开平 |
| WS K 线 | `crypto-ws-kline` | 5m + 15m 持续 WS |
| REST K 线 | `crypto-fast-collector` | 30min 轮询，仅 1h/4h/1d |

**日志**：scheduler → `logs/scheduler_YYYY-MM-DD.log`；main → `logs/main_YYYY-MM-DD.log` + `logs/main_systemd.log`（非 journalctl 主输出）。

---

## 3. 账户与数据表

| 概念 | 值 / 表 | 说明 |
|------|---------|------|
| 模拟盘 account_id | **2**（`PAPER_ACCOUNT_ID`） | `futures_positions` / `futures_orders` |
| 实盘持仓 | `live_futures_positions` | 按 `user_api_keys` 多账号 |
| 模拟↔实盘关联 | `futures_orders.paper_position_id` → 实盘 `paper_position_id` 字段 | 平仓映射用 |
| 同步状态 | `futures_orders.live_sync_status` | NULL=待同步窗内；SYNCED/SKIPPED/FAILED |

---

## 4. 实盘开仓同步（REQ-LIVE-OPEN）— 最高优先级

**实现**: `app/services/paper_limit_sync_service.py`  
**触发**: `app/main.py` 启动 `PaperLimitSyncService`，每 **10 秒**  
**成交入口**: `app/trading/futures_trading_engine.py` → `fill_paper_limit_order`

### 4.1 业务定义

> **模拟盘某笔开仓订单 FILLED 的瞬间**，若实盘总开关已开且该 source/symbol 通过闸门，则在 Binance 开对应实盘仓。  
> **任何其他时机（含用户稍后打开实盘开关）均不得对该笔或历史笔补开实盘。**

### 4.2 流程（必须按序）

```
模拟限价/市价成交 (fill_paper_limit_order)
  ├─ decide_live_sync_at_paper_fill(symbol, source)
  │    ├─ check_live_open_allowed 通过 → live_sync_status 保持 NULL
  │    └─ 不通过（含 live_trading_enabled=0）→ 当场 live_sync_status='SKIPPED'
  │
  └─ PaperLimitSync 每 10s（仅 live_trading_enabled=1 时扫描）
       ├─ mark_stale_unsynced_paper_orders：fill_time 超过 5 分钟仍为 NULL → SKIPPED
       ├─ 仅 pick：NULL + fill_time 在 5 分钟内 + 模拟 account_id=2 + 持仓仍 open
       ├─ check_live_open_allowed → 不通过则 SKIPPED（不得留 NULL）
       └─ BinanceFuturesEngine.open_position → SYNCED / FAILED
```

### 4.3 用户打开 `live_trading_enabled=1` 时

**实现**: `app/api/system_settings_api.py`  
**必须**调用 `skip_all_pending_paper_live_sync()`：将所有仍 `open` 且 `live_sync_status IS NULL` 的模拟开仓单标 **SKIPPED**。  
**禁止**借此批量同步历史模拟仓。

### 4.4 live_sync_status 语义

| 状态 | 含义 | 可否再同步 |
|------|------|------------|
| NULL | 成交瞬间闸门通过，在时间窗内待 PaperSync | 仅 5 分钟内 |
| SYNCED | 已开实盘 | 否 |
| SKIPPED | 实盘关/闸门拒/超窗/开开关清理 | **永不再同步** |
| FAILED | 技术失败（API/引擎/无 SLTP） | 否（防重复下单） |

### 4.5 常量

| 常量 | 值 | 说明 |
|------|-----|------|
| `LIVE_SYNC_FILL_WINDOW_MINUTES` | **5** | 超过则 SKIPPED，禁止回填 |

### 4.6 已废弃路径（禁止恢复）

- `_sync_to_live()` 在 explore/predict worker 内**直接**下单：代码仍存在但**不得**在成交链路中调用；统一走 PaperLimitSync。  
- 按 `live_sync_status IS NULL` 扫描 **2 小时** 历史单：已删除，**禁止**恢复。

---

## 5. 实盘平仓同步（REQ-LIVE-CLOSE）

**闸门**: `live_close_enabled=1` + `should_sync_live_for_source(source)`  
**实现**: `gemini_position_advisor._close_live_position`、`BinanceFuturesEngine.close_position_direct`、smart_trader 关仓路径  

| 规则 | 说明 |
|------|------|
| 开仓闸门 | TOP50/白名单等 **仅开仓**检查 |
| 平仓 | 按 `paper_position_id` 映射平实盘，**不再**查 TOP50/白名单 |
| 模拟关仓 alone | **不会**自动平交易所；须显式 sync 路径 |

---

## 6. 实盘/模拟闸门（REQ-GATES）

**实现**: `app/services/trading_gates.py`

### 6.1 总开关

| setting | 默认 | 作用 |
|---------|------|------|
| `live_trading_enabled` | 0 | 实盘**开仓**总开关 |
| `live_close_enabled` | 0 | 模拟平仓时是否同步平交易所 |

### 6.2 可实盘 source 白名单（LIVE_SYNC_SOURCES）

仅以下 source 可参与实盘开仓同步：

- `gemini_explore`
- `gemini_predict`
- `deepseek_explore`
- `deepseek_predict`
- `gemini_midline_long` / `gemini_midline_short`
- `deepseek_midline_long` / `deepseek_midline_short`

**其余**（GPT 探索/预测、战术、反转、smart_trader、BTC 动量等）**仅模拟仓**。

### 6.3 check_live_open_allowed 检查顺序

1. 北京时间开仓时段（当前**已解除**，全天允许）  
2. `live_trading_enabled=1`  
3. source ∈ LIVE_SYNC_SOURCES  
4. symbol 亏损冷却 / 止损冷却  
5. `check_live_symbol_allowed`：**仅 L0 白名单**（`rating_level=0`）且 `live_whitelist_enabled=1`  

> TOP50 实盘闸门（`live_top50_required`）**已废弃**，代码 `is_live_top50_required()` 恒 false。

### 6.4 模拟盘开仓闸门 check_simulated_symbol_allowed

- 拒绝 L3  
- 允许：TOP50 / 有评级且非 L3 / candidate_pool 内  
- L3 模拟：默认 **不禁止**（`blacklist_level3_enabled` 默认 0）；L3 **仍禁止实盘**

### 6.5 实盘保证金

- **主探索/预测/中线**：`user_api_keys.max_position_value` × `get_live_margin_ratio(symbol)`  
- **中线策略**额外：杠杆取纸面单或 **5x**；SL **6%** / TP **20%**（与模拟一致，经 PaperSync 从纸面单换算）  
- L0=1.0x，L2=0.125x，L1/L3 禁止实盘（中线同样须过 L0 白名单闸门）  

---

## 7. 模拟开仓路径（REQ-PAPER-OPEN）

**限价入口**: `app/services/paper_limit_entry.py` → `create_paper_limit_order`  
**成交**: `futures_trading_engine.fill_paper_limit_order`  
**闸门**: `app/services/paper_open_gate.py` → `gate_simulated_open`（含开仓顾问）

| 步骤 | 说明 |
|------|------|
| 1 | 策略 worker / smart_trader 调用 `create_paper_limit_order`（或 engine 限价模式） |
| 2 | PENDING 限价 → executor 每 5s 用 **ticker 最新价**（与 UI 一致，非 mark）触价成交；超时按 `paper_limit_timeout_action` 放弃或转市价 → FILLED |
| 3 | 成交瞬间 §4.2 决定 live_sync_status |
| 4 | PaperLimitSync 在 5 分钟窗内同步实盘（若 NULL） |

**限价偏移**（`paper_limit_entry.py`）：

| 类型 | 偏移来源 |
|------|----------|
| **中线**四策略 | 固定 **做多−3% / 做空+3%**（`MIDLINE_LIMIT_*_OFFSET_PCT`，不受系统设定影响） |
| **探索/预测/smart_trader 等** | `system_settings.paper_limit_long_offset_pct` / `paper_limit_short_offset_pct`（Web 可调，默认 **0.5%**，范围 **0.1~1%**） |

**中线策略**（`gemini_midline_*` / `deepseek_midline_*`）：量化扫描，**跳过**开仓/持仓顾问；**不参与** `SmartExitOptimizer` 监控（由 `position_sl_tp_monitor` 负责 SL/TP、ai-trail-tp、到期/爆仓）；模拟保证金 500U；**限价偏移 做多−3% / 做空+3%**、**6 小时未成交过期**；**限价成交后**经 PaperLimitSync 同步实盘（须 `live_trading_enabled=1` + L0 白名单）；实盘保证金同 API `max_position_value` × 评级比例；杠杆/SL/TP 同策略常量；模拟平仓时须 `live_close_enabled=1` 且 source ∈ LIVE_SYNC_SOURCES 才同步平交易所。

---

## 8. AI 主探索 / 主预测（REQ-AI-EP）

**Prompt**: `ai_explore_prompt.py` / `ai_predict_prompt.py`（中文生产）  
**Worker**: `gemini/deepseek_*_explore_worker.py`、`gemini/deepseek_predictor.py`

### 8.1 调度

| 任务 | schedule | 防重 |
|------|----------|------|
| 探索 | every(4).hours + every(10).min | worker 内距上次 ok ≥4h；status partial 非 running |
| 预测 | every(4).hours + every(5).min | `*_predict_next_due_utc` 认领 |

### 8.2 持仓参数（默认）

- SL **3%** / TP **5%** / 杠杆 **5x** / 保证金 **500U** / 持仓 **4h**  
- 置信度门槛 ≥ **0.65**（`EXPLORE_CONFIDENCE_THRESHOLD` / `PREDICT_CONFIDENCE_THRESHOLD`，以代码为准）  
- `explore_catalyst_technical_ok` 硬门槛（含 15m 结构、量价、趋势与 category 一致）

### 8.3 K 线判据（INV-08 展开）

| 层级 | 15m | 1h |
|------|-----|-----|
| **定方向** | 16 根=4h 窗口；**价格趋势 + 量价** | 不得单独定方向 |
| **交叉验证** | 近 4~6 根结构 | 近 4 根 ≈ 同 4h 窗口 |
| **背景** | narrative.15m | narrative.24 根 + 表 |
| **辅证** | — | RSI(1h)、7d 距离 |

**catalyst 必写**：15m 价格趋势 + 量价 + 结构；bullish/bearish 与趋势矛盾 → `skipped_weak_catalyst`。

### 8.4 数据

- 探索读 `load_candidate_pool_for_explore()`（**禁止** pool 全表 DELETE）  
- K 线叙事：1h = 24 根趋势 + 近 6 明细；15m = 16 根（4h）  

---

## 9. 开仓 / 持仓顾问（REQ-ADVISOR）

**实现**: `open_advisor_strategy_rubrics.py`、`gemini_position_advisor.py`、`hold_advisor_query.py`  
**路由**: `open_advisor_routing.py` — 探索/预测等按 source 选 rubric  

| 类型 | 节奏 | 核心判据 |
|------|------|----------|
| 开仓顾问 | 模拟开仓前（部分策略可 skip LLM） | **15m 趋势 + 量价** 与 side 一致 |
| 持仓顾问 tick | scheduler **每 5min** | 浮盈仓 **5min/仓**；其余 **15min/仓**；**浮盈转亏**立即 urgent 再审 |
| 持仓顾问决策 | 15m 表主审 | 浮盈 ROI≥+5% 且 15m 转弱 → 倾向 observe/sell；亏损仍严格审查 |

**AI 轻量移动止盈**（`position_sl_tp_monitor.py`）：探索/预测/中线等 AI 仓在硬 SL/TP 之外，peak 价格收益 **≥3%** 后回撤 **≥1%** 程序化平仓（`ai-trail-tp`）；不走 early-sl/breakeven。

探索/预测：`precheck_open_advisor` 复用 `explore_catalyst_technical_ok(side=)`；`validate_open_advisor_approval` 检查 15m K 线反向统计。

---

## 10. 战术 / 反转 / 其他 AI 策略

| 类型 | 调度 | conf | 实盘 |
|------|------|------|------|
| 战术四策略 ×3 教师 | 15 槽位，15min 轮询 | ≥0.55 | **否** |
| 顶空底多 ×3 教师 | 4h + 轮询 | ≥0.65 | **否** |
| GPT 探索/预测 | 同 Gemini 节奏 | 同主策略 | **否** |
| Gemini 情绪 | 8h | — | 不下单 |

详见 `docs/AI_STRATEGIES_AND_ADVISORS_ZH.md` §3–6。

---

## 11. 调度与 data_cache（REQ-SCHED）

**实现**: `app/scheduler.py`

| 任务 | 频率 |
|------|------|
| candidate_pool_snapshot | 6 min（UPSERT） |
| explore_prepared_snapshot | 15 min |
| settings_cache | 1 min |
| TOP50 + 全量评级 | 每天 02:05 |
| Gemini/DeepSeek/GPT 探索 | 4h + 10min 轮询 |
| Gemini/DeepSeek/GPT 预测 | 4h + 5min 轮询 |
| 战术 15 槽位 | 15 min |
| Gemini 情绪 | 8 h |

`scheduler_init` 错峰：探索 +15s/+90s/+120s；预测补跑 +45s/+50s/+55s。**勿**用 init 跑预测主逻辑。

---

## 12. K 线采集（REQ-KLINE）

| 周期 | 通道 | 服务 |
|------|------|------|
| 5m / 15m | **WebSocket only** | `crypto-ws-kline` |
| 1h / 4h / 1d | REST 30min 轮询 | `crypto-fast-collector` |

IP 封禁：`binance_rate_guard` + `logs/binance_ban_state.json`；封禁时 REST/WS backfill 跳过。

---

## 13. 评级与 TOP50（REQ-RATING）

**实现**: `update_top_performers.py` / `symbol_rating_manager.py`

| 等级 | 条件概要 | 模拟 | 实盘 |
|------|----------|------|------|
| L0 白名单 | 盈利≥300U 且 胜率≥40%，**或** 盈利≥100U 且 胜率≥45% | 可 | 可（须 L0） |
| L1 | 盈利>50U 或 胜率>46%（非 L0） | 可 | 禁止 |
| L2 | -100<盈利<0 或 胜率>44% | 可 | 禁止 |
| L3 | 盈利<-100U 且 胜率<44% | **可**（默认） | 禁止 |

TOP50：`top_performing_symbols` 表；模拟开仓参考，**非**实盘开仓必要条件（实盘看 L0）。

---

## 14. smart_trader 主策略（REQ-ST）

**实现**: `smart_trader_service.py`  
**source**: `smart_trader`  
**实盘**: 不走 PaperLimitSync 白名单 → **仅模拟**（除非另有独立实盘路径，当前无 LIVE_SYNC）

要点：Big4 阈值、16 道风控、SmartEntryExecutor 15min 采样、SmartExitOptimizer 平仓。

---

## 15. 代码 ↔ 需求索引

| 需求 ID | 主文件 |
|---------|--------|
| REQ-LIVE-OPEN | `paper_limit_sync_service.py`, `futures_trading_engine.py`, `system_settings_api.py` |
| REQ-LIVE-CLOSE | `trading_gates.py`, `gemini_position_advisor.py`, `binance_futures_engine.py` |
| REQ-GATES | `trading_gates.py` |
| REQ-PAPER-OPEN | `paper_limit_entry.py`, `paper_open_gate.py` |
| REQ-AI-EP | `ai_explore_prompt.py`, `ai_predict_prompt.py`, `*_explore_worker.py`, `*_predictor.py` |
| REQ-ADVISOR | `open_advisor_strategy_rubrics.py`, `gemini_position_advisor.py`, `hold_advisor_query.py`, `position_sl_tp_monitor.py` |
| REQ-SCHED | `scheduler.py`, `data_cache_service.py` |
| REQ-KLINE | `binance_ws_kline_collector.py`, `fast_collector_service.py` |
| REQ-RATING | `update_top_performers.py` |
| REQ-ST | `smart_trader_service.py` |

---

## 16. 变更记录

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-06-18 | v4.0 | **重写权威需求文档**；明确 INV-01/02 实盘仅成交瞬间同步；PaperSync 5 分钟窗；开开关 skip 历史单；15m 量价定方向 |
| 2026-06-18 | — | fix `f9db7a64` 修复实盘开关回填历史模拟仓 |
| 2026-06-18 | — | fix `fe61e698` 15m 趋势+量价 catalyst 门槛 |
| 2026-06-18 | — | 中线限价单过期时间 2h → **6h**（`MIDLINE_LIMIT_TIMEOUT_MINUTES=360`） |
| 2026-06-18 | — | L0 门槛放宽（300U/40%、100U/45%）；L3 默认不禁止模拟仓 |
| 2026-06-18 | — | 中线实盘保证金改为 API `max_position_value`（不再写死 100U） |
| 2026-06-21 | — | 转市价/限价成交后立即 PaperSync；API 返回 live_sync 状态 |
| 2026-06-21 | — | AI ai-trail-tp（peak≥3% 回撤≥1%）；持仓顾问浮盈 5min 复审 + 转亏 urgent；盈利 sell 门槛降至 ROI+5% |
| 2026-06-21 | — | 中线仓排除 SmartExit 监控/健康检查；仅 position_sl_tp_monitor |
| 2026-06-21 | — | 中线限价明确 做多−3% / 做空+3%（`MIDLINE_LIMIT_*_OFFSET_PCT`） |

---

## 附录 A：回归检查清单（改代码后）

- [ ] 本文对应章节已更新  
- [ ] §16 变更记录已追加  
- [ ] `live_trading_enabled` 0→1 不会 sync 历史 NULL 单（`skip_all_pending_paper_live_sync`）  
- [ ] 模拟成交时 live 关 → 当场 SKIPPED  
- [ ] fill_time > 5min 的 NULL → SKIPPED  
- [ ] 改动 scheduler 后只 restart `crypto-scheduler` 一次  
- [ ] 改动 main/PaperSync 后 restart `crypto-app-main`  
