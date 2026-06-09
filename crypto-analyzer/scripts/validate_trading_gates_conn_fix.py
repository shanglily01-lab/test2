#!/usr/bin/env python3
"""回归：模拟开仓闸门 conn/cursor 传参 + candidate_pool 放行."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql

from app.services.paper_open_gate import gate_simulated_open
from app.services.trading_gates import (
    _as_cursor,
    check_simulated_symbol_allowed,
    get_symbol_rating_info,
)
from app.utils.config_loader import get_db_config


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"OK: {msg}")


def test_as_cursor() -> None:
    conn = pymysql.connect(
        **get_db_config(), charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor
    )
    try:
        cur = conn.cursor()
        if _as_cursor(conn) is None:
            _fail("_as_cursor(conn) returned None")
        if not hasattr(_as_cursor(conn), "execute"):
            _fail("_as_cursor(conn) has no execute")
        if _as_cursor(cur) is not cur:
            _fail("_as_cursor(cursor) should return same cursor")
        _ok("_as_cursor handles Connection and Cursor")
    finally:
        conn.close()


def test_rating_info_conn_matches_cursor() -> None:
    conn = pymysql.connect(
        **get_db_config(), charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor
    )
    try:
        cur = conn.cursor()
        for symbol in ("BTC/USDT", "ENA/USDT", "HYPE/USDT", "ACU/USDT"):
            via_cur = get_symbol_rating_info(symbol, cur)
            via_conn = get_symbol_rating_info(symbol, conn)
            if via_cur != via_conn:
                _fail(f"{symbol} rating mismatch cursor={via_cur} conn={via_conn}")
        _ok("get_symbol_rating_info(conn) matches cursor for sample symbols")
    finally:
        conn.close()


def test_failed_explore_symbols_from_db() -> None:
    since = datetime.utcnow() - timedelta(hours=24)
    conn = pymysql.connect(
        **get_db_config(), charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor
    )
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT v.symbol
            FROM gemini_explore_verdicts v
            JOIN gemini_explore_runs r ON r.id = v.run_id
            WHERE r.asof_utc >= %s
              AND v.skip_reason LIKE %s
            """,
            (since, "%INSERT%"),
        )
        symbols = [r["symbol"] for r in cur.fetchall()]
        if not symbols:
            print("WARN: no gemini_explore INSERT-fail symbols in 24h, skip DB replay")
            return

        l3_blocked = []
        would_open = []
        for symbol in symbols:
            allowed, reason = check_simulated_symbol_allowed(symbol, conn)
            if allowed:
                would_open.append(symbol)
            elif "3级" in reason:
                l3_blocked.append(symbol)
            else:
                _fail(f"{symbol} still blocked after fix: {reason}")

        if not would_open:
            _fail("no formerly-failed explore symbol passes gate after fix")

        _ok(
            f"24h explore INSERT-fail replay: {len(would_open)} pass, "
            f"{len(l3_blocked)} L3 blocked (expected), total={len(symbols)}"
        )
    finally:
        conn.close()


def test_gate_simulated_open_with_conn() -> None:
    """完整 paper_open_gate 路径：传 conn，顾问 mock 为放行."""
    conn = pymysql.connect(
        **get_db_config(), charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor
    )
    try:
        with patch(
            "app.services.gemini_position_advisor.GeminiPositionAdvisor.review_open",
            return_value=(True, "test_mock_approve"),
        ):
            allowed, reason = gate_simulated_open(
                "ENA/USDT",
                "LONG",
                1.0,
                "gemini_explore",
                catalyst="test catalyst",
                conn=conn,
            )
        if not allowed:
            _fail(f"gate_simulated_open(ENA, conn=conn) rejected: {reason}")
        _ok(f"gate_simulated_open passes symbol gate with conn (reason={reason})")
    finally:
        conn.close()


def test_l3_still_blocked() -> None:
    conn = pymysql.connect(
        **get_db_config(), charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor
    )
    try:
        allowed, reason = check_simulated_symbol_allowed("HYPE/USDT", conn)
        if allowed:
            _fail("HYPE/USDT (L3) should be blocked")
        if "3级" not in reason:
            _fail(f"HYPE block reason unexpected: {reason}")
        _ok("L3 symbol still blocked with conn")
    finally:
        conn.close()


def main() -> None:
    print("=== validate_trading_gates_conn_fix ===")
    test_as_cursor()
    test_rating_info_conn_matches_cursor()
    test_failed_explore_symbols_from_db()
    test_gate_simulated_open_with_conn()
    test_l3_still_blocked()
    print("ALL PASSED")


if __name__ == "__main__":
    main()
