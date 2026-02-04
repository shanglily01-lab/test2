#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
import pymysql

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
cursor = conn.cursor()

print("=" * 120)
print("TODAY'S TRADING ANALYSIS (2026-02-04)")
print("=" * 120)

# 1. Overall stats
cursor.execute("""
    SELECT 
        COUNT(*) as total,
        SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(pnl) as total_pnl,
        AVG(pnl) as avg_pnl,
        SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as total_profit,
        SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END) as total_loss
    FROM futures_positions
    WHERE status = 'closed' AND DATE(close_time) = '2026-02-04'
""")

stats = cursor.fetchone()
total = stats['total']
wins = stats['wins']
losses = total - wins
wr = (wins / total * 100) if total > 0 else 0

print(f"\n1. OVERALL: {total} trades | Win: {wins} ({wr:.1f}%) | Loss: {losses}")
print(f"   Total PnL: ${stats['total_pnl']:.2f}")
print(f"   Profit: ${stats['total_profit']:.2f} | Loss: ${stats['total_loss']:.2f}")
if stats['total_loss'] != 0:
    print(f"   Profit Factor: {abs(stats['total_profit']/stats['total_loss']):.2f}")

# 2. By direction
print(f"\n2. BY DIRECTION:")
cursor.execute("""
    SELECT 
        position_side,
        COUNT(*) as trades,
        SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(pnl) as pnl
    FROM futures_positions
    WHERE status = 'closed' AND DATE(close_time) = '2026-02-04'
    GROUP BY position_side
""")

for row in cursor.fetchall():
    trades = row['trades']
    wins = row['wins']
    wr = (wins / trades * 100) if trades > 0 else 0
    print(f"   {row['position_side']:5s}: {trades:3d} trades | Win: {wins:2d} ({wr:5.1f}%) | PnL: ${row['pnl']:8.2f}")

# 3. By signal type (top losers)
print(f"\n3. BY SIGNAL TYPE (Top 15 losers):")
cursor.execute("""
    SELECT 
        entry_signal_type,
        COUNT(*) as trades,
        SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(pnl) as pnl
    FROM futures_positions
    WHERE status = 'closed' AND DATE(close_time) = '2026-02-04'
    GROUP BY entry_signal_type
    ORDER BY pnl ASC
    LIMIT 15
""")

for row in cursor.fetchall():
    signal = (row['entry_signal_type'] or 'NULL')[:50]
    trades = row['trades']
    wins = row['wins']
    wr = (wins / trades * 100) if trades > 0 else 0
    print(f"   {signal:50s} | {trades:3d} trades | Win: {wins:2d} ({wr:4.0f}%) | PnL: ${row['pnl']:7.2f}")

# 4. Close reasons
print(f"\n4. CLOSE REASONS:")
cursor.execute("""
    SELECT 
        CASE 
            WHEN close_reason LIKE '%止损%' THEN 'Stop Loss'
            WHEN close_reason LIKE '%止盈%' THEN 'Take Profit'
            WHEN close_reason LIKE '%时长到期%' THEN 'Timeout'
            WHEN close_reason LIKE '%方向反转%' THEN 'Reversal'
            WHEN close_reason LIKE '%强制%' THEN 'Force Close'
            ELSE 'Other'
        END as reason,
        COUNT(*) as trades,
        SUM(pnl) as pnl
    FROM futures_positions
    WHERE status = 'closed' AND DATE(close_time) = '2026-02-04'
    GROUP BY reason
    ORDER BY pnl ASC
""")

for row in cursor.fetchall():
    print(f"   {row['reason']:15s}: {row['trades']:3d} trades | PnL: ${row['pnl']:8.2f}")

# 5. Worst trades
print(f"\n5. WORST 15 TRADES:")
cursor.execute("""
    SELECT 
        symbol, position_side, entry_signal_type, pnl, roi,
        SUBSTRING(close_reason, 1, 40) as reason,
        TIMESTAMPDIFF(MINUTE, open_time, close_time) as minutes
    FROM futures_positions
    WHERE status = 'closed' AND DATE(close_time) = '2026-02-04'
    ORDER BY pnl ASC
    LIMIT 15
""")

for t in cursor.fetchall():
    signal = (t['entry_signal_type'] or 'NULL')[:35]
    print(f"   {t['symbol']:13s} {t['position_side']:5s} | ${t['pnl']:7.2f} ({t['roi']:6.1f}%) | {t['minutes']:4d}m | {signal:35s} | {t['reason']}")

# 6. Best trades (for comparison)
print(f"\n6. BEST 10 TRADES:")
cursor.execute("""
    SELECT 
        symbol, position_side, entry_signal_type, pnl, roi,
        SUBSTRING(close_reason, 1, 35) as reason
    FROM futures_positions
    WHERE status = 'closed' AND DATE(close_time) = '2026-02-04'
    ORDER BY pnl DESC
    LIMIT 10
""")

for t in cursor.fetchall():
    signal = (t['entry_signal_type'] or 'NULL')[:35]
    print(f"   {t['symbol']:13s} {t['position_side']:5s} | ${t['pnl']:7.2f} ({t['roi']:6.1f}%) | {signal:35s} | {t['reason']}")

cursor.close()
conn.close()
