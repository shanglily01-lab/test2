#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pymysql, os
from dotenv import load_dotenv

load_dotenv()
conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'),
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)
cursor = conn.cursor()

print("\n" + "="*80)
print("BREAKDOWN SIGNAL ANALYSIS")
print("="*80)

print("\n[ORIGINAL LOGIC] Breakdown Short Trigger:")
print("  1. position_pct < 30  (Price in bottom 30% of 72H range)")
print("  2. net_power_1h <= -2 (Strong bearish volume on 1H)")
print("  3. OR (net_power_1h <= -2 AND net_power_15m <= -2)")

print("\n[V5.1 LOGIC] Added Big4 Filter:")
print("  4. Big4 strength >= 70")
print("  5. Big4 direction = BEARISH")
print("  -> Only allow breakdown short when Big4 is strongly bearish")

print("\n[CURRENT STATUS] Breakdown/Breakout signals DISABLED (2026-02-09)")

# Last 30 days all signals
print("\n" + "="*80)
print("LAST 30 DAYS - ALL SIGNALS PERFORMANCE")
print("="*80)

cursor.execute('''
    SELECT
        position_side as side,
        COUNT(*) as cnt,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl
    FROM futures_positions
    WHERE account_id = 2
    AND status = 'closed'
    AND open_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
    GROUP BY position_side
''')

rows = cursor.fetchall()
if rows:
    for r in rows:
        side = r['side']
        cnt = r['cnt']
        wins = r['wins']
        total_pnl = float(r['total_pnl'])
        wr = wins/cnt*100 if cnt > 0 else 0
        print(f"{side}: {cnt} orders, {wins} wins ({wr:.1f}%), Total PNL: ${total_pnl:.2f}")
else:
    print("No orders in last 30 days")

# Check notes field for breakdown/breakout
print("\n" + "="*80)
print("SEARCHING FOR BREAKDOWN/BREAKOUT IN NOTES")
print("="*80)

cursor.execute('''
    SELECT
        position_side,
        COUNT(*) as cnt,
        SUM(realized_pnl) as total_pnl
    FROM futures_positions
    WHERE account_id = 2
    AND status = 'closed'
    AND open_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
    AND (notes LIKE '%breakdown%' OR notes LIKE '%breakout%')
    GROUP BY position_side
''')

bd_rows = cursor.fetchall()
if bd_rows:
    for r in bd_rows:
        print(f"{r['position_side']}: {r['cnt']} orders, PNL: ${float(r['total_pnl']):.2f}")
else:
    print("No breakdown/breakout signals found in notes")

# Check signal_components field
print("\n" + "="*80)
print("CHECKING signal_components FIELD")
print("="*80)

cursor.execute('''
    SELECT signal_components
    FROM futures_positions
    WHERE account_id = 2
    AND status = 'closed'
    AND open_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
    AND signal_components IS NOT NULL
    LIMIT 5
''')

comps = cursor.fetchall()
if comps:
    print("Sample signal_components:")
    for c in comps:
        print(f"  {c['signal_components']}")
else:
    print("No signal_components data found")

cursor.close()
conn.close()

print("\n" + "="*80)
print("CONCLUSION:")
print("  - Breakdown/breakout signals have been disabled")
print("  - Need to check logs/notes to see what signals were actually used")
print("  - signal_components field may have the detailed breakdown")
print("="*80 + "\n")
