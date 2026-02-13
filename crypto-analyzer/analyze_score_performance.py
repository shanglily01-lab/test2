#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析不同开仓分数段的交易表现"""
import pymysql
import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from dotenv import load_dotenv
from collections import defaultdict

# 设置Windows控制台编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST', '13.212.252.171'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER', 'app_user'),
    password=os.getenv('DB_PASSWORD', 'AppUser@2024#Secure'),
    database=os.getenv('DB_NAME', 'crypto_analyzer'),
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

cursor = conn.cursor()

print('=' * 120)
print('开仓分数 vs 交易表现分析')
print('=' * 120)

# 查询最近24小时的已平仓交易
cursor.execute('''
    SELECT
        id,
        symbol,
        position_side,
        realized_pnl,
        margin,
        open_time,
        close_time,
        source,
        entry_reason
    FROM futures_positions
    WHERE account_id = 2
      AND status = 'closed'
      AND open_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
      AND realized_pnl IS NOT NULL
    ORDER BY open_time DESC
''')

positions = cursor.fetchall()

print(f'\n📊 最近24小时共 {len(positions)} 笔已平仓交易\n')

# 从entry_reason中提取开仓分数
# 格式示例: "LONG评分:35 (SHORT:15) | 阈值:35 | ✅达标"
score_ranges = {
    '30-39分': {'trades': [], 'range': (30, 39)},
    '40-49分': {'trades': [], 'range': (40, 49)},
    '50-59分': {'trades': [], 'range': (50, 59)},
    '60-69分': {'trades': [], 'range': (60, 69)},
    '70-79分': {'trades': [], 'range': (70, 79)},
    '80-89分': {'trades': [], 'range': (80, 89)},
    '90-100分': {'trades': [], 'range': (90, 100)},
}

no_score_trades = []

for pos in positions:
    entry_reason = pos.get('entry_reason') or ''
    score = None

    # 从entry_reason提取分数
    # 格式1: "LONG评分:35" 或 "SHORT评分:35"
    # 格式2: "开仓分数 35 分"
    if 'LONG' in entry_reason and ':' in entry_reason:
        try:
            parts = entry_reason.split(':')
            for part in parts:
                if part and part[0].isdigit():
                    score = int(part.split()[0])
                    break
        except:
            pass
    elif 'SHORT' in entry_reason and ':' in entry_reason:
        try:
            parts = entry_reason.split(':')
            for part in parts:
                if part and part[0].isdigit():
                    score = int(part.split()[0])
                    break
        except:
            pass

    if score is not None:
        # 归类到分数段
        for range_name, range_data in score_ranges.items():
            min_score, max_score = range_data['range']
            if min_score <= score <= max_score:
                range_data['trades'].append({
                    'position': pos,
                    'score': score
                })
                break
    else:
        no_score_trades.append(pos)

# 统计各分数段表现
print('=' * 120)
print(f'{"分数段":<15} {"交易数":<10} {"盈利笔数":<10} {"亏损笔数":<10} {"胜率":<10} {"总盈亏":<20} {"平均每笔"}')
print('-' * 120)

for range_name in ['30-39分', '40-49分', '50-59分', '60-69分', '70-79分', '80-89分', '90-100分']:
    range_data = score_ranges[range_name]
    trades = range_data['trades']

    if not trades:
        print(f'{range_name:<15} {0:<10} {0:<10} {0:<10} {"-":<10} {"-":<20} {"-"}')
        continue

    trade_count = len(trades)
    win_count = len([t for t in trades if float(t['position']['realized_pnl']) > 0])
    loss_count = len([t for t in trades if float(t['position']['realized_pnl']) < 0])
    win_rate = win_count / trade_count * 100 if trade_count > 0 else 0
    total_pnl = sum([Decimal(str(t['position']['realized_pnl'])) for t in trades])
    avg_pnl = float(total_pnl) / trade_count if trade_count > 0 else 0

    pnl_emoji = '🟢' if total_pnl > 0 else '🔴' if total_pnl < 0 else '⚪'

    print(f'{range_name:<15} {trade_count:<10} {win_count:<10} {loss_count:<10} {win_rate:<9.1f}% {pnl_emoji} {float(total_pnl):+.2f} USDT{"":<6} {avg_pnl:+.2f} USDT')

print('\n' + '=' * 120)
print('详细分析')
print('=' * 120)

# 显示30-39分段的详细交易
low_score_trades = score_ranges['30-39分']['trades']
if low_score_trades:
    print(f'\n【30-39分段详情】（{len(low_score_trades)}笔交易）\n')

    for i, trade_data in enumerate(low_score_trades, 1):
        pos = trade_data['position']
        score = trade_data['score']
        pnl = float(pos['realized_pnl'])
        margin = float(pos['margin'])
        pnl_pct = (pnl / margin * 100) if margin > 0 else 0
        side_emoji = '🟢' if pos['position_side'] == 'LONG' else '🔴'
        pnl_emoji = '✅' if pnl > 0 else '❌'

        open_time = pos['open_time'].strftime('%m-%d %H:%M')
        close_time = pos['close_time'].strftime('%H:%M')

        print(f'{i}. {side_emoji} {pos["symbol"]:12} 分数:{score:>3} | {open_time}-{close_time} | {pnl_emoji} {pnl:+8.2f} USDT ({pnl_pct:+6.2f}%)')
        if pos.get('entry_reason'):
            print(f'   原因: {pos["entry_reason"][:100]}')
        print()

# 显示40-59分段的详细交易
mid_score_trades = score_ranges['40-49分']['trades'] + score_ranges['50-59分']['trades']
if mid_score_trades:
    print(f'\n【40-59分段详情】（{len(mid_score_trades)}笔交易）\n')

    for i, trade_data in enumerate(mid_score_trades, 1):
        pos = trade_data['position']
        score = trade_data['score']
        pnl = float(pos['realized_pnl'])
        margin = float(pos['margin'])
        pnl_pct = (pnl / margin * 100) if margin > 0 else 0
        side_emoji = '🟢' if pos['position_side'] == 'LONG' else '🔴'
        pnl_emoji = '✅' if pnl > 0 else '❌'

        open_time = pos['open_time'].strftime('%m-%d %H:%M')
        close_time = pos['close_time'].strftime('%H:%M')

        print(f'{i}. {side_emoji} {pos["symbol"]:12} 分数:{score:>3} | {open_time}-{close_time} | {pnl_emoji} {pnl:+8.2f} USDT ({pnl_pct:+6.2f}%)')

# 结论
print('\n' + '=' * 120)
print('结论与建议')
print('=' * 120)

# 计算30-39分和40+分的对比
low_trades = score_ranges['30-39分']['trades']
high_trades = []
for range_name in ['40-49分', '50-59分', '60-69分', '70-79分', '80-89分', '90-100分']:
    high_trades.extend(score_ranges[range_name]['trades'])

if low_trades and high_trades:
    low_pnl = sum([Decimal(str(t['position']['realized_pnl'])) for t in low_trades])
    high_pnl = sum([Decimal(str(t['position']['realized_pnl'])) for t in high_trades])

    low_win_rate = len([t for t in low_trades if float(t['position']['realized_pnl']) > 0]) / len(low_trades) * 100
    high_win_rate = len([t for t in high_trades if float(t['position']['realized_pnl']) > 0]) / len(high_trades) * 100

    low_avg = float(low_pnl) / len(low_trades)
    high_avg = float(high_pnl) / len(high_trades)

    print(f'\n30-39分段: {len(low_trades)}笔, 胜率{low_win_rate:.1f}%, 平均{low_avg:+.2f} USDT/笔, 总计{float(low_pnl):+.2f} USDT')
    print(f'40+分段:   {len(high_trades)}笔, 胜率{high_win_rate:.1f}%, 平均{high_avg:+.2f} USDT/笔, 总计{float(high_pnl):+.2f} USDT')

    if low_win_rate >= 50 and low_avg > 0:
        print(f'\n✅ 30-39分段（含35分）表现合格：')
        print(f'   - 胜率 {low_win_rate:.1f}% (>= 50%)')
        print(f'   - 平均盈利 {low_avg:+.2f} USDT/笔 (> 0)')
        print(f'   - 建议：保持35分阈值')
    elif low_win_rate < 50 or low_avg < 0:
        print(f'\n⚠️ 30-39分段表现不佳：')
        print(f'   - 胜率 {low_win_rate:.1f}% {"(<50%)" if low_win_rate < 50 else ""}')
        print(f'   - 平均盈利 {low_avg:+.2f} USDT/笔 {"(<0)" if low_avg < 0 else ""}')
        print(f'   - 建议：考虑提高阈值到40-45分')

    if high_win_rate > low_win_rate + 10:
        print(f'\n📊 高分段显著优于低分段（胜率差{high_win_rate - low_win_rate:.1f}%）')
        print(f'   - 建议：可以考虑提高阈值以提升整体胜率')

cursor.close()
conn.close()
