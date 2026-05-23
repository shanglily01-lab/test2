"""
检查远程 data_cache 数据 — 直接连 18.136.125.125
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pymysql
from dotenv import dotenv_values

env = dict(dotenv_values(Path(__file__).parent.parent / ".env"))

cfg = {
    'host': env['DB_HOST'],
    'port': int(env['DB_PORT']),
    'user': env['DB_USER'],
    'password': env['DB_PASSWORD'],
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
    'connect_timeout': 10,
}

def query(db, sql, args=None):
    c = {**cfg, 'database': db}
    conn = pymysql.connect(**c)
    with conn.cursor() as cur:
        cur.execute(sql, args or ())
        rows = cur.fetchall()
    conn.close()
    return rows

# 1) market_snapshot
rows = query('data_cache', 'SELECT * FROM market_snapshot WHERE id=1')
print("\n=== market_snapshot ===")
if rows:
    for k, v in rows[0].items():
        print(f"  {k:25s} = {v}")
else:
    print("  (空)")

# 2) settings_cache
rows = query('data_cache', 'SELECT COUNT(*) AS cnt FROM settings_cache')
cnt = rows[0]['cnt']
print(f"\n=== settings_cache: {cnt} 条 ===")
if cnt:
    rows = query('data_cache', 'SELECT setting_key, setting_value, updated_at FROM settings_cache LIMIT 5')
    for r in rows:
        print(f"  {r['setting_key']:40s} = {r['setting_value']}")

# 3) candidate_pool
rows = query('data_cache', 'SELECT COUNT(*) AS cnt FROM candidate_pool_snapshot')
print(f"\n=== candidate_pool_snapshot: {rows[0]['cnt']} 条 ===")

# 4) market_movers
rows = query('data_cache', 'SELECT COUNT(*) AS cnt FROM market_movers_snapshot')
print(f"\n=== market_movers_snapshot: {rows[0]['cnt']} 条 ===")

# 5) position_stats
rows = query('data_cache', 'SELECT COUNT(*) AS cnt FROM position_stats_snapshot')
print(f"\n=== position_stats_snapshot: {rows[0]['cnt']} 条 ===")

# 6) 检查 scheduler 最近的缓存刷新日志有没有报错
print(f"\n=== 当前时间: {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
