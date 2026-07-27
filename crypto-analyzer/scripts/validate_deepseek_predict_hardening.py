#!/usr/bin/env python3
"""无 API 回归：DeepSeek 预测防卡死 + L0/L1 选币 + soft-sl 匹配。"""
from __future__ import annotations

import ast
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def test_source_guards() -> None:
    path = ROOT / "app" / "services" / "deepseek_predictor.py"
    src = path.read_text(encoding="utf-8")
    for needle in (
        "PREDICT_LOCK_STALE_S",
        "_symbol_data_from_cache",
        "_try_enter_predict_round",
        "无kline回退",
        "read_timeout=45",
        "select_all_symbols_from_pool",
        "load_l0_l1_scan_symbols",
        "L0/L1 候选池",
    ):
        if needle not in src:
            _fail(f"missing guard marker: {needle}")
    get_sym_body = src.split("def _get_predict_symbols")[1].split("def _")[0]
    if "select_llm_symbols_from_pool" in get_sym_body:
        _fail("_get_predict_symbols must not use technical TOP truncate")
    if "top_performing_symbols" in get_sym_body:
        _fail("_get_predict_symbols must not fall back to TOP table (unrated noise)")
    ast.parse(src)
    print("OK source_guards")


def test_explore_l0_l1() -> None:
    path = ROOT / "app" / "services" / "deepseek_explore_worker.py"
    src = path.read_text(encoding="utf-8")
    for needle in (
        "_build_l0_l1_universe_from_cache",
        "_filter_universe_to_l0_l1",
        "load_l0_l1_scan_symbols",
        "L0/L1",
    ):
        if needle not in src:
            _fail(f"explore missing: {needle}")
    if "_build_full_universe_from_cache" in src:
        _fail("explore must not expand to full-market universe")
    ast.parse(src)
    print("OK explore_l0_l1")


def test_soft_lock_stale_reclaim() -> None:
    from app.services import deepseek_predictor as dp

    with dp._predict_guard:
        dp._predict_run_gen = 1
        dp._predict_active_gen = 1
        dp._predict_running_since = time.time() - (dp.PREDICT_LOCK_STALE_S + 5)

    gen = dp._try_enter_predict_round("test")
    if gen is None:
        _fail("stale lock should be reclaimable")
    gen2 = dp._try_enter_predict_round("test2")
    if gen2 is not None:
        _fail("fresh lock must block second enter")
    dp._leave_predict_round(gen)
    gen3 = dp._try_enter_predict_round("test3")
    if gen3 is None:
        _fail("after leave should allow enter")
    dp._leave_predict_round(gen3)
    print("OK soft_lock_stale_reclaim")


def test_universe_helpers() -> None:
    from app.services.deepseek_predictor import (
        PREDICT_CANDIDATE_LIMIT,
        PREDICT_LOCK_STALE_S,
        _filter_predict_symbols,
    )

    if PREDICT_CANDIDATE_LIMIT < 200:
        _fail("scan limit too small")
    if PREDICT_LOCK_STALE_S < 20 * 60:
        _fail("soft lock too short for multi-batch LLM")
    many = [f"S{i}/USDT" for i in range(600)]
    out = _filter_predict_symbols(many, PREDICT_CANDIDATE_LIMIT)
    if len(out) > PREDICT_CANDIDATE_LIMIT:
        _fail(f"filter exceeded limit: {len(out)}")
    print("OK universe_helpers")


def test_deepseek_soft_sl_matching() -> None:
    from app.services.position_sl_tp_monitor import (
        _DEEPSEEK_SOFT_SL_GRACE_MIN,
        _DEEPSEEK_SOFT_SL_NO_FOLLOW_LOSS_PCT,
        _DEEPSEEK_SOFT_SL_NO_FOLLOW_MIN_AGE,
        _check_ai_soft_stop,
    )

    # 旧参数会杀：age=20m, peak=0, pnl=-1.2% → DeepSeek 应仍保护
    early = _check_ai_soft_stop(
        -0.012, 0.0, 20 * 60, leverage=5, source="deepseek_predict",
    )
    if early is not None:
        _fail(f"DeepSeek soft-sl must grace past 20m/-1.2%: got {early}")

    # Gemini 仍可用旧 no_follow
    gem = _check_ai_soft_stop(
        -0.012, 0.0, 20 * 60, leverage=5, source="gemini_predict",
    )
    if gem is None or "no_follow_through" not in gem:
        _fail(f"Gemini soft-sl should still fire no_follow: got {gem}")

    # DeepSeek：满 grace 但仍未到 no_follow 最短年龄 + 损失不够深
    mid = _check_ai_soft_stop(
        -0.015, 0.0, (_DEEPSEEK_SOFT_SL_GRACE_MIN + 1) * 60,
        leverage=5, source="deepseek_explore",
    )
    if mid is not None:
        _fail(f"DeepSeek must not no_follow before min age/deeper loss: {mid}")

    # DeepSeek：年龄够 + 更深亏损才 no_follow
    late = _check_ai_soft_stop(
        _DEEPSEEK_SOFT_SL_NO_FOLLOW_LOSS_PCT,
        0.0,
        _DEEPSEEK_SOFT_SL_NO_FOLLOW_MIN_AGE * 60,
        leverage=5,
        source="deepseek_explore",
    )
    if late is None or "no_follow_through" not in late:
        _fail(f"DeepSeek soft-sl should fire deep no_follow: got {late}")
    print("OK deepseek_soft_sl_matching")


def main() -> None:
    test_source_guards()
    test_explore_l0_l1()
    test_soft_lock_stale_reclaim()
    test_universe_helpers()
    test_deepseek_soft_sl_matching()
    print("ALL PASS")


if __name__ == "__main__":
    main()
