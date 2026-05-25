"""诊断 Gemini Explore 开单问题（无GBK编码问题）"""
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

def p(text):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('ascii', 'ignore').decode('ascii'))

p("=" * 60)
p("1. 最新 gemini_explore_verdicts")
p("=" * 60)
rows = q('binance-data', """
    SELECT id, symbol, confidence, action_taken, skip_reason, run_id, created_at
    FROM gemini_explore_verdicts
    ORDER BY id DESC LIMIT 20
""")
for r in rows:
    run = str(r['run_id']) if r['run_id'] is not None else 'NULL'
    conf = str(r['confidence']) if r['confidence'] is not None else 'NULL'
    p(f"  id={str(r['id']):>5s} run={run:>4s} {str(r['symbol']):12s} "
      f"conf={conf:>6s} {str(r['action_taken']):15s} "
      f"time={str(r['created_at'])}")

p("\n" + "=" * 60)
p("2. 今天 gemini_explore 的 position 记录")
p("=" * 60)
rows = q('binance-data', """
    SELECT id, symbol, position_side, status, open_time,
           realized_pnl, notes, close_time
    FROM futures_positions
    WHERE source='gemini_explore'
      AND open_time >= '2026-05-23 00:00:00'
    ORDER BY open_time DESC
""")
if rows:
    for r in rows:
        pnl = str(r['realized_pnl']) if r['realized_pnl'] else 'N/A'
        note = str(r['notes']) if r['notes'] else ''
        p(f"  id={str(r['id']):>5s} {str(r['symbol']):12s} {str(r['position_side']):6s} "
          f"{str(r['status']):8s} open={str(r['open_time'])} pnl={pnl}")
else:
    p("  [空] 今天没有 gemini_explore position 记录")

p("\n" + "=" * 60)
p("3. scheduler 的心跳 — market_snapshot 最后更新")
p("=" * 60)
rows = q('data_cache', "SELECT MAX(updated_at) AS last_update FROM market_snapshot")
for r in rows:
    p(f"  market_snapshot 最后更新: {r['last_update']}")

p("\n" + "=" * 60)
p("4. 各来源今天开单总数")
p("=" * 60)
rows = q('binance-data', """
    SELECT source, COUNT(*) AS cnt
    FROM futures_positions
    WHERE open_time >= '2026-05-23 00:00:00'
    GROUP BY source
    ORDER BY cnt DESC
""")
for r in rows:
    p(f"  {str(r['source']):25s} 开 {str(r['cnt']):>3s} 单")

p("\n" + "=" * 60)
p("5. 今天有没有任何 gemini 轮次被触发？")
p("=" * 60)
rows = q('binance-data', """
    SELECT COUNT(*) AS cnt, MIN(created_at) AS first_t, MAX(created_at) AS last_t
    FROM gemini_explore_verdicts
    WHERE created_at >= '2026-05-23 00:00:00'
""")
for r in rows:
    p(f"  今天共有 {r['cnt']} 条 verdict, "
      f"首条: {r['first_t']}, 末条: {r['last_t']}")

p("\n" + "=" * 60)
p("6. 如果今天没有 verdict — 查看 scheduler 最后执行记录")
p("=" * 60)
rows = q('binance-data', """
    SELECT MAX(updated_at) AS last_update
    FROM data_cache.candidate_pool_snapshot
""")
for r in rows:
    p(f"  candidate_pool 最后更新: {r['last_update']}")

p("\n" + "=" * 60)
p("7. 建议")
p("=" * 60)
p("  1) 检查 scheduler 是否还在运行: ps aux | grep scheduler")
p("  2) 检查 scheduler 日志: journalctl -u crypto-scheduler --since '6 hours ago'")
p("  3) 如果有报错，可能需要重启: sudo systemctl restart crypto-scheduler")
