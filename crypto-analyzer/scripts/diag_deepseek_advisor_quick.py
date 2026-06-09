#!/usr/bin/env python3
"""Quick DeepSeek advisor open/hold activity check."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql
from app.utils.config_loader import get_db_config

conn = pymysql.connect(**get_db_config(), charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
cur = conn.cursor()

print("=== open advisor settings ===")
cur.execute(
    "SELECT setting_key, setting_value FROM system_settings "
    "WHERE setting_key LIKE %s OR setting_key LIKE %s",
    ("%open_advisor%", "%advisor%llm%"),
)
for r in cur.fetchall():
    print(r)

print("\n=== deepseek reviews 24h by type ===")
cur.execute(
    """
    SELECT review_type, source, decision, COUNT(*) AS cnt, MAX(created_at) AS last_at
    FROM deepseek_advisor_reviews
    WHERE created_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
    GROUP BY review_type, source, decision
    ORDER BY last_at DESC
    """
)
for r in cur.fetchall():
    print(r)

print("\n=== recent OPEN reviews (12h) ===")
cur.execute(
    """
    SELECT id, symbol, source, decision, created_at, LEFT(reason, 80) AS reason
    FROM deepseek_advisor_reviews
    WHERE review_type='open' AND created_at >= DATE_SUB(NOW(), INTERVAL 12 HOUR)
    ORDER BY id DESC LIMIT 20
    """
)
rows = cur.fetchall()
print(f"count={len(rows)}")
for r in rows:
    print(r)

print("\n=== ARIA/STX hold reviews ===")
cur.execute(
    """
    SELECT id, position_id, symbol, source, decision, created_at, LEFT(reason, 80) AS reason
    FROM deepseek_advisor_reviews
    WHERE symbol IN ('ARIA/USDT', 'STX/USDT') AND review_type='hold'
    ORDER BY id DESC LIMIT 10
    """
)
for r in cur.fetchall():
    print(r)

print("\n=== open ARIA/STX positions ===")
cur.execute(
    """
    SELECT id, symbol, source, open_time,
           TIMESTAMPDIFF(MINUTE, open_time, NOW()) AS hold_min
    FROM futures_positions
    WHERE symbol IN ('ARIA/USDT', 'STX/USDT') AND status='open'
    """
)
for r in cur.fetchall():
    print(r)

print("\n=== position-specific hold reviews ===")
for pid in (31370, 31451, 31452, 31371, 31375):
    cur.execute(
        """
        SELECT id, position_id, symbol, source, decision, created_at, LEFT(reason, 60) AS reason
        FROM deepseek_advisor_reviews WHERE position_id=%s AND review_type='hold' ORDER BY id DESC LIMIT 3
        """,
        (pid,),
    )
    rows = cur.fetchall()
    print(f"pid={pid} count={len(rows)}")
    for r in rows:
        print(" ", r)

cur.execute(
    """
    SELECT COUNT(*) AS n FROM futures_positions
    WHERE status='open' AND account_id=2
      AND TIMESTAMPDIFF(MINUTE, open_time, NOW()) >= 15
    """
)
print("\neligible open sim positions (>=15min):", cur.fetchone())

conn.close()
