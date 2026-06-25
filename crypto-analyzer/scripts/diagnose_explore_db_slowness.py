#!/usr/bin/env python3
"""探索页相关 DB 慢查询 / 锁等待巡检（只读）。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql

from app.utils.config_loader import get_db_config

SOURCES = ("gemini_explore", "deepseek_explore", "gemini_predict", "deepseek_predict")


def main() -> int:
    cfg = dict(get_db_config())
    cfg.setdefault("charset", "utf8mb4")
    cfg["cursorclass"] = pymysql.cursors.DictCursor
    conn = pymysql.connect(**cfg)
    try:
        with conn.cursor() as cur:
            print("=== futures_positions 索引 ===")
            cur.execute(
                """
                SELECT index_name, GROUP_CONCAT(column_name ORDER BY seq_in_index) AS cols
                FROM information_schema.statistics
                WHERE table_schema = DATABASE() AND table_name = 'futures_positions'
                GROUP BY index_name
                ORDER BY index_name
                """
            )
            for r in cur.fetchall():
                print(f"  {r['index_name']}: {r['cols']}")

            need = {
                "idx_fp_source_account_status_close",
                "idx_fp_source_account_status_open",
            }
            have = {r["index_name"] for r in cur.fetchall()} if False else set()
            cur.execute(
                """
                SELECT DISTINCT index_name FROM information_schema.statistics
                WHERE table_schema = DATABASE() AND table_name = 'futures_positions'
                """
            )
            have = {r["index_name"] for r in cur.fetchall()}
            missing = sorted(need - have)
            if missing:
                print("\n缺失推荐索引:", ", ".join(missing))
                print("  → 低峰执行 migrations/019_futures_positions_source_index.sql")
            else:
                print("\n推荐 source 索引已存在")

            print("\n=== 当前 InnoDB 锁等待 (Top 10) ===")
            try:
                cur.execute(
                    """
                    SELECT
                      r.trx_mysql_thread_id AS waiting_thread,
                      TIMESTAMPDIFF(SECOND, r.trx_wait_started, NOW()) AS wait_s,
                      LEFT(r.trx_query, 160) AS waiting_query,
                      b.trx_mysql_thread_id AS blocking_thread,
                      LEFT(b.trx_query, 160) AS blocking_query
                    FROM information_schema.innodb_lock_waits w
                    JOIN information_schema.innodb_trx b ON b.trx_id = w.blocking_trx_id
                    JOIN information_schema.innodb_trx r ON r.trx_id = w.requesting_trx_id
                    ORDER BY wait_s DESC LIMIT 10
                    """
                )
                rows = cur.fetchall()
                if not rows:
                    print("  (无)")
                else:
                    for row in rows:
                        print(row)
            except Exception as e:
                print(f"  (无法查询 innodb_lock_waits: {e})")

            print("\n=== 探索页典型 SQL 耗时 (EXPLAIN + 计时) ===")
            for src in SOURCES:
                t0 = time.perf_counter()
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt, COALESCE(SUM(realized_pnl),0) AS pnl
                    FROM futures_positions
                    WHERE source=%s AND status='closed' AND account_id=2
                      AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                    """,
                    (src,),
                )
                row = cur.fetchone()
                ms = int((time.perf_counter() - t0) * 1000)
                print(f"  {src:20} closed_30d agg: {ms}ms  rows={row.get('cnt')}")

                cur.execute(
                    """
                    EXPLAIN SELECT id FROM futures_positions
                    WHERE source=%s AND status='closed' AND account_id=2
                      AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                    ORDER BY close_time DESC LIMIT 200
                    """,
                    (src,),
                )
                plan = cur.fetchone() or {}
                print(f"    EXPLAIN closed list: key={plan.get('key')} rows={plan.get('rows')} type={plan.get('type')}")

            print("\n=== position_stats_snapshot ===")
            cur.execute(
                "SELECT source, open_count, closed_30d, win_rate_30d, updated_at "
                "FROM data_cache.position_stats_snapshot "
                "WHERE account_id=2 ORDER BY source"
            )
            snap = cur.fetchall()
            if not snap:
                print("  (空 — 探索页会回退扫主表，建议 POST /api/data-cache/refresh/position-stats)")
            else:
                for r in snap:
                    print(f"  {r['source']:20} open={r.get('open_count')} closed30={r.get('closed_30d')} wr={r.get('win_rate_30d')}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
