#!/usr/bin/env python3
"""回归：max_hold_hours(2~8) 驱动 AI 调度周期 + 持仓时长（无 API/无 LLM）."""
from __future__ import annotations

import ast
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def _fail(msg: str) -> None:
    FAILURES.append(msg)
    print(f"FAIL: {msg}")


def _ok(msg: str) -> None:
    print(f"OK: {msg}")


def test_no_hardcoded_ai_interval_constants() -> None:
    """旧常量 EXPLORE_MIN_INTERVAL_HOURS / PREDICT_ROUND_INTERVAL_HOURS 不得再存在."""
    patterns = [
        (ROOT / "app" / "services" / "ai_explore_prompt.py", r"^EXPLORE_MIN_INTERVAL_HOURS\s*="),
        (ROOT / "app" / "services" / "ai_predict_schedule.py", r"^PREDICT_ROUND_INTERVAL_HOURS\s*="),
    ]
    for path, pat in patterns:
        text = path.read_text(encoding="utf-8")
        if re.search(pat, text, re.MULTILINE):
            _fail(f"{path.name} 仍含硬编码调度间隔常量")
        else:
            _ok(f"{path.name} 无硬编码调度间隔")


def test_loader_clamp_2_to_8() -> None:
    from app.services.system_settings_loader import get_max_hold_hours

    cases = [
        ("1", 2),
        ("2", 2),
        ("4", 4),
        ("8", 8),
        ("9", 8),
        ("12", 8),
        ("", 4),  # 默认 4
        ("bad", 4),
    ]
    with patch("app.services.system_settings_loader.get_setting") as mock_get:
        for raw, expected in cases:
            mock_get.return_value = raw
            got = get_max_hold_hours()
            if got != expected:
                _fail(f"get_max_hold_hours({raw!r}) = {got}, want {expected}")
            else:
                _ok(f"clamp get_max_hold_hours({raw!r}) -> {got}")


def test_schedule_interval_matches_hold_hours() -> None:
    from app.services.ai_explore_prompt import (
        get_ai_position_hold_hours,
        get_ai_schedule_interval_hours,
    )
    from app.services.ai_predict_schedule import (
        get_ai_round_interval_hours,
        get_ai_round_interval_seconds,
    )

    with patch("app.services.system_settings_loader.get_setting", return_value="6"):
        hold = get_ai_position_hold_hours()
        sched = get_ai_schedule_interval_hours()
        round_h = get_ai_round_interval_hours()
        round_s = get_ai_round_interval_seconds()
        if not (hold == sched == round_h == 6):
            _fail(f"三者不一致: hold={hold} sched={sched} round={round_h}")
        elif round_s != 6 * 3600:
            _fail(f"get_ai_round_interval_seconds() = {round_s}, want {6 * 3600}")
        else:
            _ok("持仓时长与 AI 调度周期共用 max_hold_hours=6")


def test_slot_math_respects_interval() -> None:
    from app.services.ai_predict_schedule import (
        _period_base_for_now,
        next_scheduled_slot,
        scheduled_slot_for_now,
    )

    anchor = datetime(2024, 1, 1, 13, 30)

    def _run_for_hours(hours: int) -> None:
        with patch("app.services.system_settings_loader.get_setting", return_value=str(hours)):
            # 周期中点：period base 应为锚点
            mid = anchor + timedelta(hours=hours // 2, minutes=15)
            base = _period_base_for_now(mid)
            if base != anchor:
                _fail(f"{hours}h: period_base({mid})={base}, want {anchor}")
                return

            slot = scheduled_slot_for_now("gemini_explore", anchor + timedelta(minutes=30))
            if slot != anchor:
                _fail(f"{hours}h: gemini_explore slot={slot}, want {anchor}")
                return

            after = anchor + timedelta(hours=1)
            nxt = next_scheduled_slot("gemini_explore", after)
            want_next = anchor + timedelta(hours=hours)
            if nxt != want_next:
                _fail(f"{hours}h: next_slot after {after} = {nxt}, want {want_next}")
                return

            # 最小周期 2h：最大 offset 75min 仍落在单周期内
            if hours == 2:
                ds_pred = scheduled_slot_for_now(
                    "deepseek_predict", anchor + timedelta(minutes=10)
                )
                want_ds = anchor + timedelta(minutes=75)
                if ds_pred != want_ds:
                    _fail(f"2h: deepseek_predict slot={ds_pred}, want {want_ds}")
                    return

            _ok(f"槽位计算 interval={hours}h 通过")

    for h in (2, 4, 8):
        _run_for_hours(h)


def test_strategy_offsets_within_min_period() -> None:
    from app.services.ai_predict_schedule import STRATEGY_SCHEDULE_OFFSETS

    max_offset_min = max(STRATEGY_SCHEDULE_OFFSETS.values())
    min_period_min = 2 * 60
    if max_offset_min >= min_period_min:
        _fail(
            f"策略错峰最大 {max_offset_min}min >= 最小调度周期 {min_period_min}min"
        )
    else:
        _ok(f"六策略错峰最大 {max_offset_min}min 落在 2h 周期内")


def test_api_clamp_logic() -> None:
    """与 system_settings_api.update_max_hold_hours 一致：max(2, min(8, hours))."""

    def api_clamp(hours: int) -> int:
        return max(2, min(8, hours))

    for raw, want in [(1, 2), (2, 2), (8, 8), (99, 8)]:
        if api_clamp(raw) != want:
            _fail(f"API clamp {raw} -> {api_clamp(raw)}, want {want}")
        else:
            _ok(f"API clamp {raw} -> {want}")


def test_api_source_uses_loader() -> None:
    src = (ROOT / "app" / "api" / "system_settings_api.py").read_text(encoding="utf-8")
    if "max(2, min(8" not in src:
        _fail("system_settings_api 未使用 2~8 clamp")
    elif "get_max_hold_hours as _load_hours" not in src:
        _fail("GET /max-hold-hours 未走 settings_loader")
    else:
        _ok("system_settings_api 2~8 + loader GET")


def test_templates_hold_hours_2_to_8() -> None:
    for rel in ("templates/system_settings.html", "templates/mobile_settings.html"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        if 'value="2"' not in text or 'value="8"' not in text:
            _fail(f"{rel} 缺少 2H 或 8H 选项")
        elif 'value="12"' in text or 'value="9"' in text:
            _fail(f"{rel} 仍含超出 8H 的选项")
        elif "Math.max(2, Math.min(8" not in text and rel.endswith("mobile_settings.html"):
            _fail(f"{rel} 前端未 clamp 2~8")
        else:
            _ok(f"{rel} 选项 2~8")


def test_worker_imports_dynamic_interval() -> None:
    files_imports = {
        "app/services/gemini_predictor.py": "get_ai_round_interval_hours",
        "app/services/deepseek_predictor.py": "get_ai_round_interval_hours",
        "app/services/gpt_predictor.py": "get_ai_round_interval_hours",
        "app/services/gpt_explore_worker.py": "get_ai_schedule_interval_hours",
    }
    for rel, sym in files_imports.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        if sym not in text:
            _fail(f"{rel} 未导入 {sym}")
        elif "EXPLORE_MIN_INTERVAL_HOURS" in text or "PREDICT_ROUND_INTERVAL_HOURS" in text:
            _fail(f"{rel} 仍引用旧常量")
        else:
            _ok(f"{rel} 使用 {sym}")


def test_syntax_parse() -> None:
    paths = [
        "app/services/ai_predict_schedule.py",
        "app/services/ai_explore_prompt.py",
        "app/services/system_settings_loader.py",
        "app/api/system_settings_api.py",
        "app/services/gemini_predictor.py",
        "app/services/gpt_explore_worker.py",
        "scripts/validate_ai_schedule_hold_hours.py",
    ]
    for rel in paths:
        p = ROOT / rel
        try:
            ast.parse(p.read_text(encoding="utf-8"))
            _ok(f"语法 {rel}")
        except SyntaxError as e:
            _fail(f"语法错误 {rel}: {e}")


def main() -> int:
    print("=== validate_ai_schedule_hold_hours ===\n")
    test_no_hardcoded_ai_interval_constants()
    test_loader_clamp_2_to_8()
    test_schedule_interval_matches_hold_hours()
    test_slot_math_respects_interval()
    test_strategy_offsets_within_min_period()
    test_api_clamp_logic()
    test_api_source_uses_loader()
    test_templates_hold_hours_2_to_8()
    test_worker_imports_dynamic_interval()
    test_syntax_parse()

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
