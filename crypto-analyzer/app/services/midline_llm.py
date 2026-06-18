"""中线策略 — Gemini / DeepSeek LLM 调用."""
from __future__ import annotations

import os
import time
from typing import Optional, Tuple

from loguru import logger

from app.services.ai_explore_prompt import parse_explore_llm_json
from app.services.ai_midline_explore_prompt import (
    MIDLINE_LLM_MAX_OUTPUT_TOKENS,
    build_midline_prompt,
)
from app.services.gemini_swan_worker import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_TIMEOUT_S,
)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("DeepSeek_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_TIMEOUT_S = int(os.getenv("DEEPSEEK_TIMEOUT_S", "180"))


def call_midline_llm(
    teacher: str,
    profile: str,
    universe: dict,
    global_ctx: dict,
    historical_stats: dict,
    meta: dict,
) -> Tuple[Optional[dict], Optional[str]]:
    """teacher: gemini | deepseek。返回 (parsed, error_msg)。"""
    prompt = build_midline_prompt(
        profile, universe, global_ctx, historical_stats, meta=meta,
    )
    tag = f"中线/{teacher}/{profile}"
    logger.info(
        f"[{tag}] prompt 长度={len(prompt)} chars (~{len(prompt) // 4} tokens), "
        f"送模 {meta.get('llm_symbol_count')}/{meta.get('universe_total')} symbols"
    )
    if teacher == "gemini":
        parsed, err = _call_gemini(prompt, tag)
    elif teacher == "deepseek":
        parsed, err = _call_deepseek(prompt, tag)
    else:
        return None, f"unknown teacher: {teacher}"
    if parsed is None:
        return None, err
    parsed["_prompt"] = prompt
    return parsed, err


def _call_gemini(prompt: str, tag: str) -> Tuple[Optional[dict], Optional[str]]:
    if not GEMINI_API_KEY:
        return None, "GEMINI_API_KEY 未设置"
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return None, "缺 google-genai 依赖"

    client = genai.Client(api_key=GEMINI_API_KEY)
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_S * 1000),
    )
    t0 = time.time()
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt, config=cfg,
        )
    except Exception as e:
        logger.error(f"[{tag}] Gemini 调用失败: {e}")
        return None, f"API: {e}"

    text = (resp.text or "").strip()
    logger.info(f"[{tag}] gemini 用时 {time.time() - t0:.1f}s, output_len={len(text)}")
    parsed, parse_err = parse_explore_llm_json(text, tag)
    if parsed is None:
        return None, f"JSON解析失败: {parse_err}"
    parsed["_raw_response"] = text
    return parsed, parse_err


def _call_deepseek(prompt: str, tag: str) -> Tuple[Optional[dict], Optional[str]]:
    if not DEEPSEEK_API_KEY:
        return None, "DEEPSEEK_API_KEY 未设置"
    t0 = time.time()
    try:
        from openai import OpenAI
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是专业的加密货币中线波段分析师。只能输出合法 JSON。"
                        "本任务方向已固定，勿输出相反 category。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=MIDLINE_LLM_MAX_OUTPUT_TOKENS,
            timeout=DEEPSEEK_TIMEOUT_S,
            response_format={"type": "json_object"},
        )
    except ImportError:
        return None, "缺 openai 库"
    except Exception as e:
        logger.error(f"[{tag}] DeepSeek 调用失败: {e}")
        return None, f"API: {e}"

    text = (resp.choices[0].message.content or "").strip()
    logger.info(f"[{tag}] deepseek 用时 {time.time() - t0:.1f}s, output_len={len(text)}")
    parsed, parse_err = parse_explore_llm_json(text, tag)
    if parsed is None:
        return None, f"JSON解析失败: {parse_err}"
    parsed["_raw_response"] = text
    return parsed, parse_err
