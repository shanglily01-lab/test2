#!/usr/bin/env python3
"""主探索/主预测 prompt 与门槛回归（无 API）."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values

for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None and k not in os.environ:
        os.environ[k] = v


def test_thresholds():
    from app.services.ai_explore_prompt import (
        EXPLORE_CONFIDENCE_THRESHOLD,
        PREDICT_CONFIDENCE_THRESHOLD,
        AI_POSITION_HOLD_HOURS,
    )
    from app.services.gemini_explore_worker import EXPLORE_CONFIDENCE_THRESHOLD as G
    from app.services.deepseek_explore_worker import EXPLORE_CONFIDENCE_THRESHOLD as D

    assert EXPLORE_CONFIDENCE_THRESHOLD == 0.60
    assert PREDICT_CONFIDENCE_THRESHOLD == 0.60
    assert AI_POSITION_HOLD_HOURS == 4
    assert G == D == 0.60
    print("[PASS] thresholds unified at 0.60")


def test_universe_scoring():
    from app.services.ai_explore_prompt import _explore_universe_score, prepare_universe_for_llm

    volatile = {"change_24h": 35, "quote_volume_24h": 1e7, "tech": {"rsi_14_1h": 70}}
    moderate = {"change_24h": 8, "quote_volume_24h": 1e7, "tech": {"rsi_14_1h": 62}}
    assert _explore_universe_score(moderate) > _explore_universe_score(volatile)

    universe = {f"S{i}": {"symbol": f"S{i}", "change_24h": i, "tech": {}} for i in range(60)}
    selected, meta = prepare_universe_for_llm(universe, max_symbols=50)
    assert len(selected) == 50
    assert meta["llm_symbol_count"] == 50
    print("[PASS] universe scoring favors moderate over extreme")


def test_explore_prompt():
    from app.services.ai_explore_prompt import build_explore_prompt

    u = {
        "AAA/USDT": {
            "symbol": "AAA/USDT",
            "change_24h": 10,
            "tech": {"rsi_14_1h": 60},
            "kline_narrative": {"1h": "[1h · 24-bar trend] bullish"},
        }
    }
    p, meta = build_explore_prompt(u, {}, {})
    assert "24-bar" in p.lower() or "24 bar" in p.lower()
    assert "technical score" in p.lower()
    print(f"[PASS] explore prompt EN ({len(p)} chars, {meta['llm_symbol_count']} syms)")


def test_predict_prompt():
    from app.services.ai_predict_prompt import build_predict_prompt
    import inspect
    import app.services.gemini_predictor as gp
    import app.services.deepseek_predictor as dp

    p = build_predict_prompt(
        [{"symbol": "AAA/USDT", "kline_narrative": {"1h": "x"}, "current_price": 1.0}],
        {"big4_signal": "NEUTRAL"},
    )
    assert "4 hours" in p
    assert "24-bar" in p.lower() or "24 bar" in p.lower()
    assert "build_predict_prompt" in inspect.getsource(gp._call_gemini_predict)
    assert "build_predict_prompt" in inspect.getsource(dp._call_deepseek_predict)
    print("[PASS] predict prompts EN (shared ai_predict_prompt)")


def test_catalyst_gate_predict():
    from app.services.ai_explore_prompt import (
        explore_catalyst_technical_ok,
        sym_data_for_catalyst_gate,
    )

    sym = sym_data_for_catalyst_gate({
        "rsi_14_1h": 55,
        "kline_narrative": {"1h": "整体24根偏多 近5根连阳"},
    })
    good = "1h 整体24根偏多 近5根连阳 15m 同向 RSI 1h=55 EMA20 支撑"
    ok, _ = explore_catalyst_technical_ok(good, "RSI=55", sym)
    assert ok

    bad = "24h 已涨 18% 涨幅过大应回调"
    ok, reason = explore_catalyst_technical_ok(bad, "24h+18%", sym)
    assert not ok, reason
    print("[PASS] predict catalyst gate")


def test_sym_data_for_gate():
    from app.services.ai_explore_prompt import sym_data_for_catalyst_gate

    d = sym_data_for_catalyst_gate({"rsi_14_1h": 48.0, "tech": {}})
    assert d["tech"]["rsi_14_1h"] == 48.0
    print("[PASS] sym_data_for_catalyst_gate")


def test_gpt_en_prompts():
    from app.services.gpt_llm_client import GPT_JSON_SYSTEM_EN
    import inspect
    import app.services.gpt_explore_worker as gew
    import app.services.gpt_predictor as gp

    assert "JSON" in GPT_JSON_SYSTEM_EN
    explore_src = inspect.getsource(gew._call_gpt_explore)
    assert "build_explore_prompt" in explore_src
    assert "GPT_JSON_SYSTEM_EN" in explore_src
    predict_src = inspect.getsource(gp._call_gpt_predict)
    assert "build_predict_prompt" in predict_src
    assert "GPT_JSON_SYSTEM_EN" in predict_src
    import app.services.gemini_position_advisor as gpa
    open_src = inspect.getsource(gpa.GeminiPositionAdvisor._build_open_prompt)
    hold_src = inspect.getsource(gpa.GeminiPositionAdvisor._build_prompt)
    assert "build_open_advisor_prompt" in open_src
    assert "paper position" in hold_src.lower()
    print("[PASS] GPT + advisor prompts English")


def test_gpt_llm_aligns_gemini():
    from app.services.ai_explore_prompt import EXPLORE_LLM_MAX_OUTPUT_TOKENS
    from app.services.gpt_llm_client import GPT_LLM_TEMPERATURE
    import inspect
    import app.services.gpt_explore_worker as gew

    assert GPT_LLM_TEMPERATURE == 0.1
    src = inspect.getsource(gew._call_gpt_explore)
    assert "gpt_chat_json" in src
    assert "EXPLORE_LLM_MAX_OUTPUT_TOKENS" in src
    assert "2200" not in src
    print("[PASS] GPT explore invoke (low temp, 8k tokens)")


def main():
    tests = [
        test_thresholds,
        test_universe_scoring,
        test_explore_prompt,
        test_predict_prompt,
        test_catalyst_gate_predict,
        test_sym_data_for_gate,
        test_gpt_en_prompts,
        test_gpt_llm_aligns_gemini,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
        except Exception as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    if failed:
        sys.exit(1)
    print("-" * 40)
    print("全部通过")


if __name__ == "__main__":
    main()
