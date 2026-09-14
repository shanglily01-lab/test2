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
    assert TRADEABLE_PLAYBOOKS == frozenset({"A1", "A2", "B2", "B3", "C1", "C3", "C4"})
    from app.services.brain_config import (
        BRAIN_MIN_EDGE_SCORE_SHORT,
        PILOT_SHORT_PLAYBOOKS,
        PLAYBOOK_MARGIN_MULTIPLIER,
        PLAYBOOK_MIN_EDGE_SCORE,
    )
    assert BRAIN_MIN_EDGE_SCORE_SHORT == 0.90
    assert PILOT_SHORT_PLAYBOOKS == frozenset({"A2", "B2", "C1"})
    assert PLAYBOOK_MIN_EDGE_SCORE["C1"] == 0.80
    assert PLAYBOOK_MIN_EDGE_SCORE["A2"] == 0.80
    assert PLAYBOOK_MIN_EDGE_SCORE["B2"] == 0.80
    assert PLAYBOOK_MIN_EDGE_SCORE["C3"] == 0.70
    assert PLAYBOOK_MARGIN_MULTIPLIER["C3"] < 1.0
    assert PLAYBOOK_MARGIN_MULTIPLIER["B3"] < PLAYBOOK_MARGIN_MULTIPLIER["C1"]
    assert BRAIN_SL_PCT >= 1.0, f"BRAIN_SL_PCT={BRAIN_SL_PCT} 疑似小数比例，应为百分点"
    assert BRAIN_TP_PCT >= 1.0, f"BRAIN_TP_PCT={BRAIN_TP_PCT} 疑似小数比例，应为百分点"
    assert is_brain_source(BRAIN_SOURCE)
    assert is_brain_source("brain_long")
    assert not is_brain_source("deepseek_explore")
    from app.services.trading_gates import LIVE_SYNC_SOURCES, should_sync_live_for_source
    assert BRAIN_SOURCE in LIVE_SYNC_SOURCES
    assert should_sync_live_for_source(BRAIN_SOURCE)
    assert should_sync_live_for_source("brain_long")
    assert should_sync_live_for_source("midline_long")
    assert not should_sync_live_for_source("gemini_midline_long")
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
        "check_paper_direction_allowed",
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
    elif "check_paper_direction_allowed" not in src:
        _fail("paper_open_gate 未过 allow_long/allow_short 方向开关")
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
    assert should_use_deepseek_hold_advisor("midline_long") is True
    hold_q = (ROOT / "app/services/hold_advisor_query.py").read_text(encoding="utf-8")
    if "brain_source_sql_exclude" in hold_q:
        _fail("hold_advisor_query 不应再排除 BRAIN")
    else:
        _ok("hold advisor includes brain")
    mon = (ROOT / "app/services/position_sl_tp_monitor.py").read_text(encoding="utf-8")
    if "check_structure_swing_exit" not in mon or "uses_structure_swing_hold" not in mon:
        _fail("position_sl_tp_monitor 未接入结构买卖点出场")
    elif "_maybe_structure_swing_close" not in mon:
        _fail("monitor 缺少 _maybe_structure_swing_close")
    else:
        _ok("monitor structure swing exit (no 4-6h force close)")
    if "check_brain_trail_lock" in (ROOT / "app/services/brain_trail_exit.py").read_text(encoding="utf-8"):
        _ok("brain_trail_exit 模块仍保留（结构出场后 BRAIN 不再调用）")
    adv = (ROOT / "app/services/deepseek_position_advisor.py").read_text(encoding="utf-8")
    if "advisor_suggest_only" not in adv:
        _fail("DeepSeek 持仓顾问未对 BRAIN/中线改为仅建议")
    elif "brain_ds_force_close" in adv:
        _fail("BRAIN 持仓顾问不得再 force-close")
    else:
        _ok("hold advisor suggest-only for BRAIN/midline")
    from app.services.position_advisor_impl import (
        HOLD_ADVISOR_JSON_SYSTEM_ZH,
        HOLD_ADVISOR_STRUCTURE_JSON_SYSTEM_ZH,
        PositionAdvisorCore,
    )
    if "4 小时交易窗口" not in HOLD_ADVISOR_JSON_SYSTEM_ZH:
        _fail("探索/预测持仓顾问仍须 4h 窗口")
    elif "不限持仓时长" not in HOLD_ADVISOR_STRUCTURE_JSON_SYSTEM_ZH:
        _fail("结构波段持仓顾问 system 未取消时长限制")
    elif "禁止因持仓小时数" not in HOLD_ADVISOR_STRUCTURE_JSON_SYSTEM_ZH:
        _fail("结构波段持仓顾问未禁止时长/回吐卖")
    else:
        _ok("hold advisor structure rubric vs 4h explore rubric")
    ds_adv = (ROOT / "app/services/deepseek_position_advisor.py").read_text(encoding="utf-8")
    if "_temper_structure_swing_hold" not in ds_adv:
        _fail("DeepSeek tick 未接入 _temper_structure_swing_hold")
    elif "structure_hold" not in ds_adv:
        _fail("DeepSeek tick 未按结构仓跳过浮盈/亏损 temper")
    else:
        _ok("hold advisor tick skips profit/loss temper for structure")
    pos = {
        "entry_price": 100, "leverage": 5, "position_side": "LONG",
        "symbol": "BTC/USDT", "hold_hours": 8.0, "source": "brain_swing",
    }
    ctx = {
        "klines_15m": [], "klines_5m": [], "klines_1h": [],
        "structure_swing": True, "structure_status": "wait_stall",
        "structure_ready": False, "structure_why": "still_at_high",
        "structure_program_close": False, "big4_signal": "LONG",
        "narrative_15m": "", "narrative_1h": "",
    }
    p = PositionAdvisorCore._build_prompt(pos, 101.0, ctx)
    if "4 小时交易窗口" in p or "合适高点" not in p:
        _fail("BRAIN 持仓 prompt 仍是 4h 或缺少结构卖点")
    else:
        _ok("BRAIN hold prompt is structure swing")
    pos_ds = dict(pos)
    pos_ds["source"] = "deepseek_explore"
    ctx_ds = dict(ctx)
    ctx_ds["structure_swing"] = False
    p_ds = PositionAdvisorCore._build_prompt(pos_ds, 101.0, ctx_ds)
    if "4 小时交易窗口" not in p_ds:
        _fail("DeepSeek 探索持仓 prompt 丢掉了 4h 窗口")
    else:
        _ok("explore hold prompt still 4h")
    hold_a, _ = PositionAdvisorCore._temper_structure_swing_hold(
        "sell", "浮盈回吐该卖",
        {"structure_swing": True, "structure_program_close": False, "structure_status": "wait_stall"},
    )
    if hold_a != "hold":
        _fail("结构点未到时 sell 应打回 hold")
    else:
        _ok("structure temper blocks early sell")
    obs_a, _ = PositionAdvisorCore._temper_structure_swing_hold(
        "hold", "仍顺向",
        {"structure_swing": True, "structure_program_close": True, "structure_status": "exhaustion_ready"},
    )
    if obs_a != "observe":
        _fail("结构点已到 hold 应升为 observe")
    else:
        _ok("structure temper observes when program close ready")
    regime_src = (ROOT / "app/services/brain_market_regime.py").read_text(encoding="utf-8")
    if "big4_long_allows_exhaustion" not in regime_src:
        _fail("Big4 LONG 未放开 B3/C4 高点滞涨空")
    elif "big4_long_allows_breakdown_C1" not in regime_src:
        _fail("Big4 LONG 未放开 C1 破位跟风")
    elif "big4_long_blocks_short" not in regime_src:
        _fail("BRAIN 未在 Big4 LONG 时阻断 A2/B2 逆势空")
    elif "big4_not_long_blocks_C3" not in regime_src:
        _fail("BRAIN 未要求 C3 须 Big4 LONG")
    else:
        _ok("Big4 LONG allows C4/C1 shorts, blocks A2/B2/B3; C3 needs Big4 LONG")
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
        "big4_short_blocks_long" in orch2
        or "short_needs_big4_short" in orch2
        or "_strong_token_short_override" in orch2
    ):
        _fail("orchestrator 未完整接入 A2/C1 受控补空门控")
    else:
        _ok("orchestrator controlled short gates")

    from app.services.brain_strategy_orchestrator import _fast_event_winprob_allowed
    c3 = {
        "playbook": "C3",
        "signals": ["h1_breakout_up", "impulse_up"],
    }
    ok_fast, reason_fast = _fast_event_winprob_allowed("LONG", c3, 0.48, 0.52)
    assert ok_fast and "relaxed" in reason_fast
    ok_bad, reason_bad = _fast_event_winprob_allowed("LONG", c3, 0.40, 0.58)
    assert not ok_bad and "too_bad" in reason_bad
    b3 = {
        "playbook": "B3",
        "signals": ["exhaustion_up", "long_upper_wick"],
    }
    ok_b3, _ = _fast_event_winprob_allowed("SHORT", b3, 0.52, 0.47)
    assert ok_b3
    _ok("fast-event winprob")

    orch2 = (ROOT / "app/services/brain_strategy_orchestrator.py").read_text(encoding="utf-8")
    if "compute_pullback_entry" not in orch2:
        _fail("orchestrator 未接入回调买入点")
    else:
        _ok("orchestrator pullback entry")


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
    act_bull, pull_bull, keep_bull = trail_levels_from_sl_tp(8.0, 12.0, bull_long=True)
    assert act_bull >= 2.0 and pull_bull >= 0.8 and keep_bull >= 0.5
    assert check_brain_trail_lock(0.008, 0.013, activate_pct=act_bull, pullback_pct=pull_bull) is None
    assert check_brain_trail_lock(0.015, 0.024, activate_pct=act_bull, pullback_pct=pull_bull)
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
    orch = (ROOT / "app/services/brain_strategy_orchestrator.py").read_text(encoding="utf-8")
    if "load_brain_universe" not in orch:
        _fail("BRAIN 未改扫市值前300")
    else:
        _ok("BRAIN universe market-cap top 300")
    _ok("tick config + live status")


def test_market_regime_page_uses_brain_gates() -> None:
    page = (ROOT / "templates/market_regime.html").read_text(encoding="utf-8")
    js = (ROOT / "static/js/market_regime_page.js").read_text(encoding="utf-8")
    api = (ROOT / "app/api/market_regime_api.py").read_text(encoding="utf-8")
    if "$62,400" in page or "Predict: Flat" in page:
        _fail("行情识别页仍是写死演示")
    if "/api/market-regime/live" not in js:
        _fail("行情识别 JS 未读 /live")
    if "evaluate_big4_gate" not in api or "evaluate_global_daily_regime" not in api:
        _fail("行情识别 API 未接 BRAIN 闸门")
    _ok("行情识别页接 BRAIN Big4 + 日线")


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


def test_playbook_impulse_c3() -> None:
    from app.services.brain_playbook import classify_playbook

    impulse_1h = []
    p = 100.0
    for i in range(120):
        if i == 119:
            p = 104.0
            vol = 9000
        else:
            p += 0.01
            vol = 1000
        impulse_1h.append({
            "open_price": p - 0.05, "high_price": p + 0.1,
            "low_price": p - 0.1, "close_price": p, "volume": vol,
        })

    impulse_15m = []
    q = 100.0
    for i in range(80):
        if i >= 76:
            q += 0.65
            vol = 2500
        else:
            q += 0.01
            vol = 500
        impulse_15m.append({
            "open_price": q - 0.03, "high_price": q + 0.08,
            "low_price": q - 0.08, "close_price": q, "volume": vol,
        })

    out = classify_playbook(
        impulse_1h,
        impulse_15m,
        big4={"big4_ok": False, "bias": "FLAT", "reason": "big4_weak"},
        win_prob_long=0.6,
        win_prob_short=0.4,
    )
    assert out["playbook"] == "C3", out
    assert out["side"] == "LONG"
    assert "impulse_up" in out["signals"]
    _ok("playbook impulse C3")


def _bar(p: float, vol: float = 500.0, hi: float = 0.08, lo: float = 0.08) -> dict:
    return {
        "open_price": p - 0.02,
        "high_price": p + hi,
        "low_price": p - lo,
        "close_price": p,
        "volume": vol,
    }


def test_playbook_bounce_is_not_a1() -> None:
    from app.services.brain_playbook import classify_playbook
    from app.services.entry_timing import compute_pullback_entry

    rows_1h = []
    p = 100.0
    for i in range(180):
        p += 0.12
        rows_1h.append(_bar(p, vol=1000 + i, hi=0.2, lo=0.2))

    rows_15m = []
    q = 118.0
    for _ in range(96):
        q += 0.04
        rows_15m.append(_bar(q, vol=800, hi=0.08, lo=0.06))
    for _ in range(8):
        q -= 0.35
        rows_15m.append(_bar(q, vol=1600, hi=0.04, lo=0.18))
    dump_low = q
    for _ in range(8):
        q += 0.22
        rows_15m.append(_bar(q, vol=350, hi=0.10, lo=0.03))
    assert q > dump_low

    out = classify_playbook(
        rows_1h,
        rows_15m,
        big4={"big4_ok": True, "bias": "LONG"},
        win_prob_long=0.62,
        win_prob_short=0.40,
    )
    assert "15m_bounce_from_low" in out["signals"], out["signals"]
    assert out["playbook"] != "A1", out
    assert out["side"] != "LONG" or out["playbook"] not in {"A1", "B4"}, out

    bounce_entry = compute_pullback_entry(
        "LONG",
        "A1",
        rows_15m,
        playbook_row={
            "signals": [
                "ema_bull_align", "hh_hl", "15m_higher_low",
                "volume_shrink_pullback", "15m_bounce_from_low",
            ]
        },
        ref_price=rows_15m[-1]["close_price"],
    )
    assert bounce_entry.ready is False, bounce_entry
    assert bounce_entry.reason == "bounce_not_pullback", bounce_entry
    _ok("dump+bounce is not A1 LONG; entry veto bounce_not_pullback")


def test_playbook_real_pullback_still_a1() -> None:
    from app.services.brain_playbook import classify_playbook

    rows_1h = []
    p = 100.0
    for i in range(180):
        p += 0.12
        rows_1h.append(_bar(p, vol=1000 + i, hi=0.2, lo=0.2))

    rows_15m = []
    q = 118.0
    for _ in range(88):
        q += 0.03
        rows_15m.append(_bar(q, vol=800, hi=0.08, lo=0.05))
    window_low = q
    for _ in range(8):
        q += 0.28
        rows_15m.append(_bar(q, vol=900, hi=0.10, lo=0.05))
    peak = q
    floor = peak * 0.985
    for i in range(10):
        q = peak - (peak - floor) * ((i + 1) / 10.0)
        rows_15m.append(_bar(q, vol=260, hi=0.04, lo=0.05))

    out = classify_playbook(
        rows_1h,
        rows_15m,
        big4={"big4_ok": True, "bias": "LONG"},
        win_prob_long=0.62,
        win_prob_short=0.40,
    )
    assert "15m_trend_high_pullback" in out["signals"], out["signals"]
    assert out["playbook"] == "A1", out
    assert out["side"] == "LONG", out
    _ok("real 15m pullback from high still A1")


def test_playbook_pullback_then_bounce_still_a1() -> None:
    """回踩后从回踩低点抬起仍是 A1，不能当成死猫跳否决。"""
    from app.services.brain_playbook import classify_playbook
    from app.services.entry_timing import compute_pullback_entry

    rows_1h = []
    p = 100.0
    for i in range(180):
        p += 0.12
        rows_1h.append(_bar(p, vol=1000 + i, hi=0.2, lo=0.2))

    rows_15m = []
    q = 118.0
    for _ in range(88):
        q += 0.03
        rows_15m.append(_bar(q, vol=800, hi=0.08, lo=0.05))
    for _ in range(8):
        q += 0.28
        rows_15m.append(_bar(q, vol=900, hi=0.10, lo=0.05))
    peak = q
    floor = peak * 0.985
    for i in range(8):
        q = peak - (peak - floor) * ((i + 1) / 8.0)
        rows_15m.append(_bar(q, vol=260, hi=0.04, lo=0.05))
    for _ in range(3):
        q += (peak - floor) * 0.10
        rows_15m.append(_bar(q, vol=280, hi=0.04, lo=0.06))

    out = classify_playbook(
        rows_1h,
        rows_15m,
        big4={"big4_ok": True, "bias": "LONG"},
        win_prob_long=0.62,
        win_prob_short=0.40,
    )
    assert "15m_trend_high_pullback" in out["signals"], out["signals"]
    assert "15m_bounce_from_low" in out["signals"], out["signals"]
    assert out["playbook"] == "A1", out
    assert out["side"] == "LONG", out

    ready_or_wait = compute_pullback_entry(
        "LONG",
        "A1",
        rows_15m,
        playbook_row={"signals": list(out["signals"])},
        ref_price=rows_15m[-1]["close_price"],
    )
    assert ready_or_wait.reason != "bounce_not_pullback", ready_or_wait
    _ok("trend-high pullback then bounce from pull low is still A1")


def _failed_retest_15m() -> list:
    rows = []
    q = 100.0
    for _ in range(40):
        q += 0.02
        rows.append(_bar(q, vol=700, hi=0.06, lo=0.04))
    for _ in range(8):
        q += 0.45
        rows.append(_bar(q, vol=1400, hi=0.12, lo=0.04))
    for _ in range(8):
        q -= 0.28
        rows.append(_bar(q, vol=1100, hi=0.04, lo=0.12))
    for i in range(6):
        q += 0.18
        rows.append(_bar(q, vol=380, hi=0.22 if i == 5 else 0.08, lo=0.03))
    return rows


def test_playbook_failed_retest_is_short() -> None:
    from app.services.brain_playbook import classify_playbook
    from app.services.entry_timing import compute_pullback_entry

    rows_1h = []
    p = 100.0
    for i in range(180):
        p += 0.08
        rows_1h.append(_bar(p, vol=1000 + i, hi=0.2, lo=0.2))
    rows_15m = _failed_retest_15m()

    out = classify_playbook(
        rows_1h,
        rows_15m,
        big4={"big4_ok": True, "bias": "LONG"},
        win_prob_long=0.62,
        win_prob_short=0.40,
    )
    assert "15m_failed_retest" in out["signals"], out["signals"]
    assert out["playbook"] != "A1", out
    assert out["playbook"] == "B3", out
    assert out["side"] == "SHORT", out

    a1_block = compute_pullback_entry(
        "LONG",
        "A1",
        rows_15m,
        playbook_row={"signals": ["ema_bull_align", "hh_hl", "15m_failed_retest"]},
        ref_price=rows_15m[-1]["close_price"],
    )
    assert a1_block.ready is False, a1_block
    assert a1_block.reason == "failed_retest_short", a1_block

    short_wait = compute_pullback_entry(
        "SHORT",
        "B3",
        rows_15m,
        playbook_row={"signals": ["15m_failed_retest", "15m_lower_high"]},
        ref_price=rows_15m[-1]["close_price"],
    )
    assert short_wait.ready is False, short_wait
    assert short_wait.status in {"wait_stall", "missed_high"}, short_wait

    peak = max(float(r["high_price"]) for r in rows_15m[-8:])
    reject_close = peak * (1.0 - 0.0035)
    rows_reject = list(rows_15m) + [{
        "open_price": peak * 0.999,
        "high_price": peak * 1.0004,
        "low_price": reject_close * 0.997,
        "close_price": reject_close,
        "volume": 520,
    }]
    short_ready = compute_pullback_entry(
        "SHORT",
        "B3",
        rows_reject,
        playbook_row={"signals": ["15m_failed_retest", "15m_lower_high", "long_upper_wick"]},
        ref_price=reject_close,
    )
    assert short_ready.ready is True, short_ready
    assert short_ready.status == "exhaustion_ready", short_ready
    _ok("prior high then lower high is B3 SHORT, not A1; reject K still opens")


def test_directional_gate() -> None:
    from app.services.brain_winrate import directional_open_allowed

    ok, reason = directional_open_allowed("LONG", 0.60, 0.50)
    assert ok, reason
    ok2, reason2 = directional_open_allowed("LONG", 0.56, 0.54)
    assert not ok2 and "rel_edge" in reason2
    ok3, _ = directional_open_allowed("SHORT", 0.40, 0.58)
    assert ok3
    _ok("directional_open_allowed")


def test_brain_market_regime() -> None:
    from app.services.brain_market_regime import (
        BEAR_TREND,
        BULL_TREND,
        CRASH_DOWN,
        GLOBAL_DAILY_BEAR_PROBE,
        LOW_VOL_NO_TRADE,
        PANIC_REBOUND,
        TOKEN_DIVERGENCE,
        brain_open_regime_decision,
        classify_global_daily_regime_from_rows,
    )

    a1 = {
        "side": "LONG",
        "playbook": "A1",
        "confirmed": True,
        "edge_score": 0.90,
        "features": {"h1_side": "LONG", "m15_side": "LONG"},
        "signals": ["ema_bull_align", "hh_hl", "15m_higher_low"],
    }
    dec = brain_open_regime_decision(
        big4={"big4_ok": True, "bias": "LONG", "bull_count": 3, "bear_count": 0},
        playbook_row=a1,
        side="LONG",
        playbook="A1",
    )
    assert dec.regime == BULL_TREND and dec.margin_multiplier > 0

    dec2 = brain_open_regime_decision(
        big4={"big4_ok": True, "bias": "SHORT", "bull_count": 0, "bear_count": 3},
        playbook_row=a1,
        side="LONG",
        playbook="A1",
    )
    assert dec2.regime == BEAR_TREND and dec2.margin_multiplier == 0

    c1 = {
        "side": "SHORT",
        "playbook": "C1",
        "confirmed": True,
        "edge_score": 0.85,
        "features": {"h1_side": "SHORT", "m15_side": "SHORT"},
        "signals": ["crash_spike", "break_support", "volume_expand_down"],
    }
    dec3 = brain_open_regime_decision(
        big4={"big4_ok": True, "bias": "FLAT", "bull_count": 1, "bear_count": 1},
        playbook_row=c1,
        side="SHORT",
        playbook="C1",
    )
    assert dec3.regime in (CRASH_DOWN, TOKEN_DIVERGENCE) and dec3.margin_multiplier > 0

    dec4 = brain_open_regime_decision(
        big4={"big4_ok": False, "bias": "FLAT", "reason": "big4_weak"},
        playbook_row=a1,
        side="LONG",
        playbook="A1",
    )
    assert dec4.regime == LOW_VOL_NO_TRADE and dec4.margin_multiplier == 0

    rebound_short = {
        "side": "SHORT",
        "playbook": "C1",
        "confirmed": True,
        "edge_score": 0.85,
        "features": {"h1_side": "SHORT", "m15_side": "SHORT"},
        "signals": ["crash_spike", "break_support", "long_lower_wick"],
    }
    dec5 = brain_open_regime_decision(
        big4={"big4_ok": True, "bias": "FLAT", "bull_count": 1, "bear_count": 1},
        playbook_row=rebound_short,
        side="SHORT",
        playbook="C1",
    )
    assert dec5.regime == PANIC_REBOUND and dec5.margin_multiplier == 0

    def _daily_rows(closes):
        return [
            {
                "open_price": c,
                "high_price": c * 1.01,
                "low_price": c * 0.99,
                "close_price": c,
                "volume": 1000,
            }
            for c in closes
        ]

    bear_closes = [100 - i * 0.45 for i in range(120)]
    global_bear = classify_global_daily_regime_from_rows(
        _daily_rows(bear_closes),
        _daily_rows([80 - i * 0.35 for i in range(120)]),
    )
    assert global_bear["global_regime"] == GLOBAL_DAILY_BEAR_PROBE

    dec6 = brain_open_regime_decision(
        big4={"big4_ok": True, "bias": "LONG", "bull_count": 3, "bear_count": 0},
        playbook_row=a1,
        side="LONG",
        playbook="A1",
        global_regime=global_bear,
    )
    assert dec6.margin_multiplier > 0 and "defers_to_big4_long_A1" in dec6.reason

    dec6_flat = brain_open_regime_decision(
        big4={"big4_ok": True, "bias": "FLAT", "bull_count": 1, "bear_count": 1},
        playbook_row=a1,
        side="LONG",
        playbook="A1",
        global_regime=global_bear,
    )
    assert dec6_flat.margin_multiplier == 0 and "global_daily_bear_probe_blocks_long" in dec6_flat.reason

    b3_exh = {
        "side": "SHORT",
        "playbook": "B3",
        "confirmed": True,
        "edge_score": 0.90,
        "features": {"h1_side": "LONG", "m15_side": "SHORT"},
        "signals": ["pump_spike", "long_upper_wick", "volume_diverge_bear", "exhaustion_up"],
    }
    dec_b3_bull = brain_open_regime_decision(
        big4={"big4_ok": True, "bias": "LONG", "bull_count": 3, "bear_count": 0},
        playbook_row=b3_exh,
        side="SHORT",
        playbook="B3",
    )
    assert dec_b3_bull.margin_multiplier == 0 and "big4_long_blocks_short" in dec_b3_bull.reason

    dec_b3_flat = brain_open_regime_decision(
        big4={"big4_ok": True, "bias": "FLAT", "bull_count": 1, "bear_count": 1},
        playbook_row=b3_exh,
        side="SHORT",
        playbook="B3",
    )
    assert dec_b3_flat.margin_multiplier > 0, dec_b3_flat

    b3_pause = {
        "side": "SHORT",
        "playbook": "B3",
        "confirmed": True,
        "edge_score": 0.90,
        "features": {"h1_side": "LONG", "m15_side": "LONG", "stall_at_high": True},
        "signals": ["pump_spike", "stall_at_high", "15m_stop_new_high", "near_7d_high"],
    }
    dec_b3_pause = brain_open_regime_decision(
        big4={"big4_ok": True, "bias": "LONG", "bull_count": 3, "bear_count": 0},
        playbook_row=b3_pause,
        side="SHORT",
        playbook="B3",
    )
    assert dec_b3_pause.margin_multiplier == 0 and "big4_long_blocks_short" in dec_b3_pause.reason

    dec_c1_vs_big4_long = brain_open_regime_decision(
        big4={"big4_ok": True, "bias": "LONG", "bull_count": 3, "bear_count": 0},
        playbook_row=c1,
        side="SHORT",
        playbook="C1",
        global_regime=global_bear,
    )
    assert (
        dec_c1_vs_big4_long.margin_multiplier > 0
        and "big4_long_allows_breakdown_C1" in dec_c1_vs_big4_long.reason
    )

    a2_vs_bull = {
        "side": "SHORT",
        "playbook": "A2",
        "confirmed": True,
        "edge_score": 0.90,
        "features": {"h1_side": "SHORT", "m15_side": "SHORT"},
        "signals": ["ema_bear_align", "lh_ll", "15m_lower_high", "ema_reject"],
    }
    dec_a2_bull = brain_open_regime_decision(
        big4={"big4_ok": True, "bias": "LONG", "bull_count": 3, "bear_count": 0},
        playbook_row=a2_vs_bull,
        side="SHORT",
        playbook="A2",
    )
    assert dec_a2_bull.margin_multiplier == 0 and "big4_long_blocks_short" in dec_a2_bull.reason

    a2 = {
        "side": "SHORT",
        "playbook": "A2",
        "confirmed": True,
        "edge_score": 0.86,
        "features": {"h1_side": "SHORT", "m15_side": "SHORT"},
        "signals": ["ema_bear_align", "lh_ll", "15m_lower_high", "ema_reject"],
    }
    dec7 = brain_open_regime_decision(
        big4={"big4_ok": True, "bias": "FLAT", "bull_count": 1, "bear_count": 1},
        playbook_row=a2,
        side="SHORT",
        playbook="A2",
        global_regime=global_bear,
    )
    assert dec7.margin_multiplier > 0 and "A2" in dec7.reason

    c3_impulse = {
        "side": "LONG",
        "playbook": "C3",
        "confirmed": True,
        "edge_score": 0.95,
        "features": {"h1_side": "LONG", "m15_side": "LONG"},
        "signals": ["h1_breakout_up", "impulse_up", "pump_spike"],
    }
    dec8 = brain_open_regime_decision(
        big4={"big4_ok": False, "bias": "FLAT", "reason": "big4_weak"},
        playbook_row=c3_impulse,
        side="LONG",
        playbook="C3",
        global_regime=global_bear,
    )
    assert dec8.margin_multiplier == 0 and "big4_not_long_blocks_C3" in dec8.reason

    dec8b = brain_open_regime_decision(
        big4={"big4_ok": True, "bias": "LONG", "bull_count": 3, "bear_count": 0},
        playbook_row=c3_impulse,
        side="LONG",
        playbook="C3",
    )
    assert dec8b.margin_multiplier > 0 and "C3" in dec8b.reason

    b3_exhaustion = {
        "side": "SHORT",
        "playbook": "B3",
        "confirmed": True,
        "edge_score": 0.86,
        "features": {"h1_side": "LONG", "m15_side": "SHORT"},
        "signals": ["pump_spike", "long_upper_wick", "volume_diverge_bear", "exhaustion_up"],
    }
    dec9 = brain_open_regime_decision(
        big4={"big4_ok": False, "bias": "FLAT", "reason": "big4_weak"},
        playbook_row=b3_exhaustion,
        side="SHORT",
        playbook="B3",
        global_regime=global_bear,
    )
    assert dec9.margin_multiplier > 0 and "exhaustion_B3" in dec9.reason

    _ok("brain_market_regime")


def test_orchestrator_syntax() -> None:
    for rel in (
        "app/services/brain_config.py",
        "app/services/brain_market_regime.py",
        "app/services/brain_risk_params.py",
        "app/services/brain_trail_exit.py",
        "app/services/brain_wick.py",
        "app/services/brain_market_analyzer.py",
        "app/services/brain_winrate.py",
        "app/services/brain_playbook.py",
        "app/services/brain_opportunity_store.py",
        "app/services/brain_strategy_orchestrator.py",
        "app/services/entry_timing.py",
        "app/services/structure_swing_exit.py",
        "app/services/position_sl_tp_monitor.py",
        "app/services/smart_exit_optimizer.py",
        "app/trading/futures_trading_engine.py",
        "smart_trader_service.py",
    ):
        path = ROOT / rel
        ast.parse(path.read_text(encoding="utf-8"))
        _ok(f"syntax {rel}")


def test_entry_timing_pullback() -> None:
    from app.services.entry_timing import compute_pullback_entry

    bars = []
    p = 100.0
    for i in range(36):
        p += 0.02
        bars.append({
            "open_price": p - 0.01,
            "high_price": p + 0.03,
            "low_price": p - 0.02,
            "close_price": p,
            "volume": 800,
        })
    spike = p
    for i in range(4):
        spike += 0.55
        bars.append({
            "open_price": spike - 0.10,
            "high_price": spike + 0.12,
            "low_price": spike - 0.08,
            "close_price": spike,
            "volume": 2800,
        })
    chase = compute_pullback_entry(
        "LONG", "C3", bars,
        playbook_row={"signals": ["impulse_up", "h1_breakout_up", "break_resistance"]},
        ref_price=bars[-1]["close_price"],
    )
    assert chase.ready is False, chase
    assert chase.status in {"wait_pullback", "wait_bounce"}, chase

    pull = list(bars)
    peak = pull[-1]["close_price"]
    for i, frac in enumerate((0.25, 0.45, 0.62, 0.72, 0.78)):
        p = peak - (peak - 101.30) * frac
        pull.append({
            "open_price": p + 0.04,
            "high_price": p + 0.05,
            "low_price": p - 0.03,
            "close_price": p,
            "volume": 350,
        })
    ready = compute_pullback_entry(
        "LONG", "C3", pull,
        playbook_row={"signals": ["break_resistance", "volume_shrink_pullback", "15m_higher_low"]},
        ref_price=pull[-1]["close_price"],
    )
    assert ready.ready is True, ready
    assert ready.status == "pullback_ready"

    grind = []
    g = 100.0
    for _ in range(40):
        g += 0.04
        grind.append({
            "open_price": g - 0.01,
            "high_price": g + 0.03,
            "low_price": g - 0.02,
            "close_price": g,
            "volume": 900,
        })
    a1_chase = compute_pullback_entry(
        "LONG", "A1", grind,
        playbook_row={"signals": ["ema_bull_align", "hh_hl", "15m_higher_low"]},
        ref_price=grind[-1]["close_price"],
    )
    assert a1_chase.ready is False, a1_chase
    assert a1_chase.status == "wait_pullback"

    shallow = list(grind)
    peak_g = shallow[-1]["close_price"]
    px = peak_g * 0.9955
    shallow.append({
        "open_price": peak_g - 0.02,
        "high_price": peak_g + 0.01,
        "low_price": px - 0.02,
        "close_price": px,
        "volume": 400,
    })
    a1_shallow = compute_pullback_entry(
        "LONG", "A1", shallow,
        playbook_row={"signals": ["ema_bull_align", "hh_hl", "15m_higher_low", "volume_shrink_pullback"]},
        ref_price=px,
    )
    assert a1_shallow.ready is False, a1_shallow
    assert a1_shallow.status in {"wait_pullback", "wait_bounce"}, a1_shallow

    a1_ready = compute_pullback_entry(
        "LONG", "A1", pull,
        playbook_row={"signals": ["ema_bull_align", "hh_hl", "volume_shrink_pullback", "15m_higher_low", "15m_trend_high_pullback"]},
        ref_price=pull[-1]["close_price"],
    )
    assert a1_ready.ready is True, a1_ready
    assert a1_ready.status == "pullback_ready"

    stall = compute_pullback_entry(
        "LONG", "A1", pull,
        playbook_row={
            "signals": [
                "ema_bull_align", "volume_shrink_pullback",
                "15m_stop_new_high", "15m_lower_high", "top_callback",
            ]
        },
        ref_price=pull[-1]["close_price"],
    )
    assert stall.ready is False, stall
    assert stall.reason == "stall_high_not_pullback", stall
    _ok("entry timing waits for C3 pullback then arms the limit")


def _pump_bars() -> list:
    bars = []
    p = 100.0
    for _ in range(36):
        p += 0.02
        bars.append({
            "open_price": p - 0.01,
            "high_price": p + 0.03,
            "low_price": p - 0.02,
            "close_price": p,
            "volume": 800,
        })
    for _ in range(4):
        p += 0.55
        bars.append({
            "open_price": p - 0.10,
            "high_price": p + 0.12,
            "low_price": p - 0.08,
            "close_price": p,
            "volume": 2800,
        })
    return bars, p


def test_entry_timing_exhaustion_short() -> None:
    from app.services.entry_timing import compute_pullback_entry

    bars, peak = _pump_bars()
    bars.append({
        "open_price": peak - 0.02,
        "high_price": peak + 0.45,
        "low_price": peak - 0.15,
        "close_price": peak - 0.08,
        "volume": 900,
    })
    ready = compute_pullback_entry(
        "SHORT", "B3", bars,
        playbook_row={"signals": ["pump_spike", "exhaustion_up", "long_upper_wick", "volume_diverge_bear"]},
        ref_price=bars[-1]["close_price"],
    )
    assert ready.ready is True, ready
    assert ready.status == "exhaustion_ready"
    assert ready.mode == "exhaustion"
    assert ready.limit_price is not None and ready.limit_price > bars[-1]["close_price"]

    accel, p = _pump_bars()
    accel.append({
        "open_price": p - 0.05,
        "high_price": p + 0.50,
        "low_price": p - 0.06,
        "close_price": p + 0.48,
        "volume": 3600,
    })
    waiting = compute_pullback_entry(
        "SHORT", "B3", accel,
        playbook_row={"signals": ["pump_spike"]},
        ref_price=accel[-1]["close_price"],
    )
    assert waiting.ready is False, waiting
    assert waiting.status in {"wait_stall", "invalidated"}, waiting

    dumped, peak2 = _pump_bars()
    dumped.append({
        "open_price": peak2 - 0.02,
        "high_price": peak2 + 0.20,
        "low_price": peak2 - 0.10,
        "close_price": peak2 - 0.08,
        "volume": 900,
    })
    dump_px = dumped[-1]["close_price"]
    for _ in range(4):
        dump_px -= 0.55
        dumped.append({
            "open_price": dump_px + 0.08,
            "high_price": dump_px + 0.10,
            "low_price": dump_px - 0.06,
            "close_price": dump_px,
            "volume": 1200,
        })
    missed = compute_pullback_entry(
        "SHORT", "B3", dumped,
        playbook_row={"signals": ["exhaustion_up", "long_upper_wick"]},
        ref_price=dumped[-1]["close_price"],
    )
    assert missed.ready is False, missed
    assert missed.status == "missed_high", missed

    sitting, peak3 = _pump_bars()
    tagged = max(b["high_price"] for b in sitting[-8:])
    sitting.append({
        "open_price": tagged - 0.02,
        "high_price": tagged,
        "low_price": tagged - 0.04,
        "close_price": tagged,
        "volume": 900,
    })
    waiting_cb = compute_pullback_entry(
        "SHORT", "B3", sitting,
        playbook_row={"signals": ["pump_spike", "long_upper_wick", "volume_diverge_bear", "exhaustion_up"]},
        ref_price=sitting[-1]["close_price"],
    )
    assert waiting_cb.ready is False, waiting_cb
    assert waiting_cb.status in {"wait_stall", "invalidated"}, waiting_cb

    pause, peak4 = _pump_bars()
    pause.append({
        "open_price": peak4 - 0.02,
        "high_price": peak4 + 0.05,
        "low_price": peak4 - 0.08,
        "close_price": peak4 - 0.03,
        "volume": 1100,
    })
    premature = compute_pullback_entry(
        "SHORT", "B3", pause,
        playbook_row={"signals": ["pump_spike", "stall_at_high", "15m_stop_new_high", "near_7d_high"]},
        ref_price=pause[-1]["close_price"],
    )
    assert premature.ready is False, premature
    assert premature.status in {"wait_stall", "invalidated"}, premature
    _ok("entry timing sells B3 first callback off the high, waits if still extending or no callback yet")


def test_entry_timing_c1_follow() -> None:
    from app.services.entry_timing import compute_pullback_entry

    bars = []
    for _ in range(48):
        bars.append({
            "open_price": 99.98,
            "high_price": 100.06,
            "low_price": 99.90,
            "close_price": 100.00,
            "volume": 700,
        })
    px = 100.0
    for _ in range(4):
        px -= 0.45
        bars.append({
            "open_price": px + 0.10,
            "high_price": px + 0.12,
            "low_price": px - 0.08,
            "close_price": px,
            "volume": 2600,
        })
    ready = compute_pullback_entry(
        "SHORT", "C1", bars,
        playbook_row={"signals": ["break_support", "volume_expand_down", "crash_spike", "impulse_down"]},
        ref_price=bars[-1]["close_price"],
    )
    assert ready.ready is True, ready
    assert ready.status == "breakdown_ready"
    assert ready.mode == "follow"
    assert ready.limit_price is not None and ready.limit_price > bars[-1]["close_price"]
    _ok("entry timing follows a fresh C1 breakdown instead of waiting for a bounce")


def test_entry_timing_a2_b2_wait_for_reject() -> None:
    from app.services.entry_timing import compute_pullback_entry

    dump = []
    px = 108.0
    for _ in range(40):
        px -= 0.06
        dump.append({
            "open_price": px + 0.03,
            "high_price": px + 0.05,
            "low_price": px - 0.02,
            "close_price": px,
            "volume": 900,
        })
    a2_dump = compute_pullback_entry(
        "SHORT", "A2", dump,
        playbook_row={"signals": ["ema_bear_align", "lh_ll", "volume_shrink_pullback", "15m_lower_high"]},
        ref_price=dump[-1]["close_price"],
    )
    assert a2_dump.ready is False, a2_dump

    bounce = list(dump)
    p = bounce[-1]["close_price"]
    for _ in range(3):
        p += 0.10
        bounce.append({
            "open_price": p - 0.04,
            "high_price": p + 0.01,
            "low_price": p - 0.05,
            "close_price": p,
            "volume": 400,
        })
    a2_no_reject = compute_pullback_entry(
        "SHORT", "A2", bounce,
        playbook_row={"signals": ["ema_bear_align", "lh_ll", "volume_shrink_pullback", "15m_lower_high"]},
        ref_price=bounce[-1]["close_price"],
    )
    assert a2_no_reject.ready is False, a2_no_reject

    reject = list(bounce)
    mid = reject[-1]["close_price"]
    reject.append({
        "open_price": mid + 0.01,
        "high_price": mid + 0.18,
        "low_price": mid - 0.06,
        "close_price": mid - 0.02,
        "volume": 500,
    })
    a2_ready = compute_pullback_entry(
        "SHORT", "A2", reject,
        playbook_row={"signals": ["ema_bear_align", "lh_ll", "ema_reject", "long_upper_wick"]},
        ref_price=reject[-1]["close_price"],
    )
    assert a2_ready.ready is True, a2_ready
    assert a2_ready.status == "pullback_ready"

    b2_dump = compute_pullback_entry(
        "SHORT", "B2", dump,
        playbook_row={"signals": ["break_support", "volume_expand_down", "ema_bear_align"]},
        ref_price=dump[-1]["close_price"],
    )
    assert b2_dump.ready is False, b2_dump
    assert b2_dump.reason == "need_bounce_before_fail"

    b2_bounce = list(dump)
    q = b2_bounce[-1]["close_price"]
    for _ in range(5):
        q += 0.18
        b2_bounce.append({
            "open_price": q - 0.04,
            "high_price": q + 0.02,
            "low_price": q - 0.05,
            "close_price": q,
            "volume": 400,
        })
    fail = list(b2_bounce)
    fail.append({
        "open_price": b2_bounce[-1]["close_price"],
        "high_price": b2_bounce[-1]["close_price"] + 0.04,
        "low_price": dump[-1]["close_price"] - 0.08,
        "close_price": dump[-1]["close_price"] - 0.05,
        "volume": 2200,
    })
    b2_ready = compute_pullback_entry(
        "SHORT", "B2", fail,
        playbook_row={"signals": ["break_support", "ema_reject", "15m_lower_high", "long_upper_wick"]},
        ref_price=fail[-1]["close_price"],
    )
    assert b2_ready.ready is True, b2_ready
    assert b2_ready.status == "breakdown_ready"
    _ok("A2/B2 wait for a bounce reject; do not short the first dump bar")


def test_entry_timing_c3_midline_follow() -> None:
    from app.services.entry_timing import compute_pullback_entry

    bars = []
    p = 100.0
    for _ in range(48):
        bars.append({
            "open_price": p - 0.01,
            "high_price": p + 0.04,
            "low_price": p - 0.03,
            "close_price": p,
            "volume": 600,
        })
        p += 0.01
    p += 0.50
    bars.append({
        "open_price": p - 0.08,
        "high_price": p + 0.10,
        "low_price": p - 0.06,
        "close_price": p,
        "volume": 2400,
    })
    sigs = ["impulse_up", "h1_breakout_up", "break_resistance", "volume_expand_up"]
    brain = compute_pullback_entry(
        "LONG", "C3", bars,
        playbook_row={"signals": sigs},
        ref_price=bars[-1]["close_price"],
    )
    assert brain.ready is False, brain
    follow = compute_pullback_entry(
        "LONG", "C3", bars,
        playbook_row={"signals": sigs},
        ref_price=bars[-1]["close_price"],
        follow_breakout=True,
    )
    assert follow.ready is True, follow
    assert follow.status == "breakout_ready"

    blow = compute_pullback_entry(
        "LONG", "C3", bars,
        playbook_row={"signals": sigs + ["rsi_extreme_high", "near_7d_high"]},
        ref_price=bars[-1]["close_price"],
        follow_breakout=True,
    )
    assert blow.ready is False, blow
    assert blow.status == "chase_blowoff", blow

    chase = []
    q = 100.0
    for _ in range(48):
        chase.append({
            "open_price": q - 0.01,
            "high_price": q + 0.04,
            "low_price": q - 0.03,
            "close_price": q,
            "volume": 600,
        })
        q += 0.01
    for _ in range(8):
        q += 0.40
        chase.append({
            "open_price": q - 0.08,
            "high_price": q + 0.10,
            "low_price": q - 0.06,
            "close_price": q,
            "volume": 2400,
        })
    missed = compute_pullback_entry(
        "LONG", "C3", chase,
        playbook_row={"signals": sigs},
        ref_price=chase[-1]["close_price"],
        follow_breakout=True,
    )
    assert missed.ready is False, missed
    assert missed.status == "missed_break", missed
    _ok("midline C3 follows a fresh break; blowoff/extended chase blocked; BRAIN C3 still waits")


def test_rsi_entry_timing() -> None:
    from app.services.entry_timing import long_rsi_entry_ok, short_exhaustion_rsi_ok

    hot = [100.0 + i * 0.6 for i in range(40)]
    ok, why = long_rsi_entry_ok(hot, [])
    assert ok is False and "rsi_still_hot" in why, (ok, why)

    cooled = list(hot)
    last = cooled[-1]
    for i in range(1, 10):
        cooled.append(last - i * 0.9)
    ok2, why2 = long_rsi_entry_ok(cooled, [])
    assert ok2 is True, (ok2, why2)

    falling = [80.0 - i * 0.8 for i in range(40)]
    sok, swhy = short_exhaustion_rsi_ok(falling, [])
    assert sok is False and "rsi_already_oversold" in swhy, (sok, swhy)

    rising = [100.0 + i * 0.7 for i in range(40)]
    rok, rwhy = short_exhaustion_rsi_ok(rising, [])
    assert rok is False and "rsi_still_rising" in rwhy, (rok, rwhy)

    turned = list(rising)
    peak = turned[-1]
    for i in range(1, 6):
        turned.append(peak - i * 1.2)
    tok, twhy = short_exhaustion_rsi_ok(turned, ["rsi_15m_turn_down"])
    assert tok is True, (tok, twhy)
    _ok("15m RSI gates long-hot / short-rising timing")


def test_structure_swing_exit() -> None:
    from app.services.entry_timing import compute_structure_exit
    from app.services.structure_swing_exit import (
        check_structure_swing_exit,
        uses_structure_swing_hold,
    )

    assert uses_structure_swing_hold("brain_swing") is True
    assert uses_structure_swing_hold("midline_short") is True
    assert uses_structure_swing_hold("deepseek_explore") is False

    bars, peak = _pump_bars()
    bars.append({
        "open_price": peak - 0.02,
        "high_price": peak + 0.45,
        "low_price": peak - 0.15,
        "close_price": peak - 0.08,
        "volume": 900,
    })
    pb = {"signals": ["pump_spike", "exhaustion_up", "long_upper_wick", "volume_diverge_bear"]}
    px = bars[-1]["close_price"]
    sell = compute_structure_exit("LONG", bars, playbook_row=pb, ref_price=px)
    assert sell.ready is True, sell
    assert sell.status == "exhaustion_ready", sell
    assert check_structure_swing_exit(
        "LONG", bars, peak_pct=0.002, age_s=3600, ref_price=px, playbook_row=pb,
    ) is None
    assert check_structure_swing_exit(
        "LONG", bars, peak_pct=0.012, age_s=60, ref_price=px, playbook_row=pb,
    ) is None
    hit = check_structure_swing_exit(
        "LONG", bars, peak_pct=0.012, age_s=30 * 60, ref_price=px, playbook_row=pb,
    )
    assert hit and hit.startswith("structure_sell_high"), hit

    dump = []
    p = 100.0
    for _ in range(36):
        p -= 0.18
        dump.append({
            "open_price": p + 0.06,
            "high_price": p + 0.08,
            "low_price": p - 0.10,
            "close_price": p,
            "volume": 900,
        })
    tagged_low = min(b["low_price"] for b in dump[-8:])
    bounce_px = tagged_low * 1.0045
    dump.append({
        "open_price": tagged_low + 0.04,
        "high_price": bounce_px + 0.06,
        "low_price": tagged_low,
        "close_price": bounce_px,
        "volume": 800,
    })
    cover = compute_structure_exit(
        "SHORT", dump,
        playbook_row={"signals": ["rsi_15m_turn_up", "long_lower_wick"]},
        ref_price=bounce_px,
    )
    assert cover.ready is True, cover
    assert cover.status == "bounce_low_ready", cover
    orch = (ROOT / "app/services/brain_strategy_orchestrator.py").read_text(encoding="utf-8")
    assert "planned_close_time=None" in orch
    assert "take_profit_pct=None" in orch
    _ok("structure swing sells the high / covers the low; no 4-6h deadline")


def main() -> int:
    print("=== validate_brain_req ===\n")
    test_imports_and_config()
    test_wick()
    test_trend_helpers()
    test_winrate_forward()
    test_playbook_classify()
    test_playbook_impulse_c3()
    test_playbook_bounce_is_not_a1()
    test_playbook_real_pullback_still_a1()
    test_playbook_pullback_then_bounce_still_a1()
    test_playbook_failed_retest_is_short()
    test_directional_gate()
    test_brain_market_regime()
    test_tick_config()
    test_market_regime_page_uses_brain_gates()
    test_ds_auto_open_available()
    test_paper_limit_brain_force()
    test_executor_brain_expire()
    test_cross_service_safety_guards()
    test_brain_skip_open_advisor()
    test_brain_risk_params()
    test_scheduler_brain()
    test_orchestrator_syntax()
    test_entry_timing_pullback()
    test_entry_timing_exhaustion_short()
    test_entry_timing_c1_follow()
    test_entry_timing_a2_b2_wait_for_reject()
    test_entry_timing_c3_midline_follow()
    test_rsi_entry_timing()
    test_structure_swing_exit()
    print()
    if _fail_n:
        print(f"FAILED {_fail_n}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
