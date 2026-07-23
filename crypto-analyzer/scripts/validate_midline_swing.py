#!/usr/bin/env python3
"""中线策略 v2 本地冒烟测试（默认只读；--worker 才跑一轮）."""
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
    _ok("modules import")


def test_layer_logic() -> None:
    print("[2] layer helpers")
    from app.services.midline_swing_scanner import _layer1_daily, _layer2_hourly, _layer3_entry

    # 做多：30d 大涨 + RSI 中性
    closes = [100.0] * 20 + [100 + i for i in range(10)]  # last much higher
    # need 30 bars: start low end high
    closes_1d = [100.0] * 30
    closes_1d[0] = 100.0
    closes_1d[-1] = 112.0  # +12%
    vols = [1000.0] * 30
    ok, d = _layer1_daily(closes_1d, vols, "long")
    # RSI may fail on flat-ish series — just ensure function runs
    assert isinstance(ok, bool) and "change_30d_pct" in d
    _ok(f"layer1 long change={d.get('change_30d_pct')} passed={ok}")

    closes_1h = [100.0] * 140 + [101.0 + i * 0.01 for i in range(28)]
    ok2, d2 = _layer2_hourly(closes_1h, "long")
    assert isinstance(ok2, bool)
    _ok(f"layer2 ran passed={ok2}")

    highs_1d = [c + 1 for c in closes_1d]
    lows_1d = [c - 1 for c in closes_1d]
    closes_15 = [100.0] * 16
    highs_15 = [101.0] * 16
    lows_15 = [99.0] * 12 + [99.5] * 4
    vols_15 = [1000.0] * 16
    ok3, d3 = _layer3_entry(
        closes_1d, highs_1d, lows_1d, closes_15, highs_15, lows_15, vols_15, "long",
    )
    assert isinstance(ok3, bool)
    _ok(f"layer3 ran passed={ok3} reason={d3.get('reason')}")


def test_limit_price() -> None:
    print("[3] limit price ±1% default")
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
    print("[3b] live sync — midline NOT in LIVE_SYNC")
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
    # peak 4% 价格收益、回撤 1.2%、仍保留 ≥2% → 触发 ai-trail-tp
    assert _check_ai_trail_tp(0.028, 0.040) is not None
    _ok("ai-trail-tp applies to midline_long (monitor loop)")


def test_hold_advisor_includes_midline() -> None:
    print("[3d] hold advisor SQL includes midline")
    from app.services.hold_advisor_query import DEEPSEEK_HOLD_SOURCE_SQL

    assert "midline_long" not in DEEPSEEK_HOLD_SOURCE_SQL  # not excluded
    assert "gemini_midline" not in DEEPSEEK_HOLD_SOURCE_SQL
    assert "gemini_explore" in DEEPSEEK_HOLD_SOURCE_SQL
    _ok("DeepSeek hold SQL no longer excludes midline")


def test_run_summary_zh() -> None:
    print("[3e] run summary")
    from app.services.midline_explore_worker import _format_run_summary

    s = _format_run_summary(260, 5, 3, "long", "LONG", rejected=100)
    assert "config" in s and "通过5个" in s and "挂单3笔" in s
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
    test_layer_logic()
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
