"""Print DeepSeek page table schemas used by API endpoints."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql
import pymysql.cursors

from app.utils.config_loader import get_db_config


TABLES = [
    "futures_positions",
    "deepseek_explore_runs",
    "deepseek_predict_runs",
    "deepseek_explore_verdicts",
    "deepseek_predict_verdicts",
]


def main() -> None:
    conn = pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            for table in TABLES:
                cur.execute(f"SHOW COLUMNS FROM `{table}`")
                cols = [row["Field"] for row in cur.fetchall()]
                print(f"{table}: {', '.join(cols)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
