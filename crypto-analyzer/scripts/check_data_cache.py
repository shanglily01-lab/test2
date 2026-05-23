"""
检查 data_cache 数据情况 — 直接连远程数据库
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pymysql
from dotenv import dotenv_values

env = dict(dotenv_values(Path(__file__).parent.parent / ".env"))

cfg = {
    'host': env.get('DB_HOST', 'localhost'),
    'port': int(env.get('DB_PORT', 3306)),
    'user': env.get('DB_USER', 'root'),
    'password': env.get('DB_PASSWORD', ''),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
}

# 1) market_snapshot
cfg['database'] = 'data_cache'
conn = pymysql.connect(**cfg)
with conn.cursor() as cur:
    cur.execute("SELECT * FROM market_snapshot WHERE id=1")
    row = cur.fetchone()
    if row:
        print("=== market_snapshot ===")
        for k, v in row.items():
            print(f"  {k:25s} = {v}")
    else:
        print("market_snapshot: 无数据")
conn.close()

# 2) 检查源表 symbol 格式 (带 / 还是不带)
cfg['database'] = 'binance-data'
conn2 = pymysql.connect(**cfg)
with conn2.cursor() as cur:
    cur.execute("SELECT DISTINCT symbol FROM price_stats_24h WHERE symbol LIKE 'BTC%' OR symbol LIKE 'ETH%' OR symbol LIKE 'SOL%' OR symbol LIKE 'BNB%' OR symbol LIKE 'XRP%' LIMIT 10")
    symbols = [r['symbol'] for r in cur.fetchall()]
    print(f"\n=== price_stats_24h 中 BTC/ETH/SOL/BNB/XRP 的 symbol 格式 ===")
    print(f"  {symbols}")

    # 检查表是否存在
    cur.execute("SHOW TABLES LIKE 'big4_trend_history'")
    print(f"\n  big4_trend_history 表: {'存在' if cur.fetchone() else '不存在'}")

    cur.execute("SHOW TABLES LIKE 'fear_greed_index'")
    print(f"  fear_greed_index 表: {'存在' if cur.fetchone() else '不存在'}")

conn2.close()

# 3) settings_cache
cfg3 = {**cfg, 'database': 'data_cache'}
conn3 = pymysql.connect(**cfg3)
with conn3.cursor() as cur:
    cur.execute("SELECT COUNT(*) AS cnt FROM settings_cache")
    cnt = cur.fetchone()['cnt']
    print(f"\n=== settings_cache: {cnt} 条记录 ===")
    if cnt > 0:
        cur.execute("SELECT setting_key, setting_value, updated_at FROM settings_cache LIMIT 10")
        for r in cur.fetchall():
            print(f"  {r['setting_key']:40s} = {r['setting_value']}")
conn3.close()

# 4) candidate_pool
conn4 = pymysql.connect(**cfg3)
with conn4.cursor() as cur:
    cur.execute("SELECT COUNT(*) AS cnt FROM candidate_pool_snapshot")
    cnt = cur.fetchone()['cnt']
    print(f"\n=== candidate_pool_snapshot: {cnt} 条记录 ===")
    if cnt > 0:
        cur.execute("SELECT symbol, current_price, change_24h, funding_rate FROM candidate_pool_snapshot ORDER BY quote_volume_24h DESC LIMIT 5")
        for r in cur.fetchall():
            print(f"  {r['symbol']:20s} price={r['current_price']} change={r['change_24h']}")
conn4.close()
