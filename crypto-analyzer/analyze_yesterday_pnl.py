#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析昨晚盈亏情况"""

import pymysql
from dotenv import load_dotenv
import os
import sys
import io
from datetime import timedelta

# 设置UTF-8输出
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'),
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

cursor = conn.cursor()

start_time = '2026-02-19 14:30:00'  # 2026-02-19 22:30 UTC+8
end_time = '2026-02-20 04:57:00'    # 2026-02-20 12:57 UTC+8

print('=' * 100)
print('昨晚盈亏详细分析 (2026-02-19 22:30 ~ 2026-02-20 12:57)')
print('=' * 100)
print()

# 按币种统计
print('【按币种统计 - 亏损最多的币种】')
print('-' * 100)

cursor.execute('''
    SELECT
        symbol,
        COUNT(*) as trade_count,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as win_count,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl,
        MIN(realized_pnl) as max_loss,
        MAX(realized_pnl) as max_profit
    FROM futures_positions
    WHERE account_id = 2
        AND status = 'closed'
        AND close_time >= %s
        AND close_time < %s
    GROUP BY symbol
    ORDER BY total_pnl ASC
    LIMIT 20
''', (start_time, end_time))

symbol_stats = cursor.fetchall()

for row in symbol_stats:
    pnl_emoji = '📉' if row['total_pnl'] < 0 else '📈'
    symbol = row['symbol']
    count = row['trade_count']
    win = row['win_count']
    total_pnl = row['total_pnl']
    avg_pnl = row['avg_pnl']
    max_loss = row['max_loss']
    win_rate = win/count*100 if count > 0 else 0
    print(f"{pnl_emoji} {symbol:12s} | 交易{count:3d}笔 | 胜率{win_rate:5.1f}% | 盈亏 {total_pnl:+8.2f} | 平均 {avg_pnl:+7.2f} | 最差 {max_loss:+7.2f}")

print()

# 按平仓原因统计
print('【按平仓原因统计】')
print('-' * 100)

cursor.execute('''
    SELECT
        notes,
        COUNT(*) as trade_count,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as win_count,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl
    FROM futures_positions
    WHERE account_id = 2
        AND status = 'closed'
        AND close_time >= %s
        AND close_time < %s
    GROUP BY notes
    ORDER BY total_pnl ASC
''', (start_time, end_time))

reason_stats = cursor.fetchall()

for row in reason_stats:
    pnl_emoji = '📉' if row['total_pnl'] < 0 else '📈'
    reason = row['notes'] or '未知'
    count = row['trade_count']
    win = row['win_count']
    total_pnl = row['total_pnl']
    avg_pnl = row['avg_pnl']
    win_rate = win/count*100 if count > 0 else 0
    print(f"{pnl_emoji} {reason:20s} | {count:3d}笔 | 胜率{win_rate:5.1f}% | 盈亏 {total_pnl:+8.2f} | 平均 {avg_pnl:+7.2f}")

print()

# 最大亏损的10笔交易
print('【最大亏损的10笔交易】')
print('-' * 100)

cursor.execute('''
    SELECT
        symbol,
        position_side,
        entry_price,
        close_price,
        quantity,
        leverage,
        realized_pnl,
        notes,
        close_time
    FROM futures_positions
    WHERE account_id = 2
        AND status = 'closed'
        AND close_time >= %s
        AND close_time < %s
    ORDER BY realized_pnl ASC
    LIMIT 10
''', (start_time, end_time))

worst_trades = cursor.fetchall()

for i, trade in enumerate(worst_trades, 1):
    side = '做多' if trade['position_side'] == 'LONG' else '做空'
    reason = trade['notes'] or '未知'
    # 转换为UTC+8时间
    close_time_utc8 = trade['close_time'] + timedelta(hours=8)
    time_str = close_time_utc8.strftime('%m-%d %H:%M')
    entry = trade['entry_price']
    close = trade['close_price']
    pnl = trade['realized_pnl']
    symbol = trade['symbol']
    lev = trade['leverage']

    print(f"{i:2d}. {time_str} | {symbol:12s} {side} {lev}x | {entry:.6f}→{close:.6f} | {pnl:+8.2f} | {reason}")

print()

# 总结
print('【总结】')
print('-' * 100)

cursor.execute('''
    SELECT
        COUNT(*) as total_trades,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as win_count,
        SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as lose_count,
        SUM(realized_pnl) as total_pnl,
        MIN(realized_pnl) as max_loss,
        MAX(realized_pnl) as max_profit,
        AVG(realized_pnl) as avg_pnl
    FROM futures_positions
    WHERE account_id = 2
        AND status = 'closed'
        AND close_time >= %s
        AND close_time < %s
''', (start_time, end_time))

summary = cursor.fetchone()

print(f"总交易数: {summary['total_trades']} 笔")
print(f"盈利交易: {summary['win_count']} 笔")
print(f"亏损交易: {summary['lose_count']} 笔")
print(f"胜率: {summary['win_count']/summary['total_trades']*100:.2f}%")
print(f"总盈亏: {summary['total_pnl']:+.2f} USDT")
print(f"最大单笔盈利: {summary['max_profit']:+.2f} USDT")
print(f"最大单笔亏损: {summary['max_loss']:+.2f} USDT")
print(f"平均盈亏: {summary['avg_pnl']:+.2f} USDT")

print()
print('=' * 100)

conn.close()
