# AI 策略与顾问 — 完整说明（中文）

> 文档版本：2026-06-21 · 与生产代码对齐  
> **实盘同步 / 闸门 / 15m 定方向 / 限价偏移**：以 [`REQUIREMENTS_LOGIC_ZH.md`](./REQUIREMENTS_LOGIC_ZH.md) 为准；本文侧重 AI 策略细节。

## 1. 总览

系统在三套 **教师模型**（Gemini、DeepSeek、GPT）上运行多类 **AI 策略**，统一走 **模拟仓**（`futures_positions.account_id=2`）。开仓前经 **开仓顾问** 审查；持仓满 15 分钟后由 **持仓顾问** 每 **15min** 监管（浮盈转亏 urgent 立即再审）。

```text
crypto-scheduler (app/scheduler.py)
  ├─ data_cache: candidate_pool (6min) → explore_prepared (15min)
  ├─ 主探索 ×3 / 主预测 ×3
  ├─ 中线量化 ×4 (gemini/deepseek × long/short) — 6h + 10min 轮询
  ├─ 战术探索 15 槽位 (5策略×3教师) + 15min 轮询
  └─ Gemini 情绪 (8h)

crypto-scheduler (每 15min)
  └─ Gemini + DeepSeek 持仓顾问 tick（每仓 15min；浮盈转亏 urgent）

crypto-app-main
  └─ position_sl_tp_monitor (1s)：探索/预测 ai-trail-tp；中线仅硬 SL/TP，不参与 SmartExit

任意模拟开仓
  └─ paper_open_gate.gate_simulated_open()
```

| 类别 | 是否 LLM | 典型 source 前缀 | 实盘同步 |
|------|----------|------------------|----------|
| 主探索 | 是 | `gemini_explore` / `deepseek_explore` / `gpt_explore` | **`gemini_explore` / `deepseek_explore`**（+ 开仓 TOP50/白名单）；GPT 仅模拟 |
| 主预测 | 是 | `*_predict` | 仅 **`deepseek_predict`**；Gemini/GPT 仅模拟 |
| 顶空底多 | 是 | `*_reversal` | 否 |
| 战术四策略 | 是 | `*_pullback` 等 | 否 |
| **中线做多/做空** | **否（量化）** | `gemini/deepseek_midline_*` | **是**（须 L0 白名单 + `live_trading_enabled`） |
| 开仓/持仓顾问 | 是 | 按 source 路由（**中线跳过**） | 持仓 sell：`live_close_enabled=1` 且有 `paper_position_id` 绑定时平交易所 |
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
| SL / TP | **3% / 5%** |
| source | `gemini_explore` / `deepseek_explore` / `gpt_explore` |

### 3.5 Kill Switch（`system_settings`）

| Key | 典型默认 |
|-----|----------|
| `gemini_explore_enabled` | 0 |
| `deepseek_explore_enabled` | 0 |
| `gpt_explore_enabled` | 0 |

### 3.6 实盘

- **`gemini_explore` / `deepseek_explore`**：`check_live_open_allowed()` 通过后 `_sync_to_live()`（见 §11）。
- **GPT 探索**：仅模拟。

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

与主探索相同：**4h 持仓、SL 3%、TP 5%、5x、conf≥0.60、catalyst gate**。弱 catalyst → `skipped_weak_catalyst`。

### 4.4 Kill Switch

| Key | 典型默认 |
|-----|----------|
| `gemini_predict_enabled` | 1 |
| `deepseek_predict_enabled` | 0 |
| `gpt_predict_enabled` | 0 |

### 4.5 实盘

- **`deepseek_predict`**：`_sync_to_live()` + `check_live_open_allowed`（TOP50/白名单）。
- **Gemini / GPT 预测**：仅模拟。

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
| SL / TP | 默认 **3% / 5%**（`ReversalExploreConfig` 默认，与主探索一致） |
| 持仓 | 4h |
| GPT 每轮 entry 数 | 2–3（`GPT_TACTICAL_MIN/MAX_ENTRIES`） |

代码层预检与 prompt 对齐：追涨 RSI≤68、`below_7d_high` 距离；回调 RSI、7d 高等（见 `tactical_catalyst_ok`）。

### 6.4 数据表

`{teacher}_{pullback,rebound,chase,dump}_explore_runs` / `*_verdicts`（migration 009、017）

---

## 6.5 中线做多/做空（Gemini / DeepSeek · 量化，非 LLM）

### 6.5.1 职责

L0/L1 标的池 + 24×1D / 60×1H 技术评分扫描，**不调用 LLM**，**跳过**开仓/持仓顾问，**不参与** `SmartExitOptimizer`（由 `position_sl_tp_monitor` 负责**仅硬 SL/TP**、15 天到期、爆仓；**无 ai-trail-tp**）。

### 6.5.2 代码入口

| 组件 | 路径 |
|------|------|
| 常量 | `midline_swing_config.py` |
| 扫描 | `midline_swing_scanner.py` |
| Worker | `midline_explore_worker.py` |
| API / Web Tab | `midline_swing_api.py` · `static/js/midline_swing_tab.js` |

调度：`scheduler.py` 每 **6h** + **10min** 轮询四路 source。

### 6.5.3 模拟单参数

| 项 | 值 |
|----|-----|
| source | `gemini_midline_long/short` · `deepseek_midline_long/short` |
| 保证金 | 500 U |
| 杠杆 | 5x |
| 计划持仓 | **15 天** |
| SL / TP | **6% / 20%** |
| 限价偏移 | **做多 −3% / 做空 +3%**（`MIDLINE_LIMIT_*_OFFSET_PCT`） |
| 限价超时 | **6h**（`MIDLINE_LIMIT_TIMEOUT_MINUTES=360`） |

非中线探索/预测等仍读 `system_settings.paper_limit_long/short_offset_pct`（默认 0.5%，Web 可调 0.1~1%）。

### 6.5.4 实盘

- source ∈ `LIVE_SYNC_SOURCES`；开仓须 **L0 白名单**（`rating_level=0`）；保证金 = API `max_position_value` × 评级比例。
- 限价 **FILLED** 后走 `PaperLimitSync`（5 分钟窗）；成交瞬间可 `sync_filled_order_now` 立即同步。

### 6.5.5 Kill Switch

`gemini_midline_long_enabled` / `gemini_midline_short_enabled` / `deepseek_midline_long_enabled` / `deepseek_midline_short_enabled`

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
| `gemini_midline_*` / `deepseek_midline_*` | **跳过**（`skip_open_advisor=True`） |
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

模拟仓 OPEN 且持仓 **≥15 分钟** 后请求 LLM：`hold` | `observe` | `sell`。

- **调度**：`crypto-scheduler` 每 **15 分钟** tick 一次  
- **复审间隔**：每仓 **15 分钟**；**浮盈转亏** → urgent 立即再审  
- **浮盈转亏**：上次审核浮盈、现已亏损 → **立即 urgent 再审**（不等间隔）

### 8.2 代码入口

| 教师 | 类 | 监管 source |
|------|-----|-------------|
| Gemini | `gemini_position_advisor.GeminiPositionAdvisor.tick` | `gemini_explore` / `gemini_predict` |
| DeepSeek | `deepseek_position_advisor` | 其他 source（**不含**四路 `*_midline_*`） |

`crypto-scheduler` 每 **15 分钟** 调用 Gemini / DeepSeek 两个 tick。

### 8.3 决策依据（中文 prompt）

- **主依据**：近 **16 根 15m**（4h 窗口）K 线表 + 量价/RSI；1h 交叉验证  
- **Big4**：仅辅证，**不得单独触发 sell**  
- **盈利侧**：ROI≥**+8%** 且 15m **明确**转弱（反向≥4）→ 倾向 observe/sell；`_temper_premature_sell` 严格拦截过早 sell  
- **亏损分档**（保证金 ROI%）：轻微 >-5%、中度 >-12%、严重 ≤-15%；深亏 `hold` 经 `_temper_losing_hold` 统计复核  
- **程序化锁利**：探索/预测 `position_sl_tp_monitor` **ai-trail-tp**（peak 价格收益≥3%，回撤≥1%）；**中线不含**

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
| `live_top50_required` | 开仓：在 `top_performing_symbols`（每 4 小时刷新 TOP50）可开实仓 |
| `live_whitelist_enabled` | 开仓：`rating_level=0` 可开实仓 |
| `blacklist_level3_enabled` | L3 禁止开仓（多在开模拟前检查） |

**开仓按 source 白名单**（`LIVE_SYNC_SOURCES`）：主探索/预测四路 + **四路中线**。GPT/战术/反转等只写模拟仓。

**北京时间实盘开仓时段**：仅 **10:00-16:00**、**22:00-次日04:00** 允许同步/直接开实盘；服务器 UTC 对应 **02:00-08:00**、**14:00-20:00**。模拟开仓不受该时段限制。

**开仓检查链**（`check_live_open_allowed`）：`live_trading_enabled` → source ∈ `LIVE_SYNC_SOURCES` → `check_live_symbol_allowed`（**仅 L0 白名单** `rating_level=0`；L1/L2/L3 拒绝）。`live_top50_required` 等设置已废弃，不再参与开仓判断。

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
