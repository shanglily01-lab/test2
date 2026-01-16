#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import io
import mysql.connector
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB_CONFIG = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

conn = mysql.connector.connect(**DB_CONFIG)
cursor = conn.cursor(dictionary=True)

yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

query = '''
SELECT
    symbol,
    position_side,
    entry_price,
    mark_price,
    entry_ema_diff,
    realized_pnl,
    TIMESTAMPDIFF(MINUTE, open_time, close_time) as holding_minutes,
    notes
FROM futures_positions
WHERE status = 'CLOSED'
    AND DATE(close_time) = %s
    AND entry_signal_type IN ('sustained_trend', 'sustained_trend_entry')
ORDER BY close_time ASC
'''

cursor.execute(query, (yesterday,))
positions = cursor.fetchall()

print('='*100)
print(f'V3策略问题分析 - {yesterday}')
print('='*100)
print()

if not positions:
    print('没有V3交易记录')
else:
    print(f'总计: {len(positions)}笔交易\n')

    ema_diffs = []

    for i, pos in enumerate(positions, 1):
        ema_diff = float(pos['entry_ema_diff'] or 0)
        pnl = float(pos['realized_pnl'] or 0)

        ema_diffs.append(ema_diff)

        reason = pos['notes'] or ''
        if '|' in reason:
            reason = reason.split('|')[0]
        reason = reason[:30]

        print(f'{i:2d}. {pos["symbol"]:<15} {pos["position_side"]:<5} | '
              f'EMA差值: {ema_diff:>7.4f}% | '
              f'盈亏: ${pnl:>7.2f} | '
              f'{reason}')

    print()
    print('='*100)
    print('统计分析:')
    print(f'平均入场EMA差值: {sum(ema_diffs)/len(ema_diffs):.4f}%')
    print(f'最小入场EMA差值: {min(ema_diffs):.4f}%')
    print(f'最大入场EMA差值: {max(ema_diffs):.4f}%')
    print()

    # 统计<0.5%的入场
    weak_entries = [d for d in ema_diffs if abs(d) < 0.5]
    print(f'入场EMA差值<0.5%的交易: {len(weak_entries)}笔 ({len(weak_entries)/len(ema_diffs)*100:.1f}%)')

    # 统计<1.0%的入场
    weak_entries_1 = [d for d in ema_diffs if abs(d) < 1.0]
    print(f'入场EMA差值<1.0%的交易: {len(weak_entries_1)}笔 ({len(weak_entries_1)/len(ema_diffs)*100:.1f}%)')

cursor.close()
conn.close()
