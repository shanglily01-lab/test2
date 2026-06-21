#!/usr/bin/env python3
"""回归：限价触价用 ticker（非 mark），与 batch UI 一致。"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"OK: {msg}")


def main() -> None:
    fp = ROOT / "app" / "utils" / "futures_price.py"
    ex = ROOT / "app" / "services" / "futures_limit_order_executor.py"
    src_fp = fp.read_text(encoding="utf-8")
    src_ex = ex.read_text(encoding="utf-8")
    ast.parse(src_fp)
    ast.parse(src_ex)

    if "def get_futures_limit_trigger_price" not in src_fp:
        fail("missing get_futures_limit_trigger_price")
    trigger_block = src_fp.split("def get_futures_limit_trigger_price", 1)[1].split("def _rest_futures_mark_price", 1)[0]
    if "get_trade_price_sync" in trigger_block:
        fail("trigger price must not use get_trade_price_sync (mark-first)")
    if "get_full_ticker_map" not in trigger_block:
        fail("trigger price must use get_full_ticker_map")
    if "get_futures_limit_trigger_price" not in src_ex:
        fail("executor must call get_futures_limit_trigger_price")
    if "get_futures_trade_price" in src_ex:
        fail("executor still uses get_futures_trade_price (mark)")
    if "_recover_stale_filling_orders" not in src_ex:
        fail("executor missing FILLING recovery")
    ok("limit trigger price = ticker; executor wired; FILLING recovery present")
    print("\nvalidate_limit_trigger_price: PASS")


if __name__ == "__main__":
    main()
