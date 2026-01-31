#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析24H内所有信号组件的盈利和亏损表现
"""

import pymysql
import os
from dotenv import load_dotenv
from collections import defaultdict
import json

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'),
    cursorclass=pymysql.cursors.DictCursor,
    connect_timeout=30,
    read_timeout=30,
    write_timeout=30
)

cursor = conn.cursor()

# 获取24H内所有已平仓的持仓
cursor.execute('''
    SELECT
        symbol,
        position_side,
        entry_score,
        signal_components,
        realized_pnl,
        open_time,
        close_time
    FROM futures_positions
    WHERE status = 'CLOSED'
    AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
    ORDER BY close_time DESC
''')

positions = cursor.fetchall()

print('24H Trading Performance Analysis')
print('='*100)
print(f'Total Trades: {len(positions)}')

if not positions:
    print('No trades in last 24H')
    cursor.close()
    conn.close()
    exit()

# 统计总体数据
total_pnl = sum(float(p['realized_pnl']) for p in positions)
profit_trades = [p for p in positions if float(p['realized_pnl']) > 0]
loss_trades = [p for p in positions if float(p['realized_pnl']) < 0]

print(f'Win Rate: {len(profit_trades)}/{len(positions)} ({len(profit_trades)/len(positions)*100:.1f}%)')
print(f'Total PnL: {total_pnl:.2f} USDT')
print()

# 按信号组件统计
component_stats = defaultdict(lambda: {
    'count': 0,
    'wins': 0,
    'losses': 0,
    'profit': 0,
    'loss': 0,
    'total_pnl': 0,
    'trades': []
})

for pos in positions:
    pnl = float(pos['realized_pnl'])
    components = pos['signal_components']

    if components:
        try:
            comp_dict = json.loads(components)
            for comp_name, comp_score in comp_dict.items():
                component_stats[comp_name]['count'] += 1
                component_stats[comp_name]['total_pnl'] += pnl
                component_stats[comp_name]['trades'].append({
                    'symbol': pos['symbol'],
                    'side': pos['position_side'],
                    'pnl': pnl,
                    'score': float(comp_score)
                })

                if pnl > 0:
                    component_stats[comp_name]['wins'] += 1
                    component_stats[comp_name]['profit'] += pnl
                else:
                    component_stats[comp_name]['losses'] += 1
                    component_stats[comp_name]['loss'] += pnl
        except:
            pass

# 按净盈亏排序显示
print('Signal Component Performance (sorted by Net PnL):')
print('='*100)
print(f'{"Component":<30} {"Count":<8} {"Win%":<8} {"Profit":<12} {"Loss":<12} {"Net PnL":<12} {"Avg":<10}')
print('-'*100)

sorted_components = sorted(component_stats.items(), key=lambda x: x[1]['total_pnl'], reverse=True)

for comp_name, stats in sorted_components:
    avg_pnl = stats['total_pnl'] / stats['count'] if stats['count'] > 0 else 0
    win_rate = stats['wins'] / stats['count'] * 100 if stats['count'] > 0 else 0
    print(f'{comp_name:<30} {stats["count"]:<8} {win_rate:<7.1f}% {stats["profit"]:>11.2f} {stats["loss"]:>11.2f} {stats["total_pnl"]:>11.2f} {avg_pnl:>9.2f}')

print()
print('='*100)
print('Top 5 Profitable Components:')
print('-'*100)
for i, (comp_name, stats) in enumerate(sorted_components[:5], 1):
    avg = stats['total_pnl'] / stats['count']
    win_rate = stats['wins'] / stats['count'] * 100
    print(f'{i}. {comp_name}: {stats["total_pnl"]:+.2f} USDT ({stats["count"]} trades, {win_rate:.1f}% win, avg {avg:+.2f})')

print()
print('Top 5 Loss-Making Components:')
print('-'*100)
for i, (comp_name, stats) in enumerate(sorted_components[-5:][::-1], 1):
    avg = stats['total_pnl'] / stats['count']
    win_rate = stats['wins'] / stats['count'] * 100
    print(f'{i}. {comp_name}: {stats["total_pnl"]:+.2f} USDT ({stats["count"]} trades, {win_rate:.1f}% win, avg {avg:+.2f})')

# 分析LONG vs SHORT
print()
print('='*100)
print('LONG vs SHORT Performance:')
print('-'*100)

long_trades = [p for p in positions if p['position_side'] == 'LONG']
short_trades = [p for p in positions if p['position_side'] == 'SHORT']

if long_trades:
    long_pnl = sum(float(p['realized_pnl']) for p in long_trades)
    long_wins = len([p for p in long_trades if float(p['realized_pnl']) > 0])
    print(f'LONG:  {len(long_trades)} trades, {long_wins}/{len(long_trades)} wins ({long_wins/len(long_trades)*100:.1f}%), PnL: {long_pnl:+.2f} USDT')
else:
    print('LONG:  0 trades')

if short_trades:
    short_pnl = sum(float(p['realized_pnl']) for p in short_trades)
    short_wins = len([p for p in short_trades if float(p['realized_pnl']) > 0])
    print(f'SHORT: {len(short_trades)} trades, {short_wins}/{len(short_trades)} wins ({short_wins/len(short_trades)*100:.1f}%), PnL: {short_pnl:+.2f} USDT')
else:
    print('SHORT: 0 trades')

cursor.close()
conn.close()
