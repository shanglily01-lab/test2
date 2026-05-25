"""
诊断 Gemini 探索 — 查 manual_close_all 原因
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pymysql
from dotenv import dotenv_values
from pymysql.cursors import DictCursor

env = dict(dotenv_values(Path(__file__).parent.parent / ".env"))
cfg = {'host': env['DB_HOST'], 'port': int(env['DB_PORT']),
       'user': env['DB_USER'], 'password': env['DB_PASSWORD'],
       'charset': 'utf8mb4', 'cursorclass': DictCursor}

def q(db, sql, args=None):
    c = {**cfg, 'database': db}; conn = pymysql.connect(**c)
    with conn.cursor() as cur:
        if args is not None: cur.execute(sql, args)
        else: cur.execute(sql)
        rows = cur.fetchall()
    conn.close(); return rows

print("=" * 60)
print("1. manual_close_all 来自哪个来源？什么时候触发的？")
print("=" * 60)
rows = q('binance-data', """
    SELECT notes, source, COUNT(*) AS cnt,
           MIN(close_time) AS first_close, MAX(close_time) AS last_close
    FROM futures_positions
    WHERE status='closed' AND notes IS NOT NULL
    GROUP BY notes, source
    ORDER BY MAX(close_time) DESC
    LIMIT 20
""")
for r in rows:
    print(f"  {r['source']:20s} notes={r['notes']:30s} x{r['cnt']:4d}  "
          f"{r['first_close']} ~ {r['last_close']}")

print("\n" + "=" * 60)
print("2. 最近一轮 gemini_explore 开的单（run_id=22）现在怎么样了？")
print("=" * 60)
rows = q('binance-data', """
    SELECT id, symbol, status, realized_pnl, notes, close_time,
           open_time
    FROM futures_positions
    WHERE source='gemini_explore' AND open_time >= '2026-05-23 00:00:00'
    ORDER BY open_time DESC
""")
if rows:
    for r in rows:
        status = r['status']
        pnl = f"pnl={r['realized_pnl']:+.2f}" if r['realized_pnl'] else "pnl=N/A"
        note = f"notes={r['notes']}" if r['notes'] else ""
        ct = f"close={r['close_time']}" if r['close_time'] else ""
        print(f"  id={r['id']:5d} {r['symbol']:12s} {status:8s} {pnl:12s} {note:30s} {ct}")
else:
    print("  今日无 gemini_explore 开单")

print("\n" + "=" * 60)
print("3. 今天有哪些来源开了单")
print("=" * 60)
rows = q('binance-data', """
    SELECT source, COUNT(*) AS cnt,
           SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_cnt
    FROM futures_positions
    WHERE open_time >= '2026-05-23 00:00:00'
    GROUP BY source
    ORDER BY cnt DESC
""")
for r in rows:
    print(f"  {r['source']:20s} 开 {r['cnt']:3d} 单, 当前持仓 {r['open_cnt']:3d}")

print("\n" + "=" * 60)
print("4. gemini_explore 的 max_positions 设置（是否满了）")
print("=" * 60)
try:
    rows = q('binance-data', "SELECT setting_key, setting_value FROM system_settings WHERE setting_key LIKE '%explore%max%' OR setting_key LIKE '%max_position%'")
    for r in rows:
        print(f"  {r['setting_key']:40s} = {r['setting_value']}")
except:
    print("  (无相关设置)")
