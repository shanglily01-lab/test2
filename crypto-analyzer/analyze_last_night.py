#!/usr/bin/env python
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

print("=" * 100)
print("Last Night Trading Analysis (2026-02-09 evening ~ 2026-02-10)")
print("=" * 100)

# Worst symbols
cursor.execute('''
    SELECT
        symbol,
        COUNT(*) as cnt,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl
    FROM futures_positions
    WHERE account_id = 2
    AND status = 'closed'
    AND open_time >= '2026-02-09 18:00:00'
    GROUP BY symbol
    ORDER BY total_pnl ASC
''')

rows = cursor.fetchall()

print("\nSymbol Performance:")
print("Symbol         Trades  Wins   Total PNL")
print("-" * 100)

for r in rows:
    sym = r['symbol']
    cnt = r['cnt']
    wins = r['wins']
    pnl = float(r['total_pnl'])
    print(f"{sym:<14} {cnt:>6}  {wins:>4}   ${pnl:>9.2f}")

# Overall summary
cursor.execute('''
    SELECT
        COUNT(*) as total,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl
    FROM futures_positions
    WHERE account_id = 2
    AND status = 'closed'
    AND open_time >= '2026-02-09 18:00:00'
''')

summary = cursor.fetchone()
total = summary['total']
wins = summary['wins']
total_pnl = float(summary['total_pnl'])
wr = wins / total * 100 if total > 0 else 0

print("-" * 100)
print(f"TOTAL:         {total:>6}  {wins:>4}   ${total_pnl:>9.2f}  (Win Rate: {wr:.1f}%)")

cursor.close()
conn.close()

print("\n" + "=" * 100)
