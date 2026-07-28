#!/usr/bin/env python3
"""Midline strategy win-rate probe (paper account_id=2)."""
from __future__ import annotations

import sys
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.utils.config_loader import get_db_config

SRCS = (
    "midline_long",
    "midline_short",
    "gemini_midline_long",
    "gemini_midline_short",
    "deepseek_midline_long",
    "deepseek_midline_short",
)
V2 = ("midline_long", "midline_short")


def main() -> None:
    conn = pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    cur = conn.cursor()

    print("=== open now ===")
    cur.execute(
        """
        SELECT source, position_side, COUNT(*) AS n,
               ROUND(SUM(COALESCE(unrealized_pnl, 0)), 2) AS upnl
        FROM futures_positions
        WHERE account_id=2 AND LOWER(status)='open'
          AND source IN %s
        GROUP BY source, position_side
        ORDER BY n DESC
        """,
        (SRCS,),
    )
    for r in cur.fetchall():
        print(r)

    windows = [
        ("all", "1=1"),
        ("7d", "close_time >= UTC_TIMESTAMP() - INTERVAL 7 DAY"),
        ("since_v2", "close_time >= '2026-07-24'"),
        ("since_loosen", "close_time >= '2026-07-27'"),
    ]
    print("=== closed by source ===")
    for label, cond in windows:
        cur.execute(
            f"""
            SELECT source, COUNT(*) AS n,
                   SUM(CASE WHEN COALESCE(realized_pnl,0)>0 THEN 1 ELSE 0 END) AS wins,
                   ROUND(SUM(CASE WHEN COALESCE(realized_pnl,0)>0 THEN 1 ELSE 0 END)
                         / COUNT(*) * 100, 1) AS win_pct,
                   ROUND(SUM(COALESCE(realized_pnl,0)), 2) AS pnl,
                   ROUND(AVG(COALESCE(realized_pnl,0)), 2) AS avg_pnl
            FROM futures_positions
            WHERE account_id=2 AND status='closed'
              AND source IN %s AND {cond}
            GROUP BY source
            ORDER BY n DESC
            """,
            (SRCS,),
        )
        print("---", label)
        rows = cur.fetchall()
        if not rows:
            print("(none)")
        for r in rows:
            print(r)

    print("=== v2 by position_side (since Jul24) ===")
    cur.execute(
        """
        SELECT source, position_side, COUNT(*) AS n,
               SUM(CASE WHEN COALESCE(realized_pnl,0)>0 THEN 1 ELSE 0 END) AS wins,
               ROUND(SUM(CASE WHEN COALESCE(realized_pnl,0)>0 THEN 1 ELSE 0 END)
                     / COUNT(*) * 100, 1) AS win_pct,
               ROUND(SUM(COALESCE(realized_pnl,0)), 2) AS pnl
        FROM futures_positions
        WHERE account_id=2 AND status='closed'
          AND source IN %s
          AND close_time >= '2026-07-24'
        GROUP BY source, position_side
        """,
        (V2,),
    )
    for r in cur.fetchall():
        print(r)

    print("=== hold buckets (v2 since Jul24) ===")
    cur.execute(
        """
        SELECT
          CASE
            WHEN TIMESTAMPDIFF(MINUTE, open_time, close_time) < 30 THEN '<30m'
            WHEN TIMESTAMPDIFF(MINUTE, open_time, close_time) < 120 THEN '30m-2h'
            WHEN TIMESTAMPDIFF(MINUTE, open_time, close_time) < 240 THEN '2-4h'
            WHEN TIMESTAMPDIFF(MINUTE, open_time, close_time) < 480 THEN '4-8h'
            ELSE '>=8h'
          END AS bucket,
          COUNT(*) AS n,
          SUM(CASE WHEN COALESCE(realized_pnl,0)>0 THEN 1 ELSE 0 END) AS wins,
          ROUND(SUM(COALESCE(realized_pnl,0)), 2) AS pnl
        FROM futures_positions
        WHERE account_id=2 AND status='closed'
          AND source IN %s
          AND close_time >= '2026-07-24'
        GROUP BY bucket
        ORDER BY FIELD(bucket, '<30m', '30m-2h', '2-4h', '4-8h', '>=8h')
        """,
        (V2,),
    )
    for r in cur.fetchall():
        print(r)

    print("=== top notes (v2 since Jul24) ===")
    cur.execute(
        """
        SELECT LEFT(COALESCE(notes, ''), 90) AS reason, COUNT(*) AS n,
               ROUND(SUM(COALESCE(realized_pnl,0)), 2) AS pnl,
               ROUND(AVG(COALESCE(realized_pnl,0)), 2) AS avg_pnl
        FROM futures_positions
        WHERE account_id=2 AND status='closed'
          AND source IN %s
          AND close_time >= '2026-07-24'
        GROUP BY LEFT(COALESCE(notes, ''), 90)
        ORDER BY n DESC
        LIMIT 15
        """,
        (V2,),
    )
    for r in cur.fetchall():
        print(r)

    print("=== worst 12 ===")
    cur.execute(
        """
        SELECT symbol, source, position_side, ROUND(realized_pnl, 2) AS pnl,
               TIMESTAMPDIFF(MINUTE, open_time, close_time) AS hold_m,
               LEFT(COALESCE(notes, ''), 70) AS notes, close_time
        FROM futures_positions
        WHERE account_id=2 AND status='closed'
          AND source IN %s
          AND close_time >= '2026-07-24'
        ORDER BY realized_pnl ASC
        LIMIT 12
        """,
        (V2,),
    )
    for r in cur.fetchall():
        print(r)

    print("=== best 8 ===")
    cur.execute(
        """
        SELECT symbol, source, position_side, ROUND(realized_pnl, 2) AS pnl,
               TIMESTAMPDIFF(MINUTE, open_time, close_time) AS hold_m,
               LEFT(COALESCE(notes, ''), 70) AS notes
        FROM futures_positions
        WHERE account_id=2 AND status='closed'
          AND source IN %s
          AND close_time >= '2026-07-24'
        ORDER BY realized_pnl DESC
        LIMIT 8
        """,
        (V2,),
    )
    for r in cur.fetchall():
        print(r)

    conn.close()


if __name__ == "__main__":
    main()
