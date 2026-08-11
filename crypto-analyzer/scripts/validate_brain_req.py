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
        BRAIN_SL_PCT,
        BRAIN_TP_PCT,
        BRAIN_HOLD_HOURS,
        BRAIN_MIN_EDGE_SCORE,
        BRAIN_REQUIRE_CONFIRMED_PREFIXES,
        BRAIN_SHORT_BIG4_BIAS_REQUIRED,
        BRAIN_SHORT_BIG4_FLAT_STRONG_OVERRIDE,
        BRAIN_SHORT_FLAT_OVERRIDE_MIN_EDGE,
        BRAIN_SHORT_FLAT_OVERRIDE_PLAYBOOKS,
        BRAIN_STRATEGIC_CLOSE_ENABLED,
        TRADEABLE_PLAYBOOKS,
        WIN_PROB_MIN,
        is_brain_source,
        brain_source_sql_exclude,
    )
    assert WIN_PROB_MIN == 0.55
    assert BRAIN_SL_PCT == 4.5
    assert BRAIN_TP_PCT == 8.0
    assert BRAIN_HOLD_HOURS == 6
    assert BRAIN_STRATEGIC_CLOSE_ENABLED is False
    assert BRAIN_MIN_EDGE_SCORE == 0.75
    assert BRAIN_REQUIRE_CONFIRMED_PREFIXES == ("A", "B")
    assert TRADEABLE_PLAYBOOKS == frozenset({"A1", "A2", "C1"})
    from app.services.brain_config import (
        BRAIN_LONG_BLOCK_WHEN_BIG4_SHORT,
        BRAIN_MIN_EDGE_SCORE_SHORT,
        PILOT_SHORT_PLAYBOOKS,
        PLAYBOOK_MARGIN_MULTIPLIER,
        PLAYBOOK_MIN_EDGE_SCORE,
    )
    assert BRAIN_MIN_EDGE_SCORE_SHORT == 0.90
    assert PILOT_SHORT_PLAYBOOKS == frozenset({"A2", "C1"})
    assert PLAYBOOK_MIN_EDGE_SCORE["C1"] == 0.80
    assert PLAYBOOK_MARGIN_MULTIPLIER["A2"] < 1.0
    assert PLAYBOOK_MARGIN_MULTIPLIER["C1"] < PLAYBOOK_MARGIN_MULTIPLIER["A2"]
    assert BRAIN_SHORT_BIG4_BIAS_REQUIRED is True
    assert BRAIN_SHORT_BIG4_FLAT_STRONG_OVERRIDE is True
    assert BRAIN_SHORT_FLAT_OVERRIDE_MIN_EDGE >= 0.80
    assert BRAIN_SHORT_FLAT_OVERRIDE_PLAYBOOKS == frozenset({"A2", "C1"})
    assert BRAIN_LONG_BLOCK_WHEN_BIG4_SHORT is True
    assert BRAIN_SL_PCT >= 1.0, f"BRAIN_SL_PCT={BRAIN_SL_PCT} 疑似小数比例，应为百分点"
    assert BRAIN_TP_PCT >= 1.0, f"BRAIN_TP_PCT={BRAIN_TP_PCT} 疑似小数比例，应为百分点"
    assert is_brain_source(BRAIN_SOURCE)
    assert is_brain_source("brain_long")
    assert not is_brain_source("deepseek_explore")
    assert "brain_%" in brain_source_sql_exclude("fp.source")
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
    from app.services.brain_config import BRAIN_USE_MARKET_ENTRY

    assert BRAIN_USE_MARKET_ENTRY is False  # INV-BRAIN-06 正式启用防插针限价
    src = (ROOT / "app/services/paper_limit_entry.py").read_text(encoding="utf-8")
    if BRAIN_USE_MARKET_ENTRY:
        if "BRAIN_USE_MARKET_ENTRY" not in src or "_open_paper_market_position" not in src:
            _fail("paper_limit_entry 测试期未走 BRAIN 市价")
        else:
            _ok("paper_limit_entry brain market (test)")
    else:
        if "is_brain_source" not in src or "timeout_action" not in src:
            _fail("paper_limit_entry 未接入 brain force_limit/timeout expire")
        else:
            _ok("paper_limit_entry brain limit")


def test_executor_brain_expire() -> None:
    src = (ROOT / "app/services/futures_limit_order_executor.py").read_text(encoding="utf-8")
    if "is_brain_source" not in src and "force_expire" not in src:
        _fail("executor 未强制 brain expire")
    else:
        _ok("executor brain expire")


def test_cross_service_safety_guards() -> None:
    smart_exit = (ROOT / "app/services/smart_exit_optimizer.py").read_text(encoding="utf-8")
    smart_service = (ROOT / "smart_trader_service.py").read_text(encoding="utf-8")
    engine = (ROOT / "app/trading/futures_trading_engine.py").read_text(encoding="utf-8")

    if "_is_smart_exit_excluded_source" not in smart_exit or "is_brain_source" not in smart_exit:
        _fail("SmartExit 未统一排除 BRAIN")
    elif "brain_source_sql_exclude" not in smart_service:
        _fail("smart_trader SmartExit 查询未排除 BRAIN")
    else:
        _ok("SmartExit excludes BRAIN")

    fill_guards = (
        "_revalidate_paper_limit_fill",
        "check_simulated_symbol_allowed",
        "check_symbol_loss_cooldown",
        "check_max_positions_allowed",
        "check_source_side_performance_allowed",
    )
    if not all(token in engine for token in fill_guards):
        _fail("限价成交前未完整重跑安全闸门")
    elif "_expire_paper_limit_fill_claim" not in engine:
        _fail("成交闸门拒绝后未终止订单")
    else:
        _ok("paper limit fill-time gates")

    if "FOR UPDATE" not in engine or "autocommit=False" not in engine:
        _fail("模拟平仓未使用事务行锁")
    elif "WHERE id = %s AND status = 'open'" not in engine:
        _fail("模拟平仓原子更新缺 status 条件")
    else:
        _ok("close_position atomic/idempotent")


def test_brain_skip_open_advisor() -> None:
    src = (ROOT / "app/services/paper_open_gate.py").read_text(encoding="utf-8")
    if "brain_skip_advisor" not in src or "is_brain_source" not in src:
        _fail("paper_open_gate 未跳过 BRAIN 开仓顾问")
    else:
        _ok("paper_open_gate brain_skip_advisor")
    orch = (ROOT / "app/services/brain_strategy_orchestrator.py").read_text(encoding="utf-8")
    if "skip_open_advisor=True" not in orch:
        _fail("orchestrator 未 skip_open_advisor")
    else:
        _ok("orchestrator skip_open_advisor")
    if "_open_brain_entry" not in orch:
        _fail("orchestrator 缺少 _open_brain_entry")
    else:
        _ok("orchestrator _open_brain_entry")
    if "BRAIN_STRATEGIC_CLOSE_ENABLED" not in orch:
        _fail("orchestrator 未接入战略平仓开关")
    else:
        _ok("orchestrator strategic close gated")
    from app.services.open_advisor_routing import should_use_deepseek_hold_advisor
    assert should_use_deepseek_hold_advisor("brain_swing") is True
    assert should_use_deepseek_hold_advisor("deepseek_explore") is True
    hold_q = (ROOT / "app/services/hold_advisor_query.py").read_text(encoding="utf-8")
    if "brain_source_sql_exclude" in hold_q:
        _fail("hold_advisor_query 不应再排除 BRAIN")
    else:
        _ok("hold advisor includes brain")
    mon = (ROOT / "app/services/position_sl_tp_monitor.py").read_text(encoding="utf-8")
    if "check_brain_trail_lock" not in mon or "brain_trail_exit" not in mon:
        _fail("position_sl_tp_monitor 未接入 BRAIN 新版 trail")
    elif "_check_ai_trail_tp" in mon and "if _is_brain" not in mon and "is_brain_source as _brain_src" not in mon:
        _fail("monitor 可能对 BRAIN 误用旧 ai-trail")
    else:
        _ok("monitor brain trail (new, not old ai-trail)")
    trail_mod = (ROOT / "app/services/brain_trail_exit.py").read_text(encoding="utf-8")
    if "check_brain_trail_lock" not in trail_mod or "check_brain_soft_no_follow" not in trail_mod:
        _fail("brain_trail_exit 缺 trail/soft")
    elif "check_brain_max_loss_usd" not in trail_mod:
        _fail("brain_trail_exit 缺 max_loss_usd")
    elif "check_brain_5m_adverse" not in trail_mod:
        _fail("brain_trail_exit 缺 5m_adverse")
    else:
        _ok("brain_trail_exit")
    orch2 = (ROOT / "app/services/brain_strategy_orchestrator.py").read_text(encoding="utf-8")
    if "evaluate_brain_risk_params" not in orch2 or "rows_15m=" not in orch2:
        _fail("orchestrator 未接 brain_risk_params / rows_15m")
    else:
        _ok("orchestrator risk_params")

    if (
        "big4_short_blocks_long" not in orch2
        or "short_needs_big4_short" not in orch2
        or "_strong_token_short_override" not in orch2
        or "PLAYBOOK_MARGIN_MULTIPLIER" not in orch2
        or "PLAYBOOK_MIN_EDGE_SCORE" not in orch2
    ):
        _fail("orchestrator 未完整接入 A2/C1 受控补空门控")
    else:
        _ok("orchestrator controlled short gates")

    from app.services.brain_strategy_orchestrator import _strong_token_short_override
    strong_a2 = {
        "playbook": "A2",
        "confirmed": True,
        "edge_score": 0.90,
        "features": {"h1_side": "SHORT", "m15_side": "SHORT"},
        "signals": ["ema_bear_align", "crash_spike"],
    }
    assert _strong_token_short_override(strong_a2, "FLAT")
    assert not _strong_token_short_override(strong_a2, "LONG")
    weak_pb = {
        "playbook": "B2",
        "confirmed": True,
        "edge_score": 0.90,
        "features": {"h1_side": "SHORT", "m15_side": "SHORT"},
        "signals": ["break_support"],
    }
    assert not _strong_token_short_override(weak_pb, "FLAT")
    _ok("strong token short override")


def test_brain_risk_params() -> None:
    from app.services.brain_risk_params import evaluate_brain_risk_params, atr_pct_from_rows
    from app.services.brain_config import (
        BRAIN_SL_MIN_PCT,
        BRAIN_SL_MAX_PCT,
        BRAIN_TP_MIN_PCT,
        BRAIN_TP_MAX_PCT,
        BRAIN_HOLD_MIN_HOURS,
        BRAIN_HOLD_MAX_HOURS,
        BRAIN_TRAIL_ENABLED,
        BRAIN_TRAIL_ACTIVATE_MIN_PCT,
        BRAIN_TRAIL_ACTIVATE_MAX_PCT,
    )
    assert BRAIN_TRAIL_ENABLED is True
    rows = []
    p = 100.0
    for i in range(30):
        p += 0.1
        rows.append({
            "open_price": p - 0.05,
            "high_price": p + 0.8,
            "low_price": p - 0.7,
            "close_price": p,
        })
    atr = atr_pct_from_rows(rows)
    assert atr is not None and atr > 0
    out = evaluate_brain_risk_params(
        playbook="B1", side="LONG", rows_15m=rows, win_prob=0.60, edge_score=0.7,
    )
    assert BRAIN_SL_MIN_PCT <= out["sl_pct"] <= BRAIN_SL_MAX_PCT
    assert BRAIN_TP_MIN_PCT <= out["tp_pct"] <= BRAIN_TP_MAX_PCT
    assert BRAIN_HOLD_MIN_HOURS <= out["hold_hours"] <= BRAIN_HOLD_MAX_HOURS
    assert out["sl_pct"] >= 1.0 and out["tp_pct"] >= 1.0  # 百分点非小数比例
    meta = out["risk_meta"]
    assert BRAIN_TRAIL_ACTIVATE_MIN_PCT <= meta["trail_activate_pct"] <= BRAIN_TRAIL_ACTIVATE_MAX_PCT
    from app.services.brain_config import BRAIN_SL_MIN_PCT as _slmin
    assert _slmin >= 2.5
    fb = evaluate_brain_risk_params(playbook="A1", side="LONG", rows_15m=[])
    assert fb["risk_fallback"] is True
    assert fb["sl_pct"] == 4.5 and fb["tp_pct"] == 8.0
    from app.services.brain_trail_exit import (
        check_brain_trail_lock,
        check_brain_soft_no_follow,
        trail_levels_from_sl_tp,
    )
    assert check_brain_trail_lock(0.01, 0.01) is None  # 未激活
    assert check_brain_trail_lock(0.012, 0.02, activate_pct=1.0, pullback_pct=0.45)
    from app.services.brain_config import (
        BRAIN_SOFT_NO_FOLLOW_ENABLED,
        BRAIN_SL_MAX_PCT,
        BRAIN_SOFT_NO_FOLLOW_MIN_AGE,
        BRAIN_SOFT_NO_FOLLOW_LOSS_PCT,
        BRAIN_SOFT_NO_FOLLOW_MAX_PEAK_PCT,
        BRAIN_MAX_LOSS_USD,
    )
    assert BRAIN_SL_MAX_PCT == 4.5
    assert BRAIN_SOFT_NO_FOLLOW_ENABLED is False
    assert BRAIN_MAX_LOSS_USD == 60.0
    from app.services.brain_trail_exit import check_brain_max_loss_usd
    # soft 关闭：即使满足旧条件也不砍
    assert check_brain_soft_no_follow(-0.015, 0.002, 30 * 60) is None
    assert check_brain_soft_no_follow(-0.02, 0.002, 3600) is None
    assert check_brain_max_loss_usd(-59.9) is None
    assert check_brain_max_loss_usd(-60.0)
    assert check_brain_max_loss_usd(-225.0)
    from app.services.brain_trail_exit import check_brain_5m_adverse
    from app.services.brain_config import (
        BRAIN_ADVERSE_5M_ENABLED,
        BRAIN_ADVERSE_5M_MIN_LOSS_USD,
        BRAIN_ADVERSE_5M_TRAIL_MIN,
    )
    assert BRAIN_ADVERSE_5M_ENABLED is True
    assert BRAIN_ADVERSE_5M_MIN_LOSS_USD == 40.0
    assert BRAIN_ADVERSE_5M_TRAIL_MIN == 4
    from app.services.brain_config import BRAIN_ADVERSE_5M_SKIP_PLAYBOOKS
    assert "A1" in BRAIN_ADVERSE_5M_SKIP_PLAYBOOKS
    assert check_brain_5m_adverse(-15.0, trail_against=5, against=5, favor=0, total=5) is None
    assert check_brain_5m_adverse(-25.0, trail_against=4, against=4, favor=1, total=5) is None  # 未到 40U
    assert check_brain_5m_adverse(-45.0, trail_against=3, against=3, favor=2, total=5) is None  # 连续不足
    assert check_brain_5m_adverse(-45.0, trail_against=4, against=4, favor=1, total=5)
    assert check_brain_5m_adverse(-45.0, trail_against=1, against=4, favor=1, total=5)
    # A1 豁免：即使深度逆势也不 5m 早撤
    assert check_brain_5m_adverse(
        -45.0, trail_against=5, against=5, favor=0, total=5, playbook="A1"
    ) is None
    assert check_brain_5m_adverse(
        -45.0, trail_against=5, against=5, favor=0, total=5, playbook="brain_A1"
    ) is None
    mon2 = (ROOT / "app/services/position_sl_tp_monitor.py").read_text(encoding="utf-8")
    if "check_brain_max_loss_usd" not in mon2:
        _fail("monitor 未接入 brain_max_loss_usd")
    if "_check_brain_5m_adverse_exit" not in mon2 or "brain_5m_adverse" not in mon2:
        _fail("monitor 未接入 brain_5m_adverse")
    act8, pull8, _ = trail_levels_from_sl_tp(8.0, 12.0)
    assert act8 <= 1.0  # 宽仓激活≤1.0%，峰1.1%可锁
    assert check_brain_trail_lock(0.006, 0.011, activate_pct=act8, pullback_pct=pull8)
    act_tight, _, _ = trail_levels_from_sl_tp(2.5, 3.0)
    assert BRAIN_TRAIL_ACTIVATE_MIN_PCT <= act_tight <= BRAIN_TRAIL_ACTIVATE_MAX_PCT
    # 新开仓 SL 不得再评估到 8%
    wide = evaluate_brain_risk_params(
        playbook="A1", side="LONG", atr_pct=5.0, win_prob=0.70, edge_score=0.8,
    )
    assert wide["sl_pct"] <= BRAIN_SL_MAX_PCT
    mon = (ROOT / "app/services/position_sl_tp_monitor.py").read_text(encoding="utf-8")
    if "max_profit_pct" not in mon or "db_peak" not in mon:
        _fail("monitor 未从 DB 恢复 peak")
    else:
        _ok("monitor peak restore from DB")
    _ok("brain_risk_params + trail_exit")


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
        "app/services/brain_risk_params.py",
        "app/services/brain_trail_exit.py",
        "app/services/brain_wick.py",
        "app/services/brain_market_analyzer.py",
        "app/services/brain_winrate.py",
        "app/services/brain_playbook.py",
        "app/services/brain_opportunity_store.py",
        "app/services/brain_strategy_orchestrator.py",
        "app/services/smart_exit_optimizer.py",
        "app/trading/futures_trading_engine.py",
        "smart_trader_service.py",
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
    test_cross_service_safety_guards()
    test_brain_skip_open_advisor()
    test_brain_risk_params()
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
