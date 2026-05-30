"""战术探索 workers — 回调做多 / 反弹做空 / 追涨做多 / 杀跌做空 × Gemini/DeepSeek."""
from __future__ import annotations

import os
import time
from typing import Callable, Optional, Tuple

from loguru import logger

from app.services.ai_explore_prompt import EXPLORE_LLM_MAX_OUTPUT_TOKENS
from app.services.ai_tactical_explore_prompts import (
    TACTICAL_STRATEGIES,
    TacticalStrategyDef,
    build_strategy_prompt,
    parse_tactical_llm_json,
    tactical_catalyst_ok,
    tactical_category_to_side,
)
from app.services.gemini_swan_worker import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_TIMEOUT_S
from app.services.reversal_explore_runner import (
    TacticalExploreConfig,
    run_tactical_explore_round,
)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("DeepSeek_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_TIMEOUT_S = int(os.getenv("DEEPSEEK_TIMEOUT_S", "180"))


def _make_cfg(teacher: str, strategy_key: str, defn: TacticalStrategyDef) -> TacticalExploreConfig:
    prefix = "gemini" if teacher == "gemini" else "deepseek"
    model = GEMINI_MODEL if teacher == "gemini" else DEEPSEEK_MODEL
    return TacticalExploreConfig(
        log_tag=f"{'Gemini' if teacher == 'gemini' else 'DeepSeek'}{defn.title_zh}",
        source=f"{prefix}_{strategy_key}",
        runs_table=f"{prefix}_{strategy_key}_explore_runs",
        verdicts_table=f"{prefix}_{strategy_key}_explore_verdicts",
        model_name=model,
        min_interval_hours=4.0,
        strategy_label=defn.title_zh,
    )


def _call_gemini(defn: TacticalStrategyDef, strategy_key: str):
    def _inner(universe, global_ctx, historical_stats):
        if not GEMINI_API_KEY:
            return None, "GEMINI_API_KEY 未设置"
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            return None, "缺 google-genai"
        prompt, meta = build_strategy_prompt(strategy_key, universe, global_ctx, historical_stats)
        logger.info(
            f"[Gemini{defn.title_zh}] prompt {len(prompt)} chars, "
            f"sym {meta['llm_symbol_count']}/{meta['universe_total']}"
        )
        client = genai.Client(api_key=GEMINI_API_KEY)
        gcfg = types.GenerateContentConfig(
            response_mime_type="application/json",
            http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_S * 1000),
            max_output_tokens=EXPLORE_LLM_MAX_OUTPUT_TOKENS,
        )
        t0 = time.time()
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL, contents=prompt, config=gcfg,
            )
        except Exception as e:
            return None, f"API: {e}"
        text = (resp.text or "").strip()
        logger.info(f"[Gemini{defn.title_zh}] {time.time()-t0:.1f}s out={len(text)}")
        parsed, err = parse_tactical_llm_json(text, f"Gemini{defn.title_zh}")
        if parsed is None:
            return None, f"JSON: {err}"
        parsed["_prompt"] = prompt
        parsed["_raw_response"] = text
        return parsed, None
    return _inner


def _call_deepseek(defn: TacticalStrategyDef, strategy_key: str):
    def _inner(universe, global_ctx, historical_stats):
        if not DEEPSEEK_API_KEY:
            return None, "DEEPSEEK_API_KEY 未设置"
        try:
            from openai import OpenAI
        except ImportError:
            return None, "缺 openai"
        prompt, meta = build_strategy_prompt(strategy_key, universe, global_ctx, historical_stats)
        logger.info(
            f"[DeepSeek{defn.title_zh}] prompt {len(prompt)} chars, "
            f"sym {meta['llm_symbol_count']}/{meta['universe_total']}"
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
        logger.info(f"[DeepSeek{defn.title_zh}] {time.time()-t0:.1f}s out={len(text)}")
        parsed, err = parse_tactical_llm_json(text, f"DeepSeek{defn.title_zh}")
        if parsed is None:
            return None, f"JSON: {err}"
        parsed["_prompt"] = prompt
        parsed["_raw_response"] = text
        return parsed, None
    return _inner


def _make_runner(teacher: str, strategy_key: str) -> Callable[[str], Optional[int]]:
    defn = TACTICAL_STRATEGIES[strategy_key]
    cfg = _make_cfg(teacher, strategy_key, defn)
    call_llm = _call_gemini(defn, strategy_key) if teacher == "gemini" else _call_deepseek(defn, strategy_key)

    def run(triggered_by: str = "scheduler") -> Optional[int]:
        return run_tactical_explore_round(
            cfg,
            call_llm,
            triggered_by,
            category_to_side=lambda cat, conf, d=defn: tactical_category_to_side(d, cat, conf),
            catalyst_ok=lambda cat, catl, sig, sym, d=defn: tactical_catalyst_ok(d, catl, sig, sym),
        )
    return run


# Gemini
run_gemini_pullback_explore_round = _make_runner("gemini", "pullback")
run_gemini_rebound_explore_round = _make_runner("gemini", "rebound")
run_gemini_chase_explore_round = _make_runner("gemini", "chase")
run_gemini_dump_explore_round = _make_runner("gemini", "dump")

# DeepSeek
run_deepseek_pullback_explore_round = _make_runner("deepseek", "pullback")
run_deepseek_rebound_explore_round = _make_runner("deepseek", "rebound")
run_deepseek_chase_explore_round = _make_runner("deepseek", "chase")
run_deepseek_dump_explore_round = _make_runner("deepseek", "dump")
