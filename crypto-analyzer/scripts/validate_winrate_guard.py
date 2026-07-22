"""Validate emergency win-rate guards against current DB state."""

from __future__ import annotations

import sys
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.trading_gates import (
    check_max_positions_allowed,
    check_source_side_performance_allowed,
)
from app.utils.config_loader import get_db_config


def main() -> None:
    conn = pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        print("max", check_max_positions_allowed(conn, 2))
        for source in ("deepseek_explore", "deepseek_predict"):
            for side in ("LONG", "SHORT"):
                print(source, side, check_source_side_performance_allowed(conn, source, side, 2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
