#!/usr/bin/env python3
"""REQ-BRAIN 静态回归 — 无 API / 无 DB。"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_fail_n = 0


def _ok(msg: str) -> None:
    print(f"  OK  {msg}")


def _fail(msg: str) -> None:
    global _fail_n
    _fail_n += 1
    print(f"  FAIL {msg}")


def test_imports_and_config() -> None:
    from app.services.brain_config import (
        BRAIN_SOURCE,
        WIN_PROB_MIN,
        is_brain_source,
    )
    assert WIN_PROB_MIN == 0.55
    assert is_brain_source(BRAIN_SOURCE)
    assert is_brain_source("brain_long")
    assert not is_brain_source("deepseek_explore")
    _ok("brain_config")


def test_wick() -> None:
    from app.services.brain_wick import analyze_wicks, bar_wick_metrics, limit_offset_pct_from_wicks

    m = bar_wick_metrics(100, 110, 99, 101)  # upper=9, body=1 → wick
    assert m["upper_is_wick"] is True
    bars = [
        {"open_price": 100, "high_price": 110, "low_price": 99, "close_price": 101}
        for _ in range(60)
    ]
    w = analyze_wicks(bars, frequent_ratio=0.01)
    assert w["frequent"] is True
    off = limit_offset_pct_from_wicks("LONG", w)
    assert off >= 0.1
    _ok("brain_wick")


def test_trend_helpers() -> None:
    from app.services.brain_market_analyzer import _trend_side

    # rising
    closes = [100.0 + i * 0.2 for i in range(168)]
    side, d = _trend_side(closes, 168)
    assert side == "LONG", (side, d)
    # flat
    flat = [100.0] * 96
    side2, _ = _trend_side(flat, 96)
    assert side2 == "FLAT"
    _ok("trend_side")


def test_winrate_forward() -> None:
    from app.services.brain_winrate import _forward_win, _direction_at

    closes = [100.0 + i for i in range(20)]
    assert _forward_win(closes, 5, "LONG", 4) is True
    assert _forward_win(closes, 5, "SHORT", 4) is False
    # direction with enough lookback of flat then rise — just smoke
    side = _direction_at([100.0] * 10 + [100.0 + i for i in range(20)], 25, 10)
    assert side in ("LONG", "SHORT", "FLAT")
    _ok("winrate helpers")


def test_ds_auto_open_available() -> None:
    """对照期：DeepSeek 开仓函数应可真正下单（非 brain_paused stub）。"""
    for rel in (
        "app/services/deepseek_explore_worker.py",
        "app/services/deepseek_predictor.py",
    ):
        src = (ROOT / rel).read_text(encoding="utf-8")
        if "brain_paused_ds_auto_open" in src:
            _fail(f"{rel} 仍硬暂停自动开仓（对照期应恢复）")
        elif "create_paper_limit_order" not in src:
            _fail(f"{rel} 缺少 create_paper_limit_order")
        else:
            _ok(f"{rel} open restored (对照期)")


def test_paper_limit_brain_force() -> None:
    src = (ROOT / "app/services/paper_limit_entry.py").read_text(encoding="utf-8")
    if "is_brain_source" not in src or "timeout_action" not in src:
        _fail("paper_limit_entry 未接入 brain force_limit/timeout expire")
    else:
        _ok("paper_limit_entry brain")


def test_executor_brain_expire() -> None:
    src = (ROOT / "app/services/futures_limit_order_executor.py").read_text(encoding="utf-8")
    if "is_brain_source" not in src and "force_expire" not in src:
        _fail("executor 未强制 brain expire")
    else:
        _ok("executor brain expire")


def test_scheduler_brain() -> None:
    src = (ROOT / "app/scheduler.py").read_text(encoding="utf-8")
    if "run_brain_tick" not in src:
        _fail("scheduler 未注册 BRAIN tick")
    else:
        _ok("scheduler brain tick")
    if "every(15).seconds" not in src and "every(15).second" not in src:
        # schedule API is every(15).seconds
        if "15).seconds" not in src:
            _fail("scheduler 未按 15s 调度 BRAIN")
        else:
            _ok("scheduler 15s")
    else:
        _ok("scheduler 15s")
    if "run_explore_round" not in src or "DeepSeekExplore" not in src:
        _fail("scheduler 对照期应保留 DeepSeek 探索调度")
    else:
        _ok("scheduler DeepSeek explore (对照期)")
    if "run_predict_round" not in src:
        _fail("scheduler 对照期应保留 DeepSeek 预测调度")
    else:
        _ok("scheduler DeepSeek predict (对照期)")


def test_tick_config() -> None:
    from app.services.brain_config import (
        BRAIN_TICK_BATCH_SIZE,
        BRAIN_TICK_INTERVAL_SECONDS,
    )
    assert BRAIN_TICK_BATCH_SIZE == 5
    assert BRAIN_TICK_INTERVAL_SECONDS == 15
    from app.services.brain_strategy_orchestrator import get_brain_live_status, run_brain_tick
    snap = get_brain_live_status()
    assert snap.get("batch_size") == 5
    assert callable(run_brain_tick)
    _ok("tick config + live status")


def test_playbook_classify() -> None:
    from app.services.brain_playbook import classify_playbook, extract_features

    # synthetic rising 1h + 15m
    rows_1h = []
    for i in range(180):
        p = 100 + i * 0.15
        rows_1h.append({
            "open_price": p - 0.05, "high_price": p + 0.2,
            "low_price": p - 0.2, "close_price": p, "volume": 1000 + i,
        })
    rows_15m = []
    for i in range(120):
        p = 120 + i * 0.05
        rows_15m.append({
            "open_price": p - 0.02, "high_price": p + 0.08,
            "low_price": p - 0.08, "close_price": p, "volume": 500,
        })
    big4 = {"big4_ok": True, "bias": "LONG"}
    out = classify_playbook(rows_1h, rows_15m, big4=big4, win_prob_long=0.6, win_prob_short=0.4)
    assert "playbook" in out and "signals" in out
    assert isinstance(out["signals"], list)
    _ok(f"playbook classify → {out['playbook']} side={out['side']} n_sig={len(out['signals'])}")


def test_directional_gate() -> None:
    from app.services.brain_winrate import directional_open_allowed

    ok, reason = directional_open_allowed("LONG", 0.60, 0.50)
    assert ok, reason
    ok2, reason2 = directional_open_allowed("LONG", 0.56, 0.54)
    assert not ok2 and "rel_edge" in reason2
    ok3, _ = directional_open_allowed("SHORT", 0.40, 0.58)
    assert ok3
    _ok("directional_open_allowed")


def test_orchestrator_syntax() -> None:
    for rel in (
        "app/services/brain_config.py",
        "app/services/brain_wick.py",
        "app/services/brain_market_analyzer.py",
        "app/services/brain_winrate.py",
        "app/services/brain_playbook.py",
        "app/services/brain_opportunity_store.py",
        "app/services/brain_strategy_orchestrator.py",
    ):
        path = ROOT / rel
        ast.parse(path.read_text(encoding="utf-8"))
        _ok(f"syntax {rel}")


def main() -> int:
    print("=== validate_brain_req ===\n")
    test_imports_and_config()
    test_wick()
    test_trend_helpers()
    test_winrate_forward()
    test_playbook_classify()
    test_directional_gate()
    test_tick_config()
    test_ds_auto_open_available()
    test_paper_limit_brain_force()
    test_executor_brain_expire()
    test_scheduler_brain()
    test_orchestrator_syntax()
    print()
    if _fail_n:
        print(f"FAILED {_fail_n}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
