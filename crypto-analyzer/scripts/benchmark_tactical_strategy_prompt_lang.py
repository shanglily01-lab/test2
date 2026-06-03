#!/usr/bin/env python3
"""战术 AI 策略 prompt 对照：中文 vs 英文（Gemini / DeepSeek / GPT 探索轮 LLM）。

非开仓顾问。使用与生产相同的 build_strategy_prompt / build_strategy_prompt_en + 解析链。

Usage:
  python scripts/benchmark_tactical_strategy_prompt_lang.py
  python scripts/benchmark_tactical_strategy_prompt_lang.py --strategy chase --top 20
  python scripts/benchmark_tactical_strategy_prompt_lang.py --teacher gpt
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values

for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None and k not in os.environ:
        os.environ[k] = str(v)

from app.services.ai_tactical_explore_prompts import (
    TACTICAL_STRATEGIES,
    build_strategy_prompt,
    build_strategy_prompt_zh,
    parse_tactical_llm_json,
)
from app.services.gemini_swan_worker import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_TIMEOUT_S
from app.services.gpt_config import GPT_API_KEY, GPT_BASE_URL, GPT_MODEL, GPT_TIMEOUT_S
from app.services.gpt_llm_client import GPT_JSON_SYSTEM_EN, GPT_JSON_SYSTEM_ZH, gpt_chat_json
from app.services.deepseek_position_advisor import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_TIMEOUT_S,
)
from app.services.ai_explore_prompt import EXPLORE_LLM_MAX_OUTPUT_TOKENS
from app.services.reversal_explore_runner import _get_historical_stats


def _load_bundle():
    from app.utils.config_loader import get_db_config
    from app.services.explore_prepared_bundle import get_explore_prepared_bundle
    from app.services.gemini_explore_worker import _connect

    conn = _connect()
    try:
        universe, global_ctx, from_shared = get_explore_prepared_bundle(
            conn, "benchmark_tactical", allow_rebuild=True,
        )
        return universe, global_ctx, from_shared, conn
    except Exception:
        conn.close()
        raise


def _entry_summary(parsed: dict | None) -> str:
    if not parsed:
        return "parse_fail"
    entries = [
        v for v in (parsed.get("verdicts") or [])
        if isinstance(v, dict) and str(v.get("category", "")).lower() == "entry"
    ]
    summary = (parsed.get("summary_zh") or "")[:80]
    if not entries:
        return f"entries=0 summary={summary}"
    e0 = entries[0]
    return (
        f"entries={len(entries)} "
        f"top={e0.get('symbol')} conf={e0.get('confidence')} "
        f"catalyst={str(e0.get('catalyst', ''))[:70]} "
        f"summary={summary}"
    )


def _call_gemini(prompt: str, defn) -> tuple[dict | None, float, str]:
    if not GEMINI_API_KEY:
        return None, 0.0, "no GEMINI_API_KEY"
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return None, 0.0, "no google-genai"
    client = genai.Client(api_key=GEMINI_API_KEY)
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.1,
        http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_S * 1000),
        max_output_tokens=EXPLORE_LLM_MAX_OUTPUT_TOKENS,
    )
    t0 = time.perf_counter()
    try:
        resp = client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", GEMINI_MODEL),
            contents=prompt,
            config=cfg,
        )
        text = (resp.text or "").strip()
    except Exception as e:
        return None, time.perf_counter() - t0, str(e)[:200]
    parsed, err = parse_tactical_llm_json(text, "bench_gemini", defn.fixed_side)
    return parsed, time.perf_counter() - t0, err or ""


def _call_deepseek(prompt: str, defn) -> tuple[dict | None, float, str]:
    if not DEEPSEEK_API_KEY:
        return None, 0.0, "no DEEPSEEK_API_KEY"
    try:
        from openai import OpenAI
    except ImportError:
        return None, 0.0, "no openai"
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    t0 = time.perf_counter()
    try:
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=EXPLORE_LLM_MAX_OUTPUT_TOKENS,
            temperature=0.1,
            timeout=DEEPSEEK_TIMEOUT_S,
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return None, time.perf_counter() - t0, str(e)[:200]
    parsed, err = parse_tactical_llm_json(text, "bench_deepseek", defn.fixed_side)
    return parsed, time.perf_counter() - t0, err or ""


def _call_gpt(prompt: str, defn, *, system: str) -> tuple[dict | None, float, str]:
    if not GPT_API_KEY:
        return None, 0.0, "no OPENAI_API_KEY"
    try:
        from openai import OpenAI
    except ImportError:
        return None, 0.0, "no openai"
    client = OpenAI(api_key=GPT_API_KEY, base_url=GPT_BASE_URL)
    t0 = time.perf_counter()
    try:
        text = gpt_chat_json(
            client, user_prompt=prompt, timeout=GPT_TIMEOUT_S, system_prompt=system,
        )
    except Exception as e:
        return None, time.perf_counter() - t0, str(e)[:200]
    parsed, err = parse_tactical_llm_json(text, "bench_gpt", defn.fixed_side)
    return parsed, time.perf_counter() - t0, err or ""


def _run_teacher(
    teacher: str,
    strategy_key: str,
    zh: str,
    en: str,
    defn,
    meta: dict,
) -> None:
    print(f"\n{'=' * 72}")
    print(f"Teacher: {teacher.upper()}  strategy={strategy_key} ({defn.title_zh})")
    print(
        f"  universe TOP={meta.get('llm_symbol_count')} "
        f"(precheck_dropped={meta.get('precheck_dropped', 0)})"
    )
    print(f"  ZH prompt {len(zh)} chars | EN prompt {len(en)} chars")

    if teacher == "gemini":
        call = lambda p, _s: _call_gemini(p, defn)
    elif teacher == "deepseek":
        call = lambda p, _s: _call_deepseek(p, defn)
    else:
        call = lambda p, s: _call_gpt(p, defn, system=s)

    for lang, prompt, system in (
        ("zh", zh, GPT_JSON_SYSTEM_ZH),
        ("en", en, GPT_JSON_SYSTEM_EN),
    ):
        if teacher == "gemini":
            parsed, elapsed, err = call(prompt, system)
        else:
            parsed, elapsed, err = call(prompt, system)
        line = _entry_summary(parsed)
        if err and not parsed:
            line = f"FAIL {err}"
        print(f"  [{lang.upper()}] {elapsed:.1f}s  {line}")
        time.sleep(1.5)


def main():
    ap = argparse.ArgumentParser(description="Tactical AI strategy ZH vs EN prompt benchmark")
    ap.add_argument("--strategy", default="chase", choices=sorted(TACTICAL_STRATEGIES.keys()))
    ap.add_argument("--top", type=int, default=20, help="LLM universe size (default 20 for cost)")
    ap.add_argument(
        "--teacher",
        default="",
        help="gemini|deepseek|gpt only; default all three",
    )
    args = ap.parse_args()

    defn = TACTICAL_STRATEGIES[args.strategy]
    universe, global_ctx, from_shared, conn = _load_bundle()
    print("Tactical AI strategy prompt benchmark (NOT open advisor)")
    print(f"strategy={args.strategy} top={args.top} bundle_shared={from_shared}")

    teachers = (
        [args.teacher]
        if args.teacher
        else ["gemini", "deepseek", "gpt"]
    )
    hist_by_teacher = {}
    for t in teachers:
        source = f"{t}_{args.strategy}"
        hist_by_teacher[t] = _get_historical_stats(conn, source)

    zh, meta_zh = build_strategy_prompt_zh(
        args.strategy, universe, global_ctx, hist_by_teacher[teachers[0]],
        max_symbols=args.top,
    )
    en, meta_en = build_strategy_prompt(
        args.strategy, universe, global_ctx, hist_by_teacher[teachers[0]],
        max_symbols=args.top,
    )
    conn.close()
    print(f"  tactical {args.strategy} prompts zh={len(zh)} en={len(en)} chars")

    for t in teachers:
        _run_teacher(t, args.strategy, zh, en, defn, meta_zh)

    print(f"\n{'=' * 72}")
    print("Production: all teachers use English build_strategy_prompt (delegates to _en).")
    print("GPT additionally uses GPT_JSON_SYSTEM_EN.")


if __name__ == "__main__":
    main()
