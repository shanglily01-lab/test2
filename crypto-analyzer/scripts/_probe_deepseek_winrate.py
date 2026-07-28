#!/usr/bin/env python3
"""DeepSeek strategy win-rate probe (paper account_id=2)."""
from __future__ import annotations

import sys
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.utils.config_loader import get_db_config


SOURCES = (
    "deepseek_explore",
    "deepseek_predict",
    "deepseek_reversal",
    "deepseek_pullback",
    "deepseek_rebound",
    "deepseek_chase",
    "deepseek_dump",
)


def main() -> None:
    conn = pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    cur = conn.cursor()

    print("=== by source (closed last 7d UTC) ===")
    cur.execute(
        """
        SELECT source,
               COUNT(*) AS n,
               SUM(CASE WHEN COALESCE(realized_pnl,0) > 0 THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN COALESCE(realized_pnl,0) < 0 THEN 1 ELSE 0 END) AS losses,
               SUM(CASE WHEN COALESCE(realized_pnl,0) = 0 THEN 1 ELSE 0 END) AS flats,
               ROUND(SUM(CASE WHEN COALESCE(realized_pnl,0) > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 1) AS win_pct,
               ROUND(SUM(COALESCE(realized_pnl,0)), 2) AS pnl,
               ROUND(AVG(COALESCE(realized_pnl,0)), 2) AS avg_pnl
        FROM futures_positions
        WHERE account_id=2 AND status='closed'
          AND source IN ({srcs})
          AND close_time >= UTC_TIMESTAMP() - INTERVAL 7 DAY
        GROUP BY source
        ORDER BY n DESC
        """.format(srcs=",".join("%s" for _ in SOURCES)),
        SOURCES,
    )
    for r in cur.fetchall():
        print(r)

    print("=== DeepSeek explore+predict by day (last 10d) ===")
    cur.execute(
        """
        SELECT DATE(close_time) AS d, source,
               COUNT(*) AS n,
               SUM(CASE WHEN COALESCE(realized_pnl,0) > 0 THEN 1 ELSE 0 END) AS wins,
               ROUND(SUM(CASE WHEN COALESCE(realized_pnl,0) > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 1) AS win_pct,
               ROUND(SUM(COALESCE(realized_pnl,0)), 2) AS pnl
        FROM futures_positions
        WHERE account_id=2 AND status='closed'
          AND source IN ('deepseek_explore','deepseek_predict')
          AND close_time >= UTC_TIMESTAMP() - INTERVAL 10 DAY
        GROUP BY DATE(close_time), source
        ORDER BY d DESC, source
        """
    )
    for r in cur.fetchall():
        print(r)

    print("=== notes/close markers top (explore+predict, 7d) ===")
    cur.execute(
        """
        SELECT source, COALESCE(notes,'') AS reason, COUNT(*) AS n,
               ROUND(SUM(COALESCE(realized_pnl,0)), 2) AS pnl,
               ROUND(AVG(COALESCE(realized_pnl,0)), 2) AS avg_pnl
        FROM futures_positions
        WHERE account_id=2 AND status='closed'
          AND source IN ('deepseek_explore','deepseek_predict')
          AND close_time >= UTC_TIMESTAMP() - INTERVAL 7 DAY
        GROUP BY source, COALESCE(notes,'')
        ORDER BY source, n DESC
        """
    )
    for r in cur.fetchall():
        print(r)

    print("=== side split (7d) ===")
    cur.execute(
        """
        SELECT source, position_side,
               COUNT(*) AS n,
               SUM(CASE WHEN COALESCE(realized_pnl,0) > 0 THEN 1 ELSE 0 END) AS wins,
               ROUND(SUM(CASE WHEN COALESCE(realized_pnl,0) > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 1) AS win_pct,
               ROUND(SUM(COALESCE(realized_pnl,0)), 2) AS pnl
        FROM futures_positions
        WHERE account_id=2 AND status='closed'
          AND source IN ('deepseek_explore','deepseek_predict')
          AND close_time >= UTC_TIMESTAMP() - INTERVAL 7 DAY
        GROUP BY source, position_side
        """
    )
    for r in cur.fetchall():
        print(r)

    print("=== hold minutes buckets (7d) ===")
    cur.execute(
        """
        SELECT source,
               CASE
                 WHEN TIMESTAMPDIFF(MINUTE, open_time, close_time) < 30 THEN '<30m'
                 WHEN TIMESTAMPDIFF(MINUTE, open_time, close_time) < 120 THEN '30m-2h'
                 WHEN TIMESTAMPDIFF(MINUTE, open_time, close_time) < 240 THEN '2h-4h'
                 ELSE '>=4h'
               END AS bucket,
               COUNT(*) AS n,
               SUM(CASE WHEN COALESCE(realized_pnl,0) > 0 THEN 1 ELSE 0 END) AS wins,
               ROUND(SUM(CASE WHEN COALESCE(realized_pnl,0) > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 1) AS win_pct,
               ROUND(SUM(COALESCE(realized_pnl,0)), 2) AS pnl
        FROM futures_positions
        WHERE account_id=2 AND status='closed'
          AND source IN ('deepseek_explore','deepseek_predict')
          AND close_time >= UTC_TIMESTAMP() - INTERVAL 7 DAY
          AND open_time IS NOT NULL AND close_time IS NOT NULL
        GROUP BY source, bucket
        ORDER BY source, bucket
        """
    )
    for r in cur.fetchall():
        print(r)

    print("=== worst 15 losers (7d) ===")
    cur.execute(
        """
        SELECT id, source, symbol, position_side, ROUND(realized_pnl,2) AS pnl,
               LEFT(COALESCE(notes,''),120) AS notes, open_time, close_time,
               LEFT(COALESCE(entry_reason,''),100) AS entry
        FROM futures_positions
        WHERE account_id=2 AND status='closed'
          AND source IN ('deepseek_explore','deepseek_predict')
          AND close_time >= UTC_TIMESTAMP() - INTERVAL 7 DAY
        ORDER BY realized_pnl ASC
        LIMIT 15
        """
    )
    for r in cur.fetchall():
        print(r)

    print("=== open now ===")
    cur.execute(
        """
        SELECT source, COUNT(*) n
        FROM futures_positions
        WHERE account_id=2 AND status='open'
          AND source LIKE 'deepseek%'
        GROUP BY source
        """
    )
    for r in cur.fetchall():
        print(r)

    conn.close()


if __name__ == "__main__":
    main()
