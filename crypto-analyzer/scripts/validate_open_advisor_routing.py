"""校验开仓顾问 source 路由."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.open_advisor_routing import (
    resolve_open_advisors,
    should_use_deepseek_hold_advisor,
    should_use_gemini_hold_advisor,
)


def main() -> int:
    cases = [
        ("gemini_explore", ("gemini",)),
        ("gemini_predict", ("gemini",)),
        ("gemini_reversal", ("gemini",)),
        ("gemini_pullback", ("gemini",)),
        ("deepseek_explore", ("deepseek",)),
        ("deepseek_predict", ("deepseek",)),
        ("deepseek_dump", ("deepseek",)),
        ("smart_trader", ("gemini", "deepseek")),
        ("s1_early_long", ("gemini", "deepseek")),
        ("s9_gemini_ai", ("gemini", "deepseek")),
        ("BTC_MOMENTUM", ("gemini", "deepseek")),
    ]
    failed = 0
    for source, expected in cases:
        got = resolve_open_advisors(source)
        if got != expected:
            print(f"FAIL {source}: got {got}, want {expected}")
            failed += 1
        else:
            print(f"OK   {source} -> {got}")
    hold_cases = [
        ("gemini_explore", True, False),
        ("deepseek_predict", False, True),
        ("smart_trader", True, False),
        ("s1_early_long", True, False),
    ]
    for source, gemini, deepseek in hold_cases:
        g = should_use_gemini_hold_advisor(source)
        d = should_use_deepseek_hold_advisor(source)
        if g != gemini or d != deepseek:
            print(f"FAIL hold {source}: gemini={g} deepseek={d}")
            failed += 1
        else:
            print(f"OK   hold {source}: gemini={g} deepseek={d}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
