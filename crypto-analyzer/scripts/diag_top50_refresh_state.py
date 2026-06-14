"""Inspect TOP50/rating refresh state without mutating data."""

import pymysql
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.utils.config_loader import get_db_config


def main() -> None:
    conn = pymysql.connect(**get_db_config(), cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT IS_FREE_LOCK(%s) AS free_lock",
                ("update_top_performers_refresh",),
            )
            print("lock:", cur.fetchone())

            cur.execute(
                "SELECT COUNT(*) AS cnt, MAX(last_updated) AS max_updated "
                "FROM top_performing_symbols"
            )
            print("top50:", cur.fetchone())

            cur.execute(
                """
                SELECT setting_key, setting_value, updated_at
                FROM system_settings
                WHERE setting_key IN (%s, %s)
                ORDER BY setting_key
                """,
                ("rating_refresh_next_due_utc", "rating_refresh_last_ok_utc"),
            )
            print("settings:", cur.fetchall())

            cur.execute(
                """
                SELECT ID, USER, HOST, DB, COMMAND, TIME, STATE, LEFT(INFO, 300) AS info
                FROM information_schema.PROCESSLIST
                WHERE DB = DATABASE()
                   OR INFO LIKE '%update_top_performers_refresh%'
                   OR INFO LIKE '%top_performing_symbols%'
                   OR INFO LIKE '%trading_symbol_rating%'
                ORDER BY TIME DESC
                LIMIT 20
                """
            )
            print("processlist:")
            for row in cur.fetchall():
                print(row)

            try:
                cur.execute(
                    """
                    SELECT ml.OBJECT_TYPE, ml.OBJECT_NAME, ml.LOCK_STATUS,
                           t.PROCESSLIST_ID, t.PROCESSLIST_USER, t.PROCESSLIST_HOST,
                           t.PROCESSLIST_TIME, t.PROCESSLIST_COMMAND
                    FROM performance_schema.metadata_locks ml
                    LEFT JOIN performance_schema.threads t
                      ON ml.OWNER_THREAD_ID = t.THREAD_ID
                    WHERE ml.OBJECT_TYPE = 'USER LEVEL LOCK'
                      AND ml.OBJECT_NAME = %s
                    """,
                    ("update_top_performers_refresh",),
                )
                print("user_locks:", cur.fetchall())
            except Exception as exc:
                print("user_locks_error:", repr(exc))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
