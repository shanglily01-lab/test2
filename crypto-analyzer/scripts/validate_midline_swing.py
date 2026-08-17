#!/usr/bin/env python3
"""Breakout opportunity local smoke tests; --worker runs one round."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _ok(msg: str) -> None:
    print(f"  OK  {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL {msg}")
    raise SystemExit(1)


def test_imports() -> None:
    print("[1] imports")
    import app.services.midline_swing_scanner as scanner
    from app.services.midline_swing_config import (
        MIDLINE_SOURCES,
        source_for,
        is_midline_source,
        is_active_midline_source,
    )
    assert MIDLINE_SOURCES == frozenset({"midline_long", "midline_short"})
    assert source_for("", "long") == "midline_long"
    assert is_active_midline_source("midline_long")
    assert is_midline_source("gemini_midline_long")  # legacy
    assert not is_active_midline_source("gemini_midline_long")
    assert not hasattr(scanner, "_layer1_daily")
    assert not hasattr(scanner, "_layer2_hourly")
    assert not hasattr(scanner, "_layer3_entry")
    assert not hasattr(scanner, "evaluate_symbol")
    _ok("modules import")


def test_breakout_action_opportunity() -> None:
    print("[2] 4h breakout action gate")
    from app.services.midline_swing_scanner import _breakout_action_opportunity

    c1 = _breakout_action_opportunity(
        side="SHORT",
        playbook="C1",
        edge=0.70,
        confirmed=True,
        signals={"break_support", "volume_expand_down", "15m_lower_high"},
        features={"h1_side": "SHORT", "m15_side": "SHORT", "ema_bear": True, "vol_shrink_pullback": True},
        future_4h={"side": "SHORT", "score": 0.56},
        big4_bias="SHORT",
        global_name="BEAR_TREND",
        entry_15m={"fresh_breakout": False},
    )
    assert c1["should_open"] is True, c1

    a1 = _breakout_action_opportunity(
        side="LONG",
        playbook="A1",
        edge=0.90,
        confirmed=True,
        signals={"15m_higher_low", "volume_shrink_pullback"},
        features={
            "h1_side": "LONG",
            "m15_side": "LONG",
            "ema_bull": True,
            "hh_hl": True,
            "vol_shrink_pullback": True,
        },
        future_4h={"side": "LONG", "score": 0.62},
        big4_bias="FLAT",
        global_name="TOKEN_DIVERGENCE",
        entry_15m={"fresh_breakout": False},
    )
    assert a1["should_open"] is True, a1

    c3_h1 = _breakout_action_opportunity(
        side="LONG",
        playbook="C3",
        edge=0.90,
        confirmed=True,
        signals={"h1_breakout_up", "impulse_up", "15m_higher_low"},
        features={"h1_side": "LONG", "m15_side": "LONG"},
        future_4h={"side": "LONG", "score": 0.60},
        big4_bias="FLAT",
        global_name="TOKEN_DIVERGENCE",
        entry_15m={"fresh_breakout": False},
    )
    assert c3_h1["should_open"] is True, c3_h1

    a2 = _breakout_action_opportunity(
        side="SHORT",
        playbook="A2",
        edge=0.72,
        confirmed=True,
        signals={"ema_reject", "15m_lower_high"},
        features={
            "h1_side": "SHORT",
            "m15_side": "SHORT",
            "ema_bear": True,
            "vol_shrink_pullback": True,
        },
        future_4h={"side": "SHORT", "score": 0.52},
        big4_bias="FLAT",
        global_name="TOKEN_DIVERGENCE",
        entry_15m={"fresh_breakout": False},
    )
    assert a2["should_open"] is True, a2

    blocked = _breakout_action_opportunity(
        side="SHORT",
        playbook="C1",
        edge=0.90,
        confirmed=True,
        signals={"break_support", "volume_expand_down"},
        features={"h1_side": "SHORT", "m15_side": "SHORT", "ema_bear": True},
        future_4h={"side": "FLAT", "score": 0.70},
        big4_bias="SHORT",
        global_name="BEAR_TREND",
        entry_15m={"fresh_breakout": True},
    )
    assert blocked["should_open"] is False and blocked["reason"] == "future_4h_not_actionable"

    chase = _breakout_action_opportunity(
        side="LONG",
        playbook="C3",
        edge=0.90,
        confirmed=True,
        signals={"h1_breakout_up", "impulse_up"},
        features={"h1_side": "LONG", "m15_side": "LONG"},
        future_4h={"side": "LONG", "score": 0.60},
        big4_bias="FLAT",
        global_name="TOKEN_DIVERGENCE",
        entry_15m={"fresh_breakout": True},
    )
    assert chase["should_open"] is False and chase["reason"] == "wait_pullback", chase

    b3 = _breakout_action_opportunity(
        side="SHORT",
        playbook="B3",
        edge=0.80,
        confirmed=True,
        signals={"pump_spike", "long_upper_wick", "volume_diverge_bear", "exhaustion_up", "stall_at_high"},
        features={"h1_side": "LONG", "m15_side": "LONG", "stall_at_high": True},
        future_4h={"side": "FLAT", "score": 0.20},
        big4_bias="FLAT",
        global_name="TOKEN_DIVERGENCE",
        entry_15m={"fresh_breakout": False},
    )
    assert b3["should_open"] is True, b3

    still_long = _breakout_action_opportunity(
        side="SHORT",
        playbook="B3",
        edge=0.90,
        confirmed=True,
        signals={"pump_spike", "long_upper_wick", "exhaustion_up"},
        features={"h1_side": "LONG", "m15_side": "LONG", "stall_at_high": True},
        future_4h={"side": "LONG", "score": 0.60},
        big4_bias="FLAT",
        global_name="TOKEN_DIVERGENCE",
        entry_15m={"fresh_breakout": False},
    )
    assert still_long["should_open"] is False and still_long["reason"] == "future_4h_still_long", still_long
    _ok("A2/C1 open only when the 4h breakout opportunity is aligned; C3 waits for pullback; B3 sells stall at highs")


def test_limit_price() -> None:
    print("[3] limit price +/-1% default")
    from app.services.paper_limit_entry import calc_paper_limit_price
    from app.services.midline_swing_config import (
        DEFAULT_MIDLINE_LIMIT_LONG_OFFSET_PCT,
        DEFAULT_MIDLINE_LIMIT_SHORT_OFFSET_PCT,
        get_midline_limit_offset_pct,
        is_midline_source,
        MIDLINE_SL_PCT,
        MIDLINE_TP_PCT,
        MIDLINE_HOLD_HOURS,
    )

    assert DEFAULT_MIDLINE_LIMIT_LONG_OFFSET_PCT == 1.0
    assert DEFAULT_MIDLINE_LIMIT_SHORT_OFFSET_PCT == 1.0
    assert MIDLINE_SL_PCT == 6.0 and MIDLINE_TP_PCT == 3.0
    assert MIDLINE_HOLD_HOURS == 8
    long_pct = get_midline_limit_offset_pct("LONG")
    short_pct = get_midline_limit_offset_pct("SHORT")
    lp = calc_paper_limit_price("LONG", 100.0, limit_offset_pct=long_pct)
    sp = calc_paper_limit_price("SHORT", 100.0, limit_offset_pct=short_pct)
    assert abs(lp - (100.0 * (1 - long_pct / 100))) < 0.01, lp
    assert abs(sp - (100.0 * (1 + short_pct / 100))) < 0.01, sp
    assert is_midline_source("midline_long")
    _ok(f"LONG @ {lp} (-{long_pct}%), SHORT @ {sp} (+{short_pct}%)")


def test_live_sync_whitelist() -> None:
    print("[3b] live sync: midline NOT in LIVE_SYNC")
    from app.services.trading_gates import LIVE_SYNC_SOURCES
    from app.services.midline_swing_config import MIDLINE_SOURCES, ALL_MIDLINE_SOURCES

    assert MIDLINE_SOURCES.isdisjoint(LIVE_SYNC_SOURCES)
    assert ALL_MIDLINE_SOURCES.isdisjoint(LIVE_SYNC_SOURCES)
    _ok("midline excluded from LIVE_SYNC_SOURCES (paper only)")


def test_ai_trail_for_midline() -> None:
    print("[3c] midline earlier lock / giveback")
    from app.services.position_sl_tp_monitor import (
        _is_ai_hard_sltp_source,
        _is_midline_source,
    )
    from app.services.midline_hold_exit import (
        check_midline_giveback,
        check_midline_hold_exits,
        check_midline_trail_lock,
        midline_hold_hours,
    )

    assert _is_midline_source("midline_long")
    assert _is_ai_hard_sltp_source("midline_long")
    assert check_midline_trail_lock(0.007, 0.013) is not None
    assert check_midline_trail_lock(0.012, 0.013) is None
    assert check_midline_giveback(0.0, 0.015, 30 * 60) is not None
    assert check_midline_giveback(0.0, 0.015, 10 * 60) is None
    assert check_midline_hold_exits(0.007, 0.013, 40 * 60) is not None
    assert midline_hold_hours("C3") == 4.0
    assert midline_hold_hours("A1") == 6.0
    assert midline_hold_hours("B3") == 4.0
    assert midline_hold_hours("C4") == 4.0
    _ok("midline locks at ~1.2% peak and cuts profit-to-loss")


def test_hold_advisor_includes_midline() -> None:
    print("[3d] hold advisor includes midline")
    from app.services.hold_advisor_query import DEEPSEEK_HOLD_SOURCE_SQL, GEMINI_HOLD_SOURCE_SQL
    from app.services.open_advisor_routing import should_use_deepseek_hold_advisor

    assert should_use_deepseek_hold_advisor("midline_long") is True
    assert "midline_long" not in (DEEPSEEK_HOLD_SOURCE_SQL or "")
    assert "NOT IN" not in (DEEPSEEK_HOLD_SOURCE_SQL or "")
    assert "1=0" in GEMINI_HOLD_SOURCE_SQL
    _ok("DeepSeek hold advisor now reviews midline profit giveback")


def test_run_summary_zh() -> None:
    print("[3e] run summary")
    from app.services.midline_explore_worker import _format_run_summary

    s = _format_run_summary(260, 5, 3, "long", "LONG", rejected=100)
    assert "config" in s and "5" in s and "3" in s and "LONG" in s
    _ok(s)


def test_db_and_scan() -> None:
    print("[4] DB + config.yaml scan (read-only)")
    import pymysql
    from app.utils.config_loader import get_db_config
    from app.services.midline_swing_scanner import load_midline_universe, scan_universe

    cfg = get_db_config()
    conn = pymysql.connect(**cfg, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
    try:
        symbols = load_midline_universe(conn)
        assert len(symbols) > 10, f"universe too small: {len(symbols)}"
        _ok(f"universe size={len(symbols)}")
        signals, n = scan_universe(conn, "long")
        _ok(f"long scan universe={n} passed={len(signals)}")
    finally:
        conn.close()


def test_worker_dry() -> None:
    print("[5] worker (manual, may skip if kill switch=0)")
    from app.services.midline_explore_worker import run_midline_round
    run_id = run_midline_round(source="midline_long", triggered_by="manual")
    _ok(f"run_id={run_id}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", action="store_true", help="run DB scan")
    ap.add_argument("--worker", action="store_true", help="run one manual round")
    args = ap.parse_args()

    test_imports()
    test_breakout_action_opportunity()
    test_limit_price()
    test_live_sync_whitelist()
    test_ai_trail_for_midline()
    test_hold_advisor_includes_midline()
    test_run_summary_zh()
    if args.db:
        test_db_and_scan()
    if args.worker:
        test_worker_dry()
    print("\nALL PASSED")


if __name__ == "__main__":
    main()
