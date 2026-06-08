# AI Strategies & Advisors — Reference (English)

> Version: 2026-06-05 · Aligned with production code (main explore/predict/tactical/reversal/advisors **LLM prompts are Chinese**; live sync limited to `LIVE_SYNC_SOURCES` + TOP50/whitelist on open)

## 1. Overview

The platform runs multiple **AI trading strategies** on three **teacher models** (Gemini, DeepSeek, GPT). All AI trades default to **paper** (`futures_positions.account_id=2`). **Open advisors** review before entry; **hold advisors** supervise positions after 15 minutes.

```text
crypto-scheduler (app/scheduler.py)
  ├─ data_cache: candidate_pool (6min) → explore_prepared (15min)
  ├─ Main explore ×3 / Main predict ×3
  ├─ Tactical explore: 15 slots (5 strategies × 3 teachers), 15min poll
  └─ Gemini sentiment (8h)

smart_trader_service (every 900s)
  └─ Gemini + DeepSeek + GPT hold advisor ticks

Any simulated open
  └─ paper_open_gate.gate_simulated_open()
```

| Category | LLM? | Typical `source` prefix | Live sync |
|----------|------|-------------------------|-----------|
| Main explore | Yes | `gemini_explore` / `deepseek_explore` / `gpt_explore` | **`gemini_explore`, `deepseek_explore`** (+ TOP50/whitelist on open); GPT paper only |
| Main predict | Yes | `*_predict` | **`gemini_predict`, `deepseek_predict` only** (GPT paper only) |
| Reversal | Yes | `*_reversal` | No |
| Tactical four | Yes | `*_pullback`, etc. | No |
| Open / hold advisors | Yes | Routed by `source` | `sell` closes exchange when `live_close_enabled=1` and a `paper_position_id` link exists |
| Sentiment | Yes | No orders | — |

---

## 2. Shared Data & Prompt Conventions

### 2.1 Data cache

| Table | Refresh | Purpose |
|-------|---------|---------|
| `data_cache.candidate_pool_snapshot` | 6 min | Candidates + 1h/15m narratives (24-bar trend + last ~6 bars) |
| `data_cache.explore_prepared_snapshot` | 15 min | `explore_prepared_bundle`: universe + `global_ctx`; read-only for explore/tactical |
| `data_cache.settings_cache` | 1 min | Direction gates `allow_long` / `allow_short` |

Do **not** `DELETE` the full `candidate_pool_snapshot` at refresh start (UPSERT only). Explore should use `load_candidate_pool_for_explore()` to avoid slow `kline_data` fallback locks.

### 2.2 Production prompt language (2026-06)

| Module | Production entry | File |
|--------|------------------|------|
| Main explore | `build_explore_prompt()` → `build_explore_prompt_en()` | `ai_explore_prompt.py` |
| Main predict | `build_predict_prompt()` → `build_predict_prompt_en()` | `ai_predict_prompt.py` |
| Tactical four | `build_strategy_prompt()` → `build_strategy_prompt_en()` | `ai_tactical_explore_prompts.py` |
| Reversal | `build_reversal_explore_prompt()` → EN | `ai_reversal_explore_prompt.py` |
| Open advisor | `build_open_advisor_prompt()` → `build_gpt_open_advisor_prompt()` | `open_advisor_strategy_rubrics.py` |
| Hold advisor | `GeminiPositionAdvisor._build_prompt()` | English; DeepSeek/GPT share structure |

ZH builders and `scripts/benchmark_*_prompt_lang.py` remain for regression.

### 2.3 Shared hard gates

- **Main explore/predict**: `explore_catalyst_technical_ok` + confidence ≥ **0.60**.
- **Tactical four**: `tactical_catalyst_ok` + confidence ≥ **0.55**.
- **Reversal**: `reversal_catalyst_technical_ok` + confidence ≥ **0.65**.
- LLM universe: technical score **TOP50** (`prepare_universe_for_llm`), not sorted by 24h % move.

---

## 3. Main Explore (Gemini / DeepSeek / GPT)

### 3.1 Role

Every ~4h, pick event/structure setups from the candidate pool, output LONG/SHORT, open paper positions after gates.

### 3.2 Code entry

| Teacher | Worker | Schedule |
|---------|--------|----------|
| Gemini | `gemini_explore_worker.run_explore_round` | `every(4).hours` + `every(10).minutes` |
| DeepSeek | `deepseek_explore_worker.run_explore_round` | Same |
| GPT | `gpt_explore_worker.run_explore_round` | Same + `gpt_explore_next_due_utc` |

Init stagger: Gemini +15s, DeepSeek +90s, GPT +120s.

### 3.3 De-dupe & status

- Run only if last `status='ok'` was **≥ 4h** ago (`manual` bypasses).
- In progress: `gemini_explore_runs.status='partial'` (**never `running`** — ENUM truncation).
- Process lock: skip if previous round still running (>15min stuck → investigate).

### 3.4 Paper trade params

| Field | Value |
|-------|-------|
| Margin | 500 USDT |
| Leverage | 5x |
| Hold | **4h** (`AI_POSITION_HOLD_HOURS`) |
| SL / TP | **4% / 6%** |
| `source` | `gemini_explore` / `deepseek_explore` / `gpt_explore` |

### 3.5 Kill switches (`system_settings`)

| Key | Typical default |
|-----|-----------------|
| `gemini_explore_enabled` | 0 |
| `deepseek_explore_enabled` | 0 |
| `gpt_explore_enabled` | 0 |

### 3.6 Live trading

- **`gemini_explore`, `deepseek_explore`**: `_sync_to_live()` when `check_live_open_allowed()` passes (see §11).
- **GPT explore**: paper only.

### 3.7 Tables

`{gemini,deepseek,gpt}_explore_runs` / `*_explore_verdicts`

### 3.8 Web / API

- Pages: `templates/gemini_explore.html`, `deepseek_explore.html`, `gpt_explore.html`
- Manual: `POST /api/{gemini,deepseek,gpt}-explore/run-now`

---

## 4. Main Predict (Gemini / DeepSeek / GPT)

### 4.1 Role

Every 4h, 4h directional probability on TOP50; opens paper trades when thresholds pass. Uses predict prompt, shares catalyst gate with explore.

### 4.2 Code entry

| Teacher | Worker | Schedule |
|---------|--------|----------|
| Gemini | `gemini_predictor.run_predict_round` | 4h + 5min poll + `gemini_predict_next_due_utc` |
| DeepSeek | `deepseek_predictor.run_predict_round` | `deepseek_predict_next_due_utc` |
| GPT | `gpt_predictor.run_predict_round` | `gpt_predict_next_due_utc` |

Startup catch-up: +45s / +50s / +55s (**do not run predict from `scheduler_init`**).

### 4.3 Params & gates

Same as main explore: **4h hold, SL 4%, TP 6%, 5x, conf≥0.60, catalyst gate**. Weak catalyst → `skipped_weak_catalyst`.

### 4.4 Kill switches

| Key | Typical default |
|-----|-----------------|
| `gemini_predict_enabled` | 1 |
| `deepseek_predict_enabled` | 0 |
| `gpt_predict_enabled` | 0 |

### 4.5 Live

- **`gemini_predict`, `deepseek_predict`**: `_sync_to_live()` + `check_live_open_allowed` (TOP50/whitelist).
- **`gpt_predict`**: paper only (`_sync_to_live` commented out).

### 4.6 Tables

`{gemini,deepseek,gpt}_predict_runs` / `*_predict_verdicts`

---

## 5. Reversal Explore (Top Short / Bottom Long)

### 5.1 Role

`top_reversal` → SHORT, `bottom_reversal` → LONG with multi-TF structure proof.

### 5.2 Code

- Workers: `*_reversal_explore_worker`
- Runner: `reversal_explore_runner.run_reversal_explore_round`
- Prompt: `ai_reversal_explore_prompt.py` (EN in production)

### 5.3 Schedule

Part of `tactical_explore_scheduler` (**15 jobs**); 4h cycle + 15min poll on `tactical_{source}_next_due_utc`. **No dedicated kill switch.**

### 5.4 Params

| Field | Value |
|-------|-------|
| SL / TP | **3% / 5%** |
| Hold | 4h |
| Confidence | ≥ **0.65** |

### 5.5 Tables

`{gemini,deepseek,gpt}_reversal_explore_runs` / `*_verdicts` (migration 008)

---

## 6. Tactical Four Strategies

### 6.1 Strategies & fixed side

| `profile` | Label | Side |
|-----------|-------|------|
| `pullback` | Pullback long | LONG |
| `chase` | Momentum chase | LONG |
| `rebound` | Rebound short | SHORT |
| `dump` | Breakdown short | SHORT |

### 6.2 Code

- `tactical_explore_workers.py` → `run_tactical_explore_round`
- `tactical_explore_scheduler.py` (15 jobs)
- `ai_tactical_explore_prompts.py` (EN production)

### 6.3 Params

| Field | Value |
|-------|-------|
| Confidence | ≥ **0.55** |
| SL / TP | Default **4% / 6%** (`ReversalExploreConfig` defaults) |
| Hold | 4h |
| GPT entries per round | 2–3 |

Code pre-checks align with rubric (chase RSI, 7d high distance, etc.) via `tactical_catalyst_ok`.

### 6.4 Tables

`{teacher}_{pullback,rebound,chase,dump}_explore_runs` / `*_verdicts`

---

## 7. Open Advisor

### 7.1 Flow

```text
gate_simulated_open (paper_open_gate.py)
  → resolve_open_advisors(source)
  → each teacher review_open in sequence
  → any reject blocks open; API errors degrade to allow
```

### 7.2 Routing (`open_advisor_routing.py`)

| `source` pattern | Reviewers |
|------------------|-----------|
| `gemini_explore` / `gemini_predict` | Gemini only |
| Other sources | DeepSeek only |

### 7.3 Steps (`open_advisor_strategy_rubrics.py`)

1. `check_direction_gates` — English reject messages
2. `check_expected_side`
3. `precheck_open_advisor` — tactical numeric lines
4. LLM JSON: `approve` | `reject`, **English `reason`** (≤120 words)

Profile resolved from `source` (`explore`, `predict`, `pullback`, `btc_momentum`, …).

### 7.4 Kill switches

`open_advisor_enabled`, `gemini/deepseek/gpt_open_advisor_enabled` (typically on).

### 7.5 Logging

`gemini_advisor_reviews` / `deepseek_advisor_reviews` / `gpt_advisor_reviews` (`review_type=open`)

Web: `/gemini-advisor-reviews`

---

## 8. Hold Advisor (Position Supervisor)

### 8.1 Role

For OPEN paper positions held **≥15 minutes**, poll every **15 minutes/position**: `hold` | `observe` | `sell`.

### 8.2 Code

| Teacher | Class | Sources |
|---------|-------|---------|
| Gemini | `GeminiPositionAdvisor.tick` | `gemini_explore` / `gemini_predict` |
| DeepSeek | `deepseek_position_advisor` | Other sources |

`crypto-scheduler` calls Gemini and DeepSeek every **15 minutes**.

### 8.3 Decision basis (English prompt)

- **Primary**: last **4×1h** + **6×15m** tables and stats (`G`/`R`/`D` bar codes)
- **Big4**: auxiliary only — **cannot sell on Big4 alone**
- **Loss tiers** (margin ROI%): mild >-5%, moderate >-12%, severe ≤-15%; losing `hold` reviewed by `_temper_losing_hold`

### 8.4 On `sell`

- Always close paper.
- Close exchange only if `live_close_enabled=1` and an OPEN live position is linked by `paper_position_id`; close sync no longer checks source/TOP50/whitelist.

### 8.5 Kill switches

`gemini/deepseek/gpt_position_advisor_enabled`, unified `position_advisor_enabled` in UI.

---

## 9. Gemini Market Sentiment

- **File**: `gemini_sentiment_analyzer.run_sentiment_round`
- **Schedule**: **every 8 hours** (no trading)
- **Switch**: `gemini_sentiment_enabled` (default 1)
- **Table**: `gemini_sentiment_runs`

---

---

## 11. Live Trading Gates

`app/services/trading_gates.py` (`LIVE_SYNC_SOURCES`):

| Setting | Meaning |
|---------|---------|
| `live_trading_enabled` | Master switch for **opening** live positions |
| `live_close_enabled` | Master switch for **closing** live (paper close, advisor sell, engine) |
| `live_top50_required` | Open: symbol in `top_performing_symbols` (daily TOP50) |
| `live_whitelist_enabled` | Open: `rating_level=0` whitelist |
| `blacklist_level3_enabled` | Block L3 symbols (often checked before paper open) |

**Source allowlist (open only):** `gemini_explore`, `deepseek_explore`, `gemini_predict`, `deepseek_predict`. All other strategies stay paper-only for live opens even if `live_trading_enabled=1`.

**Open chain** (`check_live_open_allowed`): `live_trading_enabled` → source ∈ allowlist → `check_live_symbol_allowed` (L1/L2/L3 blacklist rejects; TOP50 **OR** whitelist; if both gates off, no live open). Reject reasons include “blacklist level 1 blocks live”, “not in TOP 50 nor whitelist”, “strategy X paper only”.

**Close:** `live_close_enabled` + `live_futures_positions.paper_position_id` link only; **no** source/TOP50/whitelist re-check.

Max **20** OPEN positions per live account.

---

## 12. Operations Cheatsheet

| Log line | Meaning |
|----------|---------|
| `上次成功距今 Xh < 4h, 跳过` / last ok < 4h | Normal de-dupe |
| `next_due=...` not due | Normal for predict / GPT explore |
| `上一轮还未结束` >15min | Stuck lock or slow SQL |
| Start without end | Hung worker thread |

Logs: `logs/scheduler_YYYY-MM-DD.log`. After Python deploy: `sudo systemctl restart crypto-scheduler` (**once**).

---

## 13. Validation & KPI

| Script | Coverage |
|--------|----------|
| `validate_explore_predict_prompts.py` | Main explore/predict EN + thresholds |
| `validate_tactical_explore_prompts.py` | Tactical + reversal |
| `validate_open_advisor_rubrics.py` | Open/hold prompt structure |
| `validate_open_advisor_routing.py` | Advisor routing |
| `ai_win_rate_report.py` | 7-day win rate by `source`; daily <40% with ≥3 closes → must tune |

---

## 14. Source File Index

| Path | Role |
|------|------|
| `app/scheduler.py` | All AI cron jobs |
| `app/services/ai_explore_prompt.py` | Main explore + catalyst |
| `app/services/ai_predict_prompt.py` | Main predict |
| `app/services/ai_tactical_explore_prompts.py` | Tactical four |
| `app/services/ai_reversal_explore_prompt.py` | Reversal |
| `app/services/tactical_explore_scheduler.py` | 15-slot scheduler |
| `app/services/paper_open_gate.py` | Pre-open gate |
| `app/services/open_advisor_strategy_rubrics.py` | Open rubrics |
| `app/services/gemini_position_advisor.py` | Open review + hold tick |
| `smart_trader_service.py` | Hold advisor orchestration |

Chinese reference: [AI_STRATEGIES_AND_ADVISORS_ZH.md](./AI_STRATEGIES_AND_ADVISORS_ZH.md)
