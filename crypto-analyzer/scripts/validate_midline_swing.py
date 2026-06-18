#!/usr/bin/env python3
"""中线策略本地冒烟测试（默认只读扫描；--dry-run 不写库；--worker 才跑一轮）。"""
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
    from app.services.midline_swing_config import MIDLINE_SOURCES, source_for
    assert len(MIDLINE_SOURCES) == 4
    assert source_for("gemini", "long") == "gemini_midline_long"
    _ok("modules import")


def test_prompt_builder() -> None:
    print("[2b] midline LLM prompt")
    from app.services.ai_midline_explore_prompt import build_midline_prompt, expected_category

    universe = {
        "BTCUSDT": {
            "symbol": "BTCUSDT",
            "current_price": 100000,
            "rating_level": 0,
            "kline_narrative": {"1d": "test 1d", "1h": "test 1h"},
        }
    }
    meta = {"universe_total": 10, "llm_symbol_count": 1}
    prompt = build_midline_prompt("long", universe, {}, {}, meta=meta)
    assert "bullish" in prompt.lower() or "做多" in prompt
    assert expected_category("long") == "bullish"
    assert expected_category("short") == "bearish"
    _ok(f"prompt len={len(prompt)}")


def test_scoring_logic() -> None:
    print("[2] scoring logic (synthetic bars)")
    from app.services.midline_swing_scanner import _score_long, _score_short

    n1d = 24
    closes_1d = [100 + i * 0.1 for i in range(n1d)]
    lows_1d = [c - 0.5 for c in closes_1d]
    lows_1d[-5:] = [99.0] * 5
    closes_1d[-1] = 100.5
    highs_1d = [c + 0.5 for c in closes_1d]

    n1h = 60
    closes_1h = [100 + (i % 5) * 0.2 for i in range(n1h)]
    lows_1h = [c - 0.3 for c in closes_1h]
    highs_1h = [c + 0.3 for c in closes_1h]
    vols_1h = [1000.0] * 50 + [1500.0, 1600.0, 1800.0] + [1200.0] * 7

    long_score, long_detail = _score_long(closes_1d, lows_1d, closes_1h, highs_1h, lows_1h, vols_1h)
    assert long_score >= 0, long_detail
    _ok(f"long synthetic score={long_score}")

    closes_1h_s = [110 + i * 0.05 for i in range(60)]
    highs_1h_s = [c + 0.4 for c in closes_1h_s]
    lows_1h_s = [c - 0.3 for c in closes_1h_s]
    vols_1h_s = [800.0] * 58 + [2000.0, 900.0]
    closes_1d_s = [105 + i * 0.2 for i in range(24)]
    highs_1d_s = [c + 0.5 for c in closes_1d_s]
    short_score, short_detail = _score_short(
        closes_1d_s, highs_1d_s, closes_1h_s, highs_1h_s, lows_1h_s, vols_1h_s,
    )
    assert short_score >= 0, short_detail
    _ok(f"short synthetic score={short_score}")


def test_limit_price() -> None:
    print("[3] limit price ±3%")
    from app.services.paper_limit_entry import calc_paper_limit_price
    from app.services.midline_swing_config import MIDLINE_LIMIT_OFFSET_PCT, is_midline_source

    lp = calc_paper_limit_price("LONG", 100.0, limit_offset_pct=MIDLINE_LIMIT_OFFSET_PCT)
    sp = calc_paper_limit_price("SHORT", 100.0, limit_offset_pct=MIDLINE_LIMIT_OFFSET_PCT)
    assert abs(lp - 97.0) < 0.01, lp
    assert abs(sp - 103.0) < 0.01, sp
    _ok(f"LONG @ {lp}, SHORT @ {sp}")
    assert is_midline_source("gemini_midline_long") and not is_midline_source("gemini_explore")
    _ok("midline source 识别 → create_paper_limit_order 强制限价（不受全局开关影响）")


def test_db_and_scan() -> None:
    print("[4] DB + L0/L1 scan (read-only)")
    import pymysql
    from app.utils.config_loader import get_db_config
    from app.services.midline_swing_scanner import load_l0_l1_symbols, scan_universe

    cfg = get_db_config()
    conn = pymysql.connect(**cfg, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS n")
            _ok("DB connected")

            cur.execute("SHOW TABLES LIKE 'midline_swing_runs'")
            if not cur.fetchone():
                print("  WARN midline_swing_runs 不存在 — 请先执行 migrations/015_midline_swing_tables.sql")
            else:
                _ok("midline_swing_runs exists")

        symbols, _ratings = load_l0_l1_symbols(conn)
        if not symbols:
            _fail("L0/L1 池为空")
        _ok(f"L0/L1 pool size={len(symbols)} (sample: {symbols[:5]})")

        long_sigs, uni = scan_universe(conn, "long")
        short_sigs, _ = scan_universe(conn, "short")
        _ok(f"scan long: universe={uni} signals={len(long_sigs)}")
        _ok(f"scan short: universe={uni} signals={len(short_sigs)}")
        for label, sigs in (("long", long_sigs), ("short", short_sigs)):
            for s in sigs[:3]:
                print(f"       top {label}: {s['symbol']} score={s['score']}")
    finally:
        conn.close()


def test_worker_optional(run_worker: bool) -> None:
    if not run_worker:
        print("[5] worker round — SKIP (pass --worker to enable)")
        return
    print("[5] worker manual round (gemini_midline_long) — WRITES DB")
    import pymysql
    from app.utils.config_loader import get_db_config
    from app.services.midline_explore_worker import run_midline_round
    from app.services.midline_swing_config import MIDLINE_KILL_SWITCH, source_for

    source = source_for("gemini", "long")
    key = MIDLINE_KILL_SWITCH[source]
    cfg = get_db_config()
    conn = pymysql.connect(**cfg, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'midline_swing_runs'")
            if not cur.fetchone():
                print("  SKIP worker (表未迁移)")
                return
            cur.execute(
                "INSERT INTO system_settings (setting_key, setting_value) "
                "VALUES (%s, '1') ON DUPLICATE KEY UPDATE setting_value='1'",
                (key,),
            )
        _ok(f"enabled {key}")
    finally:
        conn.close()

    run_id = run_midline_round("gemini", "long", triggered_by="manual")
    if run_id is None:
        print("  WARN run_id=None (可能 6h 防重或锁占用)")
    else:
        _ok(f"run_id={run_id}")
        conn = pymysql.connect(**cfg, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, universe_size, signals_found, orders_placed, summary_zh "
                    "FROM midline_swing_runs WHERE id=%s",
                    (run_id,),
                )
                print(f"       run: {cur.fetchone()}")
                cur.execute(
                    "SELECT symbol, side, score, action_taken, skip_reason "
                    "FROM midline_swing_verdicts WHERE run_id=%s ORDER BY score DESC LIMIT 8",
                    (run_id,),
                )
                for v in cur.fetchall():
                    print(f"       verdict: {v}")
        finally:
            conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true", help="跑一轮 worker（会写库/可能下单）")
    args = parser.parse_args()

    print("=== midline swing smoke test ===\n")
    test_imports()
    test_scoring_logic()
    test_prompt_builder()
    test_limit_price()
    test_db_and_scan()
    test_worker_optional(args.worker)
    print("\n=== ALL DONE ===")


if __name__ == "__main__":
    main()
