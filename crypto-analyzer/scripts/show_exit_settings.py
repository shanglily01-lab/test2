"""Read-only exit-related system settings."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql

from app.utils.config_loader import get_db_config


KEYS = (
    "disable_sl_tp_hold",
    "max_hold_hours",
    "smart_exit_enabled",
    "stop_loss_pct",
    "take_profit_pct",
)


def main() -> None:
    conn = pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT setting_key, setting_value, updated_at
                FROM system_settings
                WHERE setting_key IN %s
                ORDER BY setting_key
                """,
                (KEYS,),
            )
            for row in cur.fetchall() or []:
                print(row)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
