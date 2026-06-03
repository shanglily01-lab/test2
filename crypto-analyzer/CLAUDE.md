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

辅助进程: fast_collector / ws_kline_collector / multi_strategy 等

## 数据库

- `binance-data`: 主数据库 (100+ 表)
- `data_cache`: 预计算缓存库 (v3.5 新增,5 张缓存表)
- 连接通过 .env 配置,需显式在服务启动时连接

## 重要规则

- **Big4RegimeMonitor / 熔断禁止改系统设置**: Big4 和盈亏熔断只能发通知，不允许修改 `system_settings` 的 `allow_long`/`allow_short`/`trading_enabled`。用户手动控制。
- **1h K线天然滞后**: 1h 最新收盘的 K 线永远是上一小时的，延迟约 60-65 分钟属正常。`BACKFILL_LAG_THRESHOLD_S['1h']=3900` (65min)，不要改小。
- **WS K线 backfill exchange name**: `_check_and_backfill` 中 exchange 必须用 `binance_futures`（匹配 `save_klines` 的存储名），不能用 `usdt_futures`。
- **关模拟仓不会自动同步实盘**: 没有后台监听服务。需要通过 `BinanceFuturesEngine.close_position_direct()` 主动在交易所平仓。
- **除 crypto-scheduler 外不要加心跳日志**: WS 采集器日志中有 `send_heartbeat` 日志是正常的 (每 20s)，不要把它当作 bug 去修。

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
| Gemini 探索 | 每 **4h** + **10min 轮询** (worker 内 4h 防重) |
| Gemini 预测 | 每 **4h 必跑** + **5min 轮询** (`system_settings.*_predict_next_due_utc`) |
| DeepSeek 探索 | 每 **4h** + **10min 轮询** |
| DeepSeek 预测 | 每 **4h 必跑** + **5min 轮询** (`deepseek_predict_next_due_utc`) |
| GPT 探索/预测 | 同 Gemini/DeepSeek 节奏 + `gpt_*_next_due_utc` |
| 战术探索 15 槽位 | 每 **15min** 轮询 (`tactical_explore_scheduler`) |
| 市场情绪分析 | 每 **8 小时** |
| ETF 同步 | 每天 06:45 |
| 金库同步 | 每天 07:30 |

### scheduler AI 运维备忘 (2026-05-31)

**生产 DB**: MariaDB 10.5（非 MySQL 8）；`INSERT ... ON DUPLICATE KEY UPDATE` 可用。

**探索/预测别混淆「延时」**:
- 正常: 探索 `上次成功距今 Xh < 4h` / 预测 `未到点 剩余 Xh (next_due=...)` — 防重，不是 scheduler 坏了。
- 异常: `上一轮还未结束` 持续 >15min — 探索线程卡死或占锁（常因回退 `kline_data` 多层 JOIN）。
- `schedule.every(N).hours` 在 **restart 后从 0 计时**；靠 **10min 轮询 + worker DB 防重** 补位。勿频繁 `systemctl restart crypto-scheduler`。

**candidate_pool_snapshot** (`data_cache_service.refresh_candidate_pool`):
- **禁止** refresh 开头 `DELETE` 全表（会导致探索读空表 → 慢路径）。已改为 **UPSERT + 本轮结束后删下架币**。
- 探索读缓存: `load_candidate_pool_for_explore()`；`get_candidate_pool` 默认**不再**限制 `change_24h` 0–100%。
- K 线叙事: **1h = 整体 24 根趋势 + 近 6 根明细** (`_make_kline_narrative`)

**主探索/预测 prompt 与门槛** (`ai_explore_prompt.py`, commit `162d8d5b`):
- LLM 候选池: `prepare_universe_for_llm` **技术面评分 TOP50**，非 |24h| 极端排序
- 开仓置信度: `EXPLORE_CONFIDENCE_THRESHOLD` / `PREDICT_CONFIDENCE_THRESHOLD` = **0.60**（与 prompt 校准表一致）
- catalyst 硬门槛: `explore_catalyst_technical_ok`（多周期 K 线 + 量化技术位；禁单根 1h / 纯涨跌幅）
- 预测同样走 catalyst gate → `skipped_weak_catalyst`
- 回归: `scripts/validate_explore_predict_prompts.py`

**gemini_explore_runs.status**: ENUM `ok` / `partial` / `error` / `skipped` — **无 `running`**。进行中登记用 **`partial`**，结束 `_finish_run` 改为 `ok`/`skipped`/`error`。`status='running'` 会 1265 Data truncated。

**预测 4h 保证**: 每 5min 轮询 + `gemini_predict_next_due_utc` / `deepseek_predict_next_due_utc` 认领窗口；启动后 +45s/+50s 补跑检查。勿用 `scheduler_init` 跑预测。进程锁在跳过时会释放。

**启动 init 错峰** (`scheduler_init`): Gemini探索 +15s, 情绪 +25s, DeepSeek探索 +90s, GPT探索 +120s；预测补跑 +45s/+50s/+55s。

**生产 LLM prompt 语言 (2026-06)**: 主探索/预测/战术/反转/开仓顾问/持仓顾问 **英文**（`build_*_prompt()` → `*_en`）；顾问 `reason` 英文。完整说明见 `docs/AI_STRATEGIES_AND_ADVISORS_ZH.md` / `AI_STRATEGIES_AND_ADVISORS_EN.md`。

**日志** (非 journalctl): `logs/scheduler_YYYY-MM-DD.log`；排查:
`grep -E "一轮开始|一轮结束|candidate_pool_snapshot 读取|回退 kline|上一轮还未结束|skipped_weak_catalyst" logs/scheduler_$(date -u +%Y-%m-%d).log`

**相关 commits**: `211990b` (UPSERT+探索读缓存+锁), `15bdfc0` (partial), `e0feb1e9` (探索 4h), `162d8d5b` (主探索/预测 prompt+门槛).

详见 `.cursor/rules/scheduler-ai-ops.mdc` 与 `.cursor/rules/ai-strategies-advisors.mdc`。

## AI 策略与顾问（完整文档）

- 中文: `docs/AI_STRATEGIES_AND_ADVISORS_ZH.md`
- English: `docs/AI_STRATEGIES_AND_ADVISORS_EN.md`

以下为速查；细节以文档为准。

## Gemini / DeepSeek / GPT AI 模块

### 顶空底多 (`*_reversal`)
- 无 kill switch；`top_reversal`→SHORT / `bottom_reversal`→LONG；SL **3%** / TP **5%** / 4h / 5x / 500U；conf≥**0.65**
- 仅模拟仓；表 `*_reversal_explore_runs`（migration 008）

### 战术四策略 + 三教师 (`*_pullback|rebound|chase|dump`)
- **15 任务**（5 策略 × 3 教师），`tactical_explore_scheduler` 4h + **15min** 轮询；共用 `explore_prepared_snapshot`
- 固定方向：pullback/chase→LONG，rebound/dump→SHORT；SL **4%** / TP **6%**；conf≥**0.55**
- 仅模拟仓；migration 009 / 017

### 主探索 (`*_explore`)
- 每 **4h** + 10min 轮询；kill switch `*_explore_enabled`（多默认 0）
- SL **4%** / TP **6%** / **4h** / 5x / 500U；conf≥**0.60** + `explore_catalyst_technical_ok`
- **实盘同步**（`trading_gates.LIVE_SYNC_SOURCES`）：仅 `gemini_explore`；DeepSeek/GPT 探索仅模拟

### 主预测 (`*_predict`)
- 每 **4h** + 5min 轮询 + `*_predict_next_due_utc`；kill switch（Gemini 预测默认 1）
- 同主探索持仓/SL/TP/门槛；**实盘**：`gemini_predict`、`deepseek_predict`；GPT 预测仅模拟

### 开仓 / 持仓顾问
- **开仓**: `paper_open_gate` — `gemini_*`→Gemini；`deepseek_*`→DeepSeek；`gpt_*`→GPT；**其它→Gemini+DeepSeek 双审**
- **持仓**: Gemini 管非 deepseek/gpt；DeepSeek 管 `deepseek_*`；GPT 管 `gpt_*`；`smart_trader` 每 15min tick
- Prompt/rubric/**reason 生产环境为英文**；开关 `*_open_advisor_enabled` / `*_position_advisor_enabled`
- 表 `gemini/deepseek/gpt_advisor_reviews`；Web `/gemini-advisor-reviews`

### gemini_sentiment_analyzer (情绪)
- 每 **8h**；`gemini_sentiment_enabled`（默认 1）；不下单

## 校验脚本 (无 API)

| 脚本 | 覆盖 |
|------|------|
| `validate_explore_predict_prompts.py` | 主探索/预测门槛与 prompt |
| `validate_tactical_explore_prompts.py` | 四战术 + 顶空底多 catalyst |
| `validate_open_advisor_rubrics.py` | 开仓/持仓顾问英文 rubric |
| `benchmark_*_prompt_lang.py` | 主/战术/顾问 prompt 中英对照（无 API） |
| `validate_tactical_explore_db.py` | 战术表结构 |
| `ai_win_rate_report.py` | **AI 按日胜率巡检** (见下) |

## AI 胜率 KPI (2026-05-31)

- **长期目标**: 各 AI `source` 胜率 **≥ 50%**（模拟仓 `account_id=2`, closed 单）
- **日检红线**: **每天**看 **当天**胜率; 当日 **< 40%** 且 closed **≥ 3 笔** → **必须优化**（提 conf / 收紧 catalyst / 调开仓顾问 rubric）
- **改完观察期**: 连续看 **7 天** 日胜率即可判断一轮改动是否有效
- **命令**: `python scripts/ai_win_rate_report.py`（近 7 天 UTC）; `--date YYYY-MM-DD` 查单日; 退出码 1 = 报表内有「必须优化」项

## 多策略服务 (multi_strategy_service)

保留策略:
- **S1** 早期做多 (5x, RSI+MA20)
- **S5** 大币超卖 (5x, BTC/ETH/BNB/SOL/XRP, RSI<32)
- **S6** 小币量能异动 (5x, 量能先行)
- **S9** Gemini AI 抄底反转 (5x, LONG-only, 每6h)

已清理策略 (2026-05-28, 从未有效开仓, 移除代码+系统设置控件):
- S2 回调做多 / S3 顶部做空 / S4 反弹做空 / S7 MA支撑 / S8 顶部反转做空
- `run_fast()` 方法已删除, `run_slow()` 只调 S1/S5/S6/S9

## 实盘控制

- **按 source 白名单**（`app/services/trading_gates.py`）：仅 `gemini_explore`、`gemini_predict`、`deepseek_predict` 可开/平实盘；其余策略（S1/S5/S6/S9、战术、反转、GPT、smart_trader 等）只写模拟仓
- **开仓总开关**: `system_settings.live_trading_enabled` (1=开启)
- **平仓总开关**: `system_settings.live_close_enabled` (1=开启；模拟平仓时同步交易所；持仓顾问 sell 亦受此规则)
- **TOP50 实仓闸门**: `system_settings.live_top50_required` (默认 1，TOP50 内可开实仓)
- **白名单实仓闸门**: `system_settings.live_whitelist_enabled` (默认 1，rating_level=0 可开实仓)
- 两者为 **或** 关系；**都关** 则即使 `live_trading_enabled=1` 也不同步实盘
- **黑名单3级**: `system_settings.blacklist_level3_enabled` (默认 1，关闭后 L3 可开仓)
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
