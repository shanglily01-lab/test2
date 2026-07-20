#!/usr/bin/env python3
"""回归：模拟盘限价单超时 system_settings (放弃 vs 转市价)，无 API/无 DB 写入。"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def _fail(msg: str) -> None:
    FAILURES.append(msg)
    print(f"FAIL: {msg}")


def _ok(msg: str) -> None:
    print(f"OK: {msg}")


def test_timeout_action_getter() -> None:
    from app.services.paper_limit_entry import (
        DEFAULT_PAPER_LIMIT_TIMEOUT_ACTION,
        PAPER_LIMIT_TIMEOUT_ACTION_CONVERT_MARKET,
        PAPER_LIMIT_TIMEOUT_ACTION_EXPIRE,
        get_paper_limit_timeout_action,
    )

    if DEFAULT_PAPER_LIMIT_TIMEOUT_ACTION != PAPER_LIMIT_TIMEOUT_ACTION_CONVERT_MARKET:
        _fail("默认应为 convert_market")
        return

    cases = [
        ("expire", PAPER_LIMIT_TIMEOUT_ACTION_EXPIRE),
        ("", PAPER_LIMIT_TIMEOUT_ACTION_CONVERT_MARKET),
        ("convert_market", PAPER_LIMIT_TIMEOUT_ACTION_CONVERT_MARKET),
        ("market", PAPER_LIMIT_TIMEOUT_ACTION_CONVERT_MARKET),
        ("convert", PAPER_LIMIT_TIMEOUT_ACTION_CONVERT_MARKET),
        ("EXPIRE", PAPER_LIMIT_TIMEOUT_ACTION_EXPIRE),
    ]
    with patch("app.services.data_cache_service.get_setting") as mock_get:
        for raw, want in cases:
            mock_get.return_value = raw
            got = get_paper_limit_timeout_action()
            if got != want:
                _fail(f"get_paper_limit_timeout_action({raw!r}) = {got}, want {want}")
            else:
                _ok(f"timeout_action {raw!r} -> {want}")


def test_executor_timeout_branches() -> None:
    from app.services import futures_limit_order_executor as mod
    from app.services.paper_limit_entry import (
        PAPER_LIMIT_TIMEOUT_ACTION_CONVERT_MARKET,
        PAPER_LIMIT_TIMEOUT_ACTION_EXPIRE,
    )

    src = Path(mod.__file__).read_text(encoding="utf-8")
    if "get_paper_limit_timeout_action" not in src:
        _fail("futures_limit_order_executor 未读取 timeout_action")
    elif "fill_paper_limit_order" not in src or "at_market=True" not in src:
        _fail("futures_limit_order_executor 未实现转市价路径")
    elif "超时放弃" not in src:
        _fail("futures_limit_order_executor 未保留放弃路径")
    else:
        _ok("执行器含放弃/转市价双分支")

    order = {
        "order_id": "TEST-ORDER",
        "symbol": "BTC/USDT",
        "side": "OPEN_LONG",
        "price": "100",
        "notes": "{}",
        "elapsed_seconds": 31 * 60,
    }
    engine = MagicMock()
    engine.fill_paper_limit_order.return_value = {"success": True, "position_id": 1}
    executor = mod.FuturesLimitOrderExecutor({}, engine)
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch.object(executor, "_connect", return_value=conn):
        with patch.object(
            executor,
            "check_and_execute_limit_orders",
            wraps=lambda: None,
        ):
            pass

    # 直接测超时分支逻辑：mock 查询返回一单
    with patch.object(executor, "_connect", return_value=conn):
        with patch(
            "app.services.futures_limit_order_executor.get_paper_limit_timeout_action",
            return_value=PAPER_LIMIT_TIMEOUT_ACTION_CONVERT_MARKET,
        ):
            cursor.fetchall.return_value = [dict(order, elapsed_seconds=31 * 60)]
            executor.check_and_execute_limit_orders()
            if not engine.fill_paper_limit_order.called:
                _fail("convert_market 未调用 fill_paper_limit_order(at_market=True)")
            else:
                _, kwargs = engine.fill_paper_limit_order.call_args
                if not kwargs.get("at_market"):
                    _fail("转市价未传 at_market=True")
                else:
                    _ok("convert_market 调用 fill_paper_limit_order(at_market=True)")

        engine.reset_mock()
        with patch(
            "app.services.futures_limit_order_executor.get_paper_limit_timeout_action",
            return_value=PAPER_LIMIT_TIMEOUT_ACTION_EXPIRE,
        ):
            cursor.fetchall.return_value = [dict(order, elapsed_seconds=31 * 60)]
            executor.check_and_execute_limit_orders()
            if engine.fill_paper_limit_order.called:
                _fail("expire 不应转市价")
            else:
                _ok("expire 超时仅取消，不转市价")


def test_api_and_ui() -> None:
    api = (ROOT / "app" / "api" / "system_settings_api.py").read_text(encoding="utf-8")
    if "paper_limit_timeout_action" not in api:
        _fail("system_settings_api 缺少 paper_limit_timeout_action")
    elif "'convert_market'" not in api:
        _fail("API 未校验 convert_market")
    else:
        _ok("system_settings_api 含 timeout_action")

    html = (ROOT / "templates" / "system_settings.html").read_text(encoding="utf-8")
    if "paperLimitTimeoutSelect" not in html:
        _fail("设置页缺少限价超时下拉")
    elif 'value="convert_market"' not in html:
        _fail("设置页缺少转市价选项")
    else:
        _ok("设置页 UI 已添加")


def test_market_fill_recalc_sl_tp() -> None:
    """超时转市价须按实际入场价重算 SL/TP，且 monitor 校验方向。"""
    engine_src = (ROOT / "app/trading/futures_trading_engine.py").read_text(encoding="utf-8")
    exit_src = (ROOT / "app/services/smart_exit_optimizer.py").read_text(encoding="utf-8")
    if "_recalc_sl_tp_for_market_fill" not in engine_src:
        _fail("futures_trading_engine 缺少市价成交 SL/TP 重算")
    elif "at_market" not in engine_src or "limit_px" not in engine_src:
        _fail("fill_paper_limit_order 未在 at_market 路径重算 SL/TP")
    elif "take_profit_price > entry_price" not in exit_src:
        _fail("smart_exit_optimizer 未校验 LONG 止盈价须高于入场价")
    else:
        _ok("市价转单重算 SL/TP + monitor 方向校验")


def test_fill_claim_prevents_double_open() -> None:
    """fill_paper_limit_order 必须先 PENDING→FILLING 原子认领（防双执行器重复开仓）。"""
    src = (ROOT / "app/trading/futures_trading_engine.py").read_text(encoding="utf-8")
    need = [
        "status='FILLING'",
        "status='PENDING' AND order_type='LIMIT'",
        "_release_paper_limit_fill_claim",
        "WHERE order_id=%s AND status='FILLING'",
    ]
    missing = [s for s in need if s not in src]
    if missing:
        _fail(f"fill_paper_limit_order 缺少认领逻辑: {missing}")
    else:
        _ok("fill_paper_limit_order 含 PENDING→FILLING 原子认领")


def test_syntax() -> None:
    paths = [
        "app/services/paper_limit_entry.py",
        "app/services/futures_limit_order_executor.py",
        "app/trading/futures_trading_engine.py",
        "app/services/smart_exit_optimizer.py",
        "app/api/system_settings_api.py",
        "scripts/validate_paper_limit_timeout_action.py",
    ]
    for rel in paths:
        try:
            ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            _ok(f"语法 {rel}")
        except SyntaxError as e:
            _fail(f"语法错误 {rel}: {e}")


def main() -> int:
    print("=== validate_paper_limit_timeout_action ===\n")
    test_timeout_action_getter()
    test_executor_timeout_branches()
    test_market_fill_recalc_sl_tp()
    test_fill_claim_prevents_double_open()
    test_api_and_ui()
    test_syntax()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} issue(s)")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
