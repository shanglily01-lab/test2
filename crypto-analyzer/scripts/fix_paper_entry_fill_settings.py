import sys

import pymysql

sys.path.insert(0, ".")

from app.utils.config_loader import get_db_config


SETTINGS = {
    "paper_limit_entry_enabled": (
        "0",
        "Disable default paper limit entry so AI-approved paper opens enter immediately.",
    ),
    "paper_limit_timeout_action": (
        "convert_market",
        "Convert timed-out paper limit entries to market fills instead of expiring silently.",
    ),
}


def main() -> None:
    conn = pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
    try:
        cur = conn.cursor()
        for key, (value, description) in SETTINGS.items():
            cur.execute(
                """
                INSERT INTO system_settings
                    (setting_key, setting_value, description, updated_at)
                VALUES
                    (%s, %s, %s, NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value=VALUES(setting_value),
                    description=VALUES(description),
                    updated_at=NOW()
                """,
                (key, value, description),
            )
        cur.execute(
            """
            SELECT setting_key, setting_value, updated_at
            FROM system_settings
            WHERE setting_key IN (%s, %s)
            ORDER BY setting_key
            """,
            tuple(SETTINGS),
        )
        for row in cur.fetchall():
            print(row)
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
