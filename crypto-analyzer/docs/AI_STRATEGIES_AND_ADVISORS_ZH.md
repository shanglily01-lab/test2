# AI 策略与顾问 — 完整说明（中文）

> 文档版本：2026-06-05 · 与生产代码对齐（主探索/预测/战术/反转/顾问 **LLM prompt 为中文**；实盘按 `LIVE_SYNC_SOURCES` 三策略 + TOP50/白名单开仓闸）

## 1. 总览

系统在三套 **教师模型**（Gemini、DeepSeek、GPT）上运行多类 **AI 策略**，统一走 **模拟仓**（`futures_positions.account_id=2`）。开仓前经 **开仓顾问** 审查；持仓满 15 分钟后由 **持仓顾问** 每 15 分钟监管。

```text
crypto-scheduler (app/scheduler.py)
  ├─ data_cache: candidate_pool (6min) → explore_prepared (15min)
  ├─ 主探索 ×3 / 主预测 ×3
  ├─ 战术探索 15 槽位 (5策略×3教师) + 15min 轮询
  └─ Gemini 情绪 (8h)

crypto-scheduler (每 15min)
  └─ Gemini + DeepSeek 持仓顾问 tick

任意模拟开仓
  └─ paper_open_gate.gate_simulated_open()
```

| 类别 | 是否 LLM | 典型 source 前缀 | 实盘同步 |
|------|----------|------------------|----------|
| 主探索 | 是 | `gemini_explore` / `deepseek_explore` / `gpt_explore` | 仅 **`deepseek_explore`**（+ 开仓 TOP50/白名单）；Gemini/GPT 仅模拟 |
| 主预测 | 是 | `*_predict` | **`gemini_predict`、`deepseek_predict`**（GPT 仅模拟） |
| 顶空底多 | 是 | `*_reversal` | 否 |
| 战术四策略 | 是 | `*_pullback` 等 | 否 |
| 开仓/持仓顾问 | 是 | 按 source 路由 | 持仓 sell：`live_close_enabled=1` 且有 `paper_position_id` 绑定时平交易所 |
| 情绪分析 | 是 | 不下单 | — |

---

## 2. 共用数据与 Prompt 约定

### 2.1 数据缓存

| 缓存表 | 刷新频率 | 用途 |
|--------|----------|------|
| `data_cache.candidate_pool_snapshot` | 6 分钟 | 全市场候选 + 1h/15m K 线叙事（24 根趋势 + 近 6 根明细） |
| `data_cache.explore_prepared_snapshot` | 15 分钟 | `explore_prepared_bundle`：universe + global_ctx，主探索/战术/反转 **只读** |
| `data_cache.settings_cache` | 1 分钟 | 方向闸门 `allow_long` / `allow_short` 等 |

**禁止** `refresh_candidate_pool` 开头全表 `DELETE`（已改为 UPSERT）。探索应使用 `load_candidate_pool_for_explore()`，避免回退 `kline_data` 慢查询占锁。

### 2.2 生产 Prompt 语言（2026-06-05 已回滚中文）

| 模块 | 生产入口 | 说明 |
|------|----------|------|
| 主探索 | `build_explore_prompt()` → `build_explore_prompt_zh()` | `ai_explore_prompt.py` |
| 主预测 | `build_predict_prompt()` → `build_predict_prompt_zh()` | `ai_predict_prompt.py` |
| 战术四策略 | `build_strategy_prompt()` → `build_strategy_prompt_zh()` | `ai_tactical_explore_prompts.py` |
| 顶空底多 | `build_reversal_explore_prompt()` → 中文模板 | `ai_reversal_explore_prompt.py` |
| 开仓顾问 | `build_open_advisor_prompt()` | 中文 rubric（三教师共用） |
| 持仓顾问 | `GeminiPositionAdvisor._build_prompt()` | 中文；DeepSeek/GPT 继承 |
| GPT system | `GPT_JSON_SYSTEM_ZH` | `gpt_*_worker` / 战术 GPT |

A/B 对照仍可用 `*_en()` 与 `scripts/benchmark_*_prompt_lang.py`。

### 2.3 共用硬门槛

- **主探索/预测**：`explore_catalyst_technical_ok` + 置信度 ≥ **0.60**（`EXPLORE_CONFIDENCE_THRESHOLD` / `PREDICT_CONFIDENCE_THRESHOLD`）。
- **战术四策略**：`tactical_catalyst_ok` + 置信度 ≥ **0.55**。
- **顶空底多**：`reversal_catalyst_technical_ok` + 置信度 ≥ **0.65**。
- LLM 候选池：技术面评分 **TOP50**（`prepare_universe_for_llm`），非按 24h 涨跌幅排序。

---

## 3. 主探索（Gemini / DeepSeek / GPT）

### 3.1 职责

每 4 小时从候选池挑选事件/结构型机会，输出 LONG/SHORT，通过闸门后开模拟单。

### 3.2 代码入口

| 教师 | Worker | 调度 |
|------|--------|------|
| Gemini | `gemini_explore_worker.run_explore_round` | `every(4).hours` + `every(10).minutes` |
| DeepSeek | `deepseek_explore_worker.run_explore_round` | 同上 |
| GPT | `gpt_explore_worker.run_explore_round` | 同上 + `gpt_explore_next_due_utc` |

启动错峰（`scheduler_init`）：Gemini +15s，DeepSeek +90s，GPT +120s。

### 3.3 防重与状态

- 距上次 `status='ok'` **≥ 4h** 才跑（`manual` 可绕过）。
- 进行中：`gemini_explore_runs.status='partial'`（**勿写 `running`**，ENUM 会截断）。
- 进程锁：上一轮未结束则跳过（异常若持续 >15min 需排查）。

### 3.4 模拟单参数

| 项 | 值 |
|----|-----|
| 保证金 | 500 U |
| 杠杆 | 5x |
| 计划持仓 | **4h**（`AI_POSITION_HOLD_HOURS`） |
| SL / TP | **4% / 6%** |
| source | `gemini_explore` / `deepseek_explore` / `gpt_explore` |

### 3.5 Kill Switch（`system_settings`）

| Key | 典型默认 |
|-----|----------|
| `gemini_explore_enabled` | 0 |
| `deepseek_explore_enabled` | 0 |
| `gpt_explore_enabled` | 0 |

### 3.6 实盘

- **`deepseek_explore`**：`check_live_open_allowed()` 通过后 `_sync_to_live()`（见 §11）。
- **Gemini / GPT 探索**：仅模拟。

### 3.7 数据表

`{gemini,deepseek,gpt}_explore_runs` / `*_explore_verdicts`

### 3.8 Web / API

- 页面：`templates/gemini_explore.html`、`deepseek_explore.html`、`gpt_explore.html`
- 手动：`POST /api/{gemini,deepseek,gpt}-explore/run-now`

---

## 4. 主预测（Gemini / DeepSeek / GPT）

### 4.1 职责

每 4 小时对 TOP50 给出 4h 方向概率（bullish/bearish），达标则开模拟单；**不走探索的事件叙事**，但共用 catalyst 技术门槛。

### 4.2 代码入口

| 教师 | Worker | 调度 |
|------|--------|------|
| Gemini | `gemini_predictor.run_predict_round` | `every(4).hours` + `every(5).minutes` + `gemini_predict_next_due_utc` |
| DeepSeek | `deepseek_predictor.run_predict_round` | `deepseek_predict_next_due_utc` |
| GPT | `gpt_predictor.run_predict_round` | `gpt_predict_next_due_utc` |

启动补跑：+45s / +50s / +55s（**勿用 scheduler_init 跑预测**）。

### 4.3 参数与门槛

与主探索相同：**4h 持仓、SL 4%、TP 6%、5x、conf≥0.60、catalyst gate**。弱 catalyst → `skipped_weak_catalyst`。

### 4.4 Kill Switch

| Key | 典型默认 |
|-----|----------|
| `gemini_predict_enabled` | 1 |
| `deepseek_predict_enabled` | 0 |
| `gpt_predict_enabled` | 0 |

### 4.5 实盘

- **`gemini_predict`、`deepseek_predict`**：`_sync_to_live()` + `check_live_open_allowed`（TOP50/白名单）。
- **GPT 预测**：仅模拟。

### 4.6 数据表

`{gemini,deepseek,gpt}_predict_runs` / `*_predict_verdicts`

---

## 5. 顶空底多（反转探索）

### 5.1 职责

识别 **顶部反转做空**（`top_reversal` → SHORT）与 **底部反转做多**（`bottom_reversal` → LONG）。

### 5.2 代码入口

- Worker：`gemini_reversal_explore_worker` / `deepseek_reversal_explore_worker` / `gpt_reversal_explore_worker`
- 共用 runner：`reversal_explore_runner.run_reversal_explore_round`
- Prompt：`ai_reversal_explore_prompt.py`（生产 EN）

### 5.3 调度

纳入 `tactical_explore_scheduler` 的 **15 个任务** 中的反转槽位；**4h 周期 + 15min 轮询**认领 `tactical_{source}_next_due_utc`。**无独立 kill switch**。

### 5.4 参数

| 项 | 值 |
|----|-----|
| SL / TP | **3% / 5%**（`REVERSAL_SL_PCT` / `REVERSAL_TP_PCT`） |
| 持仓 | 4h |
| 置信度 | ≥ **0.65** |

### 5.5 数据表

`{gemini,deepseek,gpt}_reversal_explore_runs` / `*_verdicts`（migration 008）

---

## 6. 战术四策略探索

### 6.1 策略与固定方向

| profile | 中文名 | 方向 |
|---------|--------|------|
| `pullback` | 回调做多 | LONG |
| `chase` | 追涨做多 | LONG |
| `rebound` | 反弹做空 | SHORT |
| `dump` | 杀跌做空 | SHORT |

### 6.2 代码入口

- `tactical_explore_workers.py`：四策略 × 三教师 → `run_tactical_explore_round`
- 调度：`tactical_explore_scheduler.py`（**15 任务**：5 策略 × 3 教师）
- Prompt：`ai_tactical_explore_prompts.py`（生产 EN）

### 6.3 参数

| 项 | 值 |
|----|-----|
| 置信度 | ≥ **0.55** |
| SL / TP | 默认 **4% / 6%**（`ReversalExploreConfig` 默认，与主探索一致） |
| 持仓 | 4h |
| GPT 每轮 entry 数 | 2–3（`GPT_TACTICAL_MIN/MAX_ENTRIES`） |

代码层预检与 prompt 对齐：追涨 RSI≤68、`below_7d_high` 距离；回调 RSI、7d 高等（见 `tactical_catalyst_ok`）。

### 6.4 数据表

`{teacher}_{pullback,rebound,chase,dump}_explore_runs` / `*_verdicts`（migration 009、017）

---

## 7. 开仓顾问（Open Advisor）

### 7.1 流程

```text
gate_simulated_open (paper_open_gate.py)
  → resolve_open_advisors(source)
  → 按序调用各教师 review_open
  → 任一 reject 则不开仓；API 异常则降级放行
```

### 7.2 路由（`open_advisor_routing.py`）

| source 模式 | 审查方 |
|-------------|--------|
| `gemini_explore` / `gemini_predict` | 仅 Gemini |
| 其他 source | 仅 DeepSeek |

### 7.3 审查步骤（`open_advisor_strategy_rubrics.py`）

1. `check_direction_gates` — `allow_long` / `allow_short`（英文拒绝文案）
2. `check_expected_side` — 策略固定方向
3. `precheck_open_advisor` — 战术量化硬线（RSI、7d 距离等）
4. LLM：`decision` = `approve` | `reject`，`reason` **英文**（≤120 words）

按 `source` 映射 profile：`explore` / `predict` / `pullback` / `btc_momentum`…（见 `resolve_strategy_profile`）。

### 7.4 Kill Switch

| Key | 说明 |
|-----|------|
| `open_advisor_enabled` | Web 总开关 |
| `gemini_open_advisor_enabled` | 默认 1 |
| `deepseek_open_advisor_enabled` | 默认 1 |
| `gpt_open_advisor_enabled` | 默认 1 |

### 7.5 记录

`gemini_advisor_reviews` / `deepseek_advisor_reviews` / `gpt_advisor_reviews`（`review_type=open`）

Web：`/gemini-advisor-reviews`（展示三教师记录）

---

## 8. 持仓顾问（Position / Hold Advisor）

### 8.1 职责

模拟仓 OPEN 且持仓 **≥15 分钟** 后，每 **15 分钟/仓** 请求 LLM：`hold` | `observe` | `sell`。

### 8.2 代码入口

| 教师 | 类 | 监管 source |
|------|-----|-------------|
| Gemini | `gemini_position_advisor.GeminiPositionAdvisor.tick` | `gemini_explore` / `gemini_predict` |
| DeepSeek | `deepseek_position_advisor` | 其他 source |

`crypto-scheduler` 每 **15 分钟** 调用 Gemini / DeepSeek 两个 tick。

### 8.3 决策依据（英文 prompt）

- **主依据**：近 **4 根 1h** + 近 **6 根 15m** K 线表与客观统计（`G/R/D` 序列）
- **Big4**：仅辅证，**不得单独触发 sell**
- **亏损分档**（保证金 ROI%）：轻微 >-5%、中度 >-12%、严重 ≤-15%；深亏 `hold` 经 `_temper_losing_hold` 统计复核

### 8.4 sell 后果

- **始终**关闭模拟仓。
- 仅当 `live_close_enabled=1` 且存在 `paper_position_id` 绑定的 OPEN 实盘仓时，按绑定关系平映射实盘；平仓不再按 source/TOP50/白名单过滤。

### 8.5 Kill Switch

`gemini/deepseek/gpt_position_advisor_enabled`，Web 统一项 `position_advisor_enabled`

---

## 9. Gemini 市场情绪

- **文件**：`gemini_sentiment_analyzer.run_sentiment_round`
- **调度**：**每 8 小时**（`scheduler.py`；非交易）
- **开关**：`gemini_sentiment_enabled`（默认 1）
- **表**：`gemini_sentiment_runs`
- **API**：`POST /api/gemini-sentiment/run-now`

---

---

## 11. 实盘闸门（共用）

`app/services/trading_gates.py`（`LIVE_SYNC_SOURCES`）：

| 设置 | 含义 |
|------|------|
| `live_trading_enabled` | **开仓**同步总开关 |
| `live_close_enabled` | **平仓**同步总开关（模拟平仓、持仓顾问 sell、引擎关仓） |
| `live_top50_required` | 开仓：在 `top_performing_symbols`（日终 TOP50）可开实仓 |
| `live_whitelist_enabled` | 开仓：`rating_level=0` 可开实仓 |
| `blacklist_level3_enabled` | L3 禁止开仓（多在开模拟前检查） |

**开仓按 source 白名单**：`gemini_predict`、`deepseek_explore`、`deepseek_predict`。`gemini_explore`、GPT/其它策略即使 `live_trading_enabled=1` 也只写模拟仓。

**北京时间开仓时段**：仅 **10:00-16:00**、**22:00-次日04:00** 允许开仓；服务器 UTC 对应 **02:00-08:00**、**14:00-20:00**。模拟开仓和实盘开仓都由 `trading_gates.get_beijing_open_window_status` 统一拦截。

**开仓检查链**（`check_live_open_allowed`）：`live_trading_enabled` → source ∈ `LIVE_SYNC_SOURCES` → `check_live_symbol_allowed`（L1/L2/L3 黑名单拒绝；TOP50 **或** 白名单；两闸门都关则拒绝）。不满足时日志如「黑名单1级禁止实盘」「不在 TOP 50 也非白名单」「策略 xxx 仅模拟盘」。

**平仓**：只认 `live_close_enabled` + `live_futures_positions.paper_position_id` 绑定；**不**再查 source/TOP50/白名单。

每账号 OPEN 上限 **20** 仓。

---

## 12. 调度与运维要点

| 现象 | 含义 |
|------|------|
| `上次成功距今 Xh < 4h, 跳过` | 正常防重 |
| `未到点 剩余 Xh (next_due=...)` | 预测/GPT 探索正常 |
| `上一轮还未结束` 持续 >15min | 异常：锁未释放或慢 SQL |
| 仅 `一轮开始` 无 `一轮结束` | 异常：线程卡死 |

日志：`logs/scheduler_YYYY-MM-DD.log`（非 journalctl 主输出）。

部署 Python 变更后：`sudo systemctl restart crypto-scheduler`（**只重启一次**）。

---

## 13. 校验与 KPI

| 脚本 | 用途 |
|------|------|
| `validate_explore_predict_prompts.py` | 主探索/预测门槛与 EN prompt |
| `validate_tactical_explore_prompts.py` | 战术 + 反转 |
| `validate_open_advisor_rubrics.py` | 开仓/持仓 prompt 结构 |
| `validate_open_advisor_routing.py` | 顾问路由 |
| `ai_win_rate_report.py` | 近 7 日按 source 胜率；日胜率 <40% 且 ≥3 笔 → 必须优化 |

---

## 14. 相关源文件索引

| 路径 | 说明 |
|------|------|
| `app/scheduler.py` | 全部 AI cron |
| `app/services/ai_explore_prompt.py` | 主探索 prompt + catalyst |
| `app/services/ai_predict_prompt.py` | 主预测 prompt |
| `app/services/ai_tactical_explore_prompts.py` | 战术四策略 |
| `app/services/ai_reversal_explore_prompt.py` | 顶空底多 |
| `app/services/ai_big4_prompt.py` | Big4 块 |
| `app/services/explore_prepared_bundle.py` | 共享 universe |
| `app/services/tactical_explore_scheduler.py` | 15 槽位调度 |
| `app/services/paper_open_gate.py` | 开仓闸门 |
| `app/services/open_advisor_strategy_rubrics.py` | 开仓 rubric |
| `app/services/gemini_position_advisor.py` | 开仓审查 + 持仓监管（DeepSeek/GPT 复用逻辑） |
| `smart_trader_service.py` | 顾问 tick + 部分 gate 调用 |

英文对照文档：[AI_STRATEGIES_AND_ADVISORS_EN.md](./AI_STRATEGIES_AND_ADVISORS_EN.md)
