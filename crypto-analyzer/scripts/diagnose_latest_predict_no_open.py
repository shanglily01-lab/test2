import sys

import pymysql

sys.path.insert(0, ".")

from app.utils.config_loader import get_db_config


def main() -> None:
    conn = pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT setting_key, setting_value, updated_at
            FROM system_settings
            WHERE setting_key IN (
                'paper_limit_entry_enabled',
                'paper_limit_timeout_action',
                'paper_limit_long_offset_pct',
                'paper_limit_short_offset_pct',
                'max_positions'
            )
            ORDER BY setting_key
            """
        )
        print("SETTINGS")
        for row in cur.fetchall():
            print(row)

        cur.execute(
            """
            SELECT id, asof_utc, symbol_count, predictions_made, orders_opened,
                   elapsed_s, status, error_msg, triggered_by, created_at,
                   LEFT(summary_zh, 200) AS summary_zh
            FROM deepseek_predict_runs
            ORDER BY id DESC
            LIMIT 5
            """
        )
        runs = cur.fetchall()
        print("RUNS")
        for row in runs:
            print(row)

        if not runs:
            return

        latest_id = runs[0]["id"]
        run_ids = [r["id"] for r in runs]
        placeholders = ",".join(["%s"] * len(run_ids))

        cur.execute(
            f"""
            SELECT run_id, action_taken, COUNT(*) AS cnt
            FROM deepseek_predict_verdicts
            WHERE run_id IN ({placeholders})
            GROUP BY run_id, action_taken
            ORDER BY run_id DESC, cnt DESC
            """,
            run_ids,
        )
        print("ACTIONS")
        for row in cur.fetchall():
            print(row)

        cur.execute(
            """
            SELECT action_taken, LEFT(skip_reason, 180) AS skip_reason, COUNT(*) AS cnt
            FROM deepseek_predict_verdicts
            WHERE run_id=%s
            GROUP BY action_taken, LEFT(skip_reason, 180)
            ORDER BY cnt DESC
            LIMIT 20
            """,
            (latest_id,),
        )
        print("LATEST_SKIP_REASONS")
        for row in cur.fetchall():
            print(row)

        cur.execute(
            """
            SELECT symbol, category, confidence, action_taken,
                   LEFT(skip_reason, 180) AS skip_reason,
                   LEFT(catalyst, 220) AS catalyst,
                   LEFT(data_signal, 220) AS data_signal,
                   created_at
            FROM deepseek_predict_verdicts
            WHERE run_id=%s
            ORDER BY id
            LIMIT 40
            """,
            (latest_id,),
        )
        print("LATEST_VERDICTS")
        for row in cur.fetchall():
            print(row)

        for table in (
            "deepseek_predict_runs",
            "deepseek_explore_runs",
            "gemini_predict_runs",
            "gemini_explore_runs",
        ):
            try:
                cur.execute(
                    f"""
                    SELECT id, asof_utc, symbol_count, predictions_made,
                           orders_opened, elapsed_s, status, error_msg,
                           triggered_by, created_at,
                           LEFT(summary_zh, 160) AS summary_zh
                    FROM {table}
                    ORDER BY id DESC
                    LIMIT 3
                    """
                )
                print(f"LATEST_{table}")
                for row in cur.fetchall():
                    print(row)
            except Exception as exc:
                print(f"LATEST_{table}_ERR {exc}")

        cur.execute(
            """
            SELECT source, status, COUNT(*) AS cnt
            FROM futures_positions
            WHERE account_id=2 AND LOWER(status)='open'
            GROUP BY source, status
            ORDER BY cnt DESC
            """
        )
        print("OPEN_POSITIONS_BY_SOURCE")
        for row in cur.fetchall():
            print(row)

        try:
            cur.execute(
                """
                SELECT status, order_type, side, COUNT(*) AS cnt
                FROM futures_orders
                WHERE account_id=2
                GROUP BY status, order_type, side
                ORDER BY cnt DESC
                LIMIT 20
                """
            )
            print("ORDERS_BY_STATUS")
            for row in cur.fetchall():
                print(row)
        except Exception as exc:
            print(f"ORDERS_BY_STATUS_ERR {exc}")
        cur.close()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
