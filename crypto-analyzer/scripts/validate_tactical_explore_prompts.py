#!/usr/bin/env python3
"""四战术策略 prompt / catalyst 校验回归（无 API）."""
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


def _sym(rsi=55.0, b7h=-20.0, a7l=15.0, narr_1h="偏多 上升 连阳"):
    return {
        "symbol": "TEST/USDT",
        "tech": {
            "rsi_14_1h": rsi,
            "below_7d_high_pct": b7h,
            "above_7d_low_pct": a7l,
        },
        "kline_narrative": {"1h": narr_1h, "1d": "震荡 上升"},
    }


def test_pullback_gates():
    from app.services.ai_tactical_explore_prompts import (
        PULLBACK_LONG,
        tactical_catalyst_ok,
    )

    good = (
        "1h 近24根上升通道 近5根连续阴线回踩 EMA20 支撑企稳 "
        "15m 下影 RSI 1h 从 58 回落至 48"
    )
    ok, _ = tactical_catalyst_ok(PULLBACK_LONG, good, "", _sym(rsi=52), 0.6)
    assert ok, "valid pullback"

    bad = "1h 连阴下跌 跌多了 抄底 RSI 35"
    ok, reason = tactical_catalyst_ok(PULLBACK_LONG, bad, "", _sym(rsi=35, narr_1h="偏空 连阴"), 0.6)
    assert not ok, reason


def test_rebound_gates():
    from app.services.ai_tactical_explore_prompts import REBOUND_SHORT, tactical_catalyst_ok

    good = (
        "1d 见顶后下降 1h 近24根下降通道 近4根缩量反弹碰前高 "
        "15m 上影 量能不支持反弹 相对高点 RSI 1h 52"
    )
    ok, _ = tactical_catalyst_ok(
        REBOUND_SHORT, good, "", _sym(rsi=52, b7h=-8, narr_1h="偏空 回落"), 0.6,
    )
    assert ok

    bad = "1h 强势突破 放量反弹 做多"
    ok, _ = tactical_catalyst_ok(REBOUND_SHORT, bad, "", _sym(rsi=65), 0.6)
    assert not ok


def test_chase_gates():
    from app.services.ai_tactical_explore_prompts import CHASE_LONG, tactical_catalyst_ok

    good = (
        "1h 近24根持续上涨 近6根连阳 无明显回调 量能未放大 趋势延续 RSI 1h 62"
    )
    ok, _ = tactical_catalyst_ok(
        CHASE_LONG, good, "", _sym(rsi=62, narr_1h="偏多 上升"), 0.6,
    )
    assert ok

    bad = "大幅回踩支撑反弹 回调到位 做多"
    ok, _ = tactical_catalyst_ok(CHASE_LONG, bad, "", _sym(rsi=50), 0.6)
    assert not ok


def test_dump_gates():
    from app.services.ai_tactical_explore_prompts import DUMP_SHORT, tactical_catalyst_ok

    good = (
        "1d 1h 近24根下跌趋势 近5根连阴 反弹无量 量价不支持 趋势未改 RSI 1h 38"
    )
    ok, _ = tactical_catalyst_ok(
        DUMP_SHORT, good, "", _sym(rsi=38, narr_1h="偏空 连阴"), 0.6,
    )
    assert ok

    bad = "1h 放量反弹突破 支撑 做多"
    ok, _ = tactical_catalyst_ok(DUMP_SHORT, bad, "", _sym(rsi=60), 0.6)
    assert not ok


def test_single_bar_rejected():
    from app.services.ai_tactical_explore_prompts import PULLBACK_LONG, tactical_catalyst_ok

    bad = "最近1根1h 大阴线 支撑 做多"
    ok, reason = tactical_catalyst_ok(PULLBACK_LONG, bad, "", _sym(), 0.6)
    assert not ok, reason
    assert "4~6" in reason or "1 根" in reason


def test_kline_narrative_24_split():
    from app.services.data_cache_service import _make_kline_narrative

    rows = []
    base_ot = 1_700_000_000_000
    for i in range(24):
        c = 100.0 + i * 0.5
        rows.append({
            "open_time": base_ot + i * 3_600_000,
            "open_price": c - 0.2,
            "high_price": c + 0.3,
            "low_price": c - 0.4,
            "close_price": c,
            "volume": 1000 + i,
        })
    narr = _make_kline_narrative(rows, "1h")
    assert "整体 24 根" in narr
    assert "最近 6 根" in narr


def test_prompt_build():
    from app.services.ai_tactical_explore_prompts import (
        TACTICAL_STRATEGIES,
        build_strategy_prompt,
    )

    u = {
        "AAA": {"symbol": "AAA", "change_24h": 10, "tech": {"rsi_14_1h": 70}},
        "BBB": {"symbol": "BBB", "change_24h": -10, "tech": {"rsi_14_1h": 30}},
    }
    for key in TACTICAL_STRATEGIES:
        prompt, meta = build_strategy_prompt(key, u, {}, {})
        assert meta["selection"] == f"tactical_{key}"
        assert "|24h涨跌| 取 TOP" not in prompt
        assert "非单纯 |24h涨跌|" in prompt or "非单纯" in prompt
        defn = TACTICAL_STRATEGIES[key]
        assert defn.title_zh in prompt
        if key == "pullback":
            assert "上涨趋势" in prompt and "支撑" in prompt
            assert "24 根" in prompt and "4~6" in prompt
        if key == "chase":
            assert "不要求放量" in prompt or "量能未放大" in prompt
        assert "禁止只看 1 根" in prompt


def test_db_bundle_optional():
    import pymysql
    from app.services.explore_prepared_bundle import get_explore_prepared_bundle
    from app.services.ai_tactical_explore_prompts import build_strategy_prompt
    from app.utils.config_loader import get_db_config

    conn = pymysql.connect(**get_db_config(), charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
    try:
        universe, ctx, _ = get_explore_prepared_bundle(conn, "validate_tactical", allow_rebuild=False)
        if not universe:
            print("  [skip] explore 包为空")
            return
        p, m = build_strategy_prompt("pullback", universe, ctx, {})
        print(f"  DB pullback prompt: {len(p)} chars, {m['llm_symbol_count']} syms")
    finally:
        conn.close()


def test_json_extra_data():
    from app.services.ai_explore_prompt import parse_explore_llm_json

    body = (
        '{"summary_zh":"ok","verdicts":[{"symbol":"BTCUSDT","category":"bearish",'
        '"confidence":0.7,"catalyst":"1h 近24根下降 近5根连阴","data_signal":"RSI=42",'
        '"risk_note":""}]}\n\n以上为满足条件的标的。'
    )
    parsed, err = parse_explore_llm_json(body, "test")
    assert parsed is not None, err
    assert len(parsed["verdicts"]) == 1


def main():
    tests = [
        ("json extra data", test_json_extra_data),
        ("single bar rejected", test_single_bar_rejected),
        ("kline narrative 24 split", test_kline_narrative_24_split),
        ("pullback gates", test_pullback_gates),
        ("rebound gates", test_rebound_gates),
        ("chase gates", test_chase_gates),
        ("dump gates", test_dump_gates),
        ("prompt build", test_prompt_build),
        ("DB bundle", test_db_bundle_optional),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
        except Exception as e:
            failed += 1
            print(f"[FAIL] {name}: {e}")
    if failed:
        sys.exit(1)
    print("-" * 40)
    print("全部通过")


if __name__ == "__main__":
    main()
