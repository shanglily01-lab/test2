#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析信号质量 - 检查是否存在误判"""

import pymysql
import sys
import io
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from collections import defaultdict
import os

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

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

print('=' * 120)
print('信号质量分析 - 检测误判问题')
print('=' * 120)
print()

# 分析最近24小时的信号表现
time_24h_ago = datetime.utcnow() - timedelta(hours=24)

cursor.execute('''
    SELECT
        signal_components,
        position_side,
        entry_score,
        realized_pnl,
        symbol,
        TIMESTAMPDIFF(MINUTE, created_at, close_time) as hold_minutes
    FROM futures_positions
    WHERE status = 'closed'
      AND close_time >= %s
      AND signal_components IS NOT NULL
''', (time_24h_ago,))

positions = cursor.fetchall()

# 按信号组合统计
signal_stats = defaultdict(lambda: {
    'count': 0,
    'win': 0,
    'loss': 0,
    'total_pnl': 0,
    'avg_score': 0,
    'scores': [],
    'symbols': set(),
    'avg_hold_minutes': 0,
    'hold_times': []
})

for pos in positions:
    if not pos['signal_components']:
        continue

    components = json.loads(pos['signal_components'])
    sorted_signals = sorted(components.keys())
    signal_key = ' + '.join(sorted_signals)
    full_key = f"{signal_key}_{pos['position_side']}"

    signal_stats[full_key]['count'] += 1
    signal_stats[full_key]['scores'].append(pos['entry_score'] or 0)
    signal_stats[full_key]['symbols'].add(pos['symbol'])
    signal_stats[full_key]['hold_times'].append(pos['hold_minutes'] or 0)

    pnl = pos['realized_pnl'] or 0
    signal_stats[full_key]['total_pnl'] += pnl

    if pnl > 0:
        signal_stats[full_key]['win'] += 1
    else:
        signal_stats[full_key]['loss'] += 1

# 计算平均值
for key in signal_stats:
    stats = signal_stats[key]
    stats['avg_score'] = sum(stats['scores']) / len(stats['scores']) if stats['scores'] else 0
    stats['avg_hold_minutes'] = sum(stats['hold_times']) / len(stats['hold_times']) if stats['hold_times'] else 0
    stats['win_rate'] = (stats['win'] / stats['count'] * 100) if stats['count'] > 0 else 0

# 按盈亏排序
sorted_signals = sorted(signal_stats.items(), key=lambda x: x[1]['total_pnl'])

print('=' * 120)
print('信号组合表现分析 (最近24H)')
print('=' * 120)
print()

print('### 📉 表现最差的信号 (TOP 10)')
print(f"{'信号组合':<70} {'交易':<6} {'胜率':<8} {'总盈亏':<12} {'平均分':<8}")
print('-' * 120)

worst_signals = []
for signal_key, stats in sorted_signals[:10]:
    win_rate = stats['win_rate']
    print(f"{signal_key[:68]:<70} {stats['count']:<6} {win_rate:<7.1f}% ${stats['total_pnl']:<11.2f} {stats['avg_score']:<8.1f}")

    # 记录严重误判信号
    if stats['count'] >= 3 and (win_rate < 40 or stats['total_pnl'] < -100):
        worst_signals.append((signal_key, stats))

print()
print('### 📈 表现最好的信号 (TOP 10)')
print(f"{'信号组合':<70} {'交易':<6} {'胜率':<8} {'总盈亏':<12} {'平均分':<8}")
print('-' * 120)

best_signals = []
for signal_key, stats in sorted_signals[-10:]:
    win_rate = stats['win_rate']
    print(f"{signal_key[:68]:<70} {stats['count']:<6} {win_rate:<7.1f}% ${stats['total_pnl']:<11.2f} {stats['avg_score']:<8.1f}")

    if stats['count'] >= 3 and win_rate > 60 and stats['total_pnl'] > 50:
        best_signals.append((signal_key, stats))

print()
print('=' * 120)
print('🚨 误判信号诊断')
print('=' * 120)
print()

# 分析LONG vs SHORT表现
long_stats = {'count': 0, 'win': 0, 'total_pnl': 0}
short_stats = {'count': 0, 'win': 0, 'total_pnl': 0}

for signal_key, stats in signal_stats.items():
    if signal_key.endswith('_LONG'):
        long_stats['count'] += stats['count']
        long_stats['win'] += stats['win']
        long_stats['total_pnl'] += stats['total_pnl']
    elif signal_key.endswith('_SHORT'):
        short_stats['count'] += stats['count']
        short_stats['win'] += stats['win']
        short_stats['total_pnl'] += stats['total_pnl']

long_win_rate = (long_stats['win'] / long_stats['count'] * 100) if long_stats['count'] > 0 else 0
short_win_rate = (short_stats['win'] / short_stats['count'] * 100) if short_stats['count'] > 0 else 0

print('### 方向性分析')
print(f"LONG信号: {long_stats['count']}笔 | 胜率: {long_win_rate:.1f}% | 总盈亏: ${long_stats['total_pnl']:.2f}")
print(f"SHORT信号: {short_stats['count']}笔 | 胜率: {short_win_rate:.1f}% | 总盈亏: ${short_stats['total_pnl']:.2f}")
print()

if long_win_rate < 40:
    print('❌ LONG信号严重误判! 胜率过低,可能市场处于下跌/震荡')
if short_win_rate < 40:
    print('❌ SHORT信号严重误判! 胜率过低,可能市场处于上涨')

print()
print('### 严重误判的信号组合')
print()

if worst_signals:
    print(f"{'信号组合':<70} {'方向':<6} {'交易数':<8} {'胜率':<8} {'总亏损':<12}")
    print('-' * 120)

    for signal_key, stats in worst_signals:
        # 提取方向
        if signal_key.endswith('_LONG'):
            direction = 'LONG'
            signal_name = signal_key[:-5]
        elif signal_key.endswith('_SHORT'):
            direction = 'SHORT'
            signal_name = signal_key[:-6]
        else:
            direction = 'N/A'
            signal_name = signal_key

        print(f"{signal_name[:68]:<70} {direction:<6} {stats['count']:<8} {stats['win_rate']:<7.1f}% ${stats['total_pnl']:<11.2f}")

    print()
    print(f"⚠️  发现 {len(worst_signals)} 个严重误判信号!")
    print()
else:
    print('✅ 没有发现严重误判的信号')

print()
print('=' * 120)
print('💡 诊断建议')
print('=' * 120)
print()

# 给出具体建议
if long_win_rate < 40 and short_win_rate > 50:
    print('1. 市场可能处于下跌趋势')
    print('   建议: 暂停LONG信号,只做SHORT')
    print()
elif short_win_rate < 40 and long_win_rate > 50:
    print('1. 市场可能处于上涨趋势')
    print('   建议: 暂停SHORT信号,只做LONG')
    print()
elif long_win_rate < 40 and short_win_rate < 40:
    print('1. 市场可能处于剧烈震荡')
    print('   建议: 暂停所有交易,等待趋势明朗')
    print()

if worst_signals:
    print('2. 立即禁用表现差的信号')
    print('   运行: python execute_brain_optimization.py')
    print()

print('3. 检查Big4趋势检测是否失效')
print('   运行: python test_big4_trend.py')
print()

print('4. 考虑提高开仓阈值')
print('   当前阈值: 35分')
print('   建议阈值: 45-50分')
print()

# 检查是否有重复的币种频繁交易
print('=' * 120)
print('频繁交易币种分析 (可能存在过度交易)')
print('=' * 120)
print()

symbol_trade_count = defaultdict(lambda: {'count': 0, 'total_pnl': 0, 'win': 0})

for pos in positions:
    symbol = pos['symbol']
    pnl = pos['realized_pnl'] or 0
    symbol_trade_count[symbol]['count'] += 1
    symbol_trade_count[symbol]['total_pnl'] += pnl
    if pnl > 0:
        symbol_trade_count[symbol]['win'] += 1

# 找出交易次数>5的币种
frequent_symbols = [(sym, stats) for sym, stats in symbol_trade_count.items() if stats['count'] >= 5]
frequent_symbols.sort(key=lambda x: x[1]['total_pnl'])

if frequent_symbols:
    print(f"{'币种':<15} {'交易数':<8} {'胜率':<8} {'总盈亏':<12}")
    print('-' * 60)

    for symbol, stats in frequent_symbols[:10]:
        win_rate = (stats['win'] / stats['count'] * 100) if stats['count'] > 0 else 0
        print(f"{symbol:<15} {stats['count']:<8} {win_rate:<7.1f}% ${stats['total_pnl']:<11.2f}")

    print()
    print('⚠️  以上币种交易频繁,如果持续亏损应考虑加入黑名单')
else:
    print('✅ 没有发现过度交易的币种')

cursor.close()
conn.close()
