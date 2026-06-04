#!/usr/bin/env python3
"""Diagnose GPT explore/predict scheduling (no API calls)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql
from dotenv import dotenv_values

from app.services.ai_predict_schedule import explore_round_is_due, predict_round_is_due
from app.services.gpt_config import GPT_API_KEY, GPT_BASE_URL, GPT_MODEL


def main() -> int:
    env = dotenv_values(ROOT / ".env")
    cfg = {
        "host": env["DB_HOST"],
        "port": int(env["DB_PORT"]),
        "user": env["DB_USER"],
        "password": env["DB_PASSWORD"],
        "database": env["DB_NAME"],
        "charset": "utf8mb4",
    }
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    print(f"UTC now: {now.isoformat()}")
    print(f"GPT_API_KEY: {'set (' + GPT_API_KEY[:12] + '...)' if GPT_API_KEY else 'MISSING'}")
    print(f"GPT_MODEL: {GPT_MODEL}  base: {GPT_BASE_URL}")

    conn = pymysql.connect(**cfg, cursorclass=pymysql.cursors.DictCursor, autocommit=True)
    cur = conn.cursor()
    keys = [
        "gpt_explore_enabled",
        "gpt_predict_enabled",
        "gpt_explore_next_due_utc",
        "gpt_predict_next_due_utc",
    ]
    print("\n--- system_settings ---")
    for k in keys:
        cur.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key=%s LIMIT 1",
            (k,),
        )
        r = cur.fetchone()
        print(f"  {k} = {(r or {}).get('setting_value', '<missing>')}")

    explore_due, explore_reason = explore_round_is_due(
        conn, runs_table="gpt_explore_runs", now=now, manual=False, log_tag="GPT探索"
    )
    predict_due, predict_reason = predict_round_is_due(
        conn,
        runs_table="gpt_predict_runs",
        next_due_key="gpt_predict_next_due_utc",
        now=now,
        manual=False,
        log_tag="GPT预测",
    )
    print("\n--- due check (scheduler) ---")
    print(f"  GPT探索 due={explore_due}  reason={explore_reason}")
    print(f"  GPT预测 due={predict_due}  reason={predict_reason}")

    for table, label in [("gpt_explore_runs", "explore"), ("gpt_predict_runs", "predict")]:
        print(f"\n--- {label} last 5 runs ---")
        cur.execute(
            f"SELECT id, asof_utc, status, triggered_by, elapsed_s, "
            f"LEFT(error_msg, 80) AS error_msg FROM `{table}` ORDER BY id DESC LIMIT 5"
        )
        for row in cur.fetchall():
            print(f"  {row}")

    cur.execute(
        "SELECT MAX(asof_utc) AS last_ok FROM gpt_explore_runs WHERE status='ok'"
    )
    print(f"\n  explore last_ok = {(cur.fetchone() or {}).get('last_ok')}")
    cur.execute(
        "SELECT MAX(asof_utc) AS last_ok FROM gpt_predict_runs WHERE status='ok'"
    )
    print(f"  predict last_ok = {(cur.fetchone() or {}).get('last_ok')}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
