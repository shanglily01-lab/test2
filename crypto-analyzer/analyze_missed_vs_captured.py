#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析错过的信号 vs 实际捕获的信号"""

import pymysql
import os
from dotenv import load_dotenv

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'),
    cursorclass=pymysql.cursors.DictCursor,
    charset='utf8mb4'
)

cursor = conn.cursor()

print('=' * 100)
print('错过的信号 vs 实际捕获的信号对比分析')
print('=' * 100)
print()

# 错过的高质量信号（用户提供的数据）
missed_signals = [
    {'symbol': 'XTZ/USDT', 'strength': 14.0, 'net_1h': -8, 'net_15m': -12, 'net_5m': -42},
    {'symbol': 'FIGHT/USDT', 'strength': 13.0, 'net_1h': -7, 'net_15m': -12, 'net_5m': -27},
    {'symbol': 'PUMP/USDT', 'strength': 12.5, 'net_1h': -7, 'net_15m': -11, 'net_5m': -15},
    {'symbol': 'SAND/USDT', 'strength': 12.5, 'net_1h': -8, 'net_15m': -9, 'net_5m': -27},
    {'symbol': 'INJ/USDT', 'strength': 12.0, 'net_1h': -6, 'net_15m': -12, 'net_5m': -22},
    {'symbol': 'DUSK/USDT', 'strength': 10.5, 'net_1h': -7, 'net_15m': -7, 'net_5m': -17},
    {'symbol': 'ADA/USD', 'strength': 9.5, 'net_1h': -7, 'net_15m': -5, 'net_5m': -8},
    {'symbol': 'CHZ/USDT', 'strength': 9.5, 'net_1h': -6, 'net_15m': -7, 'net_5m': -6},
    {'symbol': 'DOT/USD', 'strength': 9.0, 'net_1h': -6, 'net_15m': -6, 'net_5m': -24},
    {'symbol': 'BNB/USD', 'strength': 8.5, 'net_1h': -5, 'net_15m': -7, 'net_5m': -16},
    {'symbol': 'XRP/USD', 'strength': 7.5, 'net_1h': -5, 'net_15m': -5, 'net_5m': -8},
    {'symbol': 'LINK/USDT', 'strength': 7.0, 'net_1h': -5, 'net_15m': -4, 'net_5m': -22},
    {'symbol': 'SOL/USD', 'strength': 6.5, 'net_1h': -5, 'net_15m': -3, 'net_5m': -8},
]

print(f'1. 错过的信号统计 ({len(missed_signals)}个):')
print(f"   平均信号强度: {sum(s['strength'] for s in missed_signals) / len(missed_signals):.1f}")
print(f"   平均1H净力量: {sum(s['net_1h'] for s in missed_signals) / len(missed_signals):.1f}")
print(f"   平均5M净力量: {sum(s['net_5m'] for s in missed_signals) / len(missed_signals):.1f}")
print()

# 检查这些币种是否有过开仓记录
print('2. 检查错过的币种是否曾经开过仓:')
for signal in missed_signals:
    symbol = signal['symbol']
    cursor.execute('''
        SELECT COUNT(*) as count,
               MAX(open_time) as last_open_time,
               SUM(CASE WHEN position_side = 'SHORT' THEN 1 ELSE 0 END) as short_count
        FROM futures_positions
        WHERE symbol = %s
    ''', (symbol,))

    result = cursor.fetchone()
    if result['count'] > 0:
        print(f"   {symbol:<15} - 开仓过{result['count']}次 (SHORT:{result['short_count']}次), 最后开仓: {result['last_open_time']}")
    else:
        print(f"   {symbol:<15} - 从未开仓 ❌")

print()

# 查看实际捕获的SHORT信号的币种
print('3. 最近实际开仓的SHORT币种 (最近24小时):')
cursor.execute('''
    SELECT symbol, COUNT(*) as count
    FROM futures_positions
    WHERE position_side = 'SHORT'
    AND open_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
    GROUP BY symbol
    ORDER BY count DESC
''')

recent_shorts = cursor.fetchall()
if recent_shorts:
    for s in recent_shorts:
        print(f"   {s['symbol']:<15} - {s['count']}次")
else:
    print('   最近24小时没有开SHORT仓')

print()

# 对比：错过的币种 vs 实际开仓的币种
missed_symbols = set(s['symbol'] for s in missed_signals)
cursor.execute('''
    SELECT DISTINCT symbol
    FROM futures_positions
    WHERE position_side = 'SHORT'
''')
captured_symbols = set(row['symbol'] for row in cursor.fetchall())

print('4. 币种对比:')
print(f"   错过的币种数: {len(missed_symbols)}")
print(f"   实际开过SHORT的币种数: {len(captured_symbols)}")
print(f"   交集（错过但曾开过）: {len(missed_symbols & captured_symbols)}")
print(f"   错过且从未开过: {len(missed_symbols - captured_symbols)}")
print()

never_opened = missed_symbols - captured_symbols
if never_opened:
    print('   从未开仓的币种:')
    for sym in sorted(never_opened):
        print(f"      - {sym}")

print()
print('=' * 100)

cursor.close()
conn.close()
