# 超级大脑量化交易系统 — 业务逻辑需求文档（权威版）

**版本**: v4.5.2  
**日期**: 2026-07-31  
**状态**: **生产逻辑唯一权威来源**（代码与本文冲突时，以本文为准改代码；改代码必须同步本文）  
> **中线 v2（REQ-MIDLINE §7.2）**：已确认并落地模拟仓（`midline_long` / `midline_short`）；**暂不实盘**。  
> **超级大脑主权层（REQ-BRAIN §7.3）**：**首版已落地**；与 DeepSeek 探索/预测 **对照期并行**；对照结束后再全面暂停旧 DS 自动开仓。  
> **BRAIN v2 机会识别（§7.3.10–7.3.15）**：**已落地且开仓机会判定视为相对客观**；退出/风控见 §7.3.16。  
> **BRAIN 退出（§7.3.16 / REQ-BRAIN-RISK · v4.5.2 已落地）**：开仓按币评估 SL/TP/hold；硬 SL/TP + 计划到期 + **新版** `brain_trail_lock` / `brain_soft_no_follow`；仍跳过 DeepSeek 持仓顾问；**不做**旧 ai-trail。

> 旧版 `design/需求文档.md`（v3.6）已过时，仅作历史参考。  
> AI 策略细节补充见 `docs/AI_STRATEGIES_AND_ADVISORS_ZH.md`，但**实盘同步、闸门、15m 判据以本文为准**。

---

## 0. 文档维护规范（强制）

### 0.1 适用范围

凡改动以下任一内容，**同一 PR/提交内必须更新本文对应章节**，并在 §16 变更记录追加一行：

- 模拟/实盘开平仓路径、同步时机、闸门条件  
- AI 探索/预测/顾问 prompt 门槛或判据  
- **超级大脑主权层（REQ-BRAIN）**分析/战略/DeepSeek 顾问角色、防插针限价  
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
| **INV-09** | **REQ-BRAIN**：无自有 Playbook `LONG/SHORT` + 分向胜率门 → 不得开仓；BRAIN **跳过开仓顾问**；**禁止**机械抄 DeepSeek 自动开仓作为主权主路径 | 负期望灌水 |
| **INV-10** | **REQ-BRAIN**：Big4 疲软（动量弱且相对成交量很低、量价波动很小）→ 不得开仓 | 宏观逆势亏损 |
| **INV-11** | **REQ-BRAIN**：影线>实体×2 计插针；频繁插针禁止市价，须平均插针限价；**限价超时必须取消**（**测试期** `BRAIN_USE_MARKET_ENTRY` 暂缓） | 插针扫损 |
| **INV-12** | **REQ-BRAIN（v4.5.2）**：持仓退出以仓上评估硬 SL/TP、计划到期与新版程序化锁利为准；**不**经持仓顾问与战略翻转平仓为主路径 | 平仓权责混乱 |

> INV-09～INV-12 约束设计与运行时；中线 v2（§7.2）为独立量化路径，不受 INV-09～11 开仓链约束，但仍受 INV-01～08。

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

- `deepseek_explore`
- `deepseek_predict`

**已下线（不再实盘、不再调度）**：`gemini_explore` / `gemini_predict`（系统配置开关已移除，DB 强制关闭）。

**中线 v2**（`midline_long` / `midline_short`）：**暂不**加入白名单 → **仅模拟**（确认后再开实盘）。  
落地时须从白名单**移除**旧四路 `gemini_midline_*` / `deepseek_midline_*`（若仍残留）。

**其余**（GPT 探索/预测、战术、反转、smart_trader、BTC 动量、中线 v2 等）**仅模拟仓**。

### 6.3 check_live_open_allowed 检查顺序

1. 北京时间开仓时段（当前**已解除**，全天允许）  
2. `live_trading_enabled=1`  
3. source ∈ LIVE_SYNC_SOURCES  
4. symbol 亏损冷却 / 止损冷却  
5. `check_live_symbol_allowed`：**仅 L0 白名单**（`rating_level=0`）且 `live_whitelist_enabled=1`  

> TOP50 实盘闸门（`live_top50_required`）**已废弃**，代码 `is_live_top50_required()` 恒 false。

### 6.4 模拟盘开仓闸门 check_simulated_symbol_allowed

- **拒绝**：L2+（`rating_level>=2`）、**手动锁定**（`rating_locked=1`）  
- **允许**：TOP50 / 有评级且非禁止 / candidate_pool 内  
- L2+ 与手动锁定：**模拟盘与实盘均禁止开仓**（`check_symbol_trading_forbidden`）  
- `blacklist_level3_enabled` 设置项**已废弃**（行为恒为启用，保留仅为兼容旧 UI）

### 6.5 实盘保证金

- **主探索/预测**：`user_api_keys.max_position_value` × `get_live_margin_ratio(symbol)`  
- **中线 v2**：本期**不实盘**；若日后加入 `LIVE_SYNC_SOURCES`，保证金同主探索（API `max_position_value` × 评级比例），杠杆/SL/TP 与模拟一致（SL **6%** / TP **3%**），且须过 L0 白名单闸门  
- L0=1.0x；L1/L2/L3 禁止实盘（L2+ 同时禁止模拟）  

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
| **中线 v2**（`midline_long` / `midline_short`） | 固定 **做多−1% / 做空+1%**（策略常量，不受 `paper_limit_*_offset_pct` 影响） |
| **探索/预测/smart_trader 等** | `system_settings.paper_limit_long_offset_pct` / `paper_limit_short_offset_pct`（Web 可调，默认 **0.5%**，范围 **0.1~1%**） |

### 7.2 中线策略 v2（REQ-MIDLINE）【已落地 · 仅模拟】

> **与 INV-08 关系**：INV-08 约束 AI 探索/预测的 catalyst 定方向。中线 v2 为**独立量化策略**：方向由 **30×1d + 约 1 周 1h** 趋势定调，**15m 仅作高低位企稳/缩量入场闸门**，不视为违反 INV-08。

#### 7.2.1 目标与边界

| 项 | 约定 |
|----|------|
| 性质 | **量化**，非 LLM；改现有中线引擎，**不**再挂 Gemini/DeepSeek 教师名 |
| 标的池 | `config.yaml` 交易对全集（约 260）；保留证券过滤、L3/锁定禁止等既有闸门 |
| 实盘 | **暂不** ∈ `LIVE_SYNC_SOURCES` → 仅模拟 `account_id=2` |
| 开仓顾问 | **跳过**（`skip_open_advisor=True`） |
| 持仓顾问 | **排除**（不做 DeepSeek/Gemini 持仓顾问；避免 15m 噪音闷杀 8h 波段） |
| SmartExit | **排除**；平仓由 `position_sl_tp_monitor` |
| 移动止盈 | **接入 ai-trail-tp**（peak 价格收益 ≥**3%** 后回撤 ≥**1%**） |
| 旧策略 | **停调度并移除**：`gemini_midline_long/short`、`deepseek_midline_long/short` 及对应 kill switch / 探索页 Tab |

#### 7.2.2 Source、调度、Kill Switch

| 项 | 值 |
|----|-----|
| source | `midline_long` / `midline_short` |
| 调度 | **独立**任务：每 **4h** 全市场扫描一轮（可辅以短轮询认领，防 restart 丢槽）；**不再**走四路教师中线调度 |
| kill switch | `midline_long_enabled` / `midline_short_enabled`（默认建议 0，确认上线后再开） |
| 限价超时 | **4h**（与扫描周期对齐） |

实现入口（落地后沿用/改造）：`midline_swing_config.py`、`midline_swing_scanner.py`、`midline_explore_worker.py`（或改名为 `midline_worker`）、`midline_swing_api.py`、`app/scheduler.py`。

#### 7.2.3 模拟单参数

| 参数 | 值 |
|------|-----|
| 限价偏移 | 做多 **−1%** / 做空 **+1%** |
| 限价超时 | **4h** 未成交 → 按系统 `paper_limit_timeout_action`（放弃或转市价，与现网一致） |
| SL / TP | **止损 6%** / **止盈 3%** |
| 计划持仓 | **8 小时**（到期由 `position_sl_tp_monitor` 平仓） |
| 杠杆 | **5x** |
| 保证金 | **500U**（模拟） |
| 扫描周期 | **4h** |

#### 7.2.4 信号逻辑（默认硬规则，可后续调参）

三层 **AND** 同向才产生开仓机会；缺 K 线 → 跳过并记录 `reason`。阈值均写死为默认，Web/settings 调参为二期。

**（1）大趋势 — 30×1d**（v2.1 放宽，避免永不开仓）

- 收盘相对约 30 日前：涨幅 ≥ **+3%** → 偏多；跌幅 ≤ **−3%** → 偏空；其间 → 无方向  
- 日线 RSI(14)：做多 **30–78**；做空 **22–70**  
- 近 10 日均量 ≥ 前 20 日均量的 **0.40**

**（2）中趋势 — 约 1 周（168×1h，对照 1d）**

- 近 24 根 1h 均价相对 168 根均价：做多允许回踩至 **−1.5%** 内；做空允许反抽至 **+1.5%** 内  
- 1h RSI：做多 ≥ **35**；做空 ≤ **65**

**（3）位置与入场 — 近 4h（16×15m）+ 30×1d / 10d 高低**

- **做多（回踩企稳）**：现价落在近 30×1d 区间 **下 40%**，**或** 贴近近 10 日低点（≤低点×1.05）；且 15m 波幅收敛（≤前段 1.20）或近 3 收盘未持续创新低  
- **做空（反抽滞涨）**：现价落在近 30×1d 区间 **上 40%**，**或** 贴近近 10 日高点；且 15m 缩量（≤前段 1.05）或近 3 收盘未持续创新高

**（4）下单**

过闸 → `create_paper_limit_order`（强制限价 ±1%，对应 `midline_long` / `midline_short`）。

#### 7.2.5 Web：中线策略页与机会分析

| 项 | 约定 |
|----|------|
| 主入口 | **原 Gemini 主探索页整页改造**为「中线策略」页（路由可仍用 `/gemini-explore` 或改名，须在实现时统一导航文案） |
| DeepSeek 探索页 | **移除**中线 Tab；中线**只**在中线策略页管理 |
| Gemini 主探索 LLM UI | **下线**（本页不再展示探索/预测 LLM 流程） |
| `gemini_explore` 调度 | **本期默认仍保留**（无本页入口）；是否停调度另开确认，避免误伤实盘白名单路径 |
| 机会分析（必做） | 每轮扫描可查：时间、标的数、通过/拒绝数、多空数；列表含 symbol、方向、三层通过标记/摘要指标、建议限价、拒绝 `reason code`；可下钻单币明细（价量 RSI 摘要；K 线图二期可选） |
| 页内其它 | 参数展示/编辑（周期、限价偏移、kill switch）、限价单与模拟持仓状态、手动触发一轮扫描 |

落库建议（实现时可微调表名）：`midline_scan_runs`（轮次）+ `midline_scan_verdicts`（每 symbol 通过/拒绝与指标快照）。

#### 7.2.6 持仓管理

| 机制 | 行为 |
|------|------|
| 硬 SL/TP | SL 6% / TP 3%，`position_sl_tp_monitor` |
| 到期 | 持仓满 **8h** 平仓 |
| ai-trail-tp | **启用**（与探索/预测相同阈值） |
| 持仓顾问 | **不监管**中线；仅硬 SL/TP + ai-trail-tp + 8h 到期 |
| SmartExit | 不监控中线 source |

### 7.3 超级大脑主权层（REQ-BRAIN）【需求已确认 · 首版已落地】

> **目标**：用系统自有采集数据对 **L0 + L1** 做行情与战略判断；DeepSeek **仅为顾问**，不得再作为唯一开仓大脑。  
> **状态**：需求 v1.0 已确认；**首版代码已落地**；**对照期** DeepSeek 自动开仓暂保留并行对比（INV-BRAIN-07 暂缓）。阈值与 Big4 门槛可按 7 日样本再标定。  
> **与 INV-08**：BRAIN 大方向看 **1H（近 1 周）**，入场结构看 **15M（近 1 天）**；15m 量价仍为入场结构硬约束，与 INV-08 一致并扩展 1H 定调。

#### 7.3.1 不可变规则（INV-BRAIN）

| ID | 规则 |
|----|------|
| **INV-BRAIN-01** | 无自有分析 `side∈{LONG,SHORT}` 不得开仓 |
| **INV-BRAIN-02** | 近 **7 日**、同规则、信号后 **4h**「方向对就算赢」的实现胜率 `win_prob` **&lt; 55%** → 不得开仓 |
| **INV-BRAIN-03** | Big4 **疲软**（动量弱，且相对成交量很低 → 量价波动很小）→ 不得开仓；须有足够量价波动（门槛用近 7 日数据标定） |
| **INV-BRAIN-04** | **开仓**：自有 Playbook 主判通过后直接开仓（**跳过开仓顾问**）。**平仓**：硬 SL/TP + 按币计划到期 + 新版程序化锁利/无跟进早砍（§7.3.16）；**不经**持仓顾问 |
| **INV-BRAIN-05** | **平仓**：不以大脑战略翻转 / DeepSeek 持仓顾问为主路径；安全网为仓上评估 SL/TP、计划到期与 `brain_trail_lock` / `brain_soft_no_follow` |
| **INV-BRAIN-06** | 单根 K **影线长度 &gt; 实体长度 × 2** 计为有效插针；近 7 日插针**频繁** → **禁止市价开仓**，须按平均插针一侧挂限价；**限价超时必须取消**（禁止转市价）。**测试期例外**：`BRAIN_USE_MARKET_ENTRY=True` 时暂缓，直接市价 |
| **INV-BRAIN-07** | **旧 DeepSeek 探索/预测自动开仓**：目标为全面暂停；**当前对照期暂缓**——与 `brain_swing` **并行开仓**，便于胜率/PnL 对比；对照结束后再强制关并停调度 |

#### 7.3.2 标的与数据窗口

| 项 | 约定 |
|----|------|
| 标的池 | 仅 **L0 白名单 + L1**（`rating_level∈{0,1}` 且未 `rating_locked`） |
| **1H** | **大方向**；回看 **近 1 周** |
| **15M** | **小波动 / 入场结构**；回看 **近 1 天** |
| 辅证 | RSI(1h)、距 7d 高低、资金费率、量价叙事等（candidate_pool / kline 已采集） |
| Big4 | 必须参与闸门：疲软不开；须量价波动；代币方向须与 Big4 **对齐** 才可 `LONG/SHORT`，否则 `FLAT` |

**模拟单参数**（`brain_config.py` + `brain_risk_params.py`；与 engine 一致用**百分点**）：

| 参数 | 现行（v4.5.2） |
|------|----------------|
| SL / TP | **按币评估**（Playbook 族 × 15m ATR ± 插针/胜率调整）；失败才 fallback **5% / 8%**（打 `risk_fallback`） |
| 计划持仓 | **按币评估** hold（约 0.75–8h）；失败才 fallback **6h** |
| 杠杆 / 保证金 | **5x** / **500U** |
| 入场 | 测试期市价（`BRAIN_USE_MARKET_ENTRY`） |
| 平仓路径 | 硬 SL/TP → `brain_trail_lock` → `brain_soft_no_follow` → 计划到期；关战略平仓；跳过 DeepSeek 持仓顾问；**禁止**旧 ai-trail/soft/trend |

**对齐默认**（实现可微调但须文档化）：

- Big4 明确偏多 + 代币 1H/15M 偏多 → 可 `LONG`  
- Big4 明确偏空 + 代币 1H/15M 偏空 → 可 `SHORT`  
- 其余 → `FLAT`

#### 7.3.3 自有分析输出（每币每轮）

| 字段 | 含义 |
|------|------|
| `side` | `LONG` / `SHORT` / `FLAT` |
| `win_prob` | 0–1，见 §7.3.5 |
| `edge_score` | 可选内部打分 |
| `rationale` | 依据摘要（1H/15M 结构、量价、RSI、7d、资金费、与 Big4 对齐说明） |
| `big4_ok` | Big4 是否过可交易宏观门槛 |
| `aligned` | 代币是否与 Big4 对齐 |
| `wick_*` | 近 7 日插针频次/平均幅度/是否频繁（见 §7.3.6） |

#### 7.3.4 开仓流程

```text
扫描 L0+L1（轮询 tick）
  → Playbook 识别 + 分向胜率门 + 场景仲裁 → side≠FLAT
  → 币种闸门 / 冷却 / 同向持仓去重（gate_simulated_open → brain_skip_advisor）
  → 【测试期】create_paper_limit_order → 市价立即开仓（BRAIN_USE_MARKET_ENTRY=1；INV-BRAIN-06 暂缓）
  → 【正式】防插针限价 + 超时取消（恢复 BRAIN_USE_MARKET_ENTRY=0）
  → OPENED = 已成交持仓（市价）或已挂 PENDING 限价（正式）
```

**DeepSeek 角色（v4.4.4+）**：开仓 **不经**开仓顾问；持仓可 **强制平**。**不是**独自扫池开仓。  
**可见性（测试期市价）**：机会表 `OPENED` + `order_id` 字段存 **position_id**；立即进「BRAIN 持仓」。

**旧路径**：`deepseek_explore` / `deepseek_predict` **对照期暂保留自动开仓**（与 BRAIN 并行；INV-BRAIN-07 暂缓）。Gemini 探索/预测此前已下线。

#### 7.3.5 胜率 55%（验收口径）

| 项 | 约定 |
|----|------|
| 样本 | 近 **7 天**、L0/L1 |
| 信号 | 自有分析当时给出看多或看空 |
| 判定窗 | 信号后 **4 小时** |
| 赢的定义 | **方向对就算赢**（价格相对信号/开仓参考价朝预测方向运动即为胜；**不要求**触及 TP） |
| 开仓条件 | 该口径实现胜率 **≥ 55%**（`win_prob` 取该回测胜率或与之绑定的估计，须可解释、可回归） |

#### 7.3.6 防插针与限价执行

| 项 | 约定 |
|----|------|
| 有效插针 | 单根 K（建议主统计 **15M**，辅 **1H**）：**影线 &gt; 实体 × 2** |
| 窗口 | 该代币 **近 7 日** 上/下影频次与平均幅度 |
| 频繁 | 用近 7 日分布标定（实现时写死分位或次数阈值，须可回归） |
| 频繁时 LONG | 挂在偏低侧（**平均下影**深度附近）限价接，**禁止市价** |
| 频繁时 SHORT | 挂在偏高侧（**平均上影**高度附近）限价接，**禁止市价** |
| 超时 | **必须取消**；禁止转市价、禁止追价硬接 |
| 审计字段 | 开仓理由须含 `wick_freq`、`avg_wick_pct`、`limit_offset_used` |

#### 7.3.7 平仓流程

| 机制 | 行为 |
|------|------|
| 硬 SL | 触及本笔评估写出的 `stop_loss_price` → 平 |
| 硬 TP | 触及本笔评估写出的 `take_profit_price` → 平（开仓后短 TP grace 仍适用） |
| 程序化锁利 | `brain_trail_lock`：峰值达激活线（≈本笔 TP×40%，夹 1.2%~3%）后，回撤达线且尽量保本 → 平 |
| 无跟进早砍 | `brain_soft_no_follow`：持仓≥60min、峰值≤0.5% 且浮亏≤−1.5% → 平 |
| 计划到期 | 达本笔 `planned_close_time`（评估 hold，≤8h）→ `planned_close_time_expired` |
| 战略平仓 | **关闭**（不再因 Playbook FLAT / Big4 / 翻转主动平） |
| 持仓顾问 | **跳过**（不进 DeepSeek 持仓顾问） |
| 旧 ai-trail / soft-sl / trend-sl | **不对 BRAIN 生效**（仅用上述新版规则） |

原则：BRAIN 持仓期认 **评估硬 SL/TP + 新版锁利/早砍 + 计划到期**；开仓仍跳过开仓顾问（Playbook 主判）。详情 §7.3.16。

#### 7.3.8 模块定位（落地后）

| 模块 | 定位 |
|------|------|
| 自有分析引擎 + 战略层 | **主路径**（扫描、开平决策、防插针限价） |
| DeepSeek | 开仓**不参与**；平仓可强制平 |
| `deepseek_explore` / `deepseek_predict` 自动开仓 | **全面暂停** |
| 中线 v2 §7.2 | **独立**量化路径，不并入 BRAIN 开仓链（除非另开需求） |
| 硬 SL/TP / trail / 超时 | 安全网 |

#### 7.3.9 实现路径（已落地）

| 角色 | 路径 |
|------|------|
| 常量 / source | `app/services/brain_config.py`（`brain_swing`） |
| 分析引擎 | `app/services/brain_market_analyzer.py`（1H/15M/Big4/对齐） |
| 插针统计 | `app/services/brain_wick.py`（影线&gt;实体×2；频繁→平均插针限价） |
| 胜率回测 | `app/services/brain_winrate.py`（近7日×4h 方向胜率，进程缓存30min） |
| 战略/编排 | `app/services/brain_strategy_orchestrator.py`（**轮询 tick**：每批5币；发现即开；`get_brain_live_status`） |
| Playbook | `app/services/brain_playbook.py`（A/B/C/D 识别 + 信号字典） |
| 机会落库 | `app/services/brain_opportunity_store.py`（`brain_scan_rounds` / `brain_opportunities`，启动时 CREATE IF NOT EXISTS） |
| 分向胜率 | `app/services/brain_winrate.py`（`win_prob_long`/`win_prob_short` + 相对差≥5pp） |
| 调度 | `app/scheduler.py`：BRAIN **每15秒** `run_brain_tick`；**对照期**仍调度 DeepSeek 探索/预测 |
| 开仓闸门 | `paper_open_gate.py`：`is_brain_source` → **`brain_skip_advisor`** |
| 入场 | `BRAIN_USE_MARKET_ENTRY`：测试期市价；否则强制限价 + expire |
| 持仓退出 | `brain_risk_params` 开仓写 SL/TP/hold；`brain_trail_exit` + monitor；`BRAIN_STRATEGIC_CLOSE_ENABLED=0`；持仓顾问排除 brain；**不做**旧 ai-trail |
| 旧路径（对照） | DeepSeek 探索/预测开仓**暂保留** |
| Web / API | `/brain_strategy`；`/api/brain-swing` |
| 回归 | `scripts/validate_brain_req.py` |

**实盘**：`brain_swing` **未**加入 `LIVE_SYNC_SOURCES`（仅模拟）；另开确认后再加。

**kill switch**：`system_settings.brain_swing_enabled`（默认视为开；显式 `0` 跳过）。

**Web**：侧栏「超级大脑策略」；页面每 **5s** 拉 `/live`；市价成交后立即进持仓（同币冷却 60min；单批最多 2 次开仓）。

#### 7.3.10 BRAIN v2：机会识别与 Playbook 体系【需求已确认 · 首版已落地】

> **北极星**：只在做多胜率明显更好时做多，做空胜率明显更好时做空，没有很好把握则不开仓。盈利优先于开仓次数。  
> **核心变化**：从「分析定方向 → 过门就开」升级为 **场景识别 → 打标 → 全部落库 → 开仓另判 → 按标签评估策略优劣** 的闭环。

##### 总原则

1. **识别要全**：扫描结果无论是否开仓，全部入库并打标。
2. **开仓可以少**：标签在、单没开，也记录事后影子结果。
3. **优劣只认结果**：按标签看期望值、胜率、盈亏比、样本是否够，指导保留/加强/淘汰。

##### 四层决策

| 层 | 问题 | 产出 |
|----|------|------|
| **L1 大环境** | Big4 是否允许交易 | 疲软或无方向 → FLAT |
| **L2 场景识别** | 属于哪类 Playbook | playbook 标签 + signals 列表 |
| **L3 方向胜率** | 该方向分向胜率是否过门 | win_prob_long / win_prob_short |
| **L4 入场执行** | 过闸后市价/限价开仓 | opened / skipped（**跳过开仓顾问**） |

#### 7.3.11 Playbook 枚举（v1 基线）

> 枚举为初始基线，后续可依据评估报表增删。场景不清 / 多空故事都能讲通 → 一律 FLAT。

**趋势延续类**

| ID | 名称 | 方向 | 一句话描述 |
|----|------|------|------------|
| **A1** | 多头趋势回踩 | LONG | 上升结构中缩量回踩再起（EMA 多头排列、HH/HL、15M 回踩不破前低、回调缩量） |
| **A2** | 空头趋势反抽 | SHORT | 下降结构中缩量反抽再落（EMA 空头排列、LH/LL、15M 反抽不过前高、反弹缩量） |

**冲击反应类**

| ID | 名称 | 方向 | 一句话描述 |
|----|------|------|------------|
| **B1** | 暴跌放量反弹 | LONG | 急跌（短窗口跌幅 > N×ATR）→ 止跌（下影/15M 不再创新低）→ 反弹放量或量能回升 → 收回枢轴/EMA |
| **B2** | 弱反抽失败 | SHORT | 下跌后出现反弹，但量价无力（缩量、不过前高/EMA、RSI 弱反）→ 跌破反抽起点再顺势空 |
| **B3** | 暴涨滞涨回落 | SHORT | 急涨后长上影/放量滞涨 → 跌破短线枢轴 |
| **B4** | 暴涨回踩有力 | LONG | 急涨后缩量回踩不破关键位 → 再度放量向上（健康回踩后趋势续） |

**破位 / 假破类**

| ID | 名称 | 方向 | 一句话描述 |
|----|------|------|------------|
| **C1** | 向下破位确认 | SHORT | 有效跌破平台/EMA 簇，回抽不过破位位，量能配合 |
| **C2** | 向下假破吸筹 | LONG | 刺破后快速收回（收盘回区间内），下影长，后续 15M 抬高 |
| **C3** | 向上突破确认 | LONG | 放量突破 + 回踩确认（不破突破位） |
| **C4** | 向上假突多头陷阱 | SHORT | 突破后迅速跌回，上影/放量出货 |

**不可交易**

| ID | 名称 | 行为 | 条件 |
|----|------|------|------|
| **D1** | 震荡无边 | FLAT | EMA 纠缠、价格反复穿越、无结构 |
| **D2** | 场景冲突 | FLAT | 多空故事都成立、1H 与 15M 方向打架、edge 与 win 方向不一致 |

##### B1 / B2 详细条件（示例）

**B1 暴跌放量反弹做多 — 必选清单**：

1. 冲击：短窗口跌幅异常（15M/1H 相对 ATR 或近 N 根跌幅分位很高）
2. 止跌：15M 不再创新低，或出现长下影止跌 K
3. 量价：反弹阶段量能 ≥ 下跌末段或 ≥ 近均值（无力则观望 / 转 B2）
4. 结构：收回暴跌启动后的某枢轴，或重新站上 15M EMA20
5. 过滤：不是「第一根阴线就抄」；至少等止跌 + 一卷反弹确认
6. 禁止 B1：阴跌无止跌、反弹完全缩量、Big4 仍在加速崩且代币创新低

**B2 弱反抽失败做空 — 必选清单**：

1. 先有下跌或处于下降结构（不是大涨中的第一次回调）
2. 出现反抽，但「无力」≥ 2 条：缩量、涨幅浅、到不了前高/EMA60、RSI 反弹弱于价格、上影增多
3. 失败确认：15M 跌破反抽起点或再次跌破 EMA20
4. 禁止 B2：反抽明显放量且结构抬高（那是 B1/B4）；纯追跌无反抽

#### 7.3.12 信号字典（v1 基线）

> 每条机会从字典中选取匹配的 signal tag 组成 `signals[]` 数组。后续可扩展。

| 类别 | signal tag | 含义 |
|------|-----------|------|
| **EMA** | `ema_bull_align` | 价格 > EMA20 > EMA60 |
| | `ema_bear_align` | 价格 < EMA20 < EMA60 |
| | `ema_reclaim` | 跌破后重新站回 EMA20 |
| | `ema_reject` | 反抽到 EMA20/60 被打回 |
| **结构** | `hh_hl` | 1H 高点/低点抬高 |
| | `lh_ll` | 1H 高点/低点降低 |
| | `15m_higher_low` | 15M 回调不破前低 |
| | `15m_lower_high` | 15M 反抽不过前高 |
| | `15m_stop_new_low` | 15M 不再创新低（止跌） |
| **RSI** | `rsi_1h_healthy_long` | RSI(1H) 45~68（多头健康区） |
| | `rsi_1h_healthy_short` | RSI(1H) 32~55（空头健康区） |
| | `rsi_15m_turn_up` | 15M RSI 从超卖拐头向上 |
| | `rsi_15m_turn_down` | 15M RSI 从超买拐头向下 |
| | `rsi_extreme_high` | RSI > 72（过热警告） |
| | `rsi_extreme_low` | RSI < 28（超跌警告） |
| **量价** | `volume_expand_up` | 上涨段放量 |
| | `volume_expand_down` | 下跌段放量 |
| | `volume_shrink_pullback` | 回调/反抽缩量 |
| | `volume_diverge_bull` | 价新低量萎缩（底背离） |
| | `volume_diverge_bear` | 价新高量萎缩（顶背离） |
| **冲击** | `crash_spike` | 短窗口急跌（> N×ATR） |
| | `pump_spike` | 短窗口急涨（> N×ATR） |
| | `long_lower_wick` | 长下影止跌 |
| | `long_upper_wick` | 长上影滞涨 |
| **破位** | `break_support` | 跌破支撑/平台 |
| | `break_resistance` | 突破阻力 |
| | `false_break_down` | 假跌破快速收回 |
| | `false_break_up` | 假突破快速跌回 |
| **Big4** | `big4_bull` | Big4 偏多 |
| | `big4_bear` | Big4 偏空 |
| | `big4_weak` | Big4 疲软 |
| **其他** | `funding_crowded_long` | 费率极端正（多头拥挤） |
| | `funding_crowded_short` | 费率极端负（空头拥挤） |
| | `wick_frequent` | 近 7 日插针频繁 |
| | `near_7d_high` | 靠近 7 日高（追顶风险） |
| | `near_7d_low` | 靠近 7 日低（追底风险） |

#### 7.3.13 机会落库（全量，含未开仓）

> 每条识别到的机会一行；开仓与否、事后盈亏均在此表追溯。

**表：`brain_opportunities`**

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | PK AUTO_INCREMENT | |
| `scan_round_id` | INT FK | 所属扫描轮次 |
| `symbol` | VARCHAR | 交易对 |
| `side` | ENUM('LONG','SHORT','FLAT') | |
| `playbook` | VARCHAR(16) | A1 / B1 / C2 / D1 … |
| `signals` | JSON | `["ema_bull_align","volume_shrink_pullback","15m_higher_low"]` |
| `evidence_summary` | TEXT | 可读摘要（供 DS 确认/人工复盘） |
| `ref_price` | DECIMAL(18,8) | 识别时参考价 |
| `win_prob_long` | FLOAT | 该币近期做多分向胜率 |
| `win_prob_short` | FLOAT | 该币近期做空分向胜率 |
| `edge_score` | FLOAT | 综合打分 |
| `decision` | ENUM('OPENED','SKIPPED') | 最终裁决 |
| `skip_reason` | VARCHAR(200) | 若跳过：big4_weak / ds_reject / low_winprob / conflict… |
| `order_id` | INT NULLABLE FK | 若开仓，关联 `futures_orders.id` |
| `shadow_pnl_4h` | FLOAT NULLABLE | 不论是否开仓，4h 后按方向的影子盈亏% |
| `actual_pnl` | FLOAT NULLABLE | 若开仓，实际盈亏 USD（平仓后回填） |
| `exit_reason` | VARCHAR(100) NULLABLE | SL/TP/trail/ds_force/brain_flip/timeout… |
| `created_at` | DATETIME | |

**索引**：`(playbook, decision)`, `(symbol, created_at)`, `(scan_round_id)`

#### 7.3.14 分向胜率与场景仲裁

##### 分向胜率

- 改现有 `win_prob` 为 **`win_prob_long` / `win_prob_short` 分列计算**（样本仍为近 7 日×4h）。
- 开仓条件升级为：**绝对过线**（该方向 ≥ 55%，可后续上调至 58%）**且**比反方向至少高 **5 个百分点**。
- 两边接近 / 样本不够 → **FLAT**。

##### 场景仲裁

同一币同一轮可能命中多个 Playbook（如 B1 + C2 都沾边），仲裁规则：

1. 确认 K 是否已出现（未确认的场景优先级低）
2. 与 1H 大方向是否冲突
3. 分向胜率哪边更高
4. 仍冲突 → **FLAT，不开**

每条落库机会记录最终选定的 **唯一主 Playbook**。

#### 7.3.15 策略评估报表

> 按标签聚合，用盈利结果判断策略优劣。

**维度**：按 `playbook`，或 `playbook + signal 组合`，或 `playbook + symbol`

**指标**：

| 指标 | 说明 |
|------|------|
| 识别次数 | 该标签命中多少次 |
| 开仓次数 / 跳过次数 | |
| 开仓胜率 | 真实开仓的胜率 |
| 平均 PnL / 盈亏比 | |
| 影子胜率 | 识别但未开仓的事后 4h 方向胜率 |
| 影子 vs 实开差异 | 发现「该开没开」或「不该开却开了」 |
| 近 7 天 / 近 30 天趋势 | |

**迭代规则**（建议 INV 级）：

| 条件 | 动作 |
|------|------|
| 某 Playbook 近 30 天开仓 ≥ 10 笔且胜率 ≥ 55% | 保留 / 可放宽参数 |
| 某 Playbook 近 30 天开仓 ≥ 10 笔且胜率 < 40% | **淘汰或暂停**（禁止继续开仓） |
| 影子胜率远高于实开胜率 | 排查 skip_reason 是否过严 |
| 影子胜率远低于实开胜率 | DS 确认 / 闸门正在保护，维持不变 |
| 样本 < 10 笔 | 不做结论，继续收集 |

#### 7.3.16 BRAIN 风控参数评估 + 程序化移动锁利【已落地 · REQ-BRAIN-RISK · v4.5.2】

> **背景（2026-07-31）**：开仓 Playbook 机会判定已相对客观；惨淡主因是 **退出**：  
> 1）全市场固定 SL/TP/持仓时间，**没有按代币评估**；  
> 2）现有「移动持仓」程序（旧 **ai-trail-tp / soft-sl / trend-sl**，**不是** DeepSeek 持仓顾问）表现差。  
> **范围**：本期仅 BRAIN；探索/预测/中线旧 trail 另期对照。  
> **仍跳过**：DeepSeek 开仓顾问、DeepSeek 持仓顾问。

##### 7.3.16.1 问题定义（验收口径）

| 现象 | 根因归类 | 目标 |
|------|----------|------|
| 固定 5%/8%/6h | 无「每币风控评估器」 | 开仓时按该币波动/结构写出 SL、TP、hold |
| 亏损单直接打满 | 硬 SL 过宽或过窄；无分阶段认错 | 亏得快要早砍；噪音币要给够 SL 空间但有上限 |
| 盈利单最后变亏 | 固定持仓拖到到期；旧 trail 激活太晚 / 回撤太大 | **先锁利，再谈拿久**；禁止「赚过又亏光」成为常态 |

##### 7.3.16.2 按代币评估 SL / TP / 持仓时长（开仓瞬间）

**输入（每币）**：Playbook 族 A/B/C、15m ATR%、插针 frequent、分向胜率/edge。  
**一期实现**：`brain_risk_params.evaluate_brain_risk_params`（**ATR×Playbook 系数**；未叠该币历史同 PB 样本）。  
**输出**：`sl_pct` / `tp_pct` / `hold_hours` / `risk_meta`（含 `trail_*`）；写入限价/市价开仓 SL/TP/`planned_close_time`。  
**绝对上下限**：SL 1.5–8%、TP 2–12%、hold 0.75–8h、TP/SL≥1.2（否则抬 TP 或缩 hold）。  
**Fallback**：评估失败 → 5%/8%/6h + 日志 `risk_fallback`。

##### 7.3.16.3 程序化移动锁利（非 DeepSeek；重做旧 ai-trail）

实现：`brain_trail_exit.py` + `position_sl_tp_monitor` BRAIN 分支。

| 阶段 | 条件 | 动作 |
|------|------|------|
| 未激活 | 浮盈 &lt; 激活线 | 硬 SL；可选 soft 无跟进 |
| 激活 | 峰值 ≥ max(本笔 TP×40%, 夹 1.2%~3%) | 进入锁利 |
| 锁利回撤 | 从 peak 回撤 ≥ ≈激活×45%（夹 0.6%~1.5%） | **市价平**（`brain_trail_lock`）；低于保本缓冲亦平防吐光 |
| 无跟进 | ≥60min 且 peak≤0.5% 且 pnl≤−1.5% | `brain_soft_no_follow` |
| 到期 | `planned_close_time` | 到期平 |

**禁止**：BRAIN 走旧 `_check_ai_trail_tp` / soft-sl / trend-sl 常量路径。

##### 7.3.16.4 决策优先级（BRAIN 持仓生命周期）

```text
1. 硬 SL → 平
2. 硬 TP → 平
3. brain_trail_lock → 平
4. brain_soft_no_follow → 平
5. planned_close_time → 平
6. 战略翻转 / DeepSeek 持仓顾问 — 仍关 / 仍跳过
```

（实现上 monitor 先判计划到期再判硬 SL/TP，再进 trail/soft；与上表语义一致。）

##### 7.3.16.5 实现路径（已落地）

| 角色 | 路径 |
|------|------|
| 评估器 | `brain_risk_params.py` |
| 开仓写入 | `brain_strategy_orchestrator._open_brain_entry`（传 `rows_15m`） |
| 移动锁利 | `brain_trail_exit.py` + `position_sl_tp_monitor` |
| 路由 | 继续排除 DeepSeek 持仓顾问 |
| 回归 | `validate_brain_req.py`（百分点、上下限、新 trail、不走旧 ai-trail） |

##### 7.3.16.6 一期确认结论

- [x] 评估器一期：纯 **ATR×系数 + Playbook 分档**（历史同 PB 样本后续）
- [x] 激活线：相对 **评估 TP 的 40%**（夹 1.2%~3%；monitor 用仓上 TP 价反推）
- [x] 激活后：**回撤即平**（仍保留硬 TP 作为另一出口）
- [x] soft 无跟进：**上，仅 BRAIN**
- [x] 探索/预测：**先只改 BRAIN**
## 8. AI 主探索 / 主预测（REQ-AI-EP）

> **与 REQ-BRAIN**：BRAIN 为主判路径；**对照期** DeepSeek 探索/预测仍可自动开仓做对比（INV-BRAIN-07 暂缓）。对照结束后应全面暂停，不得再以「DeepSeek 独自扫池开仓」为主路径。

**Prompt**: `ai_explore_prompt.py` / `ai_predict_prompt.py`（中文生产）  
**Worker**: `explore_worker_impl.py`（经 `explore_worker_common`；旧 `gemini_explore_worker` 为壳）、`deepseek_*_explore_worker.py`、`deepseek_predictor.py`（Gemini 交易已下线；DeepSeek 自动开仓对照期保留）

### 8.1 调度

| 任务 | schedule | 防重 |
|------|----------|------|
| 探索 | every(2).hours + every(10).min 轮询 | 距上次 `status=ok` ≥ **max_hold_hours**；`*_explore_next_due_utc` 认领；partial 非 running |
| 预测 | every(2).hours + every(5).min 轮询 | 距上次 `status=ok` ≥ **max_hold_hours**；`*_predict_next_due_utc` 认领 |

**间隔语义（非墙钟槽）**：周期读 `system_settings.max_hold_hours`（2~8，与持仓共用）。上次成功后满 N 小时由 5/10min 轮询触发；error/skipped 不推迟；无 ok 记录则立即 due。认领 `next_due=now+N`；已逾期未 ok 时仍可重试（进程内 running lock 防并发）。

### 8.2 持仓参数（默认）

- SL **3%** / TP **5%** / 杠杆 **5x** / 保证金 **500U** / 持仓 **4h**  
- 置信度门槛 ≥ **0.75**（`EXPLORE_CONFIDENCE_THRESHOLD` / `PREDICT_CONFIDENCE_THRESHOLD`，以代码为准）
- **DeepSeek LONG 加严**（SHORT 仍 0.75）：conf≥**0.82**；RSI1h≤**68**；`below_7d_high_pct`≤**-3**；24h 涨幅≤**12%**；纯突破无回踩且距高过近拒；15m 顺向须≥反向+**2**（`deepseek_long_entry_quality_ok`）
- `explore_catalyst_technical_ok` 硬门槛：文案（15m 结构/量价/趋势）+ **真实 15m OHLC 方向复核**（16 根顺向须多于反向；近 6 根不得反向占优；近端连反向≥2 拒绝）

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
- 主预测（Gemini）：`candidate_pool_snapshot` 技术面 **TOP50**；**禁止**对 `price_stats_24h` 全市场逐 symbol 回退扫 `kline_data`  
- DeepSeek 预测：**仅 L0 白名单 + L1**（`load_l0_l1_scan_symbols` ∩ `candidate_pool`），**不扫未评级/全市场**；建数**仅读**缓存（禁 kline 回退）；建数成功后再 claim；软锁 **25min** 过期可抢占；DB `read_timeout=45`；LLM 按批 50  
- DeepSeek 探索：同样 **仅 L0/L1** 建 universe（`_build_l0_l1_universe_from_cache`），禁止扩成全市场  
- 主探索/预测开仓：`explore_catalyst_technical_ok` 在文案门槛后，用真实 15m K 线做 OHLC 方向闸门（缺 K 线则拒）  
- K 线叙事：1h = 24 根趋势 + 近 6 明细；15m = 16 根（4h）  

---

## 9. 开仓 / 持仓顾问（REQ-ADVISOR）

**实现**: `open_advisor_strategy_rubrics.py`、`position_advisor_impl.py`（经 `advisor_core`）、`hold_advisor_query.py`  
**路由**: `open_advisor_routing.py` — 探索等统一 DeepSeek；BRAIN **跳过开仓顾问与持仓顾问**；BRAIN 退出见 §7.3.7 / §7.3.16

| 类型 | 节奏 | 核心判据 |
|------|------|----------|
| 开仓顾问 | 模拟开仓前（部分策略可 skip LLM） | **15m 趋势 + 量价** 与 side 一致 |
| 持仓顾问 tick | scheduler **每 15min** | 每仓 **15min/仓**；**浮盈转亏**立即 urgent 再审 |
| 持仓顾问决策 | 15m 表主审 | 浮盈 ROI≥**+8%** 且 15m **明确**转弱（反向≥4）→ 倾向 observe/sell；sell 须 15m 近 4 根确认反转 |

**AI 轻量移动止盈**（`position_sl_tp_monitor.py`）：探索/预测及 **中线 v2**（`midline_long` / `midline_short`）在硬 SL/TP 之外，peak 价格收益 **≥3%** 后回撤 **≥1%** 程序化平仓（`ai-trail-tp`）；不走 early-sl/breakeven。

**AI soft-sl**（同 monitor）：通用探索/预测 grace **15min**、no_follow 约 **-1.2%**。**DeepSeek** explore/predict 单独加宽以匹配 15m×4h 开仓 thesis：grace **45min**；no_follow 须 age≥**60min** 且价格亏≥**约 2.2%**；profit_to_loss / mature 亦更深更晚；硬 SL 仍兜底。

开仓顾问：中线 v2 **跳过**。持仓顾问：中线 v2 **排除**（仅硬 SL/TP + ai-trail-tp + 8h）。

探索/预测：`gemini/deepseek/gpt_*_explore|predict` 在 worker 已用 **catalyst+data_signal** 过 `explore_catalyst_technical_ok` 后，开仓顾问**不再重复** catalyst 预检（`should_skip_upstream_catalyst_precheck`）；DeepSeek 同源 `deepseek_self_gated_open_skip_llm` 默认关闭，避免绕过 RSI/15m 二次复核。其它策略仍走 `precheck_open_advisor` + 可选 LLM。

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
| price_stats_24h | 1 min（`GET_LOCK('price_stats_24h_refresh')` 防重） |
| TOP50 + 全量评级 | **每 1h** + 15min 轮询（`rating_refresh_next_due_utc`） |
| Gemini/DeepSeek/GPT 探索 | 距上次 ok ≥ max_hold_hours + 10min 轮询 |
| Gemini/DeepSeek/GPT 预测 | 距上次 ok ≥ max_hold_hours + 5min 轮询 |
| **中线 v2**（`midline_long/short`） | 每 **4h**（独立调度；落地时移除旧四路 `*_midline_*` 6h 任务） |
| 战术 15 槽位 | 15 min |
| Gemini 情绪 | 8 h |
| MySQL EVENT `update_all_coin_scores` / `calculate_coin_score` | **已下线**（migration 025 DROP EVENT+PROCEDURE） |

探索页首屏：`GET /api/deepseek-explore/bootstrap` 等 **单连接**返回 status+runs+open+stats；**独立 API 连接池 20**（`read_timeout=5s`）。池内 idle **>30min** 连接 checkout 时**按时间丢弃、禁止 ping**。原 Gemini 探索页落地后改为**中线策略页**（机会分析 + 参数；见 §7.2.5），不再以 LLM 探索 bootstrap 为主。

`scheduler_init` 错峰：探索 +15s/+90s/+120s；预测补跑 +45s/+50s/+55s；中线 v2 独立错峰（实现时定）。**勿**用 init 跑预测主逻辑。

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
| L2 | -100<盈利<0 或 胜率>44% | **禁止** | 禁止 |
| L3 | 盈利<-100U 且 胜率<44% | **禁止** | 禁止 |

TOP50：`top_performing_symbols` 表；模拟开仓参考，**非**实盘开仓必要条件（实盘看 L0）。

**手动锁定**（`trading_symbol_rating.rating_locked=1`）：黑名单管理页 `/symbol_blacklist` 手动添加/编辑时默认锁定；定时 `update_top_performers` **不覆盖**等级；**模拟盘与实盘均禁止开仓**；`POST /api/rating/unlock` 解除后恢复自动规则。下架公告强制 L3 使用 `force=True` 可覆盖锁定。

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
| REQ-LIVE-CLOSE | `trading_gates.py`, `position_advisor_impl.py`, `binance_futures_engine.py` |
| REQ-GATES | `trading_gates.py` |
| REQ-PAPER-OPEN | `paper_limit_entry.py`, `paper_open_gate.py` |
| REQ-AI-EP | `ai_explore_prompt.py`, `ai_predict_prompt.py`, `explore_worker_impl.py`, `explore_worker_common.py`, `*_explore_worker.py`, `*_predictor.py` |
| REQ-ADVISOR | `open_advisor_strategy_rubrics.py`, `position_advisor_impl.py`, `advisor_core.py`, `hold_advisor_query.py`, `position_sl_tp_monitor.py` |
| REQ-SCHED | `scheduler.py`, `data_cache_service.py` |
| REQ-KLINE | `binance_ws_kline_collector.py`, `fast_collector_service.py` |
| REQ-RATING | `update_top_performers.py` |
| REQ-MIDLINE | `midline_swing_config.py`, `midline_swing_scanner.py`, `midline_explore_worker.py`（或 `midline_worker`）, `midline_swing_api.py`, 中线策略页 JS/模板, `scheduler.py`, `position_sl_tp_monitor.py`, `trading_gates.py`, 开仓/持仓顾问路由 |
| **REQ-BRAIN** | `brain_config` / `brain_wick` / `brain_market_analyzer` / `brain_winrate` / `brain_strategy_orchestrator`；`scheduler.py`；`paper_limit_entry` + executor expire；DS 自动开仓暂停；`validate_brain_req.py`；权威 §7.3 |
| **REQ-BRAIN-v2** | Playbook 识别 + 信号打标 + `brain_opportunities` 落库 + 分向胜率 + 评估报表；§7.3.10–7.3.15（**首版已落地**） |
| **REQ-BRAIN-HOLD / REQ-BRAIN-RISK** | 按币评估 SL/TP/hold + `brain_trail_lock` / soft 无跟进；§7.3.16（**v4.5.2 已落地**） |
| REQ-ST | `smart_trader_service.py` |

---

## 16. 变更记录

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-07-31 | **v4.5.2** | **REQ-BRAIN-RISK 落地 §7.3.16**：`brain_risk_params` 按币写 SL/TP/hold；`brain_trail_exit` 锁利+无跟进；monitor 启用新规则、禁用旧 ai-trail；fallback 仍 5/8/6 |
| 2026-07-31 | **v4.5.1** | **REQ-BRAIN-RISK 草案 §7.3.16**：开仓机会视为客观；瓶颈在退出——按代币评估 SL/TP/持仓时长；重做程序化移动锁利（非 DeepSeek 顾问）；明确旧 ai-trail「赚过又亏」问题；生产仍暂用 5%/8%/6h 过渡 |
| 2026-07-30 | **v4.5.0** | **BRAIN 固定退出**：SL **5%** / TP **8%** / 持仓 **6h**；关闭战略平仓与持仓顾问；monitor 不做 trail/soft/trend；§7.3.16 动态草案暂缓 |
| 2026-07-30 | **v4.4.9** | **修复 BRAIN 战略平秒砍**：禁用旧 `analyze_symbol→analysis_flat`；改 Playbook 再识别；开仓后 **45min** 内不做战略平（默认仅方向反转才平）；硬 SL/TP 仍立即生效 |
| 2026-07-30 | **v4.4.8** | **BRAIN 持仓管理主权层草案 §7.3.16**：自有持仓顾问（隔离通用 DS）；开仓/持仓 **动态 SL·TP** 与 **动态持仓时长**（取消强制固定 4h/3%/5%，仅过渡 fallback）；INV-BRAIN-HOLD；绝对上下限 |
| 2026-07-30 | **v4.4.7** | **修复 BRAIN SL/TP 单位**：`BRAIN_SL_PCT/TP` 改为百分点 3.0/5.0（此前 0.03/0.05 被 engine `/100` 成 0.03% 秒止损） |
| 2026-07-30 | **v4.4.6** | **BRAIN 平仓需求草案 §7.3.16**：平仓镜像开仓 Playbook/分向胜率/Big4；DS 持仓顾问专用 rubric；INV-BRAIN-EXIT；待确认后落地 |
| 2026-07-30 | **v4.4.5** | **BRAIN 测试期市价开仓**：`BRAIN_USE_MARKET_ENTRY=True`；INV-BRAIN-06 限价防插针暂缓；OPENED 立即进持仓 |
| 2026-07-30 | **v4.4.4** | **BRAIN 开仓跳过开仓顾问**（`brain_skip_advisor`）；INV-BRAIN-04 改为 Playbook 主判直挂限价；页内展示限价挂单（OPENED≠持仓）；API `/orders` |
| 2026-07-30 | **v4.4.3** | 清理历史死代码：删除 Gemini/GPT/tactical 壳、未用 breakout/entry/optimizer 服务群、15 个一次性 diag 脚本；scheduler 去掉 gemini_position_advisor 计数 |
| 2026-07-30 | **v4.4.2** | **BRAIN 轮询直播**：scheduler 每15s一批5币扫 L0/L1；发现机会立即开仓；API `/live`；前端 5s 刷新进度 |
| 2026-07-30 | **v4.4.1** | **BRAIN v2 首版落地**：`brain_playbook` / `brain_opportunity_store`；全量机会落库；分向胜率+相对差门；API opportunities/playbook-stats；页展示机会表 |
| 2026-07-30 | **v4.4.0** | **BRAIN v2 需求**：Playbook 场景覆盖（A/B/C/D）+ 信号字典 + 全量机会落库 `brain_opportunities` + 分向胜率 + 场景仲裁 + 按标签评估报表；§7.3.10–7.3.15 |
| 2026-07-30 | **v4.3.7** | Web：侧栏新增「超级大脑策略」`/brain_strategy`（位于中线之上）+ `/api/brain-swing` 概览/开关/持仓/手动跑一轮 |
| 2026-07-28 | **v4.3.6** | 删除 `position_advisor_impl` 内 Gemini LLM client/`review_open` 死路径；移除重复模板 `gemini_advisor_reviews.html` |
| 2026-07-28 | **v4.3.5** | 顾问审核写入抽到 `advisor_review_store`；`gemini_swan_worker`/`tick` 降为下线壳；SmartExit 仅初始化 DeepSeek 持仓顾问 |
| 2026-07-28 | **v4.3.4** | 探索共用工具迁入 `explore_universe_utils`；顾问 API/页中性化 `advisor_api`/`advisor_reviews`；停注册 Gemini Big4 路由；DeepSeek 不再依赖 `gemini_swan_worker` |
| 2026-07-28 | **v4.3.3** | 探索共用实现迁入中性 `explore_worker_impl.py`；`gemini_explore_worker.py` 降为兼容壳；活跃链路经 `explore_worker_common` |
| 2026-07-28 | **v4.3.2** | 顾问实现迁入中性 `position_advisor_impl.py`；`gemini_position_advisor.py` 降为兼容壳；活跃链路经 `advisor_core` |
| 2026-07-28 | **v4.3.1** | **对照期**：暂缓 INV-BRAIN-07；DeepSeek 探索/预测自动开仓与 `brain_swing` 并行，便于对比分析 |
| 2026-07-28 | **v4.3** | **REQ-BRAIN 首版落地**：`brain_*` 分析/胜率/编排；scheduler 2h+30min；DS 探索/预测自动开仓暂停；限价超时强制 cancel；大脑翻转平 + DS 坚决平；回归 `validate_brain_req.py` |
| 2026-07-28 | **v4.2** | **新增 REQ-BRAIN §7.3（需求已确认·待落地）**：自有分析主判 L0/L1；Big4 疲软不开；近7日×4h 方向胜率≥55%；DeepSeek 仅确认开仓/可强制平；防插针（影>实体×2、频繁则平均插针限价、超时取消）；旧 DeepSeek 自动开仓全面暂停；INV-09～12 |
| 2026-07-28 | — | **下线 Gemini 交易开关**：系统设置移除 Gemini 探索/预测；强制关闭 explore/predict/sentiment/顾问；scheduler 停调度；LIVE_SYNC 仅 DeepSeek；开仓/持仓顾问统一 DeepSeek |
| 2026-07-28 | — | DeepSeek 探索/预测改扫 **仅 L0+L1**（拒全市场/未评级）省 token；DeepSeek soft-sl 加宽（grace45 / no_follow≥60m且亏≈2.2%）匹配开仓 thesis |
| 2026-07-27 | — | 中线 v2 **退出持仓顾问**：仅硬 SL/TP + ai-trail-tp + 8h；避免顾问 15m 噪音闷杀波段仓 |
| 2026-07-27 | — | **L2 黑名单不再交易**：`check_symbol_trading_forbidden` 阈值 `rating_level>=2`（模拟+实盘均禁）；候选池/config 同步同步排除 |
| 2026-07-27 | — | DeepSeek 主探索/预测 **LONG 加严**：conf≥0.82、RSI≤68、距7d高≥3%、24h≤12%、OHLC 顺向优势+2；SHORT 不变；开仓顾问 DeepSeek LONG 同口径预检 |
| 2026-07-27 | — | 中线 v2.1 放宽入场硬规则（30d ±3%、量比 0.4、回踩/反抽区 40%、1h MA 容差）；修复「全市场 signals_found=0」无法开仓 |
| 2026-07-24 | v4.1 | **中线策略 v2 落地（模拟）**：`midline_long/short`；停旧四路调度；独立 4h；config.yaml 池；±1%/SL6%/TP3%/8h；持仓顾问+ai-trail-tp；暂不 LIVE_SYNC；Gemini 探索页→中线机会分析；migration 026 |
| 2026-07-24 | v4.1 草案 | **中线策略 v2（REQ-MIDLINE §7.2）待确认**：新 source `midline_long/short`；停移除旧四路 `*_midline_*`；独立 4h 调度；config.yaml 池；限价 ±1%、SL6%/TP3%、持仓 8h；跳过开仓顾问、接入持仓顾问+ai-trail-tp；**暂不实盘**；Gemini 探索页整页改中线机会分析；DeepSeek 探索去中线 Tab |
| 2026-07-24 | — | 恢复 L3/锁定恒禁模拟开仓（修复 `is_symbol_blocked_level3`/`check_simulated_symbol_allowed` 空实现）；`config.yaml` symbols 移除 L3/锁定；同步脚本默认排除 |
| 2026-07-24 | — | DeepSeek 预测候选池改为**全量**（L0/L1/L2/未评级，排除 L3/锁定），不再技术面 TOP50；仍缓存-only 建数 + 分批 LLM |
| 2026-07-23 | — | DeepSeek 预测加固：缓存-only 建数（禁 kline 回退）；软锁 12min 过期抢占；read_timeout=45；修「上一轮未结束」永久卡死导致整天不开单 |
| 2026-07-23 | — | 主探索/预测调度改为 **距上次 ok ≥ max_hold_hours**（取消北京 21:30 墙钟固定槽）；认领 next_due=now+周期 |
| 2026-07-23 | — | 主探索/预测固定槽：`now < 本槽` 时若上一槽未 ok → 逾期补跑（修 DeepSeek 预测 07:30~下一槽「未到点」挡死漏槽） |
| 2026-07-23 | — | 主探索/预测：开仓 conf≥**0.75**；`explore_catalyst_technical_ok` 增加真实 15m OHLC 方向复核（防文案过门） |
| 2026-07-23 | — | DeepSeek 预测：候选池改技术面 TOP50（禁 price_stats 全量）；建数后再认领 next_due；修复 MySQL 2013 扫库超时导致漏槽 |
| 2026-07-05 | — | API 池：独立 `_api_pool`；idle>30min 丢弃且不 ping；checkout 最多 pop 5 条；探索 stale 连接自动重试；首屏后暂停轮询 90s |
| 2026-07-04 | — | 探索页卡死：`/bootstrap` 单请求首屏；API 池 20；price_stats GET_LOCK；coin_scores EVENT 5min→15min |
| 2026-07-04 | — | **下线** `calculate_coin_score` / `update_all_coin_scores` EVENT+PROCEDURE（migration 025） |
| 2026-07-04 | — | API 连接池：ping 移出全局锁；丢弃死连接用底层 close（防隔夜死连接导致次日首屏锁死） |
| 2026-06-18 | v4.0 | **重写权威需求文档**；明确 INV-01/02 实盘仅成交瞬间同步；PaperSync 5 分钟窗；开开关 skip 历史单；15m 量价定方向 |
| 2026-06-18 | — | fix `f9db7a64` 修复实盘开关回填历史模拟仓 |
| 2026-06-18 | — | fix `fe61e698` 15m 趋势+量价 catalyst 门槛 |
| 2026-06-18 | — | 中线限价单过期时间 2h → **6h**（`MIDLINE_LIMIT_TIMEOUT_MINUTES=360`） |
| 2026-06-18 | — | L0 门槛放宽（300U/40%、100U/45%）；L3 默认不禁止模拟仓 |
| 2026-06-18 | — | 中线实盘保证金改为 API `max_position_value`（不再写死 100U） |
| 2026-06-21 | — | 转市价/限价成交后立即 PaperSync；API 返回 live_sync 状态 |
| 2026-06-21 | — | AI ai-trail-tp（peak≥3% 回撤≥1%）；持仓顾问浮盈 5min 复审 + 转亏 urgent；盈利 sell 门槛降至 ROI+5% |
| 2026-06-21 | — | 中线仓排除 SmartExit 监控/健康检查；仅 position_sl_tp_monitor |
| 2026-06-21 | — | 中线限价明确 做多−3% / 做空+3%；非中线仍读 system_settings 偏移 |
| 2026-06-21 | — | 需求/设计/AI 策略文档与 Cursor 上下文同步 |
| 2026-06-21 | — | 中线排除 ai-trail-tp；仅硬 SL/TP + 到期/爆仓 |
| 2026-06-21 | — | 持仓顾问恢复 15min tick；盈利复核门槛升至 ROI+8%，sell 须 15m 反向≥4 |
| 2026-07-02 | — | 开仓顾问：探索/预测 不再用仅 catalyst 重复预检（修复误 reject） |
| 2026-06-30 | — | L3 与 `rating_locked` 恒禁模拟+实盘；`check_symbol_trading_forbidden` 统一闸门 |
| 2026-06-22 | — | 评级手动锁定 `rating_locked`；黑名单管理页手动添加默认不被自动刷新覆盖 |

---

## 附录 A：回归检查清单（改代码后）

- [ ] 本文对应章节已更新  
- [ ] §16 变更记录已追加  
- [ ] `live_trading_enabled` 0→1 不会 sync 历史 NULL 单（`skip_all_pending_paper_live_sync`）  
- [ ] 模拟成交时 live 关 → 当场 SKIPPED  
- [ ] fill_time > 5min 的 NULL → SKIPPED  
- [ ] 改动 scheduler 后只 restart `crypto-scheduler` 一次  
- [ ] 改动 main/PaperSync 后 restart `crypto-app-main`  
- [~] **REQ-BRAIN**：BRAIN 开仓链已落地；**对照期** DeepSeek 自动开仓暂保留；对照结束后执行 INV-BRAIN-07 全面暂停  
