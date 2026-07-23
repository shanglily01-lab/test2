#!/usr/bin/env python3
"""无 API 回归：DeepSeek 预测防卡死 + 全量(排除L3) 选币。"""
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
        "load_trading_forbidden_symbols",
        "全量候选池(排除L3/锁定)",
    ):
        if needle not in src:
            _fail(f"missing guard marker: {needle}")
    get_sym_body = src.split("def _get_predict_symbols")[1].split("def _")[0]
    if "select_llm_symbols_from_pool" in get_sym_body:
        _fail("_get_predict_symbols must not use technical TOP truncate")
    ast.parse(src)
    print("OK source_guards")


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
        _fail("full-scan limit too small")
    if PREDICT_LOCK_STALE_S < 20 * 60:
        _fail("soft lock too short for multi-batch full scan")
    many = [f"S{i}/USDT" for i in range(600)]
    out = _filter_predict_symbols(many, PREDICT_CANDIDATE_LIMIT)
    if len(out) > PREDICT_CANDIDATE_LIMIT:
        _fail(f"filter exceeded limit: {len(out)}")
    print("OK universe_helpers")


def main() -> None:
    test_source_guards()
    test_soft_lock_stale_reclaim()
    test_universe_helpers()
    print("ALL PASS")


if __name__ == "__main__":
    main()
