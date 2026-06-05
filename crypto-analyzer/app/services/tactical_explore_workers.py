"""战术探索 workers — 回调做多 / 反弹做空 / 追涨做多 / 杀跌做空 × Gemini/DeepSeek/GPT."""
from __future__ import annotations

import os
import threading
import time
from typing import Callable, Dict, Optional, Tuple

from loguru import logger

from app.services.ai_explore_prompt import (
    EXPLORE_LLM_MAX_OUTPUT_TOKENS,
    explore_llm_stub_with_trace,
)
from app.services.ai_tactical_explore_prompts import (
    TACTICAL_STRATEGIES,
    TacticalStrategyDef,
    build_strategy_prompt,
    build_strategy_prompt_en,
    build_tactical_family_prompt,
    build_tactical_family_prompt_en,
    parse_tactical_llm_json,
    parse_tactical_family_llm_json,
    tactical_catalyst_ok,
    tactical_family_category_to_strategy,
    tactical_category_to_side,
)
from app.services.gemini_swan_worker import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_TIMEOUT_S
from app.services.gpt_config import GPT_API_KEY, GPT_BASE_URL, GPT_MODEL, GPT_TIMEOUT_S
from app.services.gpt_llm_client import GPT_JSON_SYSTEM_EN, GPT_JSON_SYSTEM_ZH, gpt_chat_json
from app.services.reversal_explore_runner import (
    TacticalExploreConfig,
    run_tactical_explore_round,
)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("DeepSeek_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_TIMEOUT_S = int(os.getenv("DEEPSEEK_TIMEOUT_S", "180"))


def _make_cfg(teacher: str, strategy_key: str, defn: TacticalStrategyDef) -> TacticalExploreConfig:
    prefix = teacher
    if teacher == "gemini":
        model = GEMINI_MODEL
        label = "Gemini"
    elif teacher == "gpt":
        model = GPT_MODEL
        label = "GPT"
    else:
        model = DEEPSEEK_MODEL
        label = "DeepSeek"
    return TacticalExploreConfig(
        log_tag=f"{label}{defn.title_zh}",
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
            temperature=0.1,
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
        parsed, err = parse_tactical_llm_json(text, f"Gemini{defn.title_zh}", defn.fixed_side)
        if parsed is None:
            return explore_llm_stub_with_trace(prompt, text), f"JSON: {err}"
        parsed["_prompt"] = prompt
        parsed["_raw_response"] = text
        parsed["_screen_records"] = meta.get("screen_records") or []
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
        prompt, meta = build_strategy_prompt_en(strategy_key, universe, global_ctx, historical_stats)
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
        parsed, err = parse_tactical_llm_json(text, f"DeepSeek{defn.title_zh}", defn.fixed_side)
        if parsed is None:
            return explore_llm_stub_with_trace(prompt, text), f"JSON: {err}"
        parsed["_prompt"] = prompt
        parsed["_raw_response"] = text
        parsed["_screen_records"] = meta.get("screen_records") or []
        return parsed, None
    return _inner


def _call_gpt(defn: TacticalStrategyDef, strategy_key: str):
    def _inner(universe, global_ctx, historical_stats):
        if not GPT_API_KEY:
            return None, "OPENAI_API_KEY 未设置"
        try:
            from openai import OpenAI
        except ImportError:
            return None, "缺 openai"
        prompt, meta = build_strategy_prompt_en(
            strategy_key, universe, global_ctx, historical_stats,
        )
        logger.info(
            f"[GPT{defn.title_zh}] prompt {len(prompt)} chars, "
            f"sym {meta['llm_symbol_count']}/{meta['universe_total']}"
        )
        client = OpenAI(api_key=GPT_API_KEY, base_url=GPT_BASE_URL)
        t0 = time.time()
        try:
            text = gpt_chat_json(
                client,
                user_prompt=prompt,
                max_tokens=EXPLORE_LLM_MAX_OUTPUT_TOKENS,
                timeout=GPT_TIMEOUT_S,
                system_prompt=GPT_JSON_SYSTEM_EN,
            )
        except Exception as e:
            return None, f"API: {e}"
        logger.info(f"[GPT{defn.title_zh}] {time.time()-t0:.1f}s out={len(text)}")
        parsed, err = parse_tactical_llm_json(text, f"GPT{defn.title_zh}", defn.fixed_side)
        if parsed is None:
            return explore_llm_stub_with_trace(prompt, text), f"JSON: {err}"
        parsed["_prompt"] = prompt
        parsed["_raw_response"] = text
        parsed["_screen_records"] = meta.get("screen_records") or []
        return parsed, None
    return _inner


def _call_family(teacher: str, group_key: str, group_label: str):
    teacher_label = {"gemini": "Gemini", "deepseek": "DeepSeek", "gpt": "GPT"}[teacher]

    def _inner(universe, global_ctx, historical_stats):
        if teacher == "gemini":
            if not GEMINI_API_KEY:
                return None, "GEMINI_API_KEY missing"
            try:
                from google import genai
                from google.genai import types
            except ImportError:
                return None, "missing google-genai"
            prompt, meta = build_tactical_family_prompt(
                group_key, universe, global_ctx, historical_stats,
            )
            logger.info(
                f"[{teacher_label}{group_label}] family prompt {len(prompt)} chars, "
                f"sym {meta['llm_symbol_count']}/{meta['universe_total']}"
            )
            client = genai.Client(api_key=GEMINI_API_KEY)
            gcfg = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
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
        elif teacher == "gpt":
            if not GPT_API_KEY:
                return None, "OPENAI_API_KEY missing"
            try:
                from openai import OpenAI
            except ImportError:
                return None, "missing openai"
            prompt, meta = build_tactical_family_prompt_en(
                group_key, universe, global_ctx, historical_stats,
            )
            logger.info(
                f"[{teacher_label}{group_label}] family prompt {len(prompt)} chars, "
                f"sym {meta['llm_symbol_count']}/{meta['universe_total']}"
            )
            client = OpenAI(api_key=GPT_API_KEY, base_url=GPT_BASE_URL)
            t0 = time.time()
            try:
                text = gpt_chat_json(
                    client,
                    user_prompt=prompt,
                    max_tokens=EXPLORE_LLM_MAX_OUTPUT_TOKENS,
                    timeout=GPT_TIMEOUT_S,
                    system_prompt=GPT_JSON_SYSTEM_EN,
                )
            except Exception as e:
                return None, f"API: {e}"
        else:
            if not DEEPSEEK_API_KEY:
                return None, "DEEPSEEK_API_KEY missing"
            try:
                from openai import OpenAI
            except ImportError:
                return None, "missing openai"
            prompt, meta = build_tactical_family_prompt_en(
                group_key, universe, global_ctx, historical_stats,
            )
            logger.info(
                f"[{teacher_label}{group_label}] family prompt {len(prompt)} chars, "
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

        logger.info(f"[{teacher_label}{group_label}] family {time.time()-t0:.1f}s out={len(text)}")
        parsed, err = parse_tactical_family_llm_json(
            text, f"{teacher_label}{group_label}", group_key,
        )
        if parsed is None:
            return explore_llm_stub_with_trace(prompt, text), f"JSON: {err}"
        parsed["_prompt"] = prompt
        parsed["_raw_response"] = text
        parsed["_screen_records"] = meta.get("screen_records") or []
        return parsed, None

    return _inner


def _split_family_response(parsed: Optional[dict], strategy_key: str) -> Optional[dict]:
    if parsed is None:
        return None
    out = dict(parsed)
    verdicts = []
    for verdict in parsed.get("verdicts") or []:
        if not isinstance(verdict, dict):
            continue
        family_strategy = tactical_family_category_to_strategy(verdict.get("category") or "")
        if family_strategy != strategy_key:
            continue
        item = dict(verdict)
        item["family_category"] = item.get("category")
        item["category"] = "entry"
        verdicts.append(item)
    out["verdicts"] = verdicts
    return out


def _make_runner(teacher: str, strategy_key: str) -> Callable[[str], Optional[int]]:
    defn = TACTICAL_STRATEGIES[strategy_key]
    cfg = _make_cfg(teacher, strategy_key, defn)
    if teacher == "gemini":
        call_llm = _call_gemini(defn, strategy_key)
    elif teacher == "gpt":
        call_llm = _call_gpt(defn, strategy_key)
    else:
        call_llm = _call_deepseek(defn, strategy_key)

    def run(triggered_by: str = "scheduler") -> Optional[int]:
        return run_tactical_explore_round(
            cfg,
            call_llm,
            triggered_by,
            category_to_side=lambda cat, conf, d=defn: tactical_category_to_side(d, cat, conf),
            catalyst_ok=lambda cat, catl, sig, sym, conf=0.0, d=defn: tactical_catalyst_ok(
                d, catl, sig, sym, conf,
            ),
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

# GPT
run_gpt_pullback_explore_round = _make_runner("gpt", "pullback")
run_gpt_rebound_explore_round = _make_runner("gpt", "rebound")
run_gpt_chase_explore_round = _make_runner("gpt", "chase")
run_gpt_dump_explore_round = _make_runner("gpt", "dump")

TACTICAL_GROUP_SPECS: Dict[str, Tuple[str, ...]] = {
    "pb_rb": ("pullback", "rebound"),
    "ch_dm": ("chase", "dump"),
}
TACTICAL_GROUP_LABELS = {
    "pb_rb": "回多反空",
    "ch_dm": "追涨杀跌",
}
_group_locks: Dict[str, threading.Lock] = {}


def _get_group_lock(key: str) -> threading.Lock:
    if key not in _group_locks:
        _group_locks[key] = threading.Lock()
    return _group_locks[key]


def _make_group_runner(teacher: str, group_key: str) -> Callable[[str], Optional[int]]:
    strategies = TACTICAL_GROUP_SPECS[group_key]
    group_source = f"{teacher}_{group_key}"
    group_label = TACTICAL_GROUP_LABELS[group_key]
    runs_tables = tuple(f"{teacher}_{sk}_explore_runs" for sk in strategies)
    teacher_label = {"gemini": "Gemini", "deepseek": "DeepSeek", "gpt": "GPT"}[teacher]

    def run(triggered_by: str = "scheduler") -> Optional[int]:
        from datetime import datetime, timezone

        from app.services.ai_tactical_explore_schedule import (
            tactical_claim_next_slot,
            tactical_group_round_is_due,
            tactical_next_due_key,
        )
        from app.services.explore_prepared_bundle import get_explore_prepared_bundle
        from app.services.gemini_explore_worker import _connect
        from app.services.reversal_explore_runner import run_tactical_explore_round

        lock = _get_group_lock(group_source)
        if not lock.acquire(blocking=False):
            logger.warning(f"[{teacher_label}{group_label}] 上一轮未结束, 跳过")
            return None

        log_tag = f"{teacher_label}{group_label}"
        asof_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        manual = triggered_by == "manual"
        last_run_id: Optional[int] = None

        try:
            if not manual:
                conn_chk = _connect()
                try:
                    due, due_reason = tactical_group_round_is_due(
                        conn_chk,
                        runs_tables=runs_tables,
                        next_due_key=tactical_next_due_key(group_source),
                        now=asof_utc,
                        manual=False,
                        log_tag=log_tag,
                    )
                    if not due:
                        logger.info(f"[{log_tag}] {due_reason}, 跳过")
                        return None
                    tactical_claim_next_slot(
                        conn_chk,
                        next_due_key=tactical_next_due_key(group_source),
                        now=asof_utc,
                        log_tag=log_tag,
                    )
                    conn_chk.commit()
                finally:
                    conn_chk.close()

            logger.info(f"[{log_tag}] === 合并组开始 ({triggered_by}) ===")
            conn = _connect()
            try:
                allow_rebuild = triggered_by in (
                    "manual", "scheduler", "scheduler_init", "test",
                )
                universe, global_ctx, _ = get_explore_prepared_bundle(
                    conn, log_tag, allow_rebuild=allow_rebuild,
                )
                if len(universe) == 0 and not allow_rebuild:
                    universe, global_ctx, _ = get_explore_prepared_bundle(
                        conn, log_tag, allow_rebuild=True,
                    )
                group_meta = {
                    "strategies": {
                        sk: {"llm_symbol_count": len(universe)}
                        for sk in strategies
                    }
                }
                by_strategy = {sk: list(universe.values()) for sk in strategies}
                logger.info(
                    f"[{log_tag}] 全量共享 universe: "
                    + ", ".join(
                        f"{sk}={group_meta['strategies'][sk]['llm_symbol_count']}"
                        for sk in strategies
                    )
                )
                bundle = (universe, global_ctx)
                family_call = _call_family(teacher, group_key, group_label)
                family_response, family_err = family_call(universe, global_ctx, {})
                for sk in strategies:
                    n_cand = len(by_strategy.get(sk) or [])
                    if n_cand == 0:
                        logger.info(f"[{log_tag}] {sk} 预筛无候选, 跳过 LLM")
                        continue
                    defn = TACTICAL_STRATEGIES[sk]
                    cfg = _make_cfg(teacher, sk, defn)
                    split_response = _split_family_response(family_response, sk)

                    def call_llm(_universe, _global_ctx, _historical_stats, sr=split_response):
                        if family_response is None:
                            return None, family_err
                        return sr, family_err

                    rid = run_tactical_explore_round(
                        cfg,
                        call_llm,
                        triggered_by,
                        category_to_side=lambda cat, conf, d=defn: tactical_category_to_side(
                            d, cat, conf,
                        ),
                        catalyst_ok=lambda cat, catl, sig, sym, conf=0.0, d=defn: tactical_catalyst_ok(
                            d, catl, sig, sym, conf,
                        ),
                        preloaded_bundle=bundle,
                        skip_lock=True,
                        skip_schedule=True,
                    )
                    if rid:
                        last_run_id = rid
            finally:
                conn.close()
            logger.info(f"[{log_tag}] === 合并组结束 last_run_id={last_run_id} ===")
            return last_run_id
        finally:
            lock.release()

    return run


run_gemini_pb_rb_explore_round = _make_group_runner("gemini", "pb_rb")
run_gemini_ch_dm_explore_round = _make_group_runner("gemini", "ch_dm")
run_deepseek_pb_rb_explore_round = _make_group_runner("deepseek", "pb_rb")
run_deepseek_ch_dm_explore_round = _make_group_runner("deepseek", "ch_dm")
run_gpt_pb_rb_explore_round = _make_group_runner("gpt", "pb_rb")
run_gpt_ch_dm_explore_round = _make_group_runner("gpt", "ch_dm")
