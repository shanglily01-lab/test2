"""GPT 顶空底多探索 — 顶部做空 / 底部做多，仅模拟仓."""
from __future__ import annotations

import time
from typing import Optional, Tuple

from loguru import logger

from app.services.ai_explore_prompt import (
    EXPLORE_LLM_MAX_OUTPUT_TOKENS,
    explore_llm_stub_with_trace,
)
from app.services.ai_reversal_explore_prompt import (
    REVERSAL_HOLD_HOURS,
    REVERSAL_SL_PCT,
    REVERSAL_TP_PCT,
    build_reversal_explore_prompt,
    parse_reversal_llm_json,
)
from app.services.gpt_config import GPT_API_KEY, GPT_BASE_URL, GPT_MODEL, GPT_TIMEOUT_S
from app.services.gpt_llm_client import gpt_chat_json
from app.services.reversal_explore_runner import (
    ReversalExploreConfig,
    run_reversal_explore_round,
)

GPT_REVERSAL_CFG = ReversalExploreConfig(
    log_tag="GPT顶空底多",
    source="gpt_reversal",
    runs_table="gpt_reversal_explore_runs",
    verdicts_table="gpt_reversal_explore_verdicts",
    model_name=GPT_MODEL,
    min_interval_hours=4.0,
    sl_pct=REVERSAL_SL_PCT,
    tp_pct=REVERSAL_TP_PCT,
    hold_hours=REVERSAL_HOLD_HOURS,
)


def _call_gpt_reversal(
    universe: dict, global_ctx: dict, historical_stats: dict,
) -> Tuple[Optional[dict], Optional[str]]:
    if not GPT_API_KEY:
        return None, "OPENAI_API_KEY 未设置"
    try:
        from openai import OpenAI
    except ImportError:
        return None, "缺 openai 依赖"

    prompt, meta = build_reversal_explore_prompt(universe, global_ctx, historical_stats)
    logger.info(
        f"[GPT顶空底多] prompt {len(prompt)} chars, "
        f"symbols {meta['llm_symbol_count']}/{meta['universe_total']}"
    )

    client = OpenAI(api_key=GPT_API_KEY, base_url=GPT_BASE_URL)
    t0 = time.time()
    try:
        text = gpt_chat_json(
            client,
            user_prompt=prompt,
            max_tokens=EXPLORE_LLM_MAX_OUTPUT_TOKENS,
            timeout=GPT_TIMEOUT_S,
        )
    except Exception as e:
        return None, f"API: {e}"
    logger.info(f"[GPT顶空底多] 用时 {time.time()-t0:.1f}s, out={len(text)}")

    parsed, parse_err = parse_reversal_llm_json(text, "GPT顶空底多")
    if parsed is None:
        return explore_llm_stub_with_trace(prompt, text), f"JSON解析失败: {parse_err}"
    parsed["_prompt"] = prompt
    parsed["_raw_response"] = text
    return parsed, None


def run_gpt_reversal_explore_round(triggered_by: str = "scheduler") -> Optional[int]:
    return run_reversal_explore_round(
        GPT_REVERSAL_CFG, _call_gpt_reversal, triggered_by,
    )


if __name__ == "__main__":
    rid = run_gpt_reversal_explore_round(triggered_by="manual")
    print(f"run_id={rid}")
