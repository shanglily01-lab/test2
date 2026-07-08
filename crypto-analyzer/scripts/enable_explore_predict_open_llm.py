"""Enable LLM re-review for explore/predict open advisor flows."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql

from app.utils.config_loader import get_db_config


KEY = "explore_predict_open_advisor_llm_enabled"


def main() -> None:
    conn = pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO system_settings
                    (setting_key, setting_value, description, updated_at)
                VALUES
                    (
                        %s,
                        '1',
                        'Enable LLM re-review for explore/predict open advisor after upstream catalyst gate',
                        NOW()
                    )
                ON DUPLICATE KEY UPDATE
                    setting_value=VALUES(setting_value),
                    description=VALUES(description),
                    updated_at=NOW()
                """,
                (KEY,),
            )
            cur.execute(
                """
                SELECT setting_key, setting_value, updated_at
                FROM system_settings
                WHERE setting_key IN (
                    'deepseek_self_gated_open_skip_llm',
                    'explore_predict_open_advisor_llm_enabled',
                    'deepseek_open_advisor_enabled'
                )
                ORDER BY setting_key
                """
            )
            rows = cur.fetchall()
        conn.commit()
        for row in rows:
            print(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
