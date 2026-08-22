#!/usr/bin/env python3
"""合约自选 / 手动下单静态回归。"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _ok(msg: str) -> None:
    print(f"  OK  {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL {msg}")
    raise SystemExit(1)


def test_config_and_sync() -> None:
    from app.services.watchlist_config import (
        WATCHLIST_SOURCE,
        is_watchlist_source,
        WATCHLIST_PRICE_WS_STREAM,
        WATCHLIST_BOOK_REFRESH_SECONDS,
    )
    from app.services.trading_gates import LIVE_SYNC_SOURCES, should_sync_live_for_source
    from inspect import signature
    from app.services.paper_limit_entry import create_paper_limit_order
    from app.services.smart_exit_optimizer import _is_smart_exit_excluded_source

    assert WATCHLIST_SOURCE == "manual_watchlist"
    assert is_watchlist_source("manual_watchlist")
    assert not is_watchlist_source("brain_swing")
    assert WATCHLIST_PRICE_WS_STREAM == "miniTicker"
    assert WATCHLIST_BOOK_REFRESH_SECONDS == 30
    assert WATCHLIST_SOURCE in LIVE_SYNC_SOURCES
    assert should_sync_live_for_source(WATCHLIST_SOURCE)
    params = signature(create_paper_limit_order).parameters
    assert "explicit_limit_price" in params
    assert "min_fill_age_sec" in params
    assert _is_smart_exit_excluded_source("manual_watchlist")
    _ok("source/LIVE_SYNC/explicit limit/SmartExit exclude")


def test_gate_skip_advisor() -> None:
    src = (ROOT / "app/services/paper_open_gate.py").read_text(encoding="utf-8")
    if "watchlist_skip_advisor" not in src:
        _fail("paper_open_gate 未跳过自选开仓顾问")
    adv = (ROOT / "app/services/deepseek_position_advisor.py").read_text(encoding="utf-8")
    if "is_watchlist_source" not in adv:
        _fail("持仓顾问未把自选 sell 设为 suggest-only")
    _ok("open skip advisor; hold suggest-only")


def test_ui_and_routes() -> None:
    sidebar = (ROOT / "templates/partials/desktop_sidebar.html").read_text(encoding="utf-8")
    if 'href="/watchlist"' not in sidebar or "我的自选" not in sidebar:
        _fail("侧栏缺少我的自选")
    page = ROOT / "templates/watchlist.html"
    js = ROOT / "static/js/watchlist_page.js"
    api = ROOT / "app/api/watchlist_api.py"
    main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    if not page.exists() or not js.exists() or not api.exists():
        _fail("自选页/JS/API 文件缺失")
    if '@app.get("/watchlist")' not in main:
        _fail("main.py 未注册 /watchlist")
    if "watchlist_router" not in main:
        _fail("main.py 未 include watchlist router")
    js_txt = js.read_text(encoding="utf-8")
    if "fstream.binance.com" not in js_txt or "WebSocket" not in js_txt:
        _fail("前端未直连币安合约 WS 刷价")
    if "@miniTicker" not in js_txt:
        _fail("前端未订阅 miniTicker")
    if "/prices" not in js_txt or "STALE_MS" not in js_txt:
        _fail("前端未做 WS 静默后的服务端 1s 补价")
    if "/prices" not in api.read_text(encoding="utf-8"):
        _fail("API 未提供 /api/watchlist/prices")
    page_txt = page.read_text(encoding="utf-8")
    if "5 分钟刷新" in page_txt:
        _fail("自选页仍写 5 分钟刷价")
    if "DELETE" not in js_txt or "data-cancel" not in js_txt:
        _fail("前端未提供限价撤单")
    api_txt = api.read_text(encoding="utf-8")
    if "/orders/{order_id}" not in api_txt or "cancel_watchlist_order" not in api_txt:
        _fail("API 未提供自选撤单")
    orders = (ROOT / "app/services/watchlist_orders.py").read_text(encoding="utf-8")
    if "def cancel_watchlist_order" not in orders:
        _fail("watchlist_orders 缺少 cancel")
    _ok("menu + page + Binance futures WS prices + cancel")


def test_syntax() -> None:
    files = [
        "app/services/watchlist_config.py",
        "app/services/watchlist_store.py",
        "app/services/watchlist_orders.py",
        "app/api/watchlist_api.py",
        "app/utils/futures_price.py",
        "app/api/technical_signals_api.py",
        "app/services/paper_limit_entry.py",
        "app/services/paper_open_gate.py",
    ]
    for rel in files:
        ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        _ok(f"syntax {rel}")


if __name__ == "__main__":
    print("=== validate_watchlist ===")
    test_config_and_sync()
    test_gate_skip_advisor()
    test_ui_and_routes()
    test_syntax()
    print("ALL PASSED")
