# 超级大脑量化交易系统 — 项目上下文

## 项目概览

基于技术信号、市场趋势和智能风控的全自动加密货币量化交易平台,在 Binance U 本位合约市场自动执行交易,提供 Web 管理界面。

## 技术栈

| 层级 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn (Port 9020) |
| 数据库 | MySQL 8.0 + pymysql (远程 AWS) |
| 调度 | schedule (app/scheduler.py) |
| 交易所 | Binance Futures |
| 前端 | Jinja2 + Tailwind CSS (服务端渲染) |
| AI | Google Gemini (google.genai SDK) |
| 通知 | Telegram Bot API |
| 配置 | dotenv_values 读 .env |

## 进程架构 (3 个核心交易/调度进程 + Web)

1. **smart_trader_service.py** — U 本位合约主交易
2. **app/scheduler.py** (crypto-scheduler, systemd) — 统一调度引擎
3. **app/main.py** (FastAPI) — Web 服务 + REST API

辅助进程: **fast_collector** (30min REST 补 1h/4h/1d) / **ws_kline_collector** (5m+15m WS) / multi_strategy 等

## 数据库

- `binance-data`: 主数据库 (100+ 表)
- `data_cache`: 预计算缓存库 (v3.5 新增,5 张缓存表)
- 连接通过 .env 配置,需显式在服务启动时连接

## 重要规则

- **Big4RegimeMonitor / 熔断禁止改系统设置**: Big4 和盈亏熔断只能发通知，不允许修改 `system_settings` 的 `allow_long`/`allow_short`/`trading_enabled`。用户手动控制。
- **DB 配置形状禁止静默回退 (2026-06-15 事故)**: `get_db_config()` 返回裸 MySQL dict (`host/user/password/database`)，`DatabaseService` 期待完整结构 `{'type':'mysql','mysql':{...}}`。任何桥接代码必须 normalize 或 fail-fast，禁止因为缺少 `mysql` key 静默 fallback 到 `root@localhost`/空密码。已在 `PriceCacheService` 做兼容修复；后续改 DB 相关代码前必须检查调用方传入的配置形状。
- **FastAPI systemd 日志不在 journalctl**: `crypto-app-main.service` 的 stdout/stderr 追加到 `logs/main_systemd.log`；loguru 主日志为 `logs/main_YYYY-MM-DD.log`。`journalctl -u crypto-app-main` 主要看 systemd stop/start/timeout，不一定有 Python 异常。
- **1h K线天然滞后**: 1h 最新收盘的 K 线永远是上一小时的，延迟约 60-65 分钟属正常。`BACKFILL_LAG_THRESHOLD_S['1h']=3900` (65min)，不要改小。
- **WS K线 backfill exchange name**: `_check_and_backfill` 中 exchange 必须用 `binance_futures`（匹配 `save_klines` 的存储名），不能用 `usdt_futures`。
- **关模拟仓不会自动同步实盘**: 没有后台监听服务。需要通过 `BinanceFuturesEngine.close_position_direct()` 主动在交易所平仓。
- **除 crypto-scheduler 外不要加心跳日志**: WS 采集器日志中有 `send_heartbeat` 日志是正常的 (每 20s)，不要把它当作 bug 去修。
- **K 线采集分工 (2026-06-12)**: **5m/15m 仅 WS** (`ws_kline_collector_service` / `crypto-ws-kline`)；`fast_collector` **30 分钟轮询**，REST **只补 1h/4h/1d**（禁止 REST 拉 5m/15m，与 WS 重复会触发 Binance IP ban -1003）。封禁状态见 `logs/binance_ban_state.json` + `app/utils/binance_rate_guard.py`。
- **Kline volume 精度 (2026-06-15)**: 生产库 `kline_data.volume` 与 `taker_buy_base_volume` 已升级为 `DECIMAL(28,8)`，模型必须保持一致。原因是 DOGS/NEIRO 等低价高供应合约 base volume 会超过 `DECIMAL(20,8)` 的 12 位整数上限。
- **持仓时间 UTC (2026-06-13)**: `futures_positions.open_time` / 限价开仓成交须用 **`utc_now_naive()`**（`app/utils/position_time.py`）。历史行若 `open_time` 比 `close_time` 晚约 8h（CST/UTC 混存），成交记录持仓时长会显示 `--`；API 用 `calc_holding_minutes(..., created_at=)` 回退到 `created_at`/`fill_time` 对齐。
- **config.yaml 交易对**: 与 Binance `PERPETUAL`+`TRADING` 同步；**不含** `TRADIFI_PERPETUAL`（XAG/XAU/代币化股票等）；`securities_filter.py` 另拦股票/大宗 base。

### 2026-06-15 DB 配置事故复盘

- 症状: `logs/main_2026-06-15.log` 反复报 `Access denied for user 'root'@'localhost' (using password: NO)`，同时 `PriceCacheUpdater` / `app.database.db_service` 重试。
- 根因: `main.py` 用 `get_db_config()` 从 `.env` 读出了正确裸 MySQL dict，但 `PriceCacheService` 直接传给 `DatabaseService`；后者执行 `self.config.get('mysql', {})`，取不到后回退默认 root/空密码。
- 修复: commit `0efc818f` 在 `app/services/price_cache_service.py` 增加 `_normalize_database_service_config()`，兼容裸 MySQL dict 和完整 database config。
- 以后规则: DB password 不应靠默认值兜底；服务启动路径里出现 root/空密码日志时，优先查配置形状，不要先怀疑 `.env` 丢失。
- 服务器排查命令:
  `tail -200 logs/main_systemd.log`
  `tail -200 logs/main_$(date -u +%Y-%m-%d).log`
  `sudo systemctl status crypto-app-main --no-pager -l`
  `curl -sS --max-time 5 http://127.0.0.1:9020/api/futures/health`

## scheduler 调度任务

| 任务 | 频率 |
|------|------|
| 价格采集 | 持续 (WS + REST) |
| data_cache.market_snapshot | 每 1 分钟 |
| data_cache.market_movers_snapshot | 每 5 分钟 |
| data_cache.position_stats_snapshot | 每 30 分钟 |
| data_cache.candidate_pool_snapshot | 每 **6 分钟** (行情+K线叙事) |
| data_cache.explore_prepared_snapshot | 每 **15 分钟** (共用 universe，策略只读) |
| data_cache.settings_cache | 每 1 分钟 |
| DeepSeek 探索 | 每 **2h** + **10min 轮询**；worker 距上次 ok ≥ **max_hold_hours** |
| DeepSeek 预测 | 同 DeepSeek 探索节奏 + `deepseek_predict_next_due_utc` |
| GPT 探索/预测 | 同节奏 + `gpt_*_next_due_utc` |
| Gemini 探索 | —（已下线） | — |
| DeepSeek 探索/预测 | 每2h + 10min/5min（**对照期保留**；INV-BRAIN-07 暂缓） | `deepseek_*_enabled` |
| **REQ-BRAIN** `brain_swing` | 每 **15s** 一批 **5** 币轮询 L0/L1 | `brain_swing_enabled`；启动 +75s；发现机会挂防插针限价 |
| **中线 v2** `midline_long/short` | 每 **15min** 轮询；多单回调买 / B3/C4 冲高卖；C3/C1/B3/C4 4h / A1 6h |
| **持仓顾问** DeepSeek | 每 **15min** tick（每仓 15min；浮盈转亏 urgent；BRAIN/中线 **suggest-only**） |
| 市场情绪分析 | **已下线**（原 Gemini 情绪） |
| ETF 同步 | 每天 06:45 |
| 金库同步 | 每天 07:30 |

### scheduler AI 运维备忘 (2026-05-31)

**生产 DB**: MariaDB 10.5（非 MySQL 8）；`INSERT ... ON DUPLICATE KEY UPDATE` 可用。

**探索/预测别混淆「延时」**:
- 正常: `距上次成功不足 Nh 剩余 Xmin (last_ok=..., 下次=...)` — 防重，不是 scheduler 坏了。
- 异常: `上一轮还未结束` 持续 >15min — 探索/预测线程卡死或占锁。
- `schedule.every(N).hours` 在 **restart 后从 0 计时**；靠 **5/10min 轮询 + worker 距上次 ok** 补位。勿频繁 `systemctl restart crypto-scheduler`。

**candidate_pool_snapshot** (`data_cache_service.refresh_candidate_pool`):
- **禁止** refresh 开头 `DELETE` 全表（会导致探索读空表 → 慢路径）。已改为 **UPSERT + 本轮结束后删下架币**。
- 探索读缓存: `load_candidate_pool_for_explore()`；`get_candidate_pool` 默认**不再**限制 `change_24h` 0–100%。
- K 线叙事: **1h = 整体 24 根趋势 + 近 6 根明细** (`_make_kline_narrative`)

**主探索/预测 prompt 与门槛** (`ai_explore_prompt.py`, commit `162d8d5b`):
- LLM 候选池: `prepare_universe_for_llm` **技术面评分 TOP50**，非 |24h| 极端排序
- 开仓置信度: `EXPLORE_CONFIDENCE_THRESHOLD` / `PREDICT_CONFIDENCE_THRESHOLD` = **0.75**；catalyst 须过文案门槛 + **真实 15m OHLC** 方向复核
- catalyst 硬门槛: `explore_catalyst_technical_ok`（文案 + **真实 15m OHLC** 方向复核；禁单根 15m / 纯涨跌幅）
- 预测同样走 catalyst gate → `skipped_weak_catalyst`
- 回归: `scripts/validate_explore_predict_prompts.py`

**gemini_explore_runs.status**: ENUM `ok` / `partial` / `error` / `skipped` — **无 `running`**。进行中登记用 **`partial`**，结束 `_finish_run` 改为 `ok`/`skipped`/`error`。`status='running'` 会 1265 Data truncated。

**预测 4h 保证**: 每 5min 轮询 + `gemini_predict_next_due_utc` / `deepseek_predict_next_due_utc` 认领窗口；启动后 +45s/+50s 补跑检查。勿用 `scheduler_init` 跑预测。进程锁在跳过时会释放。

**启动 init 错峰** (`scheduler_init`): DeepSeek探索 +90s；预测补跑 +50s；DeepSeek Big4 +95s。

**生产 LLM prompt 语言 (2026-06-05)**: 主探索/预测/战术/反转/开仓顾问/持仓顾问 **中文**（`build_*_prompt()` → `*_zh`）；顾问 `reason` 中文；GPT 用 `GPT_JSON_SYSTEM_ZH`。完整说明见 `docs/AI_STRATEGIES_AND_ADVISORS_ZH.md`。

**日志** (非 journalctl): `logs/scheduler_YYYY-MM-DD.log`；排查:
`grep -E "一轮开始|一轮结束|candidate_pool_snapshot 读取|回退 kline|上一轮还未结束|skipped_weak_catalyst" logs/scheduler_$(date -u +%Y-%m-%d).log`

**相关 commits**: `211990b` (UPSERT+探索读缓存+锁), `15bdfc0` (partial), `e0feb1e9` (探索 4h), `162d8d5b` (主探索/预测 prompt+门槛), `2766ff8d` (REST K线 ban 修复), `fab88de7` (持仓时长 UTC+回退).

详见 `.cursor/rules/scheduler-ai-ops.mdc` 与 `.cursor/rules/ai-strategies-advisors.mdc`。

## K 线采集进程 (2026-06-12)

| 进程 | systemd 名 | 职责 |
|------|------------|------|
| `ws_kline_collector_service.py` | crypto-ws-kline | **5m + 15m** WebSocket 主采集；backfill exchange 名须 `binance_futures` |
| `fast_collector_service.py` | crypto-fast-collector | **每 30min 轮询**；仅在 1h/4h/1d 整点 REST 补数；IP 封禁时暂停并读 `binance_rate_guard` |

改 collector/WS 代码后分别重启对应 systemd；**勿同时跑两份 fast_collector**（无 PID 锁时会叠加 REST 触发 ban）。

## AI 策略与顾问（完整文档）

- **业务逻辑权威（必读）**: `docs/REQUIREMENTS_LOGIC_ZH.md` — 实盘同步、闸门、15m 判据、不可变硬规则
- 中文: `docs/AI_STRATEGIES_AND_ADVISORS_ZH.md`
- English: `docs/AI_STRATEGIES_AND_ADVISORS_EN.md`

以下为速查；细节以文档为准。

## Gemini / DeepSeek / GPT AI 模块

### 顶空底多 (`*_reversal`)
- 无 kill switch；`top_reversal`→SHORT / `bottom_reversal`→LONG；SL **3%** / TP **5%** / 4h / 5x / 500U；conf≥**0.65**
- 仅模拟仓；表 `*_reversal_explore_runs`（migration 008）

### 战术四策略 + 三教师 (`*_pullback|rebound|chase|dump`)
- **15 任务**（5 策略 × 3 教师），`tactical_explore_scheduler` 4h + **15min** 轮询；共用 `explore_prepared_snapshot`
- 固定方向：pullback/chase→LONG，rebound/dump→SHORT；SL **3%** / TP **5%**；conf≥**0.55**
- 仅模拟仓；migration 009 / 017

### 主探索 (`*_explore`)
- 每 **max_hold_hours**（距上次 ok）+ 10min 轮询；kill switch `*_explore_enabled`（多默认 0）
- SL **3%** / TP **5%** / **max_hold_hours** / 5x / 500U；conf≥**0.75** + `explore_catalyst_technical_ok`（含 15m OHLC）；**DeepSeek LONG** 另≥**0.82** + RSI/7d/24h/`deepseek_long_entry_quality_ok`
- **DeepSeek 选币**：仅 **L0+L1**（不扫未评级/全市场）
- **实盘同步**（`trading_gates.LIVE_SYNC_SOURCES`）：`deepseek_explore` / `deepseek_predict` / `brain_swing`（+ L0 白名单等 symbol 闸门）
- **Gemini 探索已下线**（系统配置无开关；不调度）

### 主预测 (`*_predict`)
- 每 **max_hold_hours**（距上次 ok）+ 5min 轮询 + `*_predict_next_due_utc`；kill switch
- 同主探索持仓/SL/TP/门槛；**DeepSeek 仅扫 L0+L1**；**实盘**：仅 `deepseek_predict`（+ L0）
- **DeepSeek soft-sl**（`position_sl_tp_monitor`）：grace 45min；no_follow≥60min 且亏≈2.2%（匹配开仓 thesis）
- **Gemini 预测已下线**（API / 页面 / worker 已移除）

### 超级大脑主权层（REQ-BRAIN）【需求 2026-07-28 · 首版已落地 · 对照期 · v2 Playbook 2026-07-30】
- 权威：`docs/REQUIREMENTS_LOGIC_ZH.md` §7.3（v4.5.23）
- `brain_swing`：L0/L1 模拟扫描；Playbook(A/B/C/D) 全量打标落库 `brain_opportunities`
- **实盘**：随 `live_trading_enabled` / `live_close_enabled`；**仅 L0 白名单**；成交瞬间同步，不回填历史仓
- 分向胜率 ≥55% 且比反方向高≥5pp；**跳过开仓顾问**；**强制防插针限价**（`BRAIN_USE_MARKET_ENTRY=False`），超时取消
- **入场（v4.5.22）**：LONG 等 15m 回踩（贴着近 8 根高点不挂）；**Big4 LONG 禁 A2/B2**；**B3/C4 高点滞涨挂空**（小仓 ×0.20）；**C1 破位跟风**（×0.35）；A2 弱反抽拒绝后开空
- **退出（v4.5.12 · 盈利 KPI）**：**A1 豁免 5m**；其它 5m（≥40U/4根）；**-80U**；硬 SL；trail；**soft 关**
- **执行隔离（v4.5.13）**：BRAIN 排除 SmartExit；限价成交前重跑安全闸门；模拟平仓事务行锁幂等
- 防插针：影>实体×2；频繁则平均插针限价；超时取消；**INV-BRAIN-06 已正式启用**
- **对照期**：DeepSeek 探索/预测自动开仓**暂保留**并行对比；结束后再执行 INV-BRAIN-07
- 调度：BRAIN **每15s** 一批5币；`validate_brain_req.py`；页 `/brain_strategy`


### 中线做多/做空 v2 (`midline_long` / `midline_short`)【需求 2026-07-24 · 已落地模拟】
- **量化扫描**，非 LLM；标的 `config.yaml`；**15min** 轮询；旧四路 `*_midline_*` 停并移除
- 限价：做多 A1 挂 **15m 回调区**；**C3/C1/B2 破位市价跟风**；B3/C4 顶部第一回调后市价空；A2 挂反抽区；C3/C1/B2/B3/C4 持仓 **4h** / A1/A2 **6h**；SL **6%** / TP **3%** / 5x / 500U
- **跳过**开仓顾问；**纳入**持仓顾问（**sell 只建议不执行**）；**midline_hold_exit** 默认峰≥1.2% 锁利，Big4 LONG 多单 2.5%/0.80% 且关闭 no_follow；**排除** SmartExit
- **暂不实盘**（不进 `LIVE_SYNC_SOURCES`）；Web：原 Gemini 探索页整页改破位策略/机会分析
- 权威：`docs/REQUIREMENTS_LOGIC_ZH.md` §7.2

### 开仓 / 持仓顾问
- 路由：统一 **DeepSeek**（Gemini 顾问已下线；历史 `gemini_*` 仓亦由 DeepSeek 监管）；中线/BRAIN 跳过开仓顾问、**纳入**持仓顾问（**sell 只建议不执行**）
- **BRAIN**：开仓跳过顾问；持仓进入 DeepSeek 做 thesis 复核（**sell 只建议不执行**；硬 SL/TP/trail 仍兜底）
- 持仓 tick：**15min**；每仓 15min；浮盈转亏 urgent 立即再审
- 盈利 sell（探索仓）：ROI **≥+8%** 且 15m 明确转弱（反向≥4）；过早 sell 程序化拦截恢复严格
- Prompt/rubric/**reason 中文**；开关 `deepseek_*_advisor_enabled`（系统设置「开仓/持仓顾问」）

### gemini_sentiment_analyzer (情绪)
- **已下线**（不调度；API / worker 已移除；`gemini_sentiment_enabled` 强制 0）

## 校验脚本 (无 API)

| 脚本 | 覆盖 |
|------|------|
| `validate_explore_predict_prompts.py` | 主探索/预测门槛与 prompt |
| `validate_tactical_explore_prompts.py` | 四战术 + 顶空底多 catalyst |
| `validate_open_advisor_rubrics.py` | 开仓/持仓顾问中文 rubric |
| `benchmark_*_prompt_lang.py` | 主/战术/顾问 prompt 中英对照（无 API） |
| `validate_tactical_explore_db.py` | 战术表结构 |
| `validate_brain_req.py` | **REQ-BRAIN** 静态回归（无 API） |
| `ai_win_rate_report.py` | **AI 按日胜率巡检** (见下) |

## AI 胜率 KPI (2026-05-31)

- **长期目标**: 各 AI `source` 胜率 **≥ 50%**（模拟仓 `account_id=2`, closed 单）
- **日检红线**: **每天**看 **当天**胜率; 当日 **< 40%** 且 closed **≥ 3 笔** → **必须优化**（提 conf / 收紧 catalyst / 调开仓顾问 rubric）
- **改完观察期**: 连续看 **7 天** 日胜率即可判断一轮改动是否有效
- **命令**: `python scripts/ai_win_rate_report.py`（近 7 天 UTC）; `--date YYYY-MM-DD` 查单日; 退出码 1 = 报表内有「必须优化」项

## 已下线策略 (2026-06-05)

- **市场预测器** (`PREDICTOR` / `market_predictor.py`) — 旧版 6h 预测神器
- 历史持仓 `source` 仍可在复盘页显示；不再新开仓

## 交易对评级系统（统一核心机制，2026-06-06）

此处 `update_top_performers.py` 统一管理，全仓累计（account_id=2 模拟仓已平仓，至少5笔）:

| 等级 | 条件 | 保证金倍数 |
|------|------|----------|
| L0 (白名单) | 盈利 >= 300U **且** 胜率 >= 40%，或盈利 >= 100U **且** 胜率 >= 45% | 1.0x |
| L1 (黑名单1级) | 盈利 > 50U **或** 胜率 > 46% | 0 (禁止实盘；模拟可) |
| L2 (黑名单2级) | -100 < 盈利 < 0 **或** 胜率 > 44% | 0 (模拟+实盘均禁止) |
| L3 (黑名单3级) | 盈利 < -100U **且** 胜率 < 44% | 0 (模拟+实盘均禁止) |

优先级判断逻辑: L3(双条件最严重)→L0(双条件最优)→L1→L2→默认0。

TOP50 盈利前50交易对由 `update_top_performers.py` 单独维护 `top_performing_symbols` 表。

**定时维护**: `scheduler.py` 每 4 小时统一执行 TOP50 更新 + 全量评级；`smart_trader_service.py` 也有每 4 小时兜底刷新。

**评级更新入口**: `POST /api/rating/update` / `POST /api/top50/refresh`

## 实盘控制

- **按 source 白名单**（`trading_gates.LIVE_SYNC_SOURCES`）：DeepSeek 探索/预测 + **BRAIN**；GPT/战术/反转/smart_trader/**中线 v2**/已下线 Gemini 只模拟
- **实盘开仓 symbol**：须 **L0 白名单**（`rating_level=0`）；L1/L2/L3 禁止实盘
- **限价偏移**：中线优先 **回调区**（无区才 ±1%）；其他模拟限价读 `paper_limit_long/short_offset_pct`（系统设定）
- **开仓总开关**: `system_settings.live_trading_enabled` (1=开启)
- **平仓总开关**: `system_settings.live_close_enabled` (1=开启；模拟平仓时同步交易所；持仓顾问 sell 亦受此规则)
- **北京时间实盘开仓时段**: 仅 10:00-16:00、22:00-次日04:00 允许同步/直接开实盘；服务器 UTC 对应 02:00-08:00、14:00-20:00。模拟开仓不受该时段限制。
- **废弃设置**（不再参与开仓判断）：`live_top50_required`、`live_whitelist_enabled` — 开仓仅认 **L0 白名单**
- **黑名单限制**: L1 禁止实盘；**L2/L3 与 `rating_locked` 手动锁定禁止模拟+实盘**（`check_symbol_trading_forbidden`，阈值 `rating_level>=2`）
- 各闸门由 `trading_gates.check_live_open_allowed` / `should_sync_live_for_source` 统一读取，**不在 config.yaml**
- 每账号 OPEN 上限 20 仓
- 允许实盘的策略通过 `_sync_live()` / `_sync_to_live()` 同步到 BinanceFuturesEngine

## 代码规范

- 配置: `dotenv_values` 读 .env **不读系统环境变量**
- 日志: loguru → logs/ 目录
- 语法检查: `python -c "import ast; ast.parse(...)"`
- 代码改动后必须重启对应进程 (Python 不热重载)
- Gemini 使用新版 SDK: `import google.genai as genai`
- 非必要不要对已有逻辑进行过度封装,不要过度抽象

## 进程间关系

- `smart_trader_service.py` (U本位) 负责主策略与多策略实盘
- `app/scheduler.py` (crypto-scheduler) 统一调度价格采集、Gemini 等
- `app/main.py` (FastAPI) 提供 Web 界面
- 核心进程需全部启动。某个挂了其他仍能运行，但对应功能会缺失
