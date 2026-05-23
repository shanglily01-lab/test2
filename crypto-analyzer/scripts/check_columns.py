"""
检查 price_stats_24h 的字段名和实际数据
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pymysql
from dotenv import dotenv_values
from pymysql.cursors import DictCursor

env = dict(dotenv_values(Path(__file__).parent.parent / ".env"))

conn = pymysql.connect(
    host=env['DB_HOST'], port=int(env['DB_PORT']),
    user=env['DB_USER'], password=env['DB_PASSWORD'],
    database='binance-data', charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor,
)

with conn.cursor() as cur:
    cur.execute("SHOW COLUMNS FROM price_stats_24h")
    print("=== price_stats_24h 字段 ===")
    for r in cur.fetchall():
        print(f"  {r['Field']:35s} {r['Type']}")

    print("\n=== 实测各字段名 ===")
    cur.execute("""
        SELECT symbol, current_price,
               price_change_pct_24h, price_change_24h,
               change_24h, change_pct_24h
        FROM price_stats_24h
        WHERE symbol IN ('BTC/USDT','ETH/USDT')
        LIMIT 5
    """)
    for r in cur.fetchall():
        for k, v in r.items():
            print(f"  {k:25s} = {v}")
        print()

conn.close()
