# 超级大脑量化交易系统文档

更新时间：2026-06-06

本文档按当前代码状态重写，替代 `/docs` 下旧的策略、AI、开仓逻辑和上下文文档。已下线的战术探索、顶空底多、回多反空、追涨杀跌、S 系列策略不再作为活跃系统描述。

## 1. 系统定位

本项目是一个加密货币量化交易与 AI 决策辅助系统，核心能力包括：

- Binance U 本位合约模拟盘与实盘同步。
- 多源行情采集、K 线缓存、候选池预计算。
- Gemini / DeepSeek / GPT 主探索与主预测。
- Gemini / DeepSeek Big4 综合行情分析。
- Gemini 市场情绪分析。
- 开仓顾问、持仓顾问、AI 复盘与胜率统计。
- Web 管理后台、移动端页面、系统设置开关。

系统的设计原则是：所有实盘动作必须经过显式开关、source 白名单、标的闸门和数据库状态控制；AI 模块可以辅助发现机会，但不能绕过实盘风控。

## 2. 技术栈

| 层级 | 技术 |
| --- | --- |
| Web 服务 | FastAPI + Uvicorn |
| 页面 | Jinja2 templates + Tailwind CSS + 原生 JS |
| 数据库 | MySQL / MariaDB + pymysql |
| 调度 | `schedule` + 后台线程 |
| 交易所 | Binance Futures |
| AI | Gemini、DeepSeek、GPT |
| 配置 | `.env`、`config.yaml`、`system_settings` |
| 日志 | loguru，输出到 `logs/` |

主要入口：

- Web：`app/main.py`
- 调度器：`app/scheduler.py`
- 实盘交易引擎：`app/trading/binance_futures_engine.py`
- 系统设置缓存：`app/services/system_settings_loader.py`
- 实盘闸门：`app/services/trading_gates.py`

## 3. 核心进程

| 进程 / 模块 | 职责 |
| --- | --- |
| `app/main.py` | FastAPI Web、REST API、页面路由、部分监控服务初始化 |
| `app/scheduler.py` | 统一调度缓存刷新、AI 任务、Big4、情绪分析、实盘记录同步 |
| `smart_trader_service.py` | 主交易服务，负责传统自动交易链路 |
| collector / WS 服务 | 行情、K 线、资金费率等基础数据采集 |

注意：

- Python 代码不热重载，修改后需要重启对应进程。
- `schedule.every(N).hours` 在进程重启后会重新从 0 计时，所以 AI 任务通常还配有短周期轮询和 worker 内部防重。
- 不要让 Big4、熔断或 AI 模块自动改写用户的总方向开关，`allow_long` / `allow_short` / `trading_enabled` 应由用户控制。

## 4. 数据库与缓存

主要数据库：

- 主库：交易、持仓、AI runs/verdicts、系统设置。
- `data_cache`：行情快照、候选池、探索预计算、设置缓存等。

重要表类别：

| 类别 | 表 |
| --- | --- |
| 系统设置 | `system_settings` |
| 模拟合约持仓 | `futures_positions` |
| 实盘持仓 | `live_futures_positions` |
| 主探索 | `gemini_explore_runs` / `gemini_explore_verdicts`，`deepseek_explore_runs` / `deepseek_explore_verdicts`，`gpt_explore_runs` / `gpt_explore_verdicts` |
| 主预测 | Gemini / DeepSeek / GPT predict runs/verdicts，对应 predictor 模块维护 |
| Big4 量化趋势 | `big4_trend_history` |
| Big4 LLM 分析 | Gemini / DeepSeek Big4 analysis API 使用的分析表 |
| Gemini 情绪 | `gemini_sentiment_runs` |
| 顾问复盘 | `gemini_advisor_reviews`、`deepseek_advisor_reviews`、`gpt_advisor_reviews` |

已下线后可清理的旧表：

- `{gemini,deepseek,gpt}_pullback_explore_runs`
- `{gemini,deepseek,gpt}_pullback_explore_verdicts`
- `{gemini,deepseek,gpt}_rebound_explore_runs`
- `{gemini,deepseek,gpt}_rebound_explore_verdicts`
- `{gemini,deepseek,gpt}_chase_explore_runs`
- `{gemini,deepseek,gpt}_chase_explore_verdicts`
- `{gemini,deepseek,gpt}_dump_explore_runs`
- `{gemini,deepseek,gpt}_dump_explore_verdicts`
- `{gemini,deepseek,gpt}_reversal_explore_runs`
- `{gemini,deepseek,gpt}_reversal_explore_verdicts`
- `gemini_swan_runs`
- `gemini_swan_verdicts`

不要删除：

- `gemini_sentiment_runs`：Gemini 情绪分析仍在调度器、API 和 worker 中使用。
- `{gemini,deepseek,gpt}_explore_runs` / `*_explore_verdicts`：主探索仍在使用。

## 5. 当前活跃 AI 模块

### 5.1 主探索

活跃 source：

- `gemini_explore`
- `deepseek_explore`
- `gpt_explore`

主要文件：

- `app/services/gemini_explore_worker.py`
- `app/services/deepseek_explore_worker.py`
- `app/services/gpt_explore_worker.py`
- `app/services/ai_explore_prompt.py`
- `app/api/gemini_explore_api.py`
- `app/api/deepseek_explore_api.py`
- `app/api/gpt_explore_api.py`

调度：

- 每 4 小时执行一次。
- 每 10 分钟做一次到期轮询。
- worker 内部做 4 小时防重。

页面：

- `/gemini_explore`
- `/deepseek_explore`
- `/gpt_explore`

主探索会基于候选池、K 线结构、量化指标和 AI catalyst 生成模拟开仓建议。Gemini / DeepSeek 探索允许同步实盘，GPT 探索仅模拟。

### 5.2 主预测

活跃 source：

- `gemini_predict`
- `deepseek_predict`
- `gpt_predict`

主要文件：

- `app/services/gemini_predictor.py`
- `app/services/deepseek_predictor.py`
- `app/services/gpt_predictor.py`
- `app/services/ai_predict_prompt.py`
- `app/api/gemini_predict_api.py`
- `app/api/deepseek_predict_api.py`
- `app/api/gpt_predict_api.py`

调度：

- 每 4 小时周期任务。
- 每 5 分钟读取 `*_predict_next_due_utc` 做到期轮询。
- 启动时延迟补跑检查，避免进程重启后漏掉周期。

Gemini / DeepSeek 预测允许同步实盘，GPT 预测仅模拟。

### 5.3 Big4 综合行情分析

覆盖币种：

- BTC
- ETH
- BNB
- SOL

主要文件：

- `app/services/big4_comprehensive_analyzer.py`
- `app/services/ai_big4_prompt.py`
- `app/api/big4_analysis_api.py`
- `static/js/big4_analysis_tab.js`

调度：

- Gemini Big4：每 4 小时 + 10 分钟轮询。
- DeepSeek Big4：每 4 小时 + 10 分钟轮询。
- worker 内部 `INTERVAL_HOURS = 4` 防重。

Big4 综合行情分析只提供宏观市场判断，不是直接开仓或平仓指令。

### 5.4 Gemini 市场情绪分析

主要文件：

- `app/services/gemini_sentiment_analyzer.py`
- `app/api/gemini_sentiment_api.py`

调度：

- 每 8 小时执行一次。
- kill switch：`system_settings.gemini_sentiment_enabled`，默认开启。

数据表：

- `gemini_sentiment_runs`

该模块只做市场情绪和事件影响分析，不直接下单。

### 5.5 AI Shadow

主要文件：

- `app/services/ai_shadow_explore.py`
- `app/api/ai_shadow_api.py`
- `templates/ai_shadow_compare.html`

用途：

- 对比不同 AI 模型或策略输出。
- 做影子评估和历史效果观察。

## 6. 已下线模块

以下模块已从当前活跃系统中移除，不应在新文档、页面或调度里继续作为活跃策略描述：

- 顶空底多 / reversal explore。
- 回多反空。
- 追涨杀跌。
- 战术四策略：pullback、rebound、chase、dump 的探索 worker / scheduler / API。
- S 系列策略：S1、S5、S6、S9 等。
- 旧 `gemini_swan_runs` / `gemini_swan_verdicts` 独立红黑天鹅榜落库链路。

仍可能存在少量历史 source 兼容显示逻辑。它们只用于旧持仓或旧记录展示，不代表策略仍会新开仓。

## 7. 开仓与实盘同步

实盘同步由 `app/services/trading_gates.py` 控制。

允许同步实盘的 source：

- `gemini_explore`
- `deepseek_explore`
- `gemini_predict`
- `deepseek_predict`

不允许同步实盘的 source：

- `gpt_explore`
- `gpt_predict`
- `smart_trader` 之外的未列入 source
- 历史战术、反转、S 系列 source

核心开关：

| 设置 | 作用 |
| --- | --- |
| `live_trading_enabled` | 实盘开仓总开关 |
| `live_close_enabled` | 模拟平仓时是否同步平交易所仓位 |
| `live_top50_required` | TOP50 标的实盘闸门 |
| `live_whitelist_enabled` | 白名单标的实盘闸门 |
| `blacklist_level3_enabled` | L3 黑名单禁止开仓 |

开仓同步必须同时满足：

1. `live_trading_enabled=1`
2. source 在实盘白名单中
3. 标的满足 TOP50 或白名单闸门
4. 未被 L3 黑名单拦截
5. 交易引擎和账户状态正常

平仓同步由 `live_close_enabled` 控制。关闭该开关时，模拟盘平仓不会自动平交易所仓位。

## 8. 开仓顾问与持仓顾问

主要文件：

- `app/services/open_advisor_routing.py`
- `app/services/open_advisor_strategy_rubrics.py`
- `app/services/gemini_position_advisor.py`
- `app/services/deepseek_position_advisor.py`
- `app/services/gpt_position_advisor.py`
- `app/services/gemini_advisor_reviews.py`
- `app/services/deepseek_advisor_reviews.py`
- `app/services/gpt_advisor_reviews.py`
- `app/api/gemini_advisor_api.py`

开仓顾问路由：

- `gemini_*`：Gemini 开仓顾问。
- `gpt_*`：Gemini 开仓顾问。
- `deepseek_*`：DeepSeek 开仓顾问。
- 其它 source：Gemini + DeepSeek 双审。

持仓顾问路由：

- `deepseek_*`：DeepSeek 持仓顾问。
- 其它非 DeepSeek source：Gemini 持仓顾问。
- GPT 持仓顾问当前不作为独立持仓监管入口。

顾问的职责：

- 审核开仓 catalyst 与方向是否一致。
- 检查 Big4、方向闸门、K 线结构和专属 rubric。
- 对持仓提出 hold / close 等建议。
- 记录 review，供复盘页面查看。

## 9. 调度器概览

`app/scheduler.py` 是当前统一调度核心。

| 任务 | 频率 |
| --- | --- |
| market snapshot | 约每 1 分钟 |
| market movers | 约每 5 分钟 |
| candidate pool | 约每 6 分钟 |
| explore prepared data | 约每 15 分钟 |
| position stats | 约每 30 分钟 |
| settings cache | 约每 1 分钟 |
| Gemini 探索 | 每 4 小时 + 10 分钟轮询 |
| DeepSeek 探索 | 每 4 小时 + 10 分钟轮询 |
| GPT 探索 | 每 4 小时 + 10 分钟轮询 |
| Gemini 预测 | 每 4 小时 + 5 分钟轮询 |
| DeepSeek 预测 | 每 4 小时 + 5 分钟轮询 |
| GPT 预测 | 每 4 小时 + 5 分钟轮询 |
| Gemini Big4 | 每 4 小时 + 10 分钟轮询 |
| DeepSeek Big4 | 每 4 小时 + 10 分钟轮询 |
| Gemini 情绪 | 每 8 小时 |
| 实盘记录校正 | 每 15 分钟 |
| 调度器状态打印 | 每 1 小时 |

启动错峰：

- Gemini 探索：约 15 秒后。
- Gemini 情绪：约 25 秒后。
- Gemini Big4：约 28 秒后。
- Gemini 预测补跑：约 45 秒后。
- DeepSeek 预测补跑：约 50 秒后。
- GPT 预测补跑：约 55 秒后。
- DeepSeek 探索：约 90 秒后。
- DeepSeek Big4：约 95 秒后。
- GPT 探索：约 120 秒后。

## 10. Web 页面与 API

主要页面：

| 路径 | 页面 |
| --- | --- |
| `/` | 首页 |
| `/system-settings` | 系统设置 |
| `/futures_trading` | 合约交易页面 |
| `/live_trading` | 实盘交易页面 |
| `/futures_review` | 合约复盘 |
| `/gemini_explore` | Gemini 探索 / 预测 / Big4 |
| `/deepseek_explore` | DeepSeek 探索 / 预测 / Big4 |
| `/gpt_explore` | GPT 探索 / 预测 |
| `/ai_shadow_compare` | AI Shadow 对比 |
| `/gemini_advisor_reviews` | 顾问 review |
| `/market_regime` | 市场状态 |
| `/technical-signals` | 技术信号 |

主要 API router：

- `app/api/gemini_explore_api.py`
- `app/api/deepseek_explore_api.py`
- `app/api/gpt_explore_api.py`
- `app/api/gemini_predict_api.py`
- `app/api/deepseek_predict_api.py`
- `app/api/gpt_predict_api.py`
- `app/api/big4_analysis_api.py`
- `app/api/gemini_sentiment_api.py`
- `app/api/ai_shadow_api.py`
- `app/api/gemini_advisor_api.py`
- `app/api/system_settings_api.py`
- `app/api/live_trading_api.py`
- `app/api/futures_trading_api.py`
- `app/api/data_cache_api.py`

## 11. Prompt 与语言

当前生产 prompt 以中文为主：

- 主探索：`app/services/ai_explore_prompt.py`
- 主预测：`app/services/ai_predict_prompt.py`
- Big4：`app/services/ai_big4_prompt.py`
- 开仓顾问：`app/services/open_advisor_strategy_rubrics.py`
- 持仓顾问：各 `*_position_advisor.py`

要求：

- 输出原因应可读、简短、可复盘。
- 不使用已下线战术标准审核主探索或预测。
- 不用单一 RSI、单根 K 线或纯 24h 涨跌幅作为完整开仓理由。
- catalyst 应包含多周期 K 线结构、方向自洽和风险说明。

## 12. 运行与排查

常用检查：

```powershell
python -m py_compile app\scheduler.py app\main.py
python -m py_compile app\services\gemini_explore_worker.py app\services\deepseek_explore_worker.py app\services\gpt_explore_worker.py
```

关键词扫描：

```powershell
rg -n "reversal|pullback_explore|rebound_explore|chase_explore|dump_explore|s9_gemini_ai|s1_early_long|s5_large_oversold|s6_vol_spike" app templates static docs
```

日志位置：

- `logs/scheduler_YYYY-MM-DD.log`
- Web / API 日志按当前启动方式输出。

排查原则：

- 探索提示“距上次成功不足 4h”通常是正常防重。
- 预测提示“未到 next_due”通常是正常防重。
- “上一轮还未结束”长时间持续才需要排查锁、线程或慢查询。
- 1h K 线天然滞后，最近完整 1h K 线通常是上一小时。

## 13. 维护规则

1. 删除策略时要同时清理页面入口、API router、scheduler、worker、prompt、展示名、文档和数据库旧表说明。
2. 新增实盘 source 必须显式加入 `LIVE_SYNC_SOURCES`，并确认开仓和平仓闸门。
3. 新增系统开关应写入 `system_settings`，并确保 `settings_cache` 能同步。
4. AI worker 必须有防重机制，避免调度器重启后重复开仓。
5. 任何自动风控模块不得擅自修改用户方向总开关。
6. 大表查询应优先读 `data_cache`，避免 AI 线程回退到慢路径。
7. 文档以本文为准；旧设计文档如仍存在于 `design/`，只作为历史参考。

## 14. 当前系统边界

当前保留并维护：

- 主探索：Gemini / DeepSeek / GPT。
- 主预测：Gemini / DeepSeek / GPT。
- Big4 综合行情：Gemini / DeepSeek。
- Gemini 市场情绪。
- AI Shadow。
- 开仓顾问、持仓顾问。
- 实盘同步白名单：Gemini / DeepSeek 探索与预测。

当前不再维护为活跃策略：

- 顶空底多。
- 回多反空。
- 追涨杀跌。
- pullback / rebound / chase / dump 战术探索。
- S 系列策略。
- 独立 Gemini Swan runs/verdicts 落库链路。

