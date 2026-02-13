#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析Big4信号持续时间 vs 交易盈亏的关系"""
import pymysql
import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from dotenv import load_dotenv

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
print('Big4信号持续时间 vs 交易盈亏关系分析')
print('=' * 120)

# 定义BULLISH信号时间段（从时间线分析得出）
# 格式：(开始时间, 结束时间, 持续分钟, 类别)
bullish_periods = [
    ('2026-02-12 15:27', '2026-02-12 15:42', 15, '短期'),
    ('2026-02-12 15:55', '2026-02-12 16:17', 22, '短期'),
    ('2026-02-12 21:42', '2026-02-12 22:07', 25, '短期'),
    ('2026-02-12 22:42', '2026-02-12 22:56', 14, '短期'),
    ('2026-02-12 23:27', '2026-02-13 00:22', 55, '中期'),
    ('2026-02-13 01:37', '2026-02-13 01:47', 10, '短期'),
    ('2026-02-13 02:02', '2026-02-13 02:27', 25, '短期'),
    ('2026-02-13 02:32', '2026-02-13 03:12', 40, '中期'),
    ('2026-02-13 03:27', '2026-02-13 06:12', 165, '长期'),  # 唯一接近3小时的
]

# 按持续时间分类统计
short_term = []  # <30分钟
mid_term = []    # 30-90分钟
long_term = []   # >90分钟

for start, end, duration, category in bullish_periods:
    start_dt = datetime.strptime(start, '%Y-%m-%d %H:%M')
    end_dt = datetime.strptime(end, '%Y-%m-%d %H:%M')

    # 查询这个时间段开仓的交易
    cursor.execute('''
        SELECT
            symbol,
            position_side,
            realized_pnl,
            margin,
            open_time,
            close_time,
            source
        FROM futures_positions
        WHERE account_id = 2
          AND status = 'closed'
          AND open_time >= %s
          AND open_time < %s
          AND realized_pnl IS NOT NULL
        ORDER BY open_time
    ''', (start_dt, end_dt))

    trades = cursor.fetchall()

    period_info = {
        'start': start,
        'end': end,
        'duration': duration,
        'trades': trades,
        'total_pnl': sum([Decimal(str(t['realized_pnl'])) for t in trades]),
        'trade_count': len(trades),
        'win_count': len([t for t in trades if float(t['realized_pnl']) > 0]),
        'loss_count': len([t for t in trades if float(t['realized_pnl']) < 0]),
    }

    if duration < 30:
        short_term.append(period_info)
    elif duration < 90:
        mid_term.append(period_info)
    else:
        long_term.append(period_info)

# 分析结果
print('\n信号持续时间分类统计:\n')

categories = [
    ('短期信号 (<30分钟)', short_term),
    ('中期信号 (30-90分钟)', mid_term),
    ('长期信号 (>90分钟)', long_term)
]

for cat_name, periods in categories:
    print(f'【{cat_name}】')
    print('-' * 120)

    if not periods:
        print('  无此类信号\n')
        continue

    total_trades = sum([p['trade_count'] for p in periods])
    total_pnl = sum([p['total_pnl'] for p in periods])
    total_wins = sum([p['win_count'] for p in periods])
    total_losses = sum([p['loss_count'] for p in periods])

    print(f'  信号数量: {len(periods)} 个')
    print(f'  开仓交易: {total_trades} 笔')
    print(f'  盈利交易: {total_wins} 笔')
    print(f'  亏损交易: {total_losses} 笔')
    print(f'  胜率: {(total_wins/total_trades*100 if total_trades > 0 else 0):.1f}%')
    print(f'  总盈亏: {float(total_pnl):+.2f} USDT')
    print(f'  平均每笔: {(float(total_pnl)/total_trades if total_trades > 0 else 0):+.2f} USDT')
    print()

    # 显示每个信号的详情
    for i, p in enumerate(periods, 1):
        pnl_emoji = '🟢' if p['total_pnl'] > 0 else '🔴' if p['total_pnl'] < 0 else '⚪'
        print(f'  {i}. {p["start"]} ~ {p["end"]} ({p["duration"]}分钟)')
        print(f'     交易{p["trade_count"]}笔 | 盈利{p["win_count"]}笔 | 亏损{p["loss_count"]}笔 | {pnl_emoji} {float(p["total_pnl"]):+.2f} USDT')

        # 显示具体交易
        if p['trades']:
            for trade in p['trades'][:3]:  # 只显示前3笔
                pnl = float(trade['realized_pnl'])
                margin = float(trade['margin'])
                pnl_pct = (pnl / margin * 100) if margin > 0 else 0
                side_emoji = '🟢' if trade['position_side'] == 'LONG' else '🔴'
                open_time = trade['open_time'].strftime('%H:%M')
                close_time = trade['close_time'].strftime('%H:%M')
                print(f'       {side_emoji} {trade["symbol"]:12} {open_time}-{close_time} {pnl:+8.2f} USDT ({pnl_pct:+6.2f}%)')
            if len(p['trades']) > 3:
                print(f'       ... 还有 {len(p["trades"])-3} 笔交易')
        print()

# 总结对比
print('=' * 120)
print('总结对比')
print('=' * 120)

short_total_pnl = sum([p['total_pnl'] for p in short_term])
mid_total_pnl = sum([p['total_pnl'] for p in mid_term])
long_total_pnl = sum([p['total_pnl'] for p in long_term])

short_total_trades = sum([p['trade_count'] for p in short_term])
mid_total_trades = sum([p['trade_count'] for p in mid_term])
long_total_trades = sum([p['trade_count'] for p in long_term])

print(f'\n{"类别":<20} {"信号数":<10} {"交易数":<10} {"总盈亏":<20} {"平均每笔"}')
print('-' * 120)
print(f'{"短期 (<30分钟)":<20} {len(short_term):<10} {short_total_trades:<10} {float(short_total_pnl):+.2f} USDT{"":<8} {(float(short_total_pnl)/short_total_trades if short_total_trades > 0 else 0):+.2f} USDT')
print(f'{"中期 (30-90分钟)":<20} {len(mid_term):<10} {mid_total_trades:<10} {float(mid_total_pnl):+.2f} USDT{"":<8} {(float(mid_total_pnl)/mid_total_trades if mid_total_trades > 0 else 0):+.2f} USDT')
print(f'{"长期 (>90分钟)":<20} {len(long_term):<10} {long_total_trades:<10} {float(long_total_pnl):+.2f} USDT{"":<8} {(float(long_total_pnl)/long_total_trades if long_total_trades > 0 else 0):+.2f} USDT')

print('\n' + '=' * 120)
print('结论:')
print('=' * 120)

if long_total_trades > 0 and short_total_trades > 0:
    long_avg = float(long_total_pnl) / long_total_trades
    short_avg = float(short_total_pnl) / short_total_trades

    if long_avg > short_avg:
        print(f'✅ 长期信号（>90分钟）开仓的交易表现更好！')
        print(f'   长期平均: {long_avg:+.2f} USDT/笔')
        print(f'   短期平均: {short_avg:+.2f} USDT/笔')
        print(f'   差异: {(long_avg - short_avg):+.2f} USDT/笔')
    else:
        print(f'❌ 长期信号并未带来更好的收益')
        print(f'   长期平均: {long_avg:+.2f} USDT/笔')
        print(f'   短期平均: {short_avg:+.2f} USDT/笔')

cursor.close()
conn.close()
