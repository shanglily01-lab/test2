#!/usr/bin/env python3
"""Find hold-review gaps for deepseek_predict positions."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql
from app.services.hold_advisor_query import fetch_due_hold_positions, DEEPSEEK_HOLD_SOURCE_SQL
from app.utils.config_loader import get_db_config

conn = pymysql.connect(**get_db_config(), charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)

due = fetch_due_hold_positions(
    conn,
    reviews_table="deepseek_advisor_reviews",
    source_sql=DEEPSEEK_HOLD_SOURCE_SQL + " AND fp.source='deepseek_predict'",
)
print(f"due deepseek_predict count={len(due)} (max 50/tick)")
for r in due[:10]:
    print(r)

cur = conn.cursor()
cur.execute(
    """
    SELECT fp.id, fp.symbol, fp.open_time,
           TIMESTAMPDIFF(MINUTE, fp.open_time, NOW()) AS hold_min,
           (SELECT COUNT(*) FROM deepseek_advisor_reviews r
            WHERE r.position_id=fp.id AND r.review_type='hold') AS hold_reviews
    FROM futures_positions fp
    WHERE fp.status='open' AND fp.account_id=2 AND fp.source='deepseek_predict'
      AND TIMESTAMPDIFF(MINUTE, fp.open_time, NOW()) >= 15
    ORDER BY fp.open_time ASC
    """
)
rows = cur.fetchall()
missing = [r for r in rows if r["hold_reviews"] == 0]
print(f"\neligible={len(rows)} never_reviewed={len(missing)}")
for r in missing[:10]:
    print(r)

conn.close()
