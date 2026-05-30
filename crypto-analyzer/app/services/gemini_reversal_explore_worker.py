"""Gemini 顶空底多探索 — 顶部做空 / 底部做多，仅模拟仓."""
from __future__ import annotations

import time
from typing import Optional, Tuple

from loguru import logger

from app.services.ai_explore_prompt import EXPLORE_LLM_MAX_OUTPUT_TOKENS
from app.services.ai_reversal_explore_prompt import (
    build_reversal_explore_prompt,
    parse_reversal_llm_json,
)
from app.services.gemini_swan_worker import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_TIMEOUT_S,
)
from app.services.reversal_explore_runner import (
    ReversalExploreConfig,
    run_reversal_explore_round,
)

GEMINI_REVERSAL_CFG = ReversalExploreConfig(
    log_tag="Gemini顶空底多",
    source="gemini_reversal",
    runs_table="gemini_reversal_explore_runs",
    verdicts_table="gemini_reversal_explore_verdicts",
    model_name=GEMINI_MODEL,
    min_interval_hours=4.0,
)


def _call_gemini_reversal(
    universe: dict, global_ctx: dict, historical_stats: dict,
) -> Tuple[Optional[dict], Optional[str]]:
    if not GEMINI_API_KEY:
        return None, "GEMINI_API_KEY 未设置"
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return None, "缺 google-genai 依赖"

    prompt, meta = build_reversal_explore_prompt(universe, global_ctx, historical_stats)
    logger.info(
        f"[Gemini顶空底多] prompt {len(prompt)} chars, "
        f"symbols {meta['llm_symbol_count']}/{meta['universe_total']}"
    )

    client = genai.Client(api_key=GEMINI_API_KEY)
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_S * 1000),
        max_output_tokens=EXPLORE_LLM_MAX_OUTPUT_TOKENS,
    )

    t0 = time.time()
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt, config=cfg,
        )
    except Exception as e:
        return None, f"API: {e}"

    text = (resp.text or "").strip()
    logger.info(f"[Gemini顶空底多] 用时 {time.time()-t0:.1f}s, out={len(text)}")

    parsed, parse_err = parse_reversal_llm_json(text, "Gemini顶空底多")
    if parsed is None:
        return None, f"JSON解析失败: {parse_err}"
    parsed["_prompt"] = prompt
    parsed["_raw_response"] = text
    return parsed, None


def run_gemini_reversal_explore_round(triggered_by: str = "scheduler") -> Optional[int]:
    return run_reversal_explore_round(
        GEMINI_REVERSAL_CFG, _call_gemini_reversal, triggered_by,
    )


if __name__ == "__main__":
    rid = run_gemini_reversal_explore_round(triggered_by="manual")
    print(f"run_id={rid}")
