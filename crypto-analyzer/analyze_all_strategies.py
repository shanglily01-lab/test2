#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')
import pymysql

db = {'host': '13.212.252.171', 'port': 3306, 'user': 'admin', 'password': 'Tonny@1000', 'database': 'binance-data'}
conn = pymysql.connect(**db, cursorclass=pymysql.cursors.DictCursor)
cursor = conn.cursor()

print("="*100)
print("ALL STRATEGIES TODAY (2026-02-04)")
print("="*100)

# 1. 震荡市策略
cursor.execute("""
    SELECT COUNT(*) as total, SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END) as wins, SUM(realized_pnl) as pnl
    FROM futures_positions
    WHERE status='closed' AND DATE(close_time)='2026-02-04'
      AND (entry_signal_type LIKE 'RANGE%%' OR entry_reason LIKE '%%震荡市%%')
""")
r = cursor.fetchone()
if r['total'] > 0:
    wr = r['wins']/r['total']*100
    print(f"\n1. RANGE STRATEGY: {r['total']} | Win: {r['wins']} ({wr:.0f}%) | PnL: ${r['pnl']:.2f}")
else:
    print(f"\n1. RANGE STRATEGY: 0 trades")

# 2. 趋势策略（超级大脑）
cursor.execute("""
    SELECT COUNT(*) as total, SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END) as wins, SUM(realized_pnl) as pnl
    FROM futures_positions
    WHERE status='closed' AND DATE(close_time)='2026-02-04'
      AND entry_signal_type NOT LIKE 'RANGE%%'
      AND entry_signal_type NOT LIKE 'RECOMMENDATION%%'
      AND entry_signal_type NOT LIKE 'REVERSAL%%'
      AND (entry_signal_type LIKE '%%+%%' OR entry_signal_type IN ('position_low', 'position_mid', 'position_high'))
""")
r = cursor.fetchone()
if r['total'] > 0:
    wr = r['wins']/r['total']*100
    print(f"2. TREND STRATEGY (Brain): {r['total']} | Win: {r['wins']} ({wr:.0f}%) | PnL: ${r['pnl']:.2f}")
else:
    print(f"2. TREND STRATEGY (Brain): 0 trades")

# 3. 反转策略
cursor.execute("""
    SELECT COUNT(*) as total, SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END) as wins, SUM(realized_pnl) as pnl
    FROM futures_positions
    WHERE status='closed' AND DATE(close_time)='2026-02-04'
      AND entry_signal_type LIKE 'REVERSAL%%'
""")
r = cursor.fetchone()
if r['total'] > 0:
    wr = r['wins']/r['total']*100
    print(f"3. REVERSAL STRATEGY: {r['total']} | Win: {r['wins']} ({wr:.0f}%) | PnL: ${r['pnl']:.2f}")
else:
    print(f"3. REVERSAL STRATEGY: 0 trades")

# 4. Unknown/其他
cursor.execute("""
    SELECT COUNT(*) as total, SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END) as wins, SUM(realized_pnl) as pnl
    FROM futures_positions
    WHERE status='closed' AND DATE(close_time)='2026-02-04'
      AND (entry_signal_type IS NULL OR entry_signal_type = 'unknown')
""")
r = cursor.fetchone()
if r['total'] > 0:
    wr = r['wins']/r['total']*100
    print(f"4. UNKNOWN: {r['total']} | Win: {r['wins']} ({wr:.0f}%) | PnL: ${r['pnl']:.2f}")
else:
    print(f"4. UNKNOWN: 0 trades")

# 详细看趋势策略的信号分布
print(f"\n" + "="*100)
print("TREND SIGNALS BREAKDOWN (Top 15 worst):")
print("="*100)
cursor.execute("""
    SELECT entry_signal_type, COUNT(*) as cnt, SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END) as wins, SUM(realized_pnl) as pnl
    FROM futures_positions
    WHERE status='closed' AND DATE(close_time)='2026-02-04'
      AND entry_signal_type NOT LIKE 'RANGE%%'
      AND entry_signal_type NOT LIKE 'RECOMMENDATION%%'
      AND entry_signal_type != 'unknown'
      AND entry_signal_type IS NOT NULL
    GROUP BY entry_signal_type
    ORDER BY pnl ASC
    LIMIT 15
""")

for row in cursor.fetchall():
    sig = (row['entry_signal_type'] or 'NULL')
    if len(sig) > 60:
        sig = sig[:60]
    cnt = row['cnt']
    w = row['wins']
    wrr = w/cnt*100 if cnt > 0 else 0
    print(f"{sig:60s} {cnt:2d} {w:2d} ({wrr:3.0f}%) ${row['pnl']:.2f}")

cursor.close()
conn.close()
