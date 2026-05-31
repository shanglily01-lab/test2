#!/usr/bin/env python3
"""开仓顾问策略映射与方向闸门 — 无 API 回归."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.open_advisor_strategy_rubrics import (
    build_big4_subjective_block,
    check_direction_gates,
    check_expected_side,
    resolve_strategy_profile,
)
from app.services.gemini_position_advisor import GeminiPositionAdvisor


def test_source_mapping():
    cases = [
        ("gemini_reversal", "reversal", None),
        ("deepseek_pullback", "pullback", "LONG"),
        ("gemini_rebound", "rebound", "SHORT"),
        ("gemini_chase", "chase", "LONG"),
        ("deepseek_dump", "dump", "SHORT"),
        ("gemini_explore", "explore", None),
        ("gemini_predict", "predict", None),
        ("s1_early_long", "s1", "LONG"),
        ("s5_large_oversold", "s5", "LONG"),
        ("BTC_MOMENTUM", "btc_momentum", None),
        ("smart_trader", "smart_trader", None),
        ("trend_follow", "smart_trader", None),
    ]
    for src, key, side in cases:
        p = resolve_strategy_profile(src)
        assert p.key == key, f"{src} -> {p.key} expected {key}"
        assert p.expected_side == side, f"{src} side {p.expected_side}"
    print("[PASS] source_mapping")


def test_direction_gates():
    ok, _ = check_direction_gates("LONG", True, True)
    assert ok
    ok, r = check_direction_gates("SHORT", True, False)
    assert not ok and "allow_short" in r
    ok, r = check_direction_gates("LONG", False, True)
    assert not ok and "allow_long" in r
    print("[PASS] direction_gates")


def test_expected_side():
    p = resolve_strategy_profile("gemini_pullback")
    ok, _ = check_expected_side(p, "LONG")
    assert ok
    ok, r = check_expected_side(p, "SHORT")
    assert not ok and "回调做多" in r
    print("[PASS] expected_side")


def test_open_prompt_contains_rubric():
    ctx = {
        "klines_15m": [{"t": "01-01 00:00", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}],
        "klines_1h": [],
        "big4_signal": "BEARISH",
        "big4_strength": 70,
        "allow_long": True,
        "allow_short": False,
        "btc_6h_change": -2.0,
        "eth_6h_change": -1.0,
        "narrative_1h": "[1h · 整体 24 根趋势] 偏多",
        "narrative_15m": "",
        "rsi_14_1h": 55.0,
    }
    prompt = GeminiPositionAdvisor._build_open_prompt(
        "BTC/USDT", "LONG", 100000.0, "gemini_pullback",
        "近24根上涨 近5根回踩支撑", 5, 3.0, 5.0, 4.0, ctx,
    )
    assert "回调做多" in prompt
    assert "24 根 1h" in prompt
    assert "allow_short" in prompt
    assert "1 根" in prompt and "24 根" in prompt
    print("[PASS] open_prompt")


def main():
    test_source_mapping()
    test_direction_gates()
    test_expected_side()
    test_open_prompt_contains_rubric()
    print("-" * 40)
    print("全部通过")


if __name__ == "__main__":
    main()
