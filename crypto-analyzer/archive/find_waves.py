#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
寻找2月1日后的明显波段 - 用于超级大脑回测分析
"""

import pymysql
import os
import sys
import io
from dotenv import load_dotenv
from decimal import Decimal

# Set console encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME')
}

conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
cursor = conn.cursor()

print('='*100)
print('找到2月1日后的明显波段 - 用于超级大脑回测分析')
print('='*100)
print()

# Get symbols with data
cursor.execute('''
    SELECT DISTINCT symbol
    FROM kline_data
    WHERE timeframe = '1h'
    AND open_time >= '2026-02-01'
    AND symbol IN ('BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT')
    ORDER BY symbol
''')
symbols = [row['symbol'] for row in cursor.fetchall()]

print(f'找到 {len(symbols)} 个币种有数据: {symbols}')
print()

for symbol in symbols[:2]:  # 只分析前2个币种，避免输出太长
    print(f'\n{"="*100}')
    print(f'{symbol} - 1H波段分析')
    print('='*100)

    # Get 1H klines
    cursor.execute('''
        SELECT
            open_time,
            open_price,
            high_price,
            low_price,
            close_price,
            volume
        FROM kline_data
        WHERE symbol = %s
        AND timeframe = '1h'
        AND open_time >= '2026-02-01'
        ORDER BY open_time ASC
    ''', (symbol,))

    klines = cursor.fetchall()

    if not klines:
        print(f'  无数据')
        continue

    print(f'  时间范围: {klines[0]["open_time"]} ~ {klines[-1]["open_time"]}')
    print(f'  K线数量: {len(klines)} 根')
    print()

    # Convert Decimal to float
    for k in klines:
        k['open_price'] = float(k['open_price'])
        k['high_price'] = float(k['high_price'])
        k['low_price'] = float(k['low_price'])
        k['close_price'] = float(k['close_price'])
        k['volume'] = float(k['volume'])

    # Find waves (simple algorithm: find highs and lows)
    waves = []
    for i in range(1, len(klines)-1):
        prev = klines[i-1]
        curr = klines[i]
        next_k = klines[i+1]

        # High point
        if curr['high_price'] > prev['high_price'] and curr['high_price'] > next_k['high_price']:
            if waves and waves[-1]['type'] == 'LOW':
                change_pct = (curr['high_price'] - waves[-1]['price']) / waves[-1]['price'] * 100
            else:
                change_pct = 0

            waves.append({
                'type': 'HIGH',
                'time': curr['open_time'],
                'price': curr['high_price'],
                'change': change_pct
            })

        # Low point
        elif curr['low_price'] < prev['low_price'] and curr['low_price'] < next_k['low_price']:
            if waves and waves[-1]['type'] == 'HIGH':
                change_pct = (curr['low_price'] - waves[-1]['price']) / waves[-1]['price'] * 100
            else:
                change_pct = 0

            waves.append({
                'type': 'LOW',
                'time': curr['open_time'],
                'price': curr['low_price'],
                'change': change_pct
            })

    # Filter significant waves (>= 3%)
    significant_waves = [w for w in waves if abs(w['change']) >= 3.0]

    print(f'  找到 {len(significant_waves)} 个明显波段 (涨跌幅>=3%):')
    print()

    if not significant_waves:
        print('    (无明显波段)')
        continue

    for i, wave in enumerate(significant_waves, 1):
        wave_type = '高点' if wave['type'] == 'HIGH' else '低点'
        change_str = f'+{wave["change"]:.2f}%' if wave['change'] > 0 else f'{wave["change"]:.2f}%'

        print(f'    {i}. [{wave_type}] {wave["time"]} | 价格: ${wave["price"]:.2f} | 变化: {change_str}')

    print()
    print(f'  最大涨幅波段:')
    max_up = max([w for w in significant_waves if w['change'] > 0], key=lambda x: x['change'], default=None)
    if max_up:
        print(f'    时间: {max_up["time"]} | 涨幅: +{max_up["change"]:.2f}% | 价格: ${max_up["price"]:.2f}')
    else:
        print('    无')

    print(f'  最大跌幅波段:')
    max_down = min([w for w in significant_waves if w['change'] < 0], key=lambda x: x['change'], default=None)
    if max_down:
        print(f'    时间: {max_down["time"]} | 跌幅: {max_down["change"]:.2f}% | 价格: ${max_down["price"]:.2f}')
    else:
        print('    无')

cursor.close()
conn.close()

print()
print('='*100)
print('波段分析完成')
print('='*100)
