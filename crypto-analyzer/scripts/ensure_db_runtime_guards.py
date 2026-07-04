#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install runtime DB guards used by app/scheduler/API workloads.

This script is intentionally idempotent. It adds indexes that keep hot
futures_positions risk/stat queries from scanning the whole table, and prints
currently blocking MySQL sessions for deployment diagnostics.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.utils.config_loader import get_db_config


INDEXES = {
    "futures_positions": [
        (
            "idx_fp_account_status_close_pnl",
            "ALTER TABLE futures_positions "
            "ADD INDEX idx_fp_account_status_close_pnl "
            "(account_id, status, close_time, realized_pnl)",
        ),
        (
            "idx_fp_source_account_status_close",
            "ALTER TABLE futures_positions "
            "ADD INDEX idx_fp_source_account_status_close "
            "(source, account_id, status, close_time)",
        ),
        (
            "idx_fp_source_account_status_open",
            "ALTER TABLE futures_positions "
            "ADD INDEX idx_fp_source_account_status_open "
            "(source, account_id, status, open_time)",
        ),
    ],
    "futures_orders": [
        (
            "idx_fo_account_pending_open",
            "ALTER TABLE futures_orders "
            "ADD INDEX idx_fo_account_pending_open "
            "(account_id, status, order_type, side)",
        ),
    ],
}


def _connect():
    cfg = dict(get_db_config())
    cfg.setdefault("charset", "utf8mb4")
    cfg["cursorclass"] = pymysql.cursors.DictCursor
    cfg["autocommit"] = True
    return pymysql.connect(**cfg)


def _index_exists(cur, table: str, index_name: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND index_name = %s
        LIMIT 1
        """,
        (table, index_name),
    )
    return cur.fetchone() is not None


def ensure_indexes(cur) -> None:
    for table, specs in INDEXES.items():
        for index_name, ddl in specs:
            if _index_exists(cur, table, index_name):
                print(f"OK existing index {table}.{index_name}")
                continue
            print(f"ADD index {table}.{index_name}")
            cur.execute(ddl)


def ensure_coin_scores_disabled(cur) -> None:
    """下线 coin_scores：DROP EVENT + 存储过程（扫 kline_data 拖垮 API，业务已不用）."""
    cur.execute(
        """
        SELECT EVENT_NAME, STATUS
        FROM information_schema.EVENTS
        WHERE EVENT_SCHEMA = DATABASE()
          AND EVENT_NAME = 'update_coin_scores_every_5min'
        LIMIT 1
        """
    )
    row = cur.fetchone()
    if row:
        print(f"DROP EVENT update_coin_scores_every_5min (was {row.get('STATUS')})")
        cur.execute("DROP EVENT IF EXISTS update_coin_scores_every_5min")
        print("OK coin_scores EVENT removed")
    else:
        print("OK coin_scores EVENT already absent")

    for proc in ("update_all_coin_scores", "calculate_coin_score"):
        cur.execute(
            """
            SELECT ROUTINE_NAME
            FROM information_schema.ROUTINES
            WHERE ROUTINE_SCHEMA = DATABASE()
              AND ROUTINE_TYPE = 'PROCEDURE'
              AND ROUTINE_NAME = %s
            LIMIT 1
            """,
            (proc,),
        )
        if cur.fetchone():
            print(f"DROP PROCEDURE {proc}")
            cur.execute(f"DROP PROCEDURE IF EXISTS `{proc}`")
            print(f"OK procedure {proc} removed")
        else:
            print(f"OK procedure {proc} already absent")


def print_blocking_sessions(cur) -> None:
    cur.execute(
        """
        SELECT
          r.trx_mysql_thread_id AS waiting_thread,
          TIMESTAMPDIFF(SECOND, r.trx_wait_started, NOW()) AS wait_s,
          LEFT(r.trx_query, 180) AS waiting_query,
          b.trx_mysql_thread_id AS blocking_thread,
          LEFT(b.trx_query, 180) AS blocking_query
        FROM information_schema.innodb_lock_waits w
        JOIN information_schema.innodb_trx b
          ON b.trx_id = w.blocking_trx_id
        JOIN information_schema.innodb_trx r
          ON r.trx_id = w.requesting_trx_id
        ORDER BY wait_s DESC
        LIMIT 20
        """
    )
    rows = cur.fetchall()
    if not rows:
        print("OK no current InnoDB lock waits")
        return
    print("WARN current InnoDB lock waits:")
    for row in rows:
        print(row)


def main() -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            ensure_indexes(cur)
            ensure_coin_scores_disabled(cur)
            print_blocking_sessions(cur)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
