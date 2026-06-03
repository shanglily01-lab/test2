#!/usr/bin/env python3
"""开仓顾问策略映射与方向闸门 — 无 API 回归."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.open_advisor_strategy_rubrics import (
    build_big4_subjective_block,
    build_gpt_open_advisor_prompt,
    build_strategy_review_steps,
    check_direction_gates,
    check_expected_side,
    precheck_open_advisor,
    resolve_strategy_profile,
)
from app.services.gemini_position_advisor import GeminiPositionAdvisor
from app.services.gpt_position_advisor import GPTPositionAdvisor


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


def test_per_strategy_prompts_differ():
    ctx = {
        "klines_15m": [{"t": "01-01 00:00", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}],
        "klines_1h": [],
        "big4_signal": "NEUTRAL",
        "big4_strength": 0,
        "allow_long": True,
        "allow_short": True,
        "btc_6h_change": 0,
        "eth_6h_change": 0,
        "narrative_1h": "",
        "narrative_15m": "",
        "rsi_14_1h": 60.0,
        "below_7d_high_pct": -8.0,
        "above_7d_low_pct": 10.0,
    }
    chase_p = GeminiPositionAdvisor._build_open_prompt(
        "BTC/USDT", "LONG", 1.0, "gemini_chase", "test", 5, 3.0, 5.0, 4.0, ctx,
    )
    pull_p = GeminiPositionAdvisor._build_open_prompt(
        "BTC/USDT", "LONG", 1.0, "gemini_pullback", "test", 5, 3.0, 5.0, 4.0, ctx,
    )
    explore_p = GeminiPositionAdvisor._build_open_prompt(
        "BTC/USDT", "LONG", 1.0, "gemini_explore", "test", 5, 3.0, 5.0, 4.0, ctx,
    )
    assert "追涨做多" in chase_p and "profile:    chase" in chase_p
    assert "回调做多" in pull_p and "profile:    pullback" in pull_p
    assert "禁止" in chase_p and "其它策略" in chase_p
    assert "（仅四战术）" in chase_p and "（仅四战术）" in pull_p
    assert "（仅四战术）" not in explore_p
    assert "探索专属" in explore_p or "勿用战术" in explore_p
    assert chase_p != pull_p
    print("[PASS] per_strategy_prompts_differ")


def test_precheck_chase_rsi():
    p = resolve_strategy_profile("gemini_chase")
    ok, reason = precheck_open_advisor(
        p, "LONG", {"rsi_14_1h": 72.0, "below_7d_high_pct": -10.0},
    )
    assert not ok and "追涨" in reason
    ok2, _ = precheck_open_advisor(
        p, "LONG", {"rsi_14_1h": 62.0, "below_7d_high_pct": -10.0},
    )
    assert ok2
    print("[PASS] precheck_chase_rsi")


def test_review_steps_by_profile():
    chase_steps = build_strategy_review_steps(resolve_strategy_profile("gemini_chase"))
    explore_steps = build_strategy_review_steps(resolve_strategy_profile("gemini_explore"))
    assert "（仅四战术）" in chase_steps
    assert "勿" in explore_steps and "四战术" in explore_steps
    assert "（仅四战术）" not in explore_steps
    print("[PASS] review_steps_by_profile")


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
    assert "profile:    pullback" in prompt
    assert "24 根 1h" in prompt
    assert "allow_short" in prompt
    assert "1 根" in prompt and "24 根" in prompt
    print("[PASS] open_prompt")


def test_hold_prompt_kline_focus():
    ctx = {
        "klines_15m": [
            {"t": f"01-01 {i:02d}:00", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}
            for i in range(6)
        ],
        "klines_1h": [
            {"t": f"01-01 {i:02d}:00", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}
            for i in range(4)
        ],
        "big4_signal": "BEARISH",
        "big4_strength": 80,
        "btc_6h_change": -3.0,
        "eth_6h_change": -2.0,
        "rsi_14_1h": 48.0,
    }
    pos = {
        "entry_price": 100.0,
        "leverage": 5,
        "position_side": "LONG",
        "symbol": "BTC/USDT",
        "hold_hours": 3.0,
        "source": "gemini_explore",
    }
    prompt = GeminiPositionAdvisor._build_prompt(pos, 101.0, ctx)
    assert "近 4 根 1h" in prompt
    assert "近 6 根 15m" in prompt
    assert "不得" in prompt and "Big4" in prompt
    assert "DECISION RULES" not in prompt
    assert "Strong opposite Big4" not in prompt
    assert "01-01 05:00" in prompt  # last of 6 15m bars
    assert "盈亏档位" in prompt
    assert "客观统计" in prompt
    print("[PASS] hold_prompt_kline_focus")


def test_gpt_open_prompt_english_tactical():
    ctx = {
        "klines_15m": [{"t": "01-01 00:00", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}],
        "klines_1h": [],
        "big4_signal": "NEUTRAL",
        "big4_strength": 0,
        "allow_long": True,
        "allow_short": True,
        "btc_6h_change": 0,
        "eth_6h_change": 0,
        "narrative_1h": "",
        "narrative_15m": "",
        "rsi_14_1h": 55.0,
        "below_7d_high_pct": -6.0,
        "above_7d_low_pct": 10.0,
    }
    p = GPTPositionAdvisor._build_gpt_open_prompt(
        "BTC/USDT", "LONG", 1.0, "gpt_chase", "momentum test", 5, 3.0, 5.0, 4.0, ctx,
    )
    assert "Momentum chase long" in p
    assert "gpt_tactical_precheck_pass" not in p
    assert "do not batch-reject on macro FUD alone" in p
    assert "Chinese chars" in p
    profile = resolve_strategy_profile("gpt_pullback")
    p2 = build_gpt_open_advisor_prompt(
        profile=profile,
        symbol="ETH/USDT",
        side="LONG",
        price=1.0,
        source="gpt_pullback",
        catalyst="dip",
        leverage=5,
        sl_pct=3.0,
        tp_pct=5.0,
        hold_hours=4.0,
        ctx=ctx,
        format_kline_table=GeminiPositionAdvisor._format_kline_table,
    )
    assert "Pullback long" in p2
    print("[PASS] gpt_open_prompt_english_tactical")


def test_losing_hold_temper():
    s15_bad = {"for": 1, "against": 5, "trail_against": 4, "last3": "阴阳阴", "summary": ""}
    s1h_bad = {"for": 0, "against": 3, "trail_against": 2, "last3": "阴阴阴", "summary": ""}
    act, reason = GeminiPositionAdvisor._temper_losing_hold(
        -16.0, "hold", "还能扛", "LONG", s15_bad, s1h_bad,
    )
    assert act == "sell", act
    assert "复核" in reason

    s15_ok = {"for": 4, "against": 2, "trail_against": 0, "last3": "阳阳阴", "summary": ""}
    s1h_ok = {"for": 3, "against": 1, "trail_against": 0, "last3": "阳阳阴", "summary": ""}
    act2, _ = GeminiPositionAdvisor._temper_losing_hold(
        -8.0, "hold", "15m仍顺向", "LONG", s15_ok, s1h_ok,
    )
    assert act2 == "hold"
    print("[PASS] losing_hold_temper")


def main():
    test_source_mapping()
    test_direction_gates()
    test_expected_side()
    test_per_strategy_prompts_differ()
    test_precheck_chase_rsi()
    test_review_steps_by_profile()
    test_open_prompt_contains_rubric()
    test_gpt_open_prompt_english_tactical()
    test_hold_prompt_kline_focus()
    test_losing_hold_temper()
    print("-" * 40)
    print("全部通过")


if __name__ == "__main__":
    main()
