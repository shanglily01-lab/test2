# 超级大脑量化交易系统 — 业务逻辑需求文档（权威版）

**版本**: v4.5.82
**日期**: 2026-10-22
**状态**: **生产逻辑唯一权威来源**（代码与本文冲突时，以本文为准改代码；改代码必须同步本文）  
> **Telegram（§2.1）**：所有 TG 文案英文。  
> **现行开单（§7.0）**：自动策略只开 **A1 的 1h 回调多**，确认后**市价**。A2/B/C 与空单只打标，不下单。DeepSeek 对照期另开多单（限价 + 开仓顾问）。自选按用户方向，可多可空。历史版本叠在 §16，不要用文首旧条覆盖 §7.0。  
> **操作对照**：[`docs/BRAIN_AND_BREAKOUT_OPERATOR_ZH.md`](./BRAIN_AND_BREAKOUT_OPERATOR_ZH.md)。冲突以本文为准。  
> **现货镜像（§7.4）**：跟 **BRAIN A1** + **DeepSeek LONG**；仅 **L0**；500U；`spot_live_enabled` 成交瞬间同步，打开不回填。  
> **实盘**：`live_trading_enabled` / `live_close_enabled`；成交瞬间同步；**仅 L0**；北京时段闸门**已解除**（全天）。打开开关不回填。  
> **保证金**：合约模拟统一 **1000U**。现货镜像 500U。实盘走 `max_position_value`。  
> **退出（自动多单）**：盈利 1h 冲高回落；峰值 ≥2.20% 且未到 5% 时，从峰值回撤 **1.20** 个点锁利。大脑/破位浮亏 **-200U** 平仓；DeepSeek 无硬 SL。未到亏损上限时，保证金浮亏满 **100%** 仍强平。

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
- **禁止** DeepSeek 探索/预测用 1h/RSI/24h 单独替代 15m catalyst 定方向（见 §8.3）；BRAIN/破位牛市路径以 **1h 回调做多** 为准（INV-08 v4.5.63）  
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
| **INV-08** | 牛市自动策略（BRAIN/破位/DeepSeek）：**禁止自动开空**。BRAIN/破位只做 **1h 回调做多**（仅 A1：近 24 根离高回踩 + 斜率 0%–15%，不强制高于前 24 根；48h 粗方向偏空否决 A1 多）。同一币 1 槽，两路合计最多 12 笔。DeepSeek 探索/预测仍用 15m catalyst 筛多头，空头不开仓。15m RSI 只作开仓时机。自选手动单不受此条。不写 `allow_short`（INV-04） | 牛市逆势空 / 弱市接飞刀 / 同一币叠仓 |
| **INV-09** | **REQ-BRAIN**：无自有 Playbook `LONG/SHORT` + 分向胜率门 → 不得开仓；BRAIN **跳过开仓顾问**；**禁止**机械抄 DeepSeek 自动开仓作为主权主路径 | 负期望灌水 |
| **INV-10** | **REQ-BRAIN**：Big4 疲软（动量弱且相对成交量很低、量价波动很小）→ 不得开仓 | 宏观逆势亏损 |
| **INV-11** | **REQ-BRAIN**：影线>实体×2 仍计插针。BRAIN / 破位 **A1 回调确认后市价开仓**（不再挂限价）。其它限价单超时必须取消，禁止超时转市价 | 回调后再挂限价错过抬起 |
| **INV-12** | **DeepSeek 自动多单**：亏损不设硬 SL，扛到强平。**大脑 / 破位**：不设紧止损，浮亏达到 **-200U**（`structure_loss_cap`）即平；未到该线仍扛，强平价或保证金亏满 **100%** 仍兜底。无 SL/TP 的自动仓仍须被 `position_sl_tp_monitor` 扫描。盈利走 **1h 冲高回落**。**禁止** 4–6h 对亏损单到期强平。顾问 suggest-only；不得进入 SmartExit。自选仍可用用户 SL | 平仓权责混乱 / 牛市被紧止损扫出 / 单笔亏到几百 U 仍加仓 |

> INV-09（分向胜率门）只约束 BRAIN。破位不走该胜率门，但开仓仍受 INV-08（只做 A1 多）、INV-11（A1 市价）、INV-12（浮亏 -200U）。

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

### 2.1 Telegram 交易通知（v4.5.76）

**实现**: `trade_notifier.py` · `futures_trading_engine.py`（`_notify_paper_open` / 平仓 `notify_close_position`）· `daily_review_report.py` · `binance_news_monitor.py`

**语言**：所有发到 Telegram 的文案一律 **英文**（标题、字段、策略名、持仓时长、复盘、公告、IP 封禁告警）。Web/DB 中文展示名不受影响。

| 事件 | 是否发 TG |
|------|-----------|
| 模拟盘合约**开仓成交**（市价开仓或限价成交） | **发** `[OPEN]` |
| 模拟盘合约**平仓** | **发** `[CLOSE]` |
| 实盘开仓 / 平仓 / 挂撤单 / 交易所 SL·TP 设置 | **不发** |
| 熔断、IP 封禁、每日复盘、币安公告 | **仍发**（英文运维告警） |

限价挂单未成交不发。`config.yaml` `notify_events` 用 `paper_open` / `paper_close`（旧 `live_open` / `live_close` 仅作别名，仍只触发模拟盘）。

---

## 3. 账户与数据表

| 概念 | 值 / 表 | 说明 |
|------|---------|------|
| 模拟盘 account_id | **2**（`PAPER_ACCOUNT_ID`） | `futures_positions` / `futures_orders` |
| 模拟现货 account_id | **3**（`SPOT_ACCOUNT_ID`） | `spot_positions`；跟合约成交镜像，不共用合约仓 |
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
          （取价失败须回退模拟成交价，不得因此 FAILED；禁止在「发送开仓订单」之前因 ticker 空而放弃）
          （本进程无 `engine_manager` 时不得标 FAILED：scheduler 也会成交模拟限价，实盘引擎只在 crypto-app-main 初始化；须留 NULL 等 main PaperSync）
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
| FAILED | 技术失败（已向币安下单或本进程具备实盘引擎后的 API/无 SLTP 等） | 否（防重复下单）。**禁止**在本进程无 `engine_manager` 时标 FAILED（scheduler 成交不得封死 main 补同步）。原因必须**前置**写入 `futures_orders.notes`（`PaperSync FAILED: …`），不得接在入场 JSON 后面再截断。市价数量须按 `MARKET_LOT_SIZE.maxQty` 截顶后再下单。若币安已成交且 `live_futures_positions` 已有对应 `paper_position_id`，标 **SYNCED** 不得标 FAILED。币安开仓单已接受后，SL/TP/本地保存异常也必须保存 live 行并标 SYNCED |

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
| `allow_long` | 1 | **用户做多总开关**。关则不得新挂多单限价、不得开多仓、已挂多单限价立即 EXPIRED；**不平**已有多仓 |
| `allow_short` | 1 | **用户做空总开关**。关则不得新挂空单限价、不得开空仓、已挂空单限价立即 EXPIRED；**不平**已有空仓 |
| `spot_trading_enabled` | 0 | 现货模拟镜像 |
| `spot_live_enabled` | 0 | 现货实盘：模拟开/平仓瞬间同步 Binance 现货；**打开不回填历史仓** |
| `midline_long_enabled` | 0 | 破位做多 kill switch；系统配置 / 破位策略页可开关 |
| `midline_short_enabled` | 0 | 破位做空 kill switch；系统配置 / 破位策略页可开关 |

### 6.2 可实盘 source 白名单（LIVE_SYNC_SOURCES）

仅以下 source 可参与实盘开仓同步：

- `deepseek_explore`
- `deepseek_predict`
- `brain_swing`（及 `brain_long` / `brain_short`）
- `midline_long` / `midline_short`（破位策略）
- `manual_watchlist`（合约自选手动单）

**已下线（不再实盘、不再调度）**：`gemini_explore` / `gemini_predict`（系统配置开关已移除，DB 强制关闭）。

**旧四路** `gemini_midline_*` / `deepseek_midline_*`：**不**加入白名单。

**现货镜像**（`spot_brain` / `spot_deepseek_explore` / `spot_deepseek_predict`）：**不**加入合约 `LIVE_SYNC_SOURCES`。现货实盘走独立开关 `spot_live_enabled`（模拟现货开/平仓瞬间市价同步；打开**不得**回填历史仓）。

**其余**（GPT 探索/预测、战术、反转、smart_trader、BTC 动量、现货镜像等）**仅模拟仓**。

BRAIN / 破位 / DeepSeek / **自选手动** 实盘同一套开关：`live_trading_enabled=1` 才在**成交瞬间**排队同步；L1 模拟仓当场 `SKIPPED`。打开开关**不得**回填历史模拟仓（INV-01/02）。

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

- **主探索/预测 / BRAIN / 破位**：`user_api_keys.max_position_value` × `get_live_margin_ratio(symbol)`；模拟可扫 L0+L1，实盘同步**仅 L0**  
- **破位策略** 杠杆与模拟一致（**无硬 SL / 无硬 TP** / 5x；浮亏 -200U 平仓，INV-12）  
- L0=1.0x；L1/L2/L3 禁止实盘（L2+ 同时禁止模拟）  

### 6.6 模拟保证金（v4.5.61）

- **合约模拟**一律 **1000U**（`trading_gates.get_paper_margin_usd`）：探索/预测、BRAIN、破位、自选默认、smart_trader  
- **不再**按 `rating_level` 缩到 400/200/100；**不再**乘 Playbook / 宏观 `margin_multiplier`  
- 现货镜像仍 **500U/单**（§7.4）；实盘大小仍走 §6.5  

---

## 7. 模拟开仓路径（REQ-PAPER-OPEN）

谁能开、开什么、怎么进，以 **§7.0** 为准。后文 Playbook 枚举仍用于识别和落库。**识别不等于开仓。**

### 7.0 现行开单

实现：`bull_swing_policy.auto_open_allowed`、`paper_open_gate.gate_simulated_open`、`entry_timing._a1_h1_pullback_entry`。

| 路径 | source | 现在开什么 | 怎么进 | 开仓顾问 |
|------|--------|------------|--------|----------|
| 超级大脑 | `brain_swing` | 仅 **A1 多** | 1h 回调确认后 **市价** | 跳过 |
| 破位 | `midline_long`（`midline_short` 可扫，空单不开） | 仅 **A1 多** | 与大脑同一条 A1 买点，确认后 **市价** | 跳过 |
| DeepSeek 探索/预测 | `deepseek_explore` / `deepseek_predict` | 仅 **多**。用 15m catalyst，不要求打成 A1 | **限价**（系统偏移，默认 0.5%） | DeepSeek 开仓顾问 |
| 自选 | `manual_watchlist` | 用户指定的多或空 | 用户限价或市价 | 跳过 |

GPT、战术、反转、smart_trader 若仍产出自动单，空单同样被 INV-08 拦住。它们不是主权开仓主路径。

**A1 买点（大脑与破位相同）**

1. 近 **24** 根 1h 内取波段高点；高点不得落在最近 **2** 根（还在冲高则等）。**不再**要求该高点高于前 24 根，也**不再**因回踩跌破峰前低点而拒单。
2. 现价离开该波段高点 **1.20%–8.00%**。
3. 最近 **8** 根 1h 收盘斜率在 **0%–15%**（不含 15%）才买。8 小时仍在下跌不买。
4. 最近 **3** 根斜率 **低于 -0.50%** → `1h_slope_still_falling`，不买。
5. 8 小时斜率 **≥15%** → `1h_slope_too_steep`，不开。买回高点（回补跌幅 ≥35%）→ `1h_chase_not_pullback`，不开。
6. 48h 粗方向 `h1_side=SHORT` **否决** A1 多。这类单是 10/23 之后浮亏的主体。
7. 15m RSI 只决定现在买还是再等（仍 ≥68 且未拐头，或仍 ≤32 向下，则等）。
8. 确认后按当前价市价成交。不再为 A1 挂限价。

**下单前共同闸门**（`gate_simulated_open`，大脑/破位跳过顾问也要过）

1. `allow_long=0` 不得开多；`allow_short=0` 不得开空。关掉方向时，该向未成交限价立即取消。
2. 证券代码拒绝。
3. 自动策略开空 → `bull_long_only_blocks_short`。大脑/破位非 A1 → `bull_a1_only`。
4. L2+ 与手动锁定：模拟和实盘都禁止。
5. 同一 symbol 的 OPEN 持仓 + PENDING 开仓限价合计最多 **3**。FILLING 不计入。大脑和破位更严：同一币合计只留 **1** 槽，两路开仓合计最多 **12** 笔。
6. 同一 symbol、同一方向，近 **4h** 已平且 `realized_pnl>0` → `recent_tp_cooldown`。
7. 模拟保证金 **1000U**，杠杆 **5x**。不按评级或 Playbook 缩仓。
8. 大脑同一币开仓冷却 **4h**。每 15 秒一批最多开 **1** 笔。破位每轮最多开 **1** 笔。

**大脑在 A1 买点之前还要过**（破位不走第 2 条胜率门）

1. Big4 可交易（INV-10）。疲软不开。宏观偏空 / 过渡 / 日线探底时，**代币本身**已打出 `1h_trend_high_pullback` 的 A1 仍可开（缩仓），不得整段禁开。
2. 近 7 日该方向胜率默认 ≥55%，且比反方向高 ≥5 个百分点（INV-09）。**A1 结构例外**：已确认且带 1h 回踩时，软底 **45%**；仅当反方向高出 ≥10 个百分点才硬拒（避免连亏后池子锁死整本账）。
3. A1 `edge≥0.75`，且结构 `confirmed`。
4. 本批开仓配额未满。

**成交之后**

- 成交瞬间决定实盘：`live_trading_enabled=1`、source 在白名单、symbol 为 **L0**，才同步。北京时段闸门已解除。打开开关不回填（INV-01/02）。
- 现货镜像只跟大脑 A1 多和 DeepSeek 多，500U，仅 L0。
- 机会表 `OPENED` = 已市价进持仓。

**只打标、当前不开仓**：A2、B1、B2、B3、B4、C1、C2、C3、C4，以及全部空单。标签仍落库，供复盘。D1/D2 继续视为无边，不开。

**限价只留给 DeepSeek 与自选**（`paper_limit_entry.py` → `create_paper_limit_order`；成交 `fill_paper_limit_order`）

| 步骤 | 说明 |
|------|------|
| 1 | 调用 `create_paper_limit_order`。方向开关、牛市只做多、币种闸门在 `gate_simulated_open` |
| 2 | PENDING 限价由 executor 用 ticker 触价。FILLING 后、写 FILLED 前重跑方向、评级、冷却、3 槽、仓位上限。拒绝则 EXPIRED。超时只取消，不转市价 |
| 3 | 成交瞬间 §4.2 决定 `live_sync_status` |
| 4 | PaperLimitSync 在 5 分钟窗内同步实盘（若仍为 NULL） |

| 类型 | 入场 |
|------|------|
| 大脑 / 破位 A1 | **市价**。不读 `paper_limit_*_offset_pct` |
| DeepSeek 探索/预测、smart_trader | `paper_limit_long_offset_pct` / `paper_limit_short_offset_pct`（默认 **0.5%**，范围 **0.1~1%**） |
| 自选 | 用户自己的限价或市价 |

### 7.2 破位策略（原中线 v2 / REQ-MIDLINE）【已落地 · 模拟 + 实盘】

> **与 INV-08 关系**：v4.5.63 起破位只开 **A1 1h 回调多**；禁止自动开空与 C3 追涨。买点见 §7.0（近 24 根离高回踩 + 斜率 0%–15%）。

#### 7.2.1 目标与边界

| 项 | 约定 |
|----|------|
| 性质 | **量化**，非 LLM；改现有中线引擎，**不**再挂 Gemini/DeepSeek 教师名 |
| 标的池 | `config.yaml` **市值前 300** 的 Binance U 本位永续；破位策略只用其中 **前 100**；证券过滤、L2+/锁定禁止等既有闸门仍生效 |
| 实盘 | ∈ `LIVE_SYNC_SOURCES`；随 `live_trading_enabled` / `live_close_enabled`；**仅 L0**；成交瞬间同步，打开开关不回填（INV-01/02） |
| 开仓顾问 | **跳过**（`skip_open_advisor=True`） |
| 持仓顾问 | **启用**（DeepSeek **结构点 thesis 复核**；**sell 只落库不执行**；盈利走 1h 冲高回落，浮亏 -200U 平仓） |
| SmartExit | **排除**；平仓由 `position_sl_tp_monitor` |
| 移动止盈 | **结构点优先**，错过高低点/浮盈回吐走 `check_structure_profit_lock`。`midline_hold_exit` **不再执行** |
| 旧策略 | **停调度并移除**：`gemini_midline_long/short`、`deepseek_midline_long/short` 及对应 kill switch / 探索页 Tab |

#### 7.2.2 Source、调度、Kill Switch

| 项 | 值 |
|----|-----|
| source | `midline_long` / `midline_short` |
| 调度 | 每 **15 分钟**一轮；不再走四路教师中线调度 |
| kill switch | `midline_long_enabled` / `midline_short_enabled`（默认 0）；**系统配置「策略与模式」+ 破位策略页**可开关 |
| 入场 | **A1 市价**。新单不再挂 4h 限价 |

实现入口：`midline_swing_config.py`、`midline_swing_scanner.py`、`entry_timing.py`、`midline_hold_exit.py`、`midline_explore_worker.py`、`midline_swing_api.py`、`app/scheduler.py`。

#### 7.2.3 模拟单参数

| 参数 | 值 |
|------|-----|
| 入场 | 见 §7.0。只开 A1 多，确认后市价 |
| SL / TP | **无硬 SL / 无硬 TP**。盈利：1h 冲高回落。亏损：浮亏 **-200U** 平仓 |
| 计划持仓 | **不限时** |
| 杠杆 / 保证金 | **5x** / **1000U** |
| 扫描周期 | **15 分钟** |

#### 7.2.4 开仓判定

破位开仓不再使用「日线 + 周线 + 近 4h 三层 AND」，也不再按 C1/C3/B2/B3 下单。那些条件留在 §16 历史记录里。

现行顺序（`evaluate_symbol_multiperiod` → `_breakout_action_opportunity`）：

1. K 线不够 → 跳过并记 `reason`。
2. `classify_playbook` 打标。`allowed_playbooks` 只有 **LONG / A1**。其它标签记 `playbook_*` 或 `bull_a1_only`，`should_open=false`。
3. 须 `confirmed`，且带 `1h_trend_high_pullback`。
4. `entry_timing` 按 §7.0 的 A1 买点给出 `ready` 后才市价。
5. 再过 §7.0 的共同闸门（方向开关、3 槽、止盈 4h 冷却、L2+/锁定）。

缺 K 线、非 A1、斜率还在跌、RSI 还在等，都只记拒绝原因，不下单。

#### 7.2.5 Web：破位策略页与机会分析

| 项 | 约定 |
|----|------|
| 主入口 | **原 Gemini 主探索页整页改造**为「破位机会策略」页（路由可仍用 `/midline_strategy`；**页面文案禁止写「中线策略」**） |
| DeepSeek 探索页 | **移除**中线 Tab；破位**只**在破位策略页管理 |
| Gemini 主探索 LLM UI | **下线**（本页不再展示探索/预测 LLM 流程） |
| `gemini_explore` 调度 | **本期默认仍保留**（无本页入口）；是否停调度另开确认，避免误伤实盘白名单路径 |
| 机会分析（必做） | 每轮扫描可查：时间、标的数、通过/拒绝数、多空数；列表含 symbol、方向、三层通过标记/摘要指标、建议入场、拒绝 `reason code`；可下钻单币明细 |
| 页内其它 | 参数展示/编辑（周期、限价偏移、kill switch）、限价单与模拟持仓状态、手动触发一轮扫描 |
| 交易记录 / 复盘 | 开仓信号展示 **具体 Playbook**（现行开仓为 `破位·A1 1h 回调多`、`大脑·A1 1h 回调多`），禁止只写 `midline_long` / `brain_swing` |

落库建议（实现时可微调表名）：`midline_scan_runs`（轮次）+ `midline_scan_verdicts`（每 symbol 通过/拒绝与指标快照）。

#### 7.2.6 持仓管理

| 机制 | 行为 |
|------|------|
| 硬 SL/TP | **无硬 SL / 无硬 TP**。浮亏达到 **-200U** 平仓；未到该线扛到强平（INV-12） |
| 到期 | **取消对亏损单按时长强平**。已有仓的 `planned_close_time` 对 BRAIN/破位忽略 |
| 程序化卖点 | **1h 冲高回落**；错过结构点仍止盈；峰值 ≥2.20% 且未到 5% 时回撤 1.20 个点锁利。`midline_hold_exit` **不再执行** |
| 持仓顾问 | **启用** DeepSeek（**结构点 thesis 复核**；**sell 只建议不执行**） |
| SmartExit | 不监控中线 source |

### 7.3 超级大脑主权层（REQ-BRAIN）【需求已确认 · 首版已落地】

> **目标**：用系统自有采集数据对 **L0 + L1** 做行情与战略判断；DeepSeek **仅为顾问**，不得再作为唯一开仓大脑。  
> **状态**：需求 v1.0 已确认；**首版代码已落地**；**对照期** DeepSeek 自动开仓暂保留并行对比（INV-BRAIN-07 暂缓）。阈值与 Big4 门槛可按 7 日样本再标定。  
> **与 INV-08**：v4.5.63 起 BRAIN **只开 A1 1h 回调多**；禁止自动开空与 C3。大方向与入场都看 **1H**。48h 偏空不开多。盈利 1h 冲高回落；浮亏 **-200U** 平仓。

#### 7.3.1 不可变规则（INV-BRAIN）

| ID | 规则 |
|----|------|
| **INV-BRAIN-01** | 无自有分析 `side∈{LONG,SHORT}` 不得开仓 |
| **INV-BRAIN-02** | 近 **7 日**、同规则、信号后 **4h**「方向对就算赢」的实现胜率 `win_prob` 默认 **&lt; 55%** → 不得开仓。**A1 例外（v4.5.78）**：已确认且带 `1h_trend_high_pullback` 时软底 **45%**；仅当反方向高出 ≥10pp 才硬拒 |
| **INV-BRAIN-03** | Big4 **疲软**（动量弱，且相对成交量很低 → 量价波动很小）→ 不得开仓；须有足够量价波动（门槛用近 7 日数据标定） |
| **INV-BRAIN-04** | **开仓**：自有 Playbook 主判通过后直接开仓（**跳过开仓顾问**）；仅 A1 多，且 48h 不是空头。全书最多 12 笔，同一币 1 槽。**平仓**：DeepSeek 持仓顾问复核并落库（**sell 不执行**）；盈利 1h 冲高回落；浮亏 **-200U** 平仓（§7.3.16） |
| **INV-BRAIN-05** | **平仓**：不以大脑战略翻转为主路径；持仓顾问只复核不执行；亏损安全网为 **-200U**，其后才是强平；盈利为 **1h 结构高点 / 锁利**（不因 D1/翻转/4–6h 到期强平） |
| **INV-BRAIN-06** | 单根 K **影线长度 &gt; 实体长度 × 2** 仍计为有效插针。BRAIN **A1 回调确认后市价开仓**（`BRAIN_USE_MARKET_ENTRY=True`）。不再因插针把入场改成限价。其它来源的限价超时必须取消（禁止转市价） |
| **INV-BRAIN-07** | **旧 DeepSeek 探索/预测自动开仓**：目标为全面暂停；**当前对照期暂缓**——与 `brain_swing` **并行开仓**，便于胜率/PnL 对比；对照结束后再强制关并停调度 |

#### 7.3.2 标的与数据窗口

| 项 | 约定 |
|----|------|
| 标的池 | **市值前 300**（`config.yaml` 按 CoinGecko 市值映射的 Binance USDT 永续；证券 / L2+ / 锁定剔除）。实盘开仓仍 **仅 L0** |
| **1H** | **大方向**；回看 **近 1 周** |
| **15M** | **小波动 / 入场结构**；回看 **近 1 天** |
| 辅证 | RSI(1h)、距 7d 高低、资金费率、量价叙事等（candidate_pool / kline 已采集） |
| Big4 | 必须参与闸门：疲软不开；须量价波动；代币方向须与 Big4 **对齐** 才可 `LONG/SHORT`，否则 `FLAT` |

**模拟单参数**（`brain_config.py` + `brain_risk_params.py`；与 engine 一致用**百分点**）：

| 参数 | 现行（v4.5.7） |
|------|----------------|
| SL / TP | **不写入硬 SL/TP**（评估器仍可算元数据）。盈利：1h 冲高回落。亏损：浮亏 **-200U** 平仓；保证金亏满 100% 仍强平 |
| 计划持仓 | **不限时亏单**（v4.5.57）；评估器仍可写 hold 元数据，**不写入** `planned_close_time` |
| 杠杆 / 保证金 | **5x** / 模拟统一 **1000U**（不再按评级或 Playbook 缩仓） |
| 入场 | **A1 市价**（`BRAIN_USE_MARKET_ENTRY=True`）。回调确认后按当前价成交，并在成交瞬间走实盘同步 / 现货镜像 |
| 平仓路径 | **强平**（亏损唯一硬出口）→ **1h 冲高回落 / 错过结构点 / 回吐锁利**；顾问只复核；硬 SL / 美元熔断 / 硬 TP / 5m 早撤 / 亏单到期 / SmartExit 均关闭 |

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

与 §7.0 相同。大脑每 15 秒扫 5 个市值前 300 的币，顺序是：

```text
classify_playbook（全部落库，含不开的标签）
  → 非 A1 或多空以外 → bull_a1_only / playbook_*（只打标）
  → Big4 疲软 → 不开
  → 分向胜率 ≥55% 且高于反方向 ≥5pp
  → A1 edge ≥0.75 且 confirmed
  → entry_timing：§7.0 的 1h 回调买点 ready
  → gate_simulated_open → brain_skip_advisor
  → 市价成交
  → OPENED = 已进持仓
  → 成交瞬间决定实盘；现货镜像只跟这笔 A1 多
```

Big4 已 LONG 时，日线 `DAILY_BEAR_PROBE` 不得否决已确认的 A1。无 1h 趋势新高的低点反弹不是 A1。

**DeepSeek 角色**：大脑开仓不经开仓顾问。持仓顾问做结构点复核，sell 只落库不执行。亏损不靠紧止损，浮亏 -200U 平仓。

**对照路径**：`deepseek_explore` / `deepseek_predict` 仍可自动开多（限价 + 开仓顾问）。空单不开。Gemini 探索/预测已下线。

#### 7.3.5 胜率 55%（验收口径）

| 项 | 约定 |
|----|------|
| 样本 | 近 **7 天**、L0/L1 |
| 信号 | 自有分析当时给出看多或看空 |
| 判定窗 | 信号后 **4 小时** |
| 赢的定义 | **方向对就算赢**（价格相对信号/开仓参考价朝预测方向运动即为胜；**不要求**触及 TP） |
| 开仓条件 | 该口径实现胜率 **≥ 55%**（`win_prob` 取该回测胜率或与之绑定的估计，须可解释、可回归） |

#### 7.3.6 防插针

插针仍统计（影线 > 实体 × 2）。**A1 不再因插针改挂限价**（INV-11，`BRAIN_USE_MARKET_ENTRY=True`）。下面这张表是历史限价口径，不适用于当前 A1 开仓。DeepSeek 与自选的限价超时仍只取消，不转市价。

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

现行退出以 **INV-12** 为准：盈利 1h 冲高回落与锁利；亏损不设紧止损，浮亏 **-200U** 平仓，未到该线扛到强平。下表里的硬 SL、−80U、5m 早撤、计划到期**不再执行**，留作历史对照。

| 机制 | 行为 |
|------|------|
| 硬 SL | 触及本笔评估写出的 `stop_loss_price` → 平 |
| 硬 TP | 触及本笔评估写出的 `take_profit_price` → 平（开仓后短 TP grace 仍适用） |
| 5m 逆势早撤 | 浮亏 ≥ **40U** 且近 5m **持续逆势无反转**（连续 ≥**4** 根或近窗 4/5）→ `brain_5m_adverse`。**A1 豁免**（v4.5.12：盈利 KPI，波段交给 -80/trail/硬 SL） |
| 美元熔断 | 浮亏 ≤ **-80U** → `brain_max_loss_usd`（**先于**硬 SL；压住 1000U×5×2.5%≈125U 打满） |
| 程序化锁利 | `brain_trail_lock`：默认峰值达激活线（**min(本笔 TP×40%, SL×25%)**，夹 **0.8%~1.0%**）后回撤达线 → 平；**Big4 LONG 多单**改为激活 **2.2%** / 回撤 **0.80%** / 保本 **0.50%**；peak 以内存∪`max_profit_pct` 为准并回写 DB |
| 无跟进早砍 | **关闭**（`BRAIN_SOFT_NO_FOLLOW_ENABLED=False`；v4.5.10） |
| 计划到期 | 达本笔 `planned_close_time`（评估 hold，≤8h）：浮盈≥0.8% 且未从峰值过度回撤 → 延 **2h**；否则 `planned_close_time_expired`。**不因** D1 / 场景翻转 / 取数失败强平 |
| 战略平仓 | **关闭**（不再因 Playbook FLAT / Big4 / 翻转主动平） |
| 持仓顾问 | **启用**（进入 DeepSeek **结构点 rubric**；结构未到不得因时长/浮盈 sell；**sell 只落库为 `advisor_suggest_only`，不执行平仓**） |
| 旧 ai-trail / soft-sl / trend-sl | **不对 BRAIN 生效**（仅用上述新版规则） |

原则：开仓见 §7.0（跳过开仓顾问）。持仓认 INV-12。顾问只复核，不因时长或浮盈卖。详情 §7.3.16。

#### 7.3.8 模块定位（落地后）

| 模块 | 定位 |
|------|------|
| 自有分析引擎 + 战略层 | **主路径**（扫描、只开 A1 多、市价） |
| DeepSeek | 大脑开仓**不参与**；持仓顾问只复核落库，**不执行平仓** |
| `deepseek_explore` / `deepseek_predict` 自动开仓 | **对照期仍开多**（限价 + 开仓顾问）；空单不开 |
| 破位 §7.2 | **独立**量化路径，买点与大脑相同，不并入 BRAIN 开仓函数 |
| 亏损出口 | 浮亏 **-200U** 平仓；未到该线才强平（INV-12） |

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
| 入场 | `BRAIN_USE_MARKET_ENTRY=True`：A1 回调确认后市价；成交瞬间实盘同步 |
| 持仓退出 | `structure_swing_exit`（1h 冲高回落 / 锁利）；DeepSeek 持仓顾问只复核；浮亏 **-200U** 平仓（INV-12）；排除 SmartExit |
| 旧路径（对照） | DeepSeek 探索/预测开仓**暂保留** |
| Web / API | `/brain_strategy`；`/api/brain-swing` |
| 回归 | `scripts/validate_brain_req.py` |

**实盘**：`brain_swing` ∈ `LIVE_SYNC_SOURCES`；开仓须 `live_trading_enabled=1` 且 **L0 白名单**；平仓须 `live_close_enabled=1`。模拟扫 **市值前 300**；L1/未评级成交瞬间 SKIPPED（仅 L0 进实盘）。打开开关不回填历史仓。

**kill switch**：`system_settings.brain_swing_enabled`（默认视为开；显式 `0` 跳过）。

**Web**：侧栏「超级大脑策略」；页面每 **5s** 拉 `/live`。侧栏「行情识别」`/market_regime` 只展示 **BRAIN 真正用的** Big4 1h 闸门 + BTC/ETH 日线桶（`/api/market-regime/live`），禁止再画旧 EMA/ADX 演示和写死时间轴。

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
> **开仓只认 A1 多（§7.0）。** 下表其余标签继续识别、落库，当前不下单。表内「挂空 / 跟风」是识别口径，不是现行下单指令。

**趋势延续类**

| ID | 名称 | 方向 | 一句话描述 |
|----|------|------|------------|
| **A1** | 多头趋势回踩 | LONG | **v4.5.82**：1h 从近 24 根波段高点回落 **1.20%–8.00%**（不强制高于前 24 根）。最近 8 根斜率 **0%–15%** 才买。最近 3 根低于 -0.50% 不买。≥15% 或回补跌幅≥35% 不开。48h 偏空否决 A1 多 |
| **A2** | 空头趋势反抽 | SHORT | 下降结构中离开低点进入反抽区，且出现上影/EMA 拒绝后再挂空。仅缩量或 lower_high 不够。**v4.5.36 拒绝确认后开仓** |

**冲击反应类**

| ID | 名称 | 方向 | 一句话描述 |
|----|------|------|------------|
| **B1** | 暴跌放量反弹 | LONG | 急跌（短窗口跌幅 > N×ATR）→ 止跌（下影/15M 不再创新低）→ 反弹放量或量能回升 → 收回枢轴/EMA |
| **B2** | 弱反抽失败 | SHORT | 先有下跌与弱反抽，再跌破反抽起点/EMA 后跟风做空。禁止无反抽纯追跌、禁止放量抬高。**v4.5.36 不再与 C1 共用第一根阴跌跟风** |
| **B3** | 暴涨滞涨回落 | SHORT | 急涨后涨不动 → 离高拒绝再空。**前提：Big4 不得仍 LONG**（仍 LONG 只打标）。`15m_failed_retest` 单独不够 |
| **B4** | 暴涨回踩有力 | LONG | 急涨后缩量回踩不破关键位 → 再度放量向上（健康回踩后趋势续） |

**破位 / 假破类**

| ID | 名称 | 方向 | 一句话描述 |
|----|------|------|------------|
| **C1** | 向下破位跟风 | SHORT | 放量/急跌刚跌破支撑 → **当根跟风做空**（不等回抽）。假跌破收回不开 |
| **C2** | 向下假破吸筹 | LONG | 刺破后快速收回（收盘回区间内），下影长，后续 15M 抬高 |
| **C3** | 向上突破确认 | LONG | 放量突破 + 回踩确认（不破突破位） |
| **C4** | 向上假突多头陷阱 | SHORT | 假突破收回且须上影/缩量/衰竭确认；仅高点横盘不算 C4。入场同 B3 |

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

**B2 弱反抽失败做空 — 必选清单（v4.5.36）**：

1. 先有下跌或处于下降结构（不是大涨中的第一次回调）
2. 出现反抽，但「无力」≥ 2 条：缩量、涨幅浅、到不了前高/EMA、RSI 反弹弱、上影增多
3. 失败确认：15M 跌破反抽起点或再次跌破 EMA20 后 **跟风做空**
4. 禁止 B2：反抽明显放量且结构抬高（那是 B1/B4）；纯追跌无反抽（那是 C1）；`15m_lower_high` 单独不得同时充当「已反抽」和「已拒绝」

**A2 空头趋势反抽做空 — 必选清单（v4.5.36）**：

1. 下降结构：EMA 空头排列，且 1H LH/LL 或 1H 偏空
2. 价格已离开近 8 根低点 ≥0.25%，进入 EMA / 38–50% 反抽区
3. 拒绝确认：长上影、EMA 拒绝、假突破或 RSI 拐头（缩量 / lower_high 单独不够）
4. 禁止 A2：结构已抬高且放量（那是反转多）；无反抽的阴跌（那是 C1）

**C1 向下破位跟风 — 必选清单（v4.5.17）**：

1. 刚跌破近期支撑/平台（15m 收盘破 look_lo）
2. 放量下跌或急跌/1h 向下冲击
3. 价格仍在破位低点附近（距低 ≤0.90%）→ 当根跟风挂空
4. 禁止 C1：假跌破收回区间；已远离低点再等回抽的旧逻辑已废

**B3 暴涨滞涨做空 — 必选清单（v4.5.34 / v4.5.50 / v4.5.52）**：

0. **前提**：Big4 不得为 LONG。大盘仍偏多 → 只打标、不开仓（`big4_long_blocks_short`）
1. 位置：现价距近 8 根 15m 高点 ≤ **0.85%**（冲高区，不是已回落的中段）
2. 「涨不动」证据须含 **拒绝**：长上影、价新高量萎缩、RSI 15m **从超买拐头**、假突破收回。`15m_failed_retest` 只说明结构偏空，**单独不够**当拒绝
3. 卖点：已离高 **≥0.28%** 且当根收在下半（上影或阴线）。限价挂在现价上方 **0.20–0.55%**；**禁止**等回落到 EMA 再空；**禁止**在第二次顶当根市价空
4. 等待：最后一根仍收在上沿**创新高（破前顶）** → `wait_stall`；贴高横盘尚未离开 → `at_high_wait_first_callback`
5. 放弃：已离高点 ≥ **1.35%** → `missed_high`；放量再破前高 → `invalidated`
6. 禁止 B3：4h 仍明确偏多且**没有**真实拒绝（趋势未尽，不在高点摸顶）
7. 禁止：把第二次较低的顶当成 A1「从高点回落做多」

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
| | `15m_higher_low` | 15M 近 8 根低点抬高（**单独不够**当回调；反弹也会抬低） |
| | `15m_lower_high` | 15M 反抽不过前高 |
| | `15m_pullback_from_high` | 近 16 根最后极值是高点且已离开 ≥0.28%（真回调） |
| | `15m_bounce_from_low` | 近 16 根最后极值是低点且已离开 ≥0.25%（真反弹；禁止当 A1） |
| | `15m_failed_retest` | 近 32–48 根已有高顶，回落后第二次冲高不过前高（做空，禁止当 A1） |
| | `1h_trend_high_pullback` | 1h 从近 24 根波段高点回落 1.20%–8.00%（不强制 HH 于前 24 根），且最近 8 根斜率在 0%–15%，最近 3 根不低于 -0.50%。48h 偏空时这个标签不下单（**A1 开仓必选**，v4.5.82） |
| | `15m_trend_high_pullback` | 对近 32 根 15m 趋势新高回踩 0.80%–3.50%（辅证；不能替代 1h 带） |
| | `15m_stop_new_low` | 15M 不再创新低（止跌） |
| | `15m_stop_new_high` | 15M 不再创新高（滞涨） |
| | `stall_at_high` | 靠近近期高点且不再延伸 + 上影/量价/RSI 辅证 |
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
> **仍跳过**：DeepSeek 开仓顾问。DeepSeek 持仓顾问用于 BRAIN 持仓 thesis 复核（**不执行平仓**）。

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
**绝对上下限**：SL **2.5–8%**、TP **3–12%**、hold 0.75–8h、TP/SL≥1.2（否则抬 TP 或缩 hold）。  
**Fallback**：评估失败 → 5%/8%/6h + 日志 `risk_fallback`。

##### 7.3.16.3 结构点出场（v4.5.57；v4.5.60 补锁利）

实现：`entry_timing.compute_structure_exit` + `structure_swing_exit.py` + `position_sl_tp_monitor` BRAIN/破位分支。

| 阶段 | 条件 | 动作 |
|------|------|------|
| 未到点 | 持仓 &lt;20min | 不因结构点砍；浮亏达到 **-200U** 仍平 |
| 多单卖点 | **1h** 冲高回落 `exhaustion_ready`（`h1_pump_giveback`），峰值 ≥**1.80%**，且本仓最高价到过该 1h 高点 **1.50%** 以内 | **市价平** `structure_sell_high`。回调买点下方的旧高点不算本仓冲高 |
| 空单平点 | 15m `bounce_low_ready` 且峰值 ≥**1.80%** | **市价平** `structure_cover_low` |
| 错过高低 | A1 等：`missed_high` / `missed_low`，仍浮盈 ≥**1.80%**，且本仓最好价到过该 1h 极值 **1.50%** 以内 | **市价平** `structure_missed_take`。入场前的旧高/旧低不算错过。**C1/C3/B2 不因 missed 平** |
| 中段锁利 | 峰值 ≥**2.20%** 且未到 5% trail；从峰值回撤 ≥**1.20** 个点，现价仍 > +0.15% | **市价平** `structure_profit_keep`。回撤不到 1.20 个点继续拿 |
| 浮盈回吐 | 峰值 ≥**2.20%** 现价 ≤+0.15% | **市价平** `structure_profit_to_loss`（中段锁利被跳空穿过时的兜底） |
| 锁利 trail | 价格峰值 ≥**5%**，且按杠杆折成保证金收益 ≥**25%**（5 倍时两条同为 5 个点）后，回撤 ≥0.55%（Big4 LONG 多单 0.80%） | **市价平** `structure_trail_*`。未到 5% 不启动移动止盈 |
| 持仓时长 | 浮盈停在本仓高点，或尚未走出 1h 结构卖点 | **不平**。取消 `structure_stale_take`（旧规则：≥4h 且浮盈 ≥1.50% 就卖）。亏损单仍不按时长强平 |
| 硬 TP / 5m 早撤 / 4–6h 亏单到期 | — | **关闭** |

**禁止**：BRAIN/破位走旧 `_check_ai_trail_tp` / `midline_hold_exit` / `planned_close_time_expired`。  
**运维**：`position_sl_tp_monitor` 仍回写 `max_profit_pct`。新开仓不写 TP、不写 `planned_close_time`。

##### 7.3.16.4 决策优先级（BRAIN / 破位持仓生命周期）

```text
1. 强平（liquidation：触及强平价，或保证金浮亏 ≥100%）→ 平
2. structure_sell_high（1h 冲高回落）→ 平
3. structure_missed_take / trail / profit_keep / profit_to_loss → 平。本仓没到过的旧极值、以及仅因持仓时长出现的小浮盈，不平
4. 硬 SL / 美元熔断 / 5m 早撤 / 亏单到期 — 关闭
5. 战略翻转 — 仍关；DeepSeek 持仓顾问 — 仅复核落库，sell 不执行
```

##### 7.3.16.5 实现路径（已落地）

| 角色 | 路径 |
|------|------|
| 评估器 | `brain_risk_params.py`（SL 仍按币；hold/TP 不作为平仓时钟） |
| 开仓写入 | `brain_strategy_orchestrator._open_brain_entry` / `midline_explore_worker`（`planned_close_time=None`，`take_profit_pct=None`） |
| 结构出场 | `structure_swing_exit.py` + `entry_timing.compute_structure_exit` + `position_sl_tp_monitor` |
| 路由 | BRAIN/破位纳入 DeepSeek 持仓顾问（**结构点 rubric + suggest-only**）；仍排除 SmartExit |
| 回归 | `validate_brain_req.py` · `validate_midline_swing.py` |

##### 7.3.16.6 一期确认结论

- [x] 评估器一期：纯 **ATR×系数 + Playbook 分档**（历史同 PB 样本后续）
- [x] 激活线：**min(TP×40%, SL×25%)** 夹 **0.8%~1.0%**（v4.5.5；宽仓峰≈1.1% 可锁）
- [x] 激活后：**回撤即平**（仍保留硬 TP 作为另一出口）
- [x] soft 无跟进：**关闭**
- [x] **美元熔断 -80U**
- [x] **5m 逆势早撤**：**v4.5.12 A1 豁免**；其它仍 40U+4 根
- [x] **受控补空**：A2/C1 仅 Big4 SHORT 顺势试点；B2 继续影子；A1 在 Big4 SHORT 下不追多（v4.5.14）；保证金自 v4.5.61 起与其它单同为 **1000U**
- [x] **KPI=盈利**（胜率不作优化目标；v4.5.12）
- [x] SL 上限：**4.5%**（v4.5.7；原 8% 易出 -400U 级单笔）
- [x] 探索/预测：**先只改 BRAIN**
- [x] SL 地板：**2.5%**（v4.5.3；忌大批量 1.5% 扫损）

### 7.4 现货镜像（REQ-SPOT）【v4.5.26 · 仅模拟】

**实现**: `spot_paper_mirror.py`（开仓）· `spot_trader_service.py`（持仓 TP/SL/到期）· `futures_trading_engine.fill_paper_limit_order`（成交钩子）  
**回归**: `scripts/validate_spot_paper.py`

#### 7.4.1 目标与边界

| 项 | 约定 |
|----|------|
| 方向 | **只做多**（现货不能空） |
| 信号 | 合约模拟仓 **FILLED 瞬间**跟单：BRAIN **A1**；DeepSeek 探索/预测 **LONG** |
| 不跟 | BRAIN 空单 / C3 突破跟多 / A2·B2·B3·C1·C4；破位 `midline_*`；自选 `manual_watchlist`；旧 DCA |
| 标的 | **仅 L0**（`rating_level=0`）；L1 合约仓不镜像 |
| 账户 | `spot_positions.account_id=3`；**500U/单**（模拟与实盘同一档）；最多 5 仓；同币已有 open 则跳过 |
| 实盘 | `spot_live_enabled`（缺省 0）。**仅**模拟现货刚 INSERT / 刚 CLOSED 的瞬间市价买/卖；用 `client_order_id=spot_live_{id}` 映射，禁止按 symbol 模糊平手工仓。打开开关**不得**扫描历史 open 仓补买（INV-01/02）。**不**进合约 `LIVE_SYNC_SOURCES` |
| 退出 | 独立 TP/SL（抄合约成交价）+ `planned_close_time`；**不**因合约 trail/顾问而平；平仓时若实盘开关开且有映射则市价卖 |
| kill switch | `spot_trading_enabled`（缺省 0）；关则不再新开，已开仓仍监控 |

旧 `spot_dca`（日 RSI<35 + MA99）**停止扫描与实盘挂单**。残留 open 仓仍走持仓监控。

合约成交镜像失败 **不得**回滚合约仓（INV-01 现货侧：无成交则无实盘；本期无实盘路径）。

### 7.5 合约自选（REQ-WATCHLIST）【v4.5.37】

**页面**: `/watchlist`（侧栏「合约交易」下「我的自选」）  
**实现**: `watchlist_config.py` · `watchlist_store.py` · `watchlist_orders.py` · `watchlist_api.py` · `watchlist_page.js`  
**回归**: `scripts/validate_watchlist.py`

| 项 | 约定 |
|----|------|
| source | `manual_watchlist` |
| 标的 | 用户从 `config.yaml` 市值池自选，最多 **50**；证券 / L2+ / 锁定禁止开仓 |
| 价格 | 浏览器直连 `wss://fstream.binance.com/ws/!miniTicker@arr`（约 1s）；**不**在 `crypto-app-main` 再开 markPrice WS。WS 连不上 / 3s 无 tick 则轮询 `GET /api/watchlist/prices`（服务端已有 DataHub/WS，约 1s）。下单与限价触价仍用服务端 **ticker 最新价** |
| 市价 | 先挂 PENDING 再 `fill_paper_limit_order(at_market=True)`，走 INV-01 成交瞬间同步 |
| 限价 | 用户填写委托价；executor 触价成交；页面可**撤单**；超时 **8h 取消**（禁止转市价） |
| 保证金 | 默认 **1000U**（可手改；范围 10–50000U） |
| 开仓顾问 | **跳过** |
| 持仓顾问 | DeepSeek **sell 只建议不执行** |
| SmartExit | **排除**；硬 SL/TP 由 `position_sl_tp_monitor` |
| 实盘 | ∈ `LIVE_SYNC_SOURCES`；与 BRAIN/破位同一套 `live_trading_enabled` / `live_close_enabled`；**仅 L0**；**成交瞬间**同步，打开开关不回填 |
| 现货镜像 | **不跟** |

### 7.6 信号分析页（展示）【v4.5.45】

**页面**: `/technical-signals`（侧栏「信号分析」）  
**实现**: `technical_signals_api.py` · `technical_signals_page.js` · `technical_signals.html`

| 项 | 约定 |
|----|------|
| 评分 | 读 `technical_signals_cache`（main 约 **15min** `CALL update_technical_signals_cache()`）；禁止页面写死 BTC/ETH 假行 |
| 现价 | `GET /api/technical-signals/prices` 约 **1s**，走 DataHub/WS；**不**新开 markPrice WS |
| 方向 | 优先 24h 窗口 **15m** `trend`（BULLISH→LONG / BEARISH→SHORT） |

### 7.7 Dashboard 展示页【v4.5.46】

**页面**: `/dashboard`  
**实现**: `dashboard_snapshot_service.py` · `templates/dashboard.html` · `GET /api/dashboard/snapshot` · `GET /api/dashboard/prices`

| 项 | 约定 |
|----|------|
| 现价 | BTC/ETH/BNB/SOL 走 `GET /api/dashboard/prices` 约 **1s**（DataHub/WS）；**不**新开 markPrice WS |
| 信号表 | 与 §7.6 同源：`technical_signals_cache`（禁止 `coin_kline_scores`，EVENT 已下线） |
| 其它面板 | 快照约 5min 预计算；前端 30s 读快照（盈亏/OI/新闻/聪明钱） |

### 7.8 情绪与利润分层（决策，不下单）【v4.5.67】

**实现**: `app/services/sentiment_profit_strategy.py`  
**回归**: `scripts/validate_sentiment_profit_strategy.py`（无 API / 无 DB）

交易员自己判断两层利润（0～1）和人群情绪，模块只给动作。价格把心态带到一边；下单跟着还有利润的那一边。某一层利润低于 **0.05** 视为没了，不加仓。

**按币分开**: `decide_symbols` 每个币一份 `SymbolState`（自己的情绪、这段利润、整轮利润、杠杆燃料、仓位）。比特币的账不能套用到山寨，山寨之间也不共用。同一币重复提交直接拒绝。杠杆按该币的开仓价和倍数算爆仓距离，不共用比特币的 5 倍。

| 情形 | 动作 |
|------|------|
| 情绪热，整轮上面没了、下面还有 | 趋势空。手里有别的仓则本步只平，平完再开 |
| 情绪差，整轮下面没了、上面还有 | 趋势多。手里有别的仓则本步只平，平完再开 |
| 情绪热，只是这一段上面没了，杠杆多单还在 | 阶段空。整轮不翻。先平再开 |
| 情绪差，只是这一段下面没了，整轮仍是空 | 阶段多。整轮不翻。先平再开 |
| 这段杠杆多单已爆（`leg_long_fuel_alive=0`） | 这段空头利润按 0。有阶段空则本步只平，不再开空 |
| 同向但换层（阶段仓 → 整轮仓） | 先平这一层，下一层单独再开 |
| 心态和利润还在同一边，且仓已在这一层 | 拿着，不提前猜顶底 |

`decide` 只给下一步。`plan` 给出「先平、再开」。`apply_decision` 在仓还在时拒绝开新仓。

5 倍多单爆仓价 ≈ 开仓价 × (1 − 1/5 + 0.004)，约在开仓价下方两成。距离用开仓价归一；燃料已灭或价格已过爆仓价，剩余做空利润为 0。

**边界**: 不读行情、不进 `scheduler`、不进 `LIVE_SYNC_SOURCES`、不自动开平仓。自动策略仍受 INV-08，禁止把本模块的阶段空单接进自动开仓。

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

- SL **3%** / TP **5%** / 杠杆 **5x** / 保证金 **1000U** / 持仓 **4h**  
- 置信度门槛 ≥ **0.75**（`EXPLORE_CONFIDENCE_THRESHOLD` / `PREDICT_CONFIDENCE_THRESHOLD`，以代码为准）
- **DeepSeek LONG 加严**（SHORT 仍 0.75）：conf≥**0.82**；RSI1h≤**68**；`below_7d_high_pct`≤**-3**；24h 涨幅≤**12%**；纯突破无回踩且距高过近拒；15m 顺向须≥反向+**2**（`deepseek_long_entry_quality_ok`）
- `explore_catalyst_technical_ok` 硬门槛：文案（15m 结构/量价/趋势）+ **真实 15m OHLC 方向复核**（16 根顺向须多于反向；近 6 根不得反向占优；近端连反向≥2 拒绝）

### 8.3 K 线判据（INV-08 展开）

| 层级 | 15m | 1h |
|------|-----|-----|
| **定方向（DeepSeek）** | 16 根=4h 窗口；**价格趋势 + 量价**（只开多） | 不得单独定 DeepSeek 方向 |
| **定方向（BRAIN/破位）** | 时机辅证 | **1h 上升结构 + 回调做多**（v4.5.63） |
| **交叉验证** | 近 4~6 根结构 | 近 4 根 ≈ 同 4h 窗口 |
| **背景** | narrative.15m | narrative.24 根 + 表 |
| **辅证** | RSI 开仓时机 | RSI(1h)、7d 距离 |

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
**路由**: `open_advisor_routing.py` — 探索等统一 DeepSeek；BRAIN/中线 **跳过开仓顾问，纳入持仓顾问（仅建议、不执行平仓）**；BRAIN 退出见 §7.3.7 / §7.3.16

| 类型 | 节奏 | 核心判据 |
|------|------|----------|
| 开仓顾问 | 模拟开仓前（部分策略可 skip LLM） | **15m 趋势 + 量价** 与 side 一致 |
| 持仓顾问 tick | scheduler **每 15min** | 每仓 **15min/仓**；**浮盈转亏**立即 urgent 再审 |
| 持仓顾问决策 | 探索/预测：15m 表主审；浮盈 ROI≥**+8%** 且 15m **明确**转弱（反向≥4）→ 倾向 observe/sell；sell 须 15m 近 4 根确认反转；**sell 执行平仓**。**BRAIN/破位**：结构波段 rubric（不限时；多单卖合适高、空单平合适低）；结构点未到禁止因时长/浮盈回吐 sell；**sell 只落库 `advisor_suggest_only`** |

**AI 轻量移动止盈**（`position_sl_tp_monitor.py`）：探索/预测 peak 价格收益 **≥3%** 后回撤 **≥1%**（`ai-trail-tp`）。**BRAIN / 中线 v2** 优先结构点出场；错过结构点或浮盈回吐走 `check_structure_profit_lock`（须先有 ≥**1.80%** 浮盈，且本仓最好价到过该 1h 极值 **1.50%** 以内，才因 missed 平；入场前的旧高不算错过；峰值 ≥**2.20%** 且未到 5% 时，回撤 **1.20** 个点走 `structure_profit_keep`；落到 ≤+0.15% 才走 `structure_profit_to_loss`；C1/C3/B2 不因 missed 平；持仓时长不再单独止盈）。移动止盈须价格峰值 ≥**5%** 且保证金收益 ≥**25%**（5 倍下同为 5 个点），之后 Big4 LONG 多单回撤 0.80% 才平。破位硬 SL 2.0–3.0% + **−120U**。`midline_hold_exit` 与 4–6h 亏单到期 **不再执行**。

**AI soft-sl**（同 monitor）：通用探索/预测 grace **15min**、no_follow 约 **-1.2%**。**DeepSeek** explore/predict 单独加宽以匹配 15m×4h 开仓 thesis：grace **45min**；no_follow 须 age≥**60min** 且价格亏≥**约 2.2%**；profit_to_loss / mature 亦更深更晚；硬 SL 仍兜底。

开仓顾问：中线 v2 / BRAIN **跳过**。持仓顾问：中线 v2 / BRAIN **纳入** DeepSeek（**结构点 rubric**；**sell 只建议不执行**；硬 SL/结构点兜底）。探索/预测持仓仍用 4h 窗口 + 浮盈保护。

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
| REQ-NOTIFY | `trade_notifier.py`；模拟盘开/平 `futures_trading_engine`；实盘开平仓不发 TG；熔断/复盘仍发 |
| REQ-LIVE-CLOSE | `trading_gates.py`, `position_advisor_impl.py`, `binance_futures_engine.py` |
| REQ-GATES | `trading_gates.py`（含 `allow_long`/`allow_short` 方向总开关；模拟保证金 `get_paper_margin_usd` 固定 1000U）；牛市只做多 `bull_swing_policy.py` |
| REQ-PAPER-OPEN | `bull_swing_policy.py` · `paper_open_gate.py` · `entry_timing.py` · `paper_limit_entry.py`；权威 **§7.0** |
| REQ-AI-EP | `ai_explore_prompt.py`, `ai_predict_prompt.py`, `explore_worker_impl.py`, `explore_worker_common.py`, `*_explore_worker.py`, `*_predictor.py` |
| REQ-ADVISOR | `open_advisor_strategy_rubrics.py`, `position_advisor_impl.py`, `advisor_core.py`, `hold_advisor_query.py`, `position_sl_tp_monitor.py` |
| REQ-SCHED | `scheduler.py`, `data_cache_service.py` |
| REQ-KLINE | `binance_ws_kline_collector.py`, `fast_collector_service.py` |
| REQ-RATING | `update_top_performers.py` |
| REQ-MIDLINE | `market_cap_universe.py`, `midline_swing_config.py`, `midline_swing_scanner.py`, `entry_timing.py`, `midline_hold_exit.py`, `midline_explore_worker.py`, `midline_swing_api.py`, 破位策略页 JS/模板, `strategy_display_names.py`, `scheduler.py`, `position_sl_tp_monitor.py`, `trading_gates.py`, 开仓/持仓顾问路由 |
| **REQ-BRAIN** | `brain_config` / `brain_strategy_orchestrator`（市值前 300）/ `market_cap_universe.py`；`paper_limit_entry` + executor expire；`trading_gates.LIVE_SYNC_SOURCES`；`smart_exit_optimizer` 排除；`validate_brain_req.py`；权威 §7.3；操作对照 `docs/BRAIN_AND_BREAKOUT_OPERATOR_ZH.md` |
| **REQ-BRAIN-v2** | Playbook 识别 + 信号打标 + `brain_opportunities` 落库 + 分向胜率 + 评估报表；§7.3.10–7.3.15（**首版已落地**） |
| **REQ-BRAIN-HOLD / REQ-BRAIN-RISK** | 牛市只做 A1 **1h 回调多**（近 24 根离高 1.20%–8.00%，不强制高于前 24 根；8 根斜率 **0%–15%**；近 3 根低于 -0.50% 不买；48h 偏空否决 A1）；同一币 1 槽、全书最多 12 笔；A1 胜率软底 45%；盈利 1h 冲高回落（高点须是本仓到过的）+ 中段回撤 1.20 个点锁利；不因持仓时长把 +1.50% 小浮盈平掉；浮亏 **-200U** 平仓；顾问 suggest-only；§7.3（**v4.5.82**） |
| REQ-ST | `smart_trader_service.py` |
| **REQ-SPOT** | `spot_paper_mirror.py`；`spot_live_sync.py`；`spot_trader_service.py`；`fill_paper_limit_order` 钩子；`validate_spot_paper.py`；权威 §7.4 |
| **REQ-WATCHLIST** | `watchlist_config.py` · `watchlist_store.py` · `watchlist_orders.py` · `watchlist_api.py` · `watchlist_page.js`（浏览器直连币安合约 WS + 3s 无 tick 则 `/api/watchlist/prices` 1s 补价）· `/watchlist`；`LIVE_SYNC_SOURCES` 含 `manual_watchlist`；权威 §7.5 |
| **信号分析页** | `technical_signals_api.py` · `technical_signals_page.js` · `/technical-signals`；缓存表 + 服务端现价；权威 §7.6 |
| **Dashboard 页** | `dashboard_snapshot_service.py` · `/dashboard` · `/api/dashboard/prices` 1s；信号同源 `technical_signals_cache`；权威 §7.7 |
| **情绪与利润分层** | `sentiment_profit_strategy.py`；`validate_sentiment_profit_strategy.py`；只决策、不下单、不进实盘；权威 §7.8 |

---

## 16. 变更记录

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-10-25 | **v4.5.82** | **收紧开仓、给亏损封顶**：① 8h 斜率改回 **0%–15%**；② 48h `h1_side=SHORT` 否决 A1 多；③ 大脑+破位同一币 **1** 槽、合计最多 **12** 笔；④ 每批/每轮最多开 1 笔，大脑同币冷却 4h；⑤ 浮亏 **-200U** 平仓（`structure_loss_cap`），不再扛到强平。10/25 账本：已平仓仍小赚，持仓浮亏大脑约 -10020U（64 笔）、破位约 -3441U（10 笔），同一币最多叠 3 仓，最差单 `h1=SHORT`。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §7.0/§7.3 已同步 |
| 2026-10-23 | **v4.5.81** | **修好弱市仍开不出 A1**：① 8h 斜率改回 **-1.20%–15%**；② 追涨阈值回补 **35%**；③ 已确认 1h 回踩时 48h `h1_side=SHORT` 不得滤掉 A1；④ **有 `1h_trend_high_pullback` 时强制选 A1 标签**（否则 A2 分更高抢走标签，再被只做多挡掉，真实回踩永远开不了；COMP/AERO）。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §7.0 已同步 |
| 2026-10-23 | **v4.5.80** | **修好一直不开单**：去掉 A1「近 24 根必须高于前 24 根」和 `broke_prior_low`。旧 HH 闸门把 top100 约 96 个币打成 `not_1h_trend_high_pullback`；峰前低点闸门又误伤正常回踩。现行只认近 24 根离高 1.20%–8.00% + 斜率 0%–15% + 近 3 根非自由落体。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §7.0 已同步 |
| 2026-10-23 | **v4.5.79** | **A1 8h 斜率改回 0%–15%**：按约定下限 0%、上限 15%（不含 15%）。纠正代码里误写成 -1.20%～5% 的口径。近 3 根 -0.50% 自由落体否决不变。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §7.0/§7.3 已同步 |
| 2026-10-23 | **v4.5.78** | **修好开不出单**：A1 的 8h 斜率下限改为 **-1.20%**（仍用近 3 根 -0.50% 拦自由落体）。宏观熊/过渡/崩盘/恐慌反弹/日线探底时，代币已打出 `1h_trend_high_pullback` 的 A1 可缩仓开，不再整段禁开。A1 胜率软底 **45%**，反方向高出 ≥10pp 才硬拒（避免连亏后 55% 池子锁死）。近 36h 主拦因：`regime_bear/transition_blocks_LONG_A1` + `winprob_below_0.55`；破位连续多轮 0 信号。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §7.0/§7.3 已同步 |
| 2026-10-23 | **v4.5.77** | **止盈不再按时长落袋**：取消 `structure_stale_take`（≥4h 且浮盈 ≥1.50% 就市价平）。1h 冲高回落和错过高低都要求本仓最好价到过该极值 1.50% 以内；A1 入场前的旧高点不算卖点。中段 1.20 个点锁利和 5% trail 不变。BSB 10/23 三笔在本仓高点 +1.50%～+1.75% 被提前平掉。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §7.3.16.3 已同步 |
| 2026-10-22 | **v4.5.76** | **Telegram 全文英文**：开/平仓、策略名、持仓时长、每日复盘、币安公告、IP 封禁告警一律英文。Web 中文展示不变。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §2.1 已同步 |
| 2026-10-22 | **v4.5.75** | **开单口径收成 §7.0**：自动策略只开 A1 的 1h 回调多，确认后市价。A2/B/C 与空单只打标。破位扫描改为 15 分钟，不再写 4h 三层 AND 或 C1/C3 下单。DeepSeek 对照期仍限价开多。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §7.0/§7.2/§7.3.4 已同步 |
| 2026-10-22 | **v4.5.74** | **A1 买点改 8 小时斜率**：最近 8 根 1h 收盘斜率在 0%–5% 才买。最近 3 根低于 -0.50% 仍是在跌（`1h_slope_still_falling`），不买。卖出仍用 1h 冲高回落，不用斜率由正变 0。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §7.2/§7.3 已同步 |
| 2026-10-22 | **v4.5.73** | **A1 底部看 1h 斜率**：最近 3 根 1h 收盘斜率接近 0，或小于 5%，才买。斜率为负是还在跌（`1h_slope_still_falling`），不买。斜率 ≥5% 记 `1h_slope_too_steep`。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §7.2/§7.3 已同步 |
| 2026-10-22 | **v4.5.72** | **A1 只认 1h K 线回调**：从最近一根 1h 波段高点量回落 1.20%–8.00%，1h 仍在回落才市价。1h 阳线买回高点记 `1h_chase_not_pullback`，不开。10/21 夜 28 笔里多数是贴着高点的阳线。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §7.2/§7.3 已同步 |
| 2026-10-21 | **v4.5.71** | **中段锁利**：BRAIN/破位峰值 ≥2.20% 且未到 5% trail 时，从峰值回撤 1.20 个点即市价平（`structure_profit_keep`）。+0.15% 只作跳空兜底。GRVT 3.01% 峰值吐到 +0.08% 的路径被拦住。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §7.3.16.3 已同步 |
| 2026-10-20 | **v4.5.70** | **同一交易对最多 3 单**：OPEN 持仓 + PENDING 开仓限价合计达到 3 才拒绝，不再一单一挡。BRAIN / 破位 / DeepSeek / 自选共用此槽。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §7 已同步 |
| 2026-10-20 | **v4.5.69** | **TG 文案去掉「模拟盘」**：开仓标题改为 `【开仓】`，平仓标题改为 `【平仓】`。仍只发模拟成交，实盘开平仓不发。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §2.1 已同步 |
| 2026-10-20 | **v4.5.68** | **移动止盈后移**：BRAIN/破位 trail 须价格涨满 **5%**，5 倍下保证金至少 **25%** 才启动。未到线不锁利。1h 冲高回落仍另算。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §7.3.16.3 已同步 |
| 2026-10-15 | **v4.5.67** | **情绪与利润分层**：阶段单与整轮分开，按币做账。换仓时平仓是独立一步，平完才允许开新仓；同向换层也先平。不自动开平仓、不进实盘。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §7.8 已同步 |
| 2026-10-15 | **v4.5.66** | **保证金亏满强平**：无硬 SL 的自动仓仍须被监控扫描。价格触及强平价，或保证金浮亏达到 100%，强制平仓。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md INV-12 §7.3.16 已同步 |
| 2026-10-14 | **v4.5.65** | **A1 回调改市价**：BRAIN / 破位在 1h 回调确认后直接市价成交，不再挂限价等更深回踩。未成交的 BRAIN / 破位限价作废。成交瞬间仍走实盘同步与现货镜像。DeepSeek 与自选限价不变。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md INV-11/INV-BRAIN-06 §7.2/§7.3 已同步 |
| 2026-10-13 | **v4.5.64** | **修好 1h 回调多不开仓**：A1 认 `1h_trend_high_pullback`（离近 24 根新高 1.20%–8.00%）。回踩后抬起不再记成 `bounce_not_pullback`。15m 0.80%–3.50% 浅回踩和近 8 根斐波那契窄区不再否决。破位不再因回踩当下的 4h 转弱丢掉 A1。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §7.2/§7.3 已同步 |
| 2026-10-13 | **v4.5.63** | **牛市只做多**：自动策略禁止开空；BRAIN/破位只开 A1 **1h 回调**；盈利 1h 冲高回落；亏损不设硬 SL，扛到强平。不写 allow_short。自选除外。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md INV-08/12 §7.2/§7.3 已同步 |
| 2026-09-23 | **v4.5.62** | **修盈亏比**：锁利门槛抬到 1.80%/2.20%，不再收 0.5% 碎盈；破位 SL 改为 2.0–3.0%；已开 6% 单加 −120U 熔断；C1/C3/B2 不因 missed_low 平；每轮跟风最多 3 笔。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §7.2.3/§7.3.16.3 已同步 |
| 2026-09-23 | **v4.5.61** | **模拟保证金统一 1000U**：合约模拟开仓不再按评级/Playbook/宏观倍数缩仓；现货镜像仍 500U；实盘仍走 `max_position_value`。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §6.6 已同步 |
| 2026-09-21 | **v4.5.60** | **结构仓补锁利**：错过 15m 高低点仍止盈；峰值回吐 / 4h 仍绿则平；**不**对亏损单按时长强平。禁止把盈利单扛到硬 SL。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §7.3.16.3 已同步 |
| 2026-09-14 | **v4.5.59** | **TG 改模拟盘开/平仓**：实盘开平仓与挂撤单不再发 Telegram；模拟盘成交与平仓发。熔断/复盘仍发。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §2.1 已同步 |
| 2026-09-14 | **v4.5.58** | **持仓顾问对齐结构波段**：BRAIN/破位改结构点 rubric（不限时；禁止 4h/浮盈回吐卖）；`_temper_structure_swing_hold`；探索/预测仍 4h。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §9 已同步 |
| 2026-09-14 | **v4.5.57** | **结构持仓**：BRAIN/破位取消 4–6h 到期与硬 TP/早锁利；多单低点买高点卖、空单相反（`structure_swing_exit`）。硬 SL 与 BRAIN −80U 仍兜底。需求文档: docs/REQUIREMENTS_LOGIC_ZH.md §7.2/§7.3.16 已同步 |
| 2026-08-23 | **v4.5.55** | **15m RSI 作开仓时机**：多单超买未拐头/超卖插刀则等；B3/C4 超买仍向上或已超卖则等。不定方向 |
| 2026-08-23 | **v4.5.54** | **破位跟风仍市价**：C1/C3/B2 市价跟当根；A1/A2/B3/C4 与 BRAIN 仍限价；超时取消不转市价 |
| 2026-08-23 | **v4.5.53** | （已由 v4.5.54 收窄）曾把全部破位改限价；C3/C1 跟风被拖钝，已撤回 |
| 2026-08-23 | **v4.5.52** | **B3 前提**：Big4 仍 LONG 时 B3 只打标不开仓；C1/C4 仍可小仓。近 24h 破位 B3 37 笔 −1419U |
| 2026-08-23 | **v4.5.51** | **A1 回踩抬起仍开**：有趋势新高时 bounce_from_low 不再否决 A1。多空不设分向上限 |
| 2026-08-23 | **v4.5.50** | **B3 须拒绝再空**：不过前高只打标，不得在第二次顶上直接空；8/23 破位 B3 被反弹打穿 |
| 2026-08-23 | **v4.5.49** | **A1 重定义**：只开「趋势新高回踩」；反弹/不过前高/破位阴跌不得当 A1。继续自动开仓 |
| 2026-08-23 | **v4.5.48** | **冲高不过前高做空**：已有高顶后再出现较低的第二次顶 → `15m_failed_retest` 打 B3 SHORT；禁止当 A1 回踩（`failed_retest_short`） |
| 2026-08-23 | **v4.5.47** | **反弹≠回调**：近 16 根 15m 区分 `15m_pullback_from_high` / `15m_bounce_from_low`；A1/B4 必须从高点回落；死猫跳+缩量不得打成回调做多（`bounce_not_pullback`） |
| 2026-08-22 | **v4.5.46** | **Dashboard 活数据**：Big4 现价 1s（`/api/dashboard/prices`）；信号表改读 `technical_signals_cache`（不再用已下线 `coin_kline_scores`） |
| 2026-08-22 | **v4.5.45** | **自选/信号页活数据**：自选 WS 3s 无 tick 则服务端 `/api/watchlist/prices` 1s 补价；信号分析去掉写死行，接 `technical_signals_cache` + 现价 1s 刷新 |
| 2026-08-22 | **v4.5.44** | **做多/做空按钮即硬闸门**：`allow_long=0` 不得挂多限价、不得开多、已挂多单立即取消；`allow_short` 对称。BRAIN/破位跳过顾问也必须过此闸门。已有持仓不平 |
| 2026-08-22 | **v4.5.43** | **scheduler 成交不得把实盘标 FAILED**：仅 `crypto-app-main` 有 `engine_manager`；无引擎时留 NULL，由 main PaperSync 同步。8/22 main 日志 FAILED 单在成交秒无任何 PaperSync 行。不回填历史 FAILED |
| 2026-08-22 | **v4.5.42** | **取价失败不得拦实盘开仓**：PaperSync / `open_position` 无 ticker 时用模拟成交价；8/21 日志 FAILED 单从未打出「发送开仓订单」。不回填历史 FAILED |
| 2026-08-22 | **v4.5.41** | **FAILED 原因不得被截掉**：`PaperSync FAILED` 前置写入 notes；市价数量按 `MARKET_LOT_SIZE.maxQty` 截顶。不回填历史 FAILED |
| 2026-08-22 | **v4.5.40** | **实盘开仓后处理不得翻盘**：币安已接受订单后 SL/TP/DB 异常仍保存 live 并 SYNCED；数量按 LOT_SIZE 重载精度 |
| 2026-08-22 | **v4.5.39** | **PaperSync FAILED 落原因**：失败写入 `futures_orders.notes`；币安已成交且 live 行已在则标 SYNCED（防漏映射）。不回填历史 FAILED |
| 2026-08-21 | **v4.5.38** | **行情识别页接真闸门**：`/market_regime` 展示 BRAIN Big4 1h + BTC 日线桶；去掉写死时间轴/假区间；`GLOBAL_UNKNOWN` 标明为日线混合 |
| 2026-08-21 | **v4.5.34** | **B3 衰竭确认收紧**：须离高 ≥0.28% 且当根拒绝（收在下半+上影/阴线）；贴高横盘 / RSI 仍超买 / 仅 stall 不得开空；C3/C1 未改 |
| 2026-08-21 | **v4.5.33** | **自选价格 WS**：`/watchlist` 浏览器直连币安 U 本位 `miniTicker` 实时刷价；下单/触价仍走服务端 ticker；WS 断开 REST 回退 |
| 2026-08-21 | **v4.5.32** | **合约自选**：侧栏「我的自选」；用户添加交易对；手动限价/市价；`manual_watchlist` 进 LIVE_SYNC（仅 L0，成交瞬间，打开不回填） |
| 2026-08-21 | **v4.5.31** | **操作对照文档**：新增 `docs/BRAIN_AND_BREAKOUT_OPERATOR_ZH.md`（超级大脑 vs 破位）；逻辑条款未改 |
| 2026-08-21 | **v4.5.30** | **市值扫描池**：`config.yaml` 改为市值前 300 的 Binance U 本位永续；超级大脑扫 300；破位策略扫前 100；脚本 `sync_config_market_cap_symbols.py` |
| 2026-08-21 | **v4.5.29** | **破位策略开关上系统配置**：`midline_long/short_enabled` 出现在桌面/手机系统设置与破位策略页；关则停扫新开，已开仓仍监控 |
| 2026-08-21 | **v4.5.28** | **破位策略接入实盘**：`midline_long/short` 加入 `LIVE_SYNC_SOURCES`；随实盘开/平仓总开关；仅 L0；旧四路中线仍不实盘 |
| 2026-08-21 | **v4.5.27** | **现货实盘开关** `spot_live_enabled`：模拟开/平仓瞬间同步 Binance 现货 500U；打开不回填历史仓 |
| 2026-08-21 | **v4.5.26** | **现货镜像**：跟 BRAIN A1 + DeepSeek LONG 开模拟现货；仅 L0；旧 DCA 停；单笔 **500U** |
| 2026-08-21 | **v4.5.25** | **禁止追高再入**：C3 `chase_blowoff`（RSI 极端+7日高 / 滞涨）；`missed_break` 用冲高前高点；同币同向止盈后 4h 冷却；A1 滞涨不得当回踩 |
| 2026-08-21 | **v4.5.24** | **破位策略文案 + 具体开仓信号**：交易记录/复盘展示 Playbook（如 `破位·C1 放量破位跟空`）；页面「中线」改为「破位」；source 仍 `midline_*` |
| 2026-08-21 | **v4.5.23** | **中线破位市价 + 顶部回调点**：C1/C3/B2 破位当根市价跟风；B3/C4 须第一根离开高点的拒绝才空；BRAIN 仍强制限价 |
| 2026-08-20 | **v4.5.22** | **回调做空**：Big4 仍 LONG 时禁止 A2/B2；允许 B3/C4 高点滞涨空（×0.20）与 C1 破位跟风（×0.35）；已有滞涨时不因 4h 仍多拦截 |
| 2026-08-20 | **v4.5.21** | **BRAIN 接入实盘**：`brain_swing` 加入 `LIVE_SYNC_SOURCES`；由实盘开/平仓总开关控制；实盘仅 L0 白名单 |
| 2026-08-20 | **v4.5.20** | **单边不追高**：多单贴着近 8 根 15m 高点（距高 ≤0.40%）一律 `wait_pullback`；A1 不得仅凭 `15m_higher_low` / 靠近 EMA 追多 |
| 2026-08-20 | **v4.5.19** | **顺势不做空 + 退出收口**：Big4 已 LONG 时禁止新开空单（含 C1/B3/C4）；计划到期不再因 D1/翻转强平；BRAIN/中线持仓顾问 sell 只建议不执行 |
| 2026-08-20 | **v4.5.18** | **主升浪持仓**：Big4 已 LONG 时日线 `DAILY_BEAR_PROBE` 不得否决 A1；中线/BRAIN 多单 trail 放宽；多头趋势关闭 no_follow（其它多单 180min/-2.5%）；顾问禁止仅凭 RSI 超买强平多单 |
| 2026-08-17 | **v4.5.17** | **C1 破位跟风 + A2/B2 开仓**：C1 刚破位放量即跟风做空（不等回抽）；A2 须下降结构+弱反抽拒绝；B2 须先有反抽再破起点；三者纳入 TRADEABLE |
| 2026-08-17 | **v4.5.16** | **冲高滞涨卖点**：B3/C4 在高点挂空（`entry_timing` `exhaustion_ready`；滞涨≥2条；仍加速 `wait_stall`；已离高 `missed_high`）；中线 SHORT 纳入 B3/C4；C1/A2 仍等回抽；C3/A1 仍等回踩 |
| 2026-08-17 | **v4.5.15** | **回调买入点 + 中线卖点**：BRAIN/破位须 15m 回踩进 EMA/38–50% 区才挂限价（`entry_timing`，禁止追高）；C3/C1 confirmed 改回踩/回抽；中线纳入 DeepSeek 持仓顾问；`midline_hold_exit` 峰 1.2% 锁利 + 回吐平；持仓 C3/C1 4h、A1 6h |
| 2026-08-11 | **v4.5.14** | **受控补空与偏空保护**：A2/C1 从影子改为小仓试点；A2/C1 必须 Big4 `SHORT` 顺势，A2 保证金×0.50、C1×0.35；Big4 `SHORT` 阻断 A1 追多；B2 继续只识别打标 |
| 2026-08-09 | **v4.5.13** | **执行安全收口**：BRAIN 恢复防插针强制限价并超时取消；全面排除 SmartExit；PENDING 成交前重跑安全闸门；`close_position` 事务 `FOR UPDATE` + 条件更新，防并发重复平仓/记账 |
| 2026-08-08 | **v4.5.12** | **盈利 KPI**：**A1 豁免 5m 早撤**（只靠 -80/trail/硬 SL）；胜率不作优化目标；其它 Playbook 仍 40U+4 根 |
| 2026-08-06 | **v4.5.11** | **期望值优先纠正**：暂停 **A2**；SHORT edge≥**0.90**；5m 早撤 **40U+4根**（原20U+3过勤砸胜率）；soft 仍关 |
| 2026-08-05 | **v4.5.10** | **关闭 BRAIN soft**：有 5m 早撤后 soft 重叠且更深（均约-50U），堆笔拖垮；保留 5m / -80U / trail / 硬 SL |
| 2026-08-04 | **v4.5.9** | **5m 逆势早撤**：浮亏≥20U 且 5m 持续逆势无反转 → `brain_5m_adverse`；极端行情连续反向很少收回，不必等 soft/-80 |
| 2026-08-03 | **v4.5.8** | **压住 >100U 大亏**：美元熔断 **-80U**（先于硬 SL）；soft 提前到 30min、峰门槛 0.75%（修峰≈0.7% 躲 soft 打满 SL） |
| 2026-08-03 | **v4.5.7** | **止损滞后修刀**：重开 soft（45min/峰≤0.5%/浮亏≤-1.2%）；SL 上限 8%→**4.5%**；针对胜率好却被硬 SL 大亏吃光 |
| 2026-08-02 | **v4.5.6** | **BRAIN 入场收紧**：`edge≥0.75`；A/B 须 `confirmed`；**B2/C1 暂不开仓**（仍打标）；少开 timeout/不跟进仓；**保证金仍 L0=1000U** |
| 2026-08-02 | **v4.5.5** | **宽 SL trail 激活下调**：min(TP×40%,SL×25%) 夹 0.8~1.0%；避免 8%SL 仓峰仅1.1%打满硬止损（如 1000RATS -440U） |
| 2026-08-01 | **v4.5.4** | **关闭 BRAIN soft_no_follow**：日统计胜率66.7%仍亏——均赢+32/均亏-65；soft 单日−329 vs trail+618；亏损改交硬 SL |
| 2026-07-31 | **v4.5.3** | **BRAIN 退出修刀**：SL 地板 2.5%/TP 地板 3%；trail 激活 min(TP×40%,SL×50%) 夹 1.0~1.8%；monitor 从 DB 恢复并回写 `max_profit_pct`（修重启丢峰导致 soft/trail 误判） |
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
