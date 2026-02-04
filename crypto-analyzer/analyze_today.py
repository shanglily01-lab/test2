#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')
import pymysql

db_config = {'host': '13.212.252.171', 'port': 3306, 'user': 'admin', 'password': 'Tonny@1000', 'database': 'binance-data'}
conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
cursor = conn.cursor()

print("="*120)
print("TODAY TRADING ANALYSIS (2026-02-04)")
print("="*120)

# Overall
cursor.execute("""
    SELECT COUNT(*) as total, SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
           SUM(realized_pnl) as total_pnl, SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END) as profit,
           SUM(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE 0 END) as loss
    FROM futures_positions WHERE status = 'closed' AND DATE(close_time) = '2026-02-04'
""")
s = cursor.fetchone()
total = s['total']
wins = s['wins']
losses = total - wins
wr = wins/total*100 if total > 0 else 0
pf = abs(s['profit']/s['loss']) if s['loss'] != 0 else 0

print(f"\n1. OVERALL: {total} trades | Win: {wins} ({wr:.1f}%) | Loss: {losses} | PnL: ${s['total_pnl']:.2f}")
print(f"   Profit: ${s['profit']:.2f} | Loss: ${s['loss']:.2f} | Profit Factor: {pf:.2f}")

# By direction
print(f"\n2. BY DIRECTION:")
cursor.execute("""
    SELECT position_side, COUNT(*) as cnt, SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END) as wins, SUM(realized_pnl) as pnl
    FROM futures_positions WHERE status='closed' AND DATE(close_time)='2026-02-04' GROUP BY position_side
""")
for r in cursor.fetchall():
    cnt = r['cnt']
    w = r['wins']
    wr2 = w/cnt*100 if cnt > 0 else 0
    print(f"   {r['position_side']:5s}: {cnt:3d} | Win: {w:2d} ({wr2:5.1f}%) | PnL: ${r['pnl']:.2f}")

# Top losing signals
print(f"\n3. TOP 20 LOSING SIGNALS:")
cursor.execute("""
    SELECT entry_signal_type, COUNT(*) as cnt, SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END) as wins, SUM(realized_pnl) as pnl
    FROM futures_positions WHERE status='closed' AND DATE(close_time)='2026-02-04' GROUP BY entry_signal_type ORDER BY pnl ASC LIMIT 20
""")
for r in cursor.fetchall():
    sig = (r['entry_signal_type'] or 'NULL')[:50]
    cnt = r['cnt']
    w = r['wins']
    wr3 = w/cnt*100 if cnt > 0 else 0
    print(f"   {sig:50s} | {cnt:3d} | {w:2d} ({wr3:4.0f}%) | ${r['pnl']:.2f}")

# Close reasons
print(f"\n4. CLOSE REASONS:")
cursor.execute("""
    SELECT CASE WHEN close_reason LIKE '%止损%' THEN 'Stop Loss'
                WHEN close_reason LIKE '%止盈%' THEN 'Take Profit'
                WHEN close_reason LIKE '%时长%' THEN 'Timeout'
                WHEN close_reason LIKE '%反转%' THEN 'Reversal'
                ELSE 'Other' END as reason,
           COUNT(*) as cnt, SUM(realized_pnl) as pnl
    FROM futures_positions WHERE status='closed' AND DATE(close_time)='2026-02-04' GROUP BY reason ORDER BY pnl ASC
""")
for r in cursor.fetchall():
    print(f"   {r['reason']:15s}: {r['cnt']:3d} trades | ${r['pnl']:.2f}")

# Worst trades
print(f"\n5. WORST 15 TRADES:")
cursor.execute("""
    SELECT symbol, position_side, entry_signal_type, realized_pnl, roi, close_reason, 
           TIMESTAMPDIFF(MINUTE, open_time, close_time) as mins
    FROM futures_positions WHERE status='closed' AND DATE(close_time)='2026-02-04' ORDER BY realized_pnl ASC LIMIT 15
""")
for t in cursor.fetchall():
    sig = (t['entry_signal_type'] or 'NULL')[:28]
    rsn = (t['close_reason'] or '')[:30]
    print(f"   {t['symbol']:13s} {t['position_side']:5s} | ${t['realized_pnl']:7.2f} ({t['roi']:6.1f}%) | {t['mins']:4d}m | {sig:28s} | {rsn}")

cursor.close()
conn.close()
