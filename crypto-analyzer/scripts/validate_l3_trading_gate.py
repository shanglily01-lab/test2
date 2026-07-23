#!/usr/bin/env python3
"""校验 L3 / rating_locked 开仓闸门已恢复生效。"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def test_source_not_stubbed() -> None:
    src = (ROOT / "app/services/trading_gates.py").read_text(encoding="utf-8")
    # 空实现特征
    if "def is_symbol_blocked_level3" in src:
        body = src.split("def is_symbol_blocked_level3")[1].split("def ")[0]
        if "return False" in body and "check_symbol_trading_forbidden" not in body:
            _fail("is_symbol_blocked_level3 still stubbed to False")
    if "def load_blacklist_level3_symbols" in src:
        body = src.split("def load_blacklist_level3_symbols")[1].split("def ")[0]
        if "return set()" in body and "load_trading_forbidden_symbols" not in body:
            _fail("load_blacklist_level3_symbols still stubbed empty")
    ast.parse(src)
    print("OK source_not_stubbed")


def test_gate_logic() -> None:
    from app.services import trading_gates as tg

    with patch.object(
        tg, "get_symbol_rating_info", return_value=(3, False, False)
    ):
        if not tg.is_symbol_blocked_level3("FOO/USDT"):
            _fail("L3 should be blocked")
        ok, reason = tg.check_simulated_symbol_allowed("FOO/USDT")
        if ok:
            _fail(f"L3 simulated open must be denied, got {reason!r}")

    with patch.object(
        tg, "get_symbol_rating_info", return_value=(1, False, True)
    ):
        if not tg.is_symbol_blocked_level3("BAR/USDT"):
            _fail("locked symbol should be blocked")
        ok, reason = tg.check_simulated_symbol_allowed("BAR/USDT")
        if ok:
            _fail(f"locked simulated open must be denied, got {reason!r}")

    with patch.object(
        tg, "get_symbol_rating_info", return_value=(0, True, False)
    ):
        if tg.is_symbol_blocked_level3("BTC/USDT"):
            _fail("L0 should not be blocked")
        ok, _ = tg.check_simulated_symbol_allowed("BTC/USDT")
        if not ok:
            _fail("L0 simulated open must be allowed")

    print("OK gate_logic")


def test_config_has_no_l3_sample() -> None:
    text = (ROOT / "config.yaml").read_text(encoding="utf-8")
    # spot-check known L3 samples that were stripped
    for sym in ("VANA/USDT", "1000BONK/USDT", "ZRO/USDT"):
        if f"- {sym}" in text:
            _fail(f"config.yaml still contains forbidden sample {sym}")
    print("OK config_has_no_l3_sample")


def main() -> None:
    test_source_not_stubbed()
    test_gate_logic()
    test_config_has_no_l3_sample()
    print("ALL PASS")


if __name__ == "__main__":
    main()
