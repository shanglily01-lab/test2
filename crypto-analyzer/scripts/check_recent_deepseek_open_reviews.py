"""Print recent DeepSeek open advisor reviews."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql

from app.utils.config_loader import get_db_config


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
                SELECT created_at, decision, symbol, position_side, source,
                       LEFT(reason, 120) AS reason
                FROM deepseek_advisor_reviews
                WHERE review_type='open'
                ORDER BY id DESC
                LIMIT 12
                """
            )
            rows = cur.fetchall()
        for row in rows:
            print(row)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
