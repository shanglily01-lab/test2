"""
诊断 Gemini 探索不开单的原因 — 直接连远程数据库
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pymysql
from dotenv import dotenv_values
from pymysql.cursors import DictCursor

env = dict(dotenv_values(Path(__file__).parent.parent / ".env"))

cfg = {
    'host': env['DB_HOST'], 'port': int(env['DB_PORT']),
    'user': env['DB_USER'], 'password': env['DB_PASSWORD'],
    'charset': 'utf8mb4', 'cursorclass': DictCursor,
}

def q(db, sql, args=None):
    c = {**cfg, 'database': db}
    conn = pymysql.connect(**c)
    with conn.cursor() as cur:
        cur.execute(sql, args or ())
        rows = cur.fetchall()
    conn.close()
    return rows

print("=" * 60)
print("1. 关键系统设置")
print("=" * 60)
rows = q('binance-data', """
    SELECT setting_key, setting_value, updated_at
    FROM system_settings
    WHERE setting_key IN (
        'gemini_explore_enabled',
        'allow_long', 'allow_short',
        'big4_filter_enabled',
        'u_futures_trading_enabled'
    )
    ORDER BY setting_key
""")
for r in rows:
    print(f"  {r['setting_key']:30s} = {r['setting_value']}")

print("\n" + "=" * 60)
print("2. 最近 10 条 gemini_explore 的 verdict（决策记录）")
print("=" * 60)
try:
    rows = q('binance-data', """
        SELECT id, symbol, confidence, action_taken, skip_reason,
               created_at
        FROM gemini_explore_verdicts
        ORDER BY id DESC LIMIT 10
    """)
    if rows:
        for r in rows:
            print(f"  id={r['id']:5d} | {r['symbol']:12s} | "
                  f"conf={str(r['confidence']):6s} | "
                  f"action={str(r['action_taken']):8s} | "
                  f"reason={r['skip_reason'] or ''}")
    else:
        print("  (gemini_explore_verdicts 表无数据)")
except Exception as e:
    print(f"  查 verdicts 表失败: {e}")

print("\n" + "=" * 60)
print("3. 最近一轮 gemini explore 任务的 run_id")
print("=" * 60)
try:
    rows = q('binance-data', """
        SELECT id, symbol, run_id, created_at
        FROM gemini_explore_verdicts
        ORDER BY id DESC LIMIT 1
    """)
    if rows:
        run_id = rows[0].get('run_id')
        print(f"  最新 run_id = {run_id}")
        if run_id:
            rows2 = q('binance-data', """
                SELECT action_taken, COUNT(*) AS cnt
                FROM gemini_explore_verdicts
                WHERE run_id = %s
                GROUP BY action_taken
            """, (run_id,))
            print(f"  该轮决策统计: {dict((r['action_taken'], r['cnt']) for r in rows2)}")
except Exception as e:
    print(f"  查 run_id 失败: {e}")

print("\n" + "=" * 60)
print("4. Big4RegimeMonitor 最新信号")
print("=" * 60)
try:
    rows = q('binance-data', """
        SELECT overall_signal, created_at
        FROM big4_trend_history
        ORDER BY created_at DESC LIMIT 1
    """)
    if rows:
        print(f"  signal={rows[0]['overall_signal']} at {rows[0]['created_at']}")
except Exception as e:
    print(f"  查 Big4 失败: {e}")

print("\n" + "=" * 60)
print("5. check allow_long/allow_short 是否被 Big4RegimeMonitor 覆写")
print("=" * 60)
try:
    rows = q('binance-data', """
        SELECT setting_key, setting_value
        FROM system_settings
        WHERE setting_key IN ('allow_long','allow_short')
    """)
    for r in rows:
        print(f"  {r['setting_key']:20s} = {r['setting_value']}")
except Exception as e:
    print(f"  查 allow 失败: {e}")

print("\n" + "=" * 60)
print("6. gemini_explore 当前持仓数")
print("=" * 60)
try:
    rows = q('binance-data', """
        SELECT COUNT(*) AS cnt
        FROM futures_positions
        WHERE source='gemini_explore' AND status='open'
    """)
    print(f"  gemini_explore 当前持仓: {rows[0]['cnt']} 个")
except Exception as e:
    print(f"  查持仓失败: {e}")
