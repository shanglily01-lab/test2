"""Gemini 数据清理 + 胜率统计"""
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

def x(db, sql, args=None):
    """执行写操作"""
    c = {**cfg, 'database': db}; conn = pymysql.connect(**c)
    with conn.cursor() as cur:
        if args is not None: affected = cur.execute(sql, args)
        else: affected = cur.execute(sql)
        conn.commit()
    conn.close(); return affected

def p(text):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('ascii', 'ignore').decode('ascii'))

cutoff = '2026-05-22 00:00:00'

p("=" * 70)
p("1. gemini_explore_verdicts - 清理前数据量")
p("=" * 70)
rows = q('binance-data', f"""
    SELECT 
        COUNT(*) AS total,
        SUM(CASE WHEN created_at < '{cutoff}' THEN 1 ELSE 0 END) AS before_cutoff,
        SUM(CASE WHEN created_at >= '{cutoff}' THEN 1 ELSE 0 END) AS after_cutoff
    FROM gemini_explore_verdicts
""")
for r in rows:
    p(f"  总共: {r['total']} 条,  < {cutoff}: {r['before_cutoff']} 条, >=: {r['after_cutoff']} 条")

p("\n" + "=" * 70)
p("2. gemini_explore_verdicts - 按 action_taken 分布 (< 5-22)")
p("=" * 70)
rows = q('binance-data', f"""
    SELECT action_taken, COUNT(*) AS cnt
    FROM gemini_explore_verdicts
    WHERE created_at < '{cutoff}'
    GROUP BY action_taken
    ORDER BY cnt DESC
""")
for r in rows:
    p(f"  {str(r['action_taken']):20s} {r['cnt']} 条")

p("\n" + "=" * 70)
p("3. futures_positions source=gemini_explore - 清理前")
p("=" * 70)
rows = q('binance-data', f"""
    SELECT 
        COUNT(*) AS total,
        SUM(CASE WHEN open_time < '{cutoff}' THEN 1 ELSE 0 END) AS before_cutoff,
        SUM(CASE WHEN open_time >= '{cutoff}' THEN 1 ELSE 0 END) AS after_cutoff
    FROM futures_positions
    WHERE source='gemini_explore'
""")
for r in rows:
    p(f"  总共: {r['total']} 条,  < {cutoff}: {r['before_cutoff']} 条, >=: {r['after_cutoff']} 条")

p("")
rows = q('binance-data', f"""
    SELECT status, COUNT(*) AS cnt
    FROM futures_positions
    WHERE source='gemini_explore'
      AND open_time < '{cutoff}'
    GROUP BY status
""")
for r in rows:
    p(f"  {str(r['status']):8s} {r['cnt']} 条")

p("\n" + "=" * 70)
p("4. gemini_sentiment_runs - 清理前")
p("=" * 70)
rows = q('binance-data', f"""
    SELECT COUNT(*) AS total,
        SUM(CASE WHEN asof_utc < '{cutoff}' THEN 1 ELSE 0 END) AS before_cutoff
    FROM gemini_sentiment_runs
""")
for r in rows:
    p(f"  总共: {r['total']} 条,  < {cutoff}: {r['before_cutoff']} 条")

p("\n" + "=" * 70)
p("5. gemini_predict_runs - 清理前")
p("=" * 70)
rows = q('binance-data', f"""
    SELECT COUNT(*) AS total,
        SUM(CASE WHEN asof_utc < '{cutoff}' THEN 1 ELSE 0 END) AS before_cutoff
    FROM gemini_predict_runs
""")
for r in rows:
    p(f"  总共: {r['total']} 条,  < {cutoff}: {r['before_cutoff']} 条")

p("\n" + "=" * 70)
p("6. 胜率统计 — 5月22日之后 gemini_explore")
p("=" * 70)
rows = q('binance-data', f"""
    SELECT 
        COUNT(*) AS total_closed,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
        SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) AS losses,
        ROUND(AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE NULL END), 2) AS avg_win_pnl,
        ROUND(AVG(CASE WHEN realized_pnl <= 0 THEN realized_pnl ELSE NULL END), 2) AS avg_loss_pnl,
        ROUND(SUM(realized_pnl), 2) AS total_pnl,
        ROUND(AVG(realized_pnl), 2) AS avg_pnl
    FROM futures_positions
    WHERE source='gemini_explore'
      AND open_time >= '{cutoff}'
      AND status = 'closed'
      AND realized_pnl IS NOT NULL
""")
for r in rows:
    total = r['total_closed']
    wins = r['wins']
    losses = r['losses']
    win_rate = round(wins / total * 100, 1) if total > 0 else 0
    p(f"  已平仓: {total} 单")
    p(f"  盈利: {wins} 单")
    p(f"  亏损: {losses} 单")
    p(f"  胜率: {win_rate}%")
    p(f"  平均盈利: {r['avg_win_pnl']} U")
    p(f"  平均亏损: {r['avg_loss_pnl']} U")
    p(f"  总盈亏: {r['total_pnl']} U")
    p(f"  单均盈亏: {r['avg_pnl']} U")

p("")
# 按 symbol 统计
p("按币种统计:")
rows = q('binance-data', f"""
    SELECT symbol,
        COUNT(*) AS cnt,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
        SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) AS losses,
        ROUND(SUM(realized_pnl), 2) AS total_pnl
    FROM futures_positions
    WHERE source='gemini_explore'
      AND open_time >= '{cutoff}'
      AND status = 'closed'
      AND realized_pnl IS NOT NULL
    GROUP BY symbol
    ORDER BY cnt DESC
""")
for r in rows:
    tot = r['cnt']
    w = r['wins']
    wr = round(w / tot * 100, 1) if tot > 0 else 0
    s = f"  {str(r['symbol']):12s} cnt={r['cnt']} win={w}/{r['losses']} wr={wr}% pnl={r['total_pnl']}"
    p(s)

p("\n" + "=" * 70)
p("7. 当前持仓 (gemini_explore)")
p("=" * 70)
rows = q('binance-data', """
    SELECT symbol, position_side, open_time, entry_price, unrealized_pnl, margin
    FROM futures_positions
    WHERE source='gemini_explore'
      AND status = 'open'
    ORDER BY open_time DESC
""")
if rows:
    for r in rows:
        upnl = r['unrealized_pnl'] or 0
        p(f"  {str(r['symbol']):12s} {str(r['position_side']):6s} open={str(r['open_time'])} "
          f"entry={r['entry_price']} upnl={upnl:+.2f}")
else:
    p("  暂无持仓")

p("\n" + "=" * 70)
p("8. DELETE SQL (只读，如需执行请确认)")
p("=" * 70)
print(f"DELETE FROM gemini_explore_verdicts WHERE created_at < '{cutoff}';")
print(f"-- 影响: 上一步已显示数量")
print(f"DELETE FROM futures_positions WHERE source='gemini_explore' AND open_time < '{cutoff}';")
print(f"DELETE FROM gemini_sentiment_runs WHERE asof_utc < '{cutoff}';")

p("\n" + "=" * 70)
p("9. gemini_predict 胜率 (5月22日后)")
p("=" * 70)
rows = q('binance-data', f"""
    SELECT 
        COUNT(*) AS total_runs,
        SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS ok_runs,
        SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS error_runs
    FROM gemini_predict_runs
    WHERE asof_utc >= '{cutoff}'
""")
for r in rows:
    p(f"  总运行: {r['total_runs']}, 成功: {r['ok_runs']}, 错误: {r['error_runs']}")
