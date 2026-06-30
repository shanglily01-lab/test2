#!/usr/bin/env python3
"""Benchmark explore page SQL — find slow queries."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql
from app.utils.config_loader import get_db_config

cfg = dict(get_db_config())
cfg["cursorclass"] = pymysql.cursors.DictCursor
conn = pymysql.connect(**cfg)
cur = conn.cursor()

tests = [
    ("runs_light", """
        SELECT id, asof_utc, model, universe_size, trades_opened, elapsed_s, status,
               error_msg, triggered_by, LEFT(summary_zh, 200) AS summary_short, created_at
        FROM gemini_explore_runs ORDER BY id DESC LIMIT 20
    """),
    ("runs_blob_check", """
        SELECT id, asof_utc,
               (prompt_text IS NOT NULL AND prompt_text != '') AS has_prompt,
               (raw_response IS NOT NULL AND raw_response != '') AS has_raw
        FROM gemini_explore_runs ORDER BY id DESC LIMIT 20
    """),
    ("runs_full_prod", """
        SELECT id, asof_utc, model, universe_size, trades_opened,
               elapsed_s, status, error_msg, triggered_by,
               LEFT(summary_zh, 200) AS summary_short, created_at,
               (prompt_text IS NOT NULL AND prompt_text != '') AS has_prompt,
               (raw_response IS NOT NULL AND raw_response != '') AS has_raw
        FROM gemini_explore_runs ORDER BY id DESC LIMIT 20
    """),
    ("runs_ds_full", """
        SELECT id, asof_utc, model, universe_size, trades_opened,
               elapsed_s, status, error_msg, triggered_by,
               LEFT(summary_zh, 200) AS summary_short, created_at,
               (prompt_text IS NOT NULL AND prompt_text != '') AS has_prompt,
               (raw_response IS NOT NULL AND raw_response != '') AS has_raw
        FROM deepseek_explore_runs ORDER BY id DESC LIMIT 20
    """),
    ("closed_ds_slim", """
        SELECT id, symbol, position_side, entry_price, mark_price,
               margin, realized_pnl, open_time, close_time, notes, source
        FROM futures_positions
        WHERE source='deepseek_explore' AND status='closed' AND account_id=2
          AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
        ORDER BY close_time DESC, id DESC LIMIT 80
    """),
    ("open_ds", """
        SELECT id, symbol, position_side, leverage, quantity,
               entry_price, mark_price, margin, unrealized_pnl, unrealized_pnl_pct,
               stop_loss_price, take_profit_price, open_time, planned_close_time, entry_reason
        FROM futures_positions
        WHERE source='deepseek_explore' AND status='open' AND account_id=2
        ORDER BY open_time DESC LIMIT 200
    """),
    ("refresh_agg_ds_30d", """
        SELECT COUNT(*) AS total,
          COALESCE(SUM(realized_pnl), 0) AS total_pnl,
          SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins
        FROM futures_positions
        WHERE source='deepseek_explore' AND status='closed' AND account_id=2
          AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
    """),
]

for name, sql in tests:
    t0 = time.perf_counter()
    cur.execute(sql)
    rows = cur.fetchall()
    ms = int((time.perf_counter() - t0) * 1000)
    cur.execute("EXPLAIN " + sql)
    plan = cur.fetchone() or {}
    print(
        f"{name:22} {ms:5d}ms rows={len(rows):3d} "
        f"key={plan.get('key')} type={plan.get('type')} examined={plan.get('rows')}"
    )

cur.execute("SHOW CREATE TABLE gemini_explore_runs")
row = cur.fetchone()
ddl = list(row.values())[1]
for line in ddl.split("\n"):
    if "prompt" in line.lower() or "raw" in line.lower() or "KEY" in line or "PRIMARY" in line:
        print("DDL:", line.strip())

for tbl in ("gemini_explore_runs", "deepseek_explore_runs"):
    cur.execute(
        f"SELECT AVG(LENGTH(prompt_text)) a, MAX(LENGTH(prompt_text)) m, "
        f"AVG(LENGTH(raw_response)) a2, MAX(LENGTH(raw_response)) m2, COUNT(*) c "
        f"FROM {tbl} WHERE prompt_text IS NOT NULL"
    )
    print(f"{tbl} blob sizes:", cur.fetchone())
    for label, sql in (
        ("light", f"SELECT id, asof_utc, LEFT(summary_zh,200) s FROM {tbl} ORDER BY id DESC LIMIT 20"),
        (
            "blob_flag",
            f"SELECT id, (prompt_text IS NOT NULL AND prompt_text != '') hp "
            f"FROM {tbl} ORDER BY id DESC LIMIT 20",
        ),
    ):
        t0 = time.perf_counter()
        cur.execute(sql)
        cur.fetchall()
        print(f"  {tbl} {label}:", int((time.perf_counter() - t0) * 1000), "ms")

conn.close()
