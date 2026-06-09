#!/usr/bin/env python3
"""Diagnose why STX/ARIA deepseek_predict positions lack hold advisor reviews."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql
from app.services.open_advisor_routing import should_use_deepseek_hold_advisor
from app.utils.config_loader import get_db_config

conn = pymysql.connect(**get_db_config(), charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
cur = conn.cursor()

print("=== open deepseek_predict ===")
cur.execute(
    """
    SELECT id, symbol, source, status, open_time, planned_close_time, account_id,
           TIMESTAMPDIFF(MINUTE, open_time, NOW()) AS hold_min
    FROM futures_positions
    WHERE status='open' AND source='deepseek_predict'
    ORDER BY open_time DESC
    """
)
rows = cur.fetchall()
for r in rows:
    print(r)
    print("  should_use_deepseek_hold:", should_use_deepseek_hold_advisor(r["source"]))

print("\n=== advisor settings ===")
cur.execute(
    "SELECT setting_key, setting_value FROM system_settings WHERE setting_key LIKE %s",
    ("%advisor%",),
)
for r in cur.fetchall():
    print(r)

print("\n=== deepseek hold reviews (recent, any source) ===")
cur.execute(
    """
    SELECT id, position_id, symbol, source, review_type, decision, created_at, LEFT(reason, 80) AS reason
    FROM deepseek_advisor_reviews
    WHERE review_type='hold'
    ORDER BY id DESC LIMIT 25
    """
)
for r in cur.fetchall():
    print(r)

print("\n=== hold reviews for STX/ARIA position ids ===")
pids = [r["id"] for r in rows]
if pids:
    placeholders = ",".join(["%s"] * len(pids))
    cur.execute(
        f"""
        SELECT id, position_id, symbol, decision, created_at, LEFT(reason, 80) AS reason
        FROM deepseek_advisor_reviews
        WHERE position_id IN ({placeholders})
        ORDER BY id DESC
        """,
        pids,
    )
    reviews = cur.fetchall()
    print(f"count={len(reviews)}")
    for r in reviews:
        print(r)
else:
    print("no open deepseek_predict positions")

print("\n=== eligible sim positions (account_id=2, hold>=15min) ===")
cur.execute(
    """
    SELECT source, COUNT(*) AS cnt
    FROM futures_positions
    WHERE status='open' AND account_id=2
      AND TIMESTAMPDIFF(MINUTE, open_time, NOW()) >= 15
    GROUP BY source ORDER BY cnt DESC
    """
)
for r in cur.fetchall():
    print(r)

conn.close()
