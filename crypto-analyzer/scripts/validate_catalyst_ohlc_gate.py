#!/usr/bin/env python3
"""回归：conf≥0.75 + catalyst 真实 15m OHLC 方向闸门（无 API）。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.ai_explore_prompt import (
    EXPLORE_CONFIDENCE_THRESHOLD,
    PREDICT_CONFIDENCE_THRESHOLD,
    explore_catalyst_technical_ok,
)

FAILURES: list[str] = []


def _fail(msg: str) -> None:
    FAILURES.append(msg)
    print(f"FAIL: {msg}")


def _ok(msg: str) -> None:
    print(f"OK: {msg}")


def _good_text(side: str = "bullish") -> tuple[str, str]:
    if side == "bullish":
        return (
            "15m 16根偏多连阳放量突破，近4根回踩缩量不破，RSI(1h)=58",
            "15m 10阳6阴 放量",
        )
    return (
        "15m 16根偏空连阴放量下杀，近4根反弹缩量失败，RSI(1h)=42",
        "15m 10阴6阳 放量",
    )


def _bars(pattern: str) -> list[dict]:
    """pattern: G=绿/阳 R=红/阴；o=100, green close=101, red close=99."""
    out = []
    for ch in pattern:
        if ch == "G":
            out.append({"o": 100.0, "c": 101.0, "v": 1000.0})
        elif ch == "R":
            out.append({"o": 100.0, "c": 99.0, "v": 1000.0})
        else:
            out.append({"o": 100.0, "c": 100.0, "v": 500.0})
    return out


def test_threshold() -> None:
    if EXPLORE_CONFIDENCE_THRESHOLD != 0.75 or PREDICT_CONFIDENCE_THRESHOLD != 0.75:
        _fail(
            f"threshold explore={EXPLORE_CONFIDENCE_THRESHOLD} "
            f"predict={PREDICT_CONFIDENCE_THRESHOLD}, want 0.75"
        )
    else:
        _ok("conf threshold = 0.75")


def test_ohlc_rejects_against_long() -> None:
    cat, ds = _good_text("bullish")
    # 16 bars mostly red — wrong for LONG
    bars = _bars("R" * 12 + "G" * 4)
    sym = {"symbol": "FOO/USDT", "klines_15m": bars, "tech": {"rsi_14_1h": 55}}
    with patch(
        "app.services.ai_explore_prompt._load_15m_ohlc_bars",
        return_value=[],
    ):
        ok, reason = explore_catalyst_technical_ok(
            cat, ds, sym, category="bullish", side="LONG",
        )
    if ok:
        _fail(f"should reject against-LONG OHLC, got ok reason={reason!r}")
    elif "OHLC" not in reason:
        _fail(f"reject reason should mention OHLC, got {reason!r}")
    else:
        _ok(f"against-LONG rejected: {reason}")


def test_ohlc_accepts_aligned_long() -> None:
    cat, ds = _good_text("bullish")
    bars = _bars("G" * 11 + "R" * 5)  # for>against; recent ends with some R but for>=against on last 6?
    # last 6 of G*11+R*5 = GGG RRR? bars = 11G + 5R → last 6 = G RRRRR? indices: 10G then 5R = last6 is 1G+5R against
    # Need last 6 also for >= against: e.g. 10G + 2R + 4G
    bars = _bars("G" * 10 + "R" * 2 + "G" * 4)
    sym = {"symbol": "FOO/USDT", "klines_15m": bars, "tech": {"rsi_14_1h": 55}}
    with patch(
        "app.services.ai_explore_prompt._load_15m_ohlc_bars",
        return_value=[],
    ):
        ok, reason = explore_catalyst_technical_ok(
            cat, ds, sym, category="bullish", side="LONG",
        )
    if not ok:
        _fail(f"aligned LONG should pass, got {reason!r}")
    else:
        _ok("aligned LONG OHLC passed")


def test_ohlc_rejects_trail_against() -> None:
    cat, ds = _good_text("bullish")
    # overall green majority but last 3 all red → trail_against>=2 on recent
    bars = _bars("G" * 13 + "R" * 3)
    sym = {"symbol": "FOO/USDT", "klines_15m": bars, "tech": {"rsi_14_1h": 55}}
    with patch(
        "app.services.ai_explore_prompt._load_15m_ohlc_bars",
        return_value=[],
    ):
        ok, reason = explore_catalyst_technical_ok(
            cat, ds, sym, category="bullish", side="LONG",
        )
    if ok:
        _fail(f"trail against should reject, got ok")
    else:
        _ok(f"trail against rejected: {reason}")


def test_text_still_required() -> None:
    bars = _bars("G" * 16)
    sym = {"symbol": "FOO/USDT", "klines_15m": bars}
    with patch(
        "app.services.ai_explore_prompt._load_15m_ohlc_bars",
        return_value=[],
    ):
        ok, reason = explore_catalyst_technical_ok(
            "已经涨很多了", "", sym, category="bullish", side="LONG",
        )
    if ok:
        _fail("weak text-only should still fail")
    else:
        _ok(f"text gate still active: {reason}")


def main() -> int:
    print("=== validate_catalyst_ohlc_gate ===\n")
    test_threshold()
    test_ohlc_rejects_against_long()
    test_ohlc_accepts_aligned_long()
    test_ohlc_rejects_trail_against()
    test_text_still_required()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)}")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
