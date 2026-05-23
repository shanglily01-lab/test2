"""执行清理：删除 2026-05-22 之前的数据"""
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

cutoff = '2026-05-22 00:00:00'

def x(desc, sql):
    c = {**cfg, 'database': 'binance-data'}; conn = pymysql.connect(**c)
    with conn.cursor() as cur:
        affected = cur.execute(sql)
        conn.commit()
    conn.close()
    print(f"  {desc}: 删除了 {affected} 条")

print("开始清理 ...")
print(f"截止时间: {cutoff}")
print()

x("gemini_explore_verdicts", f"DELETE FROM gemini_explore_verdicts WHERE created_at < '{cutoff}'")
x("futures_positions (gemini_explore)", f"DELETE FROM futures_positions WHERE source='gemini_explore' AND open_time < '{cutoff}'")
print()
print("清理完成")
