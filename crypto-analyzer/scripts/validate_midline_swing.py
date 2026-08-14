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
        signals={"break_support", "volume_expand_down"},
        features={"h1_side": "SHORT", "m15_side": "SHORT", "ema_bear": True},
        future_4h={"side": "SHORT", "score": 0.56},
        big4_bias="SHORT",
        global_name="BEAR_TREND",
        entry_15m={"fresh_breakout": True},
    )
    assert c1["should_open"] is True, c1

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
    _ok("A2/C1 open only when the 4h breakout opportunity is aligned")


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
    print("[3c] midline includes ai-trail-tp path")
    from app.services.position_sl_tp_monitor import (
        _check_ai_trail_tp,
        _is_ai_hard_sltp_source,
        _is_midline_source,
    )

    assert _is_midline_source("midline_long")
    assert _is_ai_hard_sltp_source("midline_long")
    # peak 4%, pullback 1.2%, still keeps >=2% profit: trigger ai-trail-tp
    assert _check_ai_trail_tp(0.028, 0.040) is not None
    _ok("ai-trail-tp applies to midline_long (monitor loop)")


def test_hold_advisor_excludes_midline() -> None:
    print("[3d] hold advisor SQL excludes midline")
    from app.services.hold_advisor_query import DEEPSEEK_HOLD_SOURCE_SQL, GEMINI_HOLD_SOURCE_SQL

    assert "midline_long" in DEEPSEEK_HOLD_SOURCE_SQL
    assert "midline_short" in DEEPSEEK_HOLD_SOURCE_SQL
    assert "NOT IN" in DEEPSEEK_HOLD_SOURCE_SQL  # midline_source_sql_not_in
    # Gemini hold advisor is retired; DeepSeek SQL no longer excludes gemini_*.
    assert "gemini_explore" not in DEEPSEEK_HOLD_SOURCE_SQL
    assert "1=0" in GEMINI_HOLD_SOURCE_SQL
    _ok("DeepSeek hold SQL excludes midline (hard SL/TP + trail + 8h only)")


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
    test_hold_advisor_excludes_midline()
    test_run_summary_zh()
    if args.db:
        test_db_and_scan()
    if args.worker:
        test_worker_dry()
    print("\nALL PASSED")


if __name__ == "__main__":
    main()
