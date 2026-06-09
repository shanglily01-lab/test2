"""Diagnose zero opens in last 12h — runs, verdicts, symbol gate overlap."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql
from dotenv import dotenv_values

from app.utils.config_loader import get_db_config
from app.utils.futures_symbol import futures_symbol_clean

HOURS = 12


def main() -> None:
    since = datetime.utcnow() - timedelta(hours=HOURS)
    conn = pymysql.connect(**get_db_config(), charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
    cur = conn.cursor()

    print(f"=== Since UTC {since.isoformat()} ({HOURS}h) ===\n")

    cur.execute(
        """
        SELECT source, status, COUNT(*) cnt
        FROM futures_positions
        WHERE account_id = 2 AND open_time >= %s
        GROUP BY source, status
        ORDER BY cnt DESC
        """,
        (since,),
    )
    print("futures_positions opens:")
    for r in cur.fetchall():
        print(f"  {r}")

    run_queries = {
        "gemini_predict_runs": "SELECT id, asof_utc, status, orders_opened, predictions_made, LEFT(error_msg,80) error_msg FROM gemini_predict_runs WHERE asof_utc >= %s ORDER BY id DESC LIMIT 8",
        "deepseek_predict_runs": "SELECT id, asof_utc, status, orders_opened, predictions_made, LEFT(error_msg,80) error_msg FROM deepseek_predict_runs WHERE asof_utc >= %s ORDER BY id DESC LIMIT 8",
        "gemini_explore_runs": "SELECT id, asof_utc, status, elapsed_s, triggered_by, LEFT(error_msg,80) error_msg FROM gemini_explore_runs WHERE asof_utc >= %s ORDER BY id DESC LIMIT 8",
        "deepseek_explore_runs": "SELECT id, asof_utc, status, elapsed_s, triggered_by, LEFT(error_msg,80) error_msg FROM deepseek_explore_runs WHERE asof_utc >= %s ORDER BY id DESC LIMIT 8",
    }
    for table, sql in run_queries.items():
        cur.execute(sql, (since,))
        rows = cur.fetchall()
        print(f"\n{table} runs ({len(rows)}):")
        for r in rows:
            print(f"  {r}")

    for verdict_table, run_table, label in [
        ("gemini_predict_verdicts", "gemini_predict_runs", "gemini_predict"),
        ("deepseek_predict_verdicts", "deepseek_predict_runs", "deepseek_predict"),
        ("gemini_explore_verdicts", "gemini_explore_runs", "gemini_explore"),
        ("deepseek_explore_verdicts", "deepseek_explore_runs", "deepseek_explore"),
    ]:
        try:
            cur.execute(
                f"""
                SELECT v.action_taken, COUNT(*) cnt
                FROM {verdict_table} v
                JOIN {run_table} r ON r.id = v.run_id
                WHERE r.asof_utc >= %s
                GROUP BY v.action_taken
                ORDER BY cnt DESC
                """,
                (since,),
            )
            rows = cur.fetchall()
            if rows:
                print(f"\n{label} verdict actions:")
                for r in rows:
                    print(f"  {r['action_taken']}: {r['cnt']}")
        except Exception as e:
            print(f"\n{label} verdicts skip: {e}")

    # symbol gate overlap
    cur.execute("SELECT symbol FROM top_performing_symbols")
    top50 = {futures_symbol_clean(r["symbol"]) for r in cur.fetchall()}
    cur.execute("SELECT symbol, rating_level FROM trading_symbol_rating")
    rated = {futures_symbol_clean(r["symbol"]): r["rating_level"] for r in cur.fetchall()}
    try:
        cur.execute(
            "SELECT symbol FROM data_cache.candidate_pool_snapshot ORDER BY quote_volume_24h DESC LIMIT 100"
        )
        pool = [futures_symbol_clean(r["symbol"]) for r in cur.fetchall()]
    except Exception:
        cur.execute("SELECT symbol FROM candidate_pool_snapshot LIMIT 100")
        pool = [futures_symbol_clean(r["symbol"]) for r in cur.fetchall()]

    allowed = [s for s in pool if s in top50 or s in rated]
    blocked = [s for s in pool if s not in top50 and s not in rated]
    print(f"\ncandidate_pool (top {len(pool)}): allowed={len(allowed)} blocked={len(blocked)}")
    if blocked[:15]:
        print(f"  blocked sample: {blocked[:15]}")

    for verdict_table, run_table, label in [
        ("gemini_predict_verdicts", "gemini_predict_runs", "gemini_predict"),
        ("deepseek_predict_verdicts", "deepseek_predict_runs", "deepseek_predict"),
    ]:
        for action in ("skipped_other", "skipped_weak_catalyst", "skipped_confidence"):
            try:
                cur.execute(
                    f"""
                    SELECT v.skip_reason, COUNT(*) cnt
                    FROM {verdict_table} v
                    JOIN {run_table} r ON r.id = v.run_id
                    WHERE r.asof_utc >= %s AND v.action_taken = %s
                    GROUP BY v.skip_reason
                    ORDER BY cnt DESC
                    LIMIT 8
                    """,
                    (since, action),
                )
                rows = cur.fetchall()
                if rows:
                    print(f"\n{label} {action}:")
                    for r in rows:
                        print(f"  [{r['cnt']}] {r['skip_reason']}")
            except Exception as e:
                print(f"{label} {action} err: {e}")

    conn.close()


if __name__ == "__main__":
    main()
