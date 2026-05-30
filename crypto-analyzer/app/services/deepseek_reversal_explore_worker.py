"""DeepSeek 顶空底多探索 — 顶部做空 / 底部做多，仅模拟仓."""
from __future__ import annotations

import os
import time
from typing import Optional, Tuple

from loguru import logger

from app.services.ai_explore_prompt import EXPLORE_LLM_MAX_OUTPUT_TOKENS
from app.services.ai_reversal_explore_prompt import (
    build_reversal_explore_prompt,
    parse_reversal_llm_json,
)
from app.services.reversal_explore_runner import (
    ReversalExploreConfig,
    run_reversal_explore_round,
)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("DeepSeek_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_TIMEOUT_S = int(os.getenv("DEEPSEEK_TIMEOUT_S", "180"))

DEEPSEEK_REVERSAL_CFG = ReversalExploreConfig(
    log_tag="DeepSeek顶空底多",
    source="deepseek_reversal",
    runs_table="deepseek_reversal_explore_runs",
    verdicts_table="deepseek_reversal_explore_verdicts",
    model_name=DEEPSEEK_MODEL,
    min_interval_hours=4.0,
)


def _call_deepseek_reversal(
    universe: dict, global_ctx: dict, historical_stats: dict,
) -> Tuple[Optional[dict], Optional[str]]:
    if not DEEPSEEK_API_KEY:
        return None, "DEEPSEEK_API_KEY 未设置"
    try:
        from openai import OpenAI
    except ImportError:
        return None, "缺 openai 依赖"

    prompt, meta = build_reversal_explore_prompt(universe, global_ctx, historical_stats)
    logger.info(
        f"[DeepSeek顶空底多] prompt {len(prompt)} chars, "
        f"symbols {meta['llm_symbol_count']}/{meta['universe_total']}"
    )

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=EXPLORE_LLM_MAX_OUTPUT_TOKENS,
            timeout=DEEPSEEK_TIMEOUT_S,
        )
    except Exception as e:
        return None, f"API: {e}"

    text = (resp.choices[0].message.content or "").strip()
    logger.info(f"[DeepSeek顶空底多] 用时 {time.time()-t0:.1f}s, out={len(text)}")

    parsed, parse_err = parse_reversal_llm_json(text, "DeepSeek顶空底多")
    if parsed is None:
        return None, f"JSON解析失败: {parse_err}"
    parsed["_prompt"] = prompt
    parsed["_raw_response"] = text
    return parsed, None


def run_deepseek_reversal_explore_round(triggered_by: str = "scheduler") -> Optional[int]:
    return run_reversal_explore_round(
        DEEPSEEK_REVERSAL_CFG, _call_deepseek_reversal, triggered_by,
    )


if __name__ == "__main__":
    rid = run_deepseek_reversal_explore_round(triggered_by="manual")
    print(f"run_id={rid}")
