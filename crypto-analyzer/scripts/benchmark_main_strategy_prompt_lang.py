#!/usr/bin/env python3
"""主探索 / 主预测 prompt 中英对照（Gemini / DeepSeek / GPT）。

Usage:
  python scripts/benchmark_main_strategy_prompt_lang.py --mode explore --top 20
  python scripts/benchmark_main_strategy_prompt_lang.py --mode predict --top 12
  python scripts/benchmark_main_strategy_prompt_lang.py --mode all --teacher gpt --top 15
"""
from __future__ import annotations

import argparse
import json
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

from app.services.ai_explore_prompt import (
    EXPLORE_LLM_MAX_OUTPUT_TOKENS,
    build_explore_prompt,
    build_explore_prompt_zh,
    parse_explore_llm_json,
)
from app.services.ai_predict_prompt import build_predict_prompt, build_predict_prompt_zh
from app.services.gemini_swan_worker import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_TIMEOUT_S
from app.services.gpt_config import GPT_API_KEY, GPT_BASE_URL, GPT_TIMEOUT_S
from app.services.gpt_llm_client import GPT_JSON_SYSTEM_EN, GPT_JSON_SYSTEM_ZH, gpt_chat_json
from app.services.deepseek_position_advisor import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_TIMEOUT_S,
)
from app.services.reversal_explore_runner import _get_historical_stats


def _load_explore_bundle():
    from app.services.gemini_explore_worker import _connect
    from app.services.explore_prepared_bundle import get_explore_prepared_bundle

    conn = _connect()
    try:
        universe, global_ctx, from_shared = get_explore_prepared_bundle(
            conn, "benchmark_main", allow_rebuild=True,
        )
        return universe, global_ctx, from_shared, conn
    except Exception:
        conn.close()
        raise


def _load_predict_data(top: int):
    from app.services.gpt_predictor import (
        _build_global_context,
        _build_symbol_data,
        _connect,
        _get_top50_symbols,
    )

    conn = _connect()
    try:
        syms = _get_top50_symbols(conn)[:top]
        symbols_data = []
        for sym in syms:
            data = _build_symbol_data(conn, sym)
            if data and data.get("current_price"):
                symbols_data.append(data)
        global_ctx = _build_global_context(conn)
        return symbols_data, global_ctx, conn
    except Exception:
        conn.close()
        raise


def _verdict_summary(parsed: dict | None, *, entry_cats: set[str]) -> str:
    if not parsed:
        return "parse_fail"
    verdicts = parsed.get("verdicts") or []
    hits = [
        v for v in verdicts
        if isinstance(v, dict) and str(v.get("category", "")).lower() in entry_cats
    ]
    summary = (parsed.get("summary_zh") or "")[:80]
    if not hits:
        return f"signals=0 skip={len(verdicts)} summary={summary}"
    h0 = hits[0]
    return (
        f"signals={len(hits)} "
        f"top={h0.get('symbol')} {h0.get('category')} conf={h0.get('confidence')} "
        f"catalyst={str(h0.get('catalyst', ''))[:70]} summary={summary}"
    )


def _call_gemini(prompt: str) -> tuple[dict | None, float, str]:
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
    parsed, err = parse_explore_llm_json(text, "bench_gemini")
    return parsed, time.perf_counter() - t0, err or ""


def _call_deepseek(prompt: str) -> tuple[dict | None, float, str]:
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
    parsed, err = parse_explore_llm_json(text, "bench_deepseek")
    return parsed, time.perf_counter() - t0, err or ""


def _call_gpt(prompt: str, *, system: str) -> tuple[dict | None, float, str]:
    if not GPT_API_KEY:
        return None, 0.0, "no GPT_API_KEY"
    try:
        from openai import OpenAI
    except ImportError:
        return None, 0.0, "no openai"
    client = OpenAI(api_key=GPT_API_KEY, base_url=GPT_BASE_URL)
    t0 = time.perf_counter()
    try:
        text = gpt_chat_json(
            client,
            user_prompt=prompt,
            timeout=GPT_TIMEOUT_S,
            system_prompt=system,
            max_tokens=EXPLORE_LLM_MAX_OUTPUT_TOKENS,
        )
    except Exception as e:
        return None, time.perf_counter() - t0, str(e)[:200]
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        parsed = json.loads(text)
        err = ""
    except json.JSONDecodeError as e:
        parsed, err = parse_explore_llm_json(text, "bench_gpt")
        err = err or str(e)
    return parsed, time.perf_counter() - t0, err or ""


def _run_lang_pair(
    teacher: str,
    mode: str,
    zh: str,
    en: str,
    meta_line: str,
) -> None:
    entry_cats = {"bullish", "bearish"}
    print(f"\n{'=' * 72}")
    print(f"Teacher: {teacher.upper()}  mode={mode}")
    print(meta_line)
    print(f"  ZH prompt {len(zh)} chars | EN prompt {len(en)} chars")

    if teacher == "gemini":
        call = lambda p, _s: _call_gemini(p)
    elif teacher == "deepseek":
        call = lambda p, _s: _call_deepseek(p)
    else:
        call = lambda p, s: _call_gpt(p, system=s)

    for lang, prompt, system in (
        ("zh", zh, GPT_JSON_SYSTEM_ZH),
        ("en", en, GPT_JSON_SYSTEM_EN),
    ):
        if teacher == "gpt":
            parsed, elapsed, err = call(prompt, system)
        else:
            parsed, elapsed, err = call(prompt, system)
        line = _verdict_summary(parsed, entry_cats=entry_cats)
        if err and not parsed:
            line = f"FAIL {err}"
        print(f"  [{lang.upper()}] {elapsed:.1f}s  {line}")
        time.sleep(1.5)


def _bench_explore(top: int, teachers: list[str]) -> None:
    universe, global_ctx, from_shared, conn = _load_explore_bundle()
    print(f"Main EXPLORE ZH vs EN  top={top} bundle_shared={from_shared}")
    hist = _get_historical_stats(conn, "gpt_explore")
    zh, meta = build_explore_prompt_zh(
        universe, global_ctx, hist, max_symbols=top,
    )
    en, _ = build_explore_prompt(
        universe, global_ctx, hist, max_symbols=top,
    )
    conn.close()
    print(f"  explore prompts zh={len(zh)} en={len(en)} chars")
    meta_line = f"  universe TOP={meta.get('llm_symbol_count')}/{meta.get('universe_total')}"
    for t in teachers:
        _run_lang_pair(t, "explore", zh, en, meta_line)


def _bench_predict(top: int, teachers: list[str]) -> None:
    symbols_data, global_ctx, conn = _load_predict_data(top)
    conn.close()
    print(f"Main PREDICT ZH vs EN  symbols={len(symbols_data)}")
    zh = build_predict_prompt_zh(symbols_data, global_ctx)
    en = build_predict_prompt(symbols_data, global_ctx)
    print(f"  predict prompts zh={len(zh)} en={len(en)} chars")
    meta_line = f"  predict symbols={len(symbols_data)} Big4={global_ctx.get('big4_signal')}"
    for t in teachers:
        _run_lang_pair(t, "predict", zh, en, meta_line)


def main():
    ap = argparse.ArgumentParser(description="Main explore/predict ZH vs EN benchmark")
    ap.add_argument("--mode", default="explore", choices=("explore", "predict", "all"))
    ap.add_argument("--top", type=int, default=18, help="explore universe / predict symbol cap")
    ap.add_argument("--teacher", default="", help="gemini|deepseek|gpt; default all")
    args = ap.parse_args()
    teachers = [args.teacher] if args.teacher else ["gemini", "deepseek", "gpt"]
    if args.mode in ("explore", "all"):
        _bench_explore(args.top, teachers)
    if args.mode in ("predict", "all"):
        _bench_predict(args.top, teachers)
    print(f"\n{'=' * 72}")
    print(
        "Production: Gemini/DeepSeek/GPT all use English build_explore/predict/strategy "
        "(build_* delegates to *_en). GPT also uses GPT_JSON_SYSTEM_EN."
    )


if __name__ == "__main__":
    main()
