#!/usr/bin/env python3
"""DeepSeek 探索/预测不开单 — 快速诊断."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql
from dotenv import dotenv_values

from app.services.paper_open_gate import gate_simulated_open
from app.services.trading_gates import check_live_open_allowed
from app.utils.config_loader import get_db_config

env = dotenv_values(str(ROOT / ".env"))
cfg = get_db_config()
conn = pymysql.connect(
    **cfg, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    cur = conn.cursor()

    section("system_settings 开关")
    keys = [
        "deepseek_explore_enabled", "deepseek_predict_enabled",
        "paper_limit_entry_enabled", "live_trading_enabled",
        "blacklist_level3_enabled",
    ]
    for k in keys:
        cur.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key=%s", (k,),
        )
        row = cur.fetchone()
        print(f"  {k} = {row['setting_value'] if row else 'MISSING'}")

    section("最近 deepseek_explore_runs")
    cur.execute(
        """
        SELECT id, asof_utc, status, trades_opened, elapsed_s, triggered_by,
               LEFT(error_msg, 100) AS err
        FROM deepseek_explore_runs ORDER BY id DESC LIMIT 5
        """
    )
    for r in cur.fetchall():
        print(r)

    section("最近 deepseek_predict_runs (若表存在)")
    try:
        cur.execute(
            """
            SELECT id, asof_utc, status, trades_opened, elapsed_s, triggered_by,
                   LEFT(error_msg, 100) AS err
            FROM deepseek_predict_runs ORDER BY id DESC LIMIT 5
            """
        )
        for r in cur.fetchall():
            print(r)
    except Exception as e:
        print(f"  (skip) {e}")

    section("探索 verdict 跳过原因 (最近一轮)")
    cur.execute("SELECT id FROM deepseek_explore_runs ORDER BY id DESC LIMIT 1")
    run = cur.fetchone()
    if run:
        cur.execute(
            """
            SELECT action_taken, COUNT(*) c,
                   GROUP_CONCAT(DISTINCT LEFT(skip_reason, 80) SEPARATOR ' | ') reasons
            FROM deepseek_explore_verdicts
            WHERE run_id=%s
            GROUP BY action_taken ORDER BY c DESC
            """,
            (run["id"],),
        )
        for r in cur.fetchall():
            print(r)

    section("预测 verdict 跳过 (最近一轮)")
    try:
        cur.execute("SELECT id FROM deepseek_predict_runs ORDER BY id DESC LIMIT 1")
        pr = cur.fetchone()
        if pr:
            cur.execute(
                """
                SELECT action_taken, COUNT(*) c,
                       GROUP_CONCAT(DISTINCT LEFT(skip_reason, 80) SEPARATOR ' | ') reasons
                FROM deepseek_predict_verdicts
                WHERE run_id=%s
                GROUP BY action_taken ORDER BY c DESC
                """,
                (pr["id"],),
            )
            for r in cur.fetchall():
                print(r)
    except Exception as e:
        print(f"  (skip) {e}")

    section("最近 deepseek 模拟开仓")
    since = datetime.utcnow() - timedelta(hours=48)
    cur.execute(
        """
        SELECT id, symbol, source, status, open_time, close_time, realized_pnl
        FROM futures_positions
        WHERE account_id=2 AND source IN ('deepseek_explore','deepseek_predict')
          AND open_time >= %s
        ORDER BY open_time DESC LIMIT 15
        """,
        (since,),
    )
    rows = cur.fetchall()
    print(f"  48h 内 {len(rows)} 笔")
    for r in rows:
        print(f"  {r}")

    section("最近 futures_orders (deepseek)")
    cur.execute(
        """
        SELECT id, symbol, side, status, order_source, created_at, notes
        FROM futures_orders
        WHERE order_source IN ('deepseek_explore','deepseek_predict')
          AND created_at >= %s
        ORDER BY id DESC LIMIT 10
        """,
        (since,),
    )
    for r in cur.fetchall():
        print(r)

    section("gate 抽样 (BTC + 最近 verdict symbol)")
    test_syms = ["BTC/USDT"]
    if run:
        cur.execute(
            """
            SELECT symbol FROM deepseek_explore_verdicts
            WHERE run_id=%s AND action_taken='opened' LIMIT 1
            """,
            (run["id"],),
        )
        o = cur.fetchone()
        if not o:
            cur.execute(
                """
                SELECT symbol FROM deepseek_explore_verdicts
                WHERE run_id=%s AND action_taken LIKE 'skipped%%'
                ORDER BY id DESC LIMIT 1
                """,
                (run["id"],),
            )
            o = cur.fetchone()
        if o:
            test_syms.append(o["symbol"])

    for src in ("deepseek_explore", "deepseek_predict"):
        for sym in test_syms:
            allowed, reason = gate_simulated_open(
                sym, "LONG", 100.0, src, "diag", leverage=5, conn=conn,
            )
            print(f"  gate_simulated_open {src} {sym} LONG: allowed={allowed} reason={reason!r}")

    section("deepseek_predict_next_due")
    cur.execute(
        "SELECT setting_key, setting_value FROM system_settings "
        "WHERE setting_key LIKE 'deepseek%due%' OR setting_key LIKE 'deepseek%enabled%'"
    )
    for r in cur.fetchall():
        print(r)

    conn.close()
    print("\nDONE")


if __name__ == "__main__":
    main()
