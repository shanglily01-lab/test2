#!/usr/bin/env python3
"""REQ-SPOT 静态回归 — 无 API / 无 DB。"""
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


def test_eligibility() -> None:
    from app.services.spot_paper_mirror import (
        DEEPSEEK_LONG_SOURCES,
        SPOT_LIVE_QUOTE_USD,
        SPOT_MIRROR_PLAYBOOKS,
        SPOT_QUOTE_USD,
        is_spot_mirror_eligible,
        spot_quote_usd,
        spot_source_for,
    )

    assert SPOT_MIRROR_PLAYBOOKS == frozenset({"A1"})
    assert DEEPSEEK_LONG_SOURCES == frozenset({"deepseek_explore", "deepseek_predict"})
    assert SPOT_QUOTE_USD == 500.0
    assert SPOT_LIVE_QUOTE_USD == 500.0
    assert spot_quote_usd(live=False) == 500.0
    assert spot_quote_usd(live=True) == 500.0
    _ok("spot mirrors A1 + DeepSeek LONG; 500U paper and live")

    ok, _ = is_spot_mirror_eligible(source="brain_swing", side="LONG", playbook="A1")
    assert ok, "A1 LONG should mirror"
    ok, why = is_spot_mirror_eligible(source="brain_swing", side="LONG", playbook="C3")
    assert not ok, why
    ok, why = is_spot_mirror_eligible(source="brain_swing", side="SHORT", playbook="A1")
    assert not ok, why
    ok, why = is_spot_mirror_eligible(source="brain_swing", side="LONG", playbook="A2")
    assert not ok, why
    _ok("BRAIN: A1 LONG yes; C3/A2/SHORT no")

    ok, _ = is_spot_mirror_eligible(source="deepseek_explore", side="LONG", playbook=None)
    assert ok
    ok, _ = is_spot_mirror_eligible(source="deepseek_predict", side="LONG")
    assert ok
    ok, why = is_spot_mirror_eligible(source="deepseek_explore", side="SHORT")
    assert not ok, why
    ok, why = is_spot_mirror_eligible(source="midline_long", side="LONG", playbook="A1")
    assert not ok, why
    _ok("DeepSeek LONG yes; SHORT / midline no")

    assert spot_source_for("brain_swing") == "spot_brain"
    assert spot_source_for("deepseek_explore") == "spot_deepseek_explore"
    _ok("spot source names")


def test_not_in_live_sync() -> None:
    from app.services.spot_paper_mirror import SPOT_SOURCES
    from app.services.trading_gates import LIVE_SYNC_SOURCES

    assert SPOT_SOURCES.isdisjoint(LIVE_SYNC_SOURCES)
    assert "spot_brain" not in LIVE_SYNC_SOURCES
    _ok("spot_* excluded from LIVE_SYNC_SOURCES")


def test_dca_removed() -> None:
    src = (ROOT / "app" / "services" / "spot_trader_service.py").read_text(encoding="utf-8")
    assert "RSI" not in src
    assert "_scan_buy_opportunities" not in src
    assert "_sync_live_buy" not in src
    assert "spot_paper_mirror" in src
    _ok("old DCA scan/live-buy removed from spot_trader_service")


def test_fill_hook() -> None:
    src = (ROOT / "app" / "trading" / "futures_trading_engine.py").read_text(encoding="utf-8")
    assert "maybe_mirror_spot_from_paper_fill" in src
    _ok("fill_paper_limit_order hooks spot mirror")


def test_settings_default_off() -> None:
    api = ast.parse((ROOT / "app" / "api" / "system_settings_api.py").read_text(encoding="utf-8"))
    found = {}

    class V(ast.NodeVisitor):
        def visit_Dict(self, node: ast.Dict) -> None:
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value in (
                    "spot_trading_enabled",
                    "spot_live_enabled",
                    "spot_close_enabled",
                ):
                    if isinstance(v, ast.Constant) and isinstance(v.value, bool):
                        found[k.value] = v.value
            self.generic_visit(node)

    V().visit(api)
    if found.get("spot_trading_enabled") is not False:
        _fail(f"spot_trading_enabled default={found.get('spot_trading_enabled')}")
    else:
        _ok("settings API default spot_trading_enabled=False")
    if found.get("spot_live_enabled") is not False:
        _fail(f"spot_live_enabled default={found.get('spot_live_enabled')}")
    else:
        _ok("settings API default spot_live_enabled=False")
    if found.get("spot_close_enabled") is not False:
        _fail(f"spot_close_enabled default={found.get('spot_close_enabled')}")
    else:
        _ok("settings API default spot_close_enabled=False")


def test_engine_no_auto_live() -> None:
    mirror = (ROOT / "app" / "services" / "spot_paper_mirror.py").read_text(encoding="utf-8")
    assert "sync_spot_live_buy" in mirror
    live = (ROOT / "app" / "services" / "spot_live_sync.py").read_text(encoding="utf-8")
    assert "is_spot_live_enabled" in live
    assert "FROM spot_positions" not in live
    assert "status='open'" not in live
    gates = (ROOT / "app" / "services" / "trading_gates.py").read_text(encoding="utf-8")
    assert 'spot_live_enabled", False' in gates or "spot_live_enabled', False" in gates
    api = (ROOT / "app" / "api" / "system_settings_api.py").read_text(encoding="utf-8")
    assert "不回填历史仓" in api
    ui = (ROOT / "templates" / "system_settings.html").read_text(encoding="utf-8")
    assert "spotLiveToggle" in ui
    _ok("spot live is fill-instant; no historical backfill scan; UI toggle present")


def main() -> int:
    print("=== validate_spot_paper ===\n")
    test_eligibility()
    test_not_in_live_sync()
    test_dca_removed()
    test_fill_hook()
    test_settings_default_off()
    test_engine_no_auto_live()
    print()
    if _fail_n:
        print(f"FAILED {_fail_n}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
