"""Apply emergency runtime guards after win-rate decay."""

from __future__ import annotations

import sys
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.utils.config_loader import get_db_config


GUARDS = {
    "deepseek_explore_enabled": "0",
    "deepseek_predict_enabled": "0",
    "max_positions": "20",
}


def upsert_system_settings(cur) -> None:
    for key, value in GUARDS.items():
        cur.execute(
            """
            INSERT INTO system_settings
              (setting_key, setting_value, description, updated_by, updated_at)
            VALUES
              (%s, %s, %s, 'codex_winrate_guard', NOW())
            ON DUPLICATE KEY UPDATE
              setting_value=VALUES(setting_value),
              description=VALUES(description),
              updated_by=VALUES(updated_by),
              updated_at=NOW()
            """,
            (key, value, "Win-rate emergency guard: pause DeepSeek opens / cap paper slots"),
        )


def upsert_settings_cache(cfg: dict) -> None:
    cache_cfg = {**cfg, "database": "data_cache"}
    conn = pymysql.connect(
        **cache_cfg,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
    try:
        with conn.cursor() as cur:
            for key, value in GUARDS.items():
                cur.execute(
                    """
                    INSERT INTO settings_cache (setting_key, setting_value, updated_at)
                    VALUES (%s, %s, NOW())
                    ON DUPLICATE KEY UPDATE
                      setting_value=VALUES(setting_value),
                      updated_at=NOW()
                    """,
                    (key, value),
                )
        conn.commit()
    finally:
        conn.close()


def fetch_rows(cur, table: str):
    cur.execute(
        f"""
        SELECT setting_key, setting_value, updated_at
        FROM {table}
        WHERE setting_key IN ('deepseek_explore_enabled', 'deepseek_predict_enabled', 'max_positions')
        ORDER BY setting_key
        """
    )
    return cur.fetchall()


def main() -> None:
    cfg = get_db_config()
    conn = pymysql.connect(
        **cfg,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
    try:
        with conn.cursor() as cur:
            upsert_system_settings(cur)
            conn.commit()
            print("system_settings", fetch_rows(cur, "system_settings"))
    finally:
        conn.close()

    try:
        upsert_settings_cache(cfg)
        cache_cfg = {**cfg, "database": "data_cache"}
        conn = pymysql.connect(
            **cache_cfg,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
        try:
            with conn.cursor() as cur:
                print("settings_cache", fetch_rows(cur, "settings_cache"))
        finally:
            conn.close()
    except Exception as exc:
        print(f"settings_cache skipped: {exc!r}")


if __name__ == "__main__":
    main()
