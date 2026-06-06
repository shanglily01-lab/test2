# 超级大脑量化交易系统 — 当前上下文

更新时间：2026-06-06

完整系统文档见：`docs/SYSTEM_DOCUMENTATION.md`。

## 当前系统边界

活跃 AI 模块：

- Gemini / DeepSeek / GPT 主探索。
- Gemini / DeepSeek / GPT 主预测。
- Gemini / DeepSeek Big4 综合行情分析。
- Gemini 市场情绪分析。
- AI Shadow 对比。
- 开仓顾问与持仓顾问。

已下线，不再作为活跃策略描述：

- 顶空底多 / reversal explore。
- 回多反空。
- 追涨杀跌。
- pullback / rebound / chase / dump 战术探索。
- S 系列策略：S1、S5、S6、S9 等。
- 独立 Gemini Swan runs/verdicts 落库链路。

## 关键运行规则

- Web 入口：`app/main.py`。
- 调度入口：`app/scheduler.py`。
- 实盘闸门：`app/services/trading_gates.py`。
- 系统设置主要来自 `system_settings`，并通过 `data_cache.settings_cache` 缓存。
- Big4、熔断、AI 分析不得自动修改用户方向总开关。
- 代码修改后需要重启对应 Python 进程。

## 调度速查

- 主探索：每 4 小时 + 10 分钟轮询，worker 内 4 小时防重。
- 主预测：每 4 小时 + 5 分钟轮询，使用 `*_predict_next_due_utc` 防重。
- Big4 综合行情：Gemini / DeepSeek 每 4 小时 + 10 分钟轮询。
- Gemini 情绪：每 8 小时。
- data_cache：market snapshot、candidate pool、explore prepared、position stats、settings cache 按短周期刷新。

## 实盘同步

允许同步实盘的 source：

- `gemini_explore`
- `deepseek_explore`
- `gemini_predict`
- `deepseek_predict`

核心开关：

- `live_trading_enabled`
- `live_close_enabled`
- `live_top50_required`
- `live_whitelist_enabled`
- `blacklist_level3_enabled`

## 数据库清理边界

可以清理的旧表：

- 战术探索表：`{gemini,deepseek,gpt}_{pullback,rebound,chase,dump}_explore_runs` 和对应 verdicts。
- 反转探索表：`{gemini,deepseek,gpt}_reversal_explore_runs` 和对应 verdicts。
- 旧 Swan 表：`gemini_swan_runs`、`gemini_swan_verdicts`。

不要删除：

- `{gemini,deepseek,gpt}_explore_runs` / `*_explore_verdicts`。
- 主预测 runs/verdicts。
- `gemini_sentiment_runs`。

