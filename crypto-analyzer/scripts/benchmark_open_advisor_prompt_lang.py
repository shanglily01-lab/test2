#!/usr/bin/env python3
"""Compare open-advisor outcomes: Chinese vs English user prompts (Gemini / DeepSeek / GPT).

Usage:
  python scripts/benchmark_open_advisor_prompt_lang.py
  python scripts/benchmark_open_advisor_prompt_lang.py --symbol ETH/USDT --source gpt_pullback
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os

from dotenv import dotenv_values

for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None and k not in os.environ:
        os.environ[k] = str(v)

# noqa: E402 — env loaded above

from app.services.gemini_position_advisor import GeminiPositionAdvisor
from app.services.open_advisor_strategy_rubrics import (
    build_gpt_open_advisor_prompt,
    build_open_advisor_prompt,
    resolve_strategy_profile,
)
from app.services.gpt_config import GPT_API_KEY, GPT_BASE_URL, GPT_MODEL, GPT_TIMEOUT_S
from app.services.gpt_position_advisor import GPTPositionAdvisor
from app.services.deepseek_position_advisor import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_TIMEOUT_S,
)

SYSTEM_ZH = (
    "你是超级交易大师，负责模拟开仓前审核。"
    "仅输出合法 JSON，含 decision(approve|reject) 与 reason。"
)
SYSTEM_EN = (
    "You are a crypto futures paper-trading open reviewer. "
    "Output ONLY valid JSON with decision (approve|reject) and reason "
    "(Chinese, <=80 chars, for operator UI)."
)

FALLBACK_CTX = {
    "klines_15m": [
        {"t": "06-04 00:00", "o": 100.0, "h": 101.2, "l": 99.8, "c": 100.9, "v": 1200.0},
        {"t": "06-04 00:15", "o": 100.9, "h": 102.0, "l": 100.5, "c": 101.5, "v": 1100.0},
        {"t": "06-04 00:30", "o": 101.5, "h": 102.5, "l": 101.0, "c": 102.1, "v": 1050.0},
        {"t": "06-04 00:45", "o": 102.1, "h": 103.0, "l": 101.8, "c": 102.8, "v": 980.0},
    ],
    "klines_1h": [
        {"t": f"06-03 {h:02d}:00", "o": 95 + h * 0.3, "h": 96 + h * 0.3, "l": 94 + h * 0.3, "c": 95.5 + h * 0.3, "v": 5000}
        for h in range(20, 24)
    ]
    + [
        {"t": f"06-04 {h:02d}:00", "o": 98 + h * 0.5, "h": 99 + h * 0.5, "l": 97.5 + h * 0.5, "c": 98.8 + h * 0.5, "v": 4800}
        for h in range(4)
    ],
    "big4_signal": "NEUTRAL",
    "big4_strength": 12.0,
    "btc_6h_change": 0.8,
    "eth_6h_change": 1.1,
    "narrative_1h": "近24根1h整体抬升，近6根连阳小幅放量，距7d高点约-5%。",
    "narrative_15m": "近4根15m延续上行，回踩浅。",
    "rsi_14_1h": 58.0,
    "below_7d_high_pct": -5.2,
    "above_7d_low_pct": 18.0,
    "allow_long": True,
    "allow_short": True,
}


def _load_ctx(symbol: str) -> dict:
    try:
        from app.utils.config_loader import get_db_config
        adv = GeminiPositionAdvisor(get_db_config())
        ctx = adv._fetch_market_context(symbol)
        ctx["allow_long"] = True
        ctx["allow_short"] = True
        if ctx.get("klines_1h"):
            return ctx
    except Exception as e:
        print(f"[warn] DB context fallback: {e}")
    return dict(FALLBACK_CTX)


def _build_prompts(source: str, symbol: str, side: str, price: float, catalyst: str, ctx: dict):
    profile = resolve_strategy_profile(source)
    fmt = GeminiPositionAdvisor._format_kline_table
    kw = dict(
        profile=profile,
        symbol=symbol,
        side=side,
        price=price,
        source=source,
        catalyst=catalyst,
        leverage=5,
        sl_pct=3.0,
        tp_pct=5.0,
        hold_hours=4.0,
        ctx=ctx,
        format_kline_table=fmt,
    )
    zh = build_open_advisor_prompt(**kw)
    en = build_gpt_open_advisor_prompt(**kw)
    return zh, en


def _call_gemini(prompt: str) -> tuple[dict | None, float, str]:
    adv = GeminiPositionAdvisor({})
    t0 = time.perf_counter()
    out = adv._call_gemini(prompt, open_mode=True)
    return out, time.perf_counter() - t0, "user-only (JSON in prompt)"


def _call_openai_compat(
    prompt: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: int,
    system: str,
) -> tuple[dict | None, float, str]:
    if not api_key:
        return None, 0.0, "no API key"
    try:
        from openai import OpenAI
    except ImportError:
        return None, 0.0, "openai package missing"
    from app.services.ai_explore_prompt import _extract_llm_json_text, _try_parse_json

    t0 = time.perf_counter()
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=512,
            timeout=timeout,
            response_format={"type": "json_object"},
        )
        text = (resp.choices[0].message.content or "").strip()
        parsed, _ = _try_parse_json(_extract_llm_json_text(text))
        elapsed = time.perf_counter() - t0
        if parsed is None:
            return None, elapsed, text[:200]
        decision = str(parsed.get("decision", "")).strip().lower()
        if decision not in ("approve", "reject"):
            decision = "approve"
        return {
            "decision": decision,
            "reason": str(parsed.get("reason", ""))[:500],
        }, elapsed, ""
    except Exception as e:
        return None, time.perf_counter() - t0, str(e)[:200]


def _snippet(text: str, n: int = 280) -> str:
    t = text.replace("\n", " ")
    return t[:n] + ("…" if len(t) > n else "")


def _run_provider(name: str, source: str, zh: str, en: str, call_fn) -> None:
    print(f"\n{'=' * 72}")
    print(f"Provider: {name}  source={source}")
    print(f"  ZH prompt chars: {len(zh)}  |  EN prompt chars: {len(en)}")
    print(f"  ZH head: {_snippet(zh, 120)}")
    print(f"  EN head: {_snippet(en, 120)}")

    for lang, prompt, system in (
        ("zh", zh, SYSTEM_ZH if name != "Gemini" else None),
        ("en", en, SYSTEM_EN if name != "Gemini" else None),
    ):
        if name == "Gemini":
            result, elapsed, err = call_fn(prompt)
        else:
            result, elapsed, err = call_fn(prompt, system=system)
        label = lang.upper()
        if result:
            print(
                f"  [{label}] {elapsed:.1f}s  decision={result.get('decision')}  "
                f"reason={result.get('reason', '')[:100]}"
            )
        else:
            print(f"  [{label}] {elapsed:.1f}s  FAILED  {err or 'no parse'}")
        time.sleep(1.2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--side", default="LONG")
    ap.add_argument("--price", type=float, default=0.0, help="0 = skip price in label only")
    ap.add_argument(
        "--source",
        default="",
        help="single source e.g. gpt_chase; default runs gemini_chase, deepseek_chase, gpt_chase",
    )
    args = ap.parse_args()

    ctx = _load_ctx(args.symbol)
    price = args.price or 100.0
    catalyst = (
        "Test: 24h channel up, last 6 bars shallow pullback then recovery; "
        "RSI ~58, room below 7d high ~5%; not overbought chase."
    )

    cases = (
        [args.source]
        if args.source
        else ["gemini_chase", "deepseek_chase", "gpt_chase"]
    )

    print("Open advisor language benchmark")
    print(f"symbol={args.symbol} side={args.side} price={price}")
    print(f"ctx: klines_1h={len(ctx.get('klines_1h', []))} rsi={ctx.get('rsi_14_1h')}")

    for source in cases:
        zh, en = _build_prompts(source, args.symbol, args.side, price, catalyst, ctx)
        if source.startswith("gemini"):
            prov = "Gemini"

            def gcall(p, system=None):
                return _call_gemini(p)

            _run_provider(prov, source, zh, en, gcall)
        elif source.startswith("deepseek"):
            prov = "DeepSeek"

            def dscall(p, system=SYSTEM_ZH):
                return _call_openai_compat(
                    p,
                    api_key=DEEPSEEK_API_KEY,
                    base_url=DEEPSEEK_BASE_URL,
                    model=DEEPSEEK_MODEL,
                    timeout=DEEPSEEK_TIMEOUT_S,
                    system=system,
                )

            _run_provider(prov, source, zh, en, dscall)
        elif source.startswith("gpt"):
            prov = "GPT"

            def gptcall(p, system=SYSTEM_ZH):
                return _call_openai_compat(
                    p,
                    api_key=GPT_API_KEY,
                    base_url=GPT_BASE_URL,
                    model=GPT_MODEL,
                    timeout=GPT_TIMEOUT_S,
                    system=system,
                )

            _run_provider(prov, source, zh, en, gptcall)
        else:
            print(f"[skip] unknown source prefix: {source}")

    print(f"\n{'=' * 72}")
    print("Done. Production: Gemini/DeepSeek use ZH prompt; GPT open uses EN prompt.")


if __name__ == "__main__":
    main()
