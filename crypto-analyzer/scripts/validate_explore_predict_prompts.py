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
    assert "技术面评分" in str(meta) or meta["llm_symbol_count"] == 50
    print("[PASS] universe scoring favors moderate over extreme")


def test_explore_prompt():
    from app.services.ai_explore_prompt import build_explore_prompt

    u = {
        "AAA/USDT": {
            "symbol": "AAA/USDT",
            "change_24h": 10,
            "tech": {"rsi_14_1h": 60},
            "kline_narrative": {"1h": "[1h · 整体 24 根趋势] 偏多"},
        }
    }
    p, meta = build_explore_prompt(u, {}, {})
    assert "24 根" in p and "4~6" in p
    assert "技术面评分" in p
    assert "|24h涨跌|" not in p or "非单纯" in p
    print(f"[PASS] explore prompt ({len(p)} chars, {meta['llm_symbol_count']} syms)")


def test_predict_prompt():
    from app.services.gemini_predictor import PREDICT_PROMPT_TEMPLATE
    from app.services.deepseek_predictor import PREDICT_PROMPT_TEMPLATE as DS

    assert "24根整体" in PREDICT_PROMPT_TEMPLATE
    assert "4~6" in PREDICT_PROMPT_TEMPLATE
    assert "0.60-0.64" in PREDICT_PROMPT_TEMPLATE
    assert "24根整体" in DS
    print("[PASS] predict prompts aligned")


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


def main():
    tests = [
        test_thresholds,
        test_universe_scoring,
        test_explore_prompt,
        test_predict_prompt,
        test_catalyst_gate_predict,
        test_sym_data_for_gate,
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
