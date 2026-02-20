#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析昨晚的开仓时间与Big4趋势对比"""

import pymysql
from dotenv import load_dotenv
import os
import sys
import io
from datetime import timedelta, datetime
from collections import defaultdict

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

# 昨晚下班到今天上班的时间范围
start_time = '2026-02-19 14:30:00'  # 22:30 UTC+8
end_time = '2026-02-20 04:57:00'    # 12:57 UTC+8

print('=' * 100)
print('昨晚开仓时间 vs Big4趋势 分析')
print('=' * 100)
print()

# 1. 查询所有山寨币的开仓时间和信号
print('【山寨币开仓时间分布】')
print('-' * 100)

cursor.execute('''
    SELECT
        symbol,
        position_side,
        open_time,
        entry_score,
        entry_reason,
        realized_pnl,
        close_time
    FROM futures_positions
    WHERE account_id = 2
        AND status = 'closed'
        AND close_time >= %s
        AND close_time < %s
        AND symbol NOT IN ('BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT')
    ORDER BY open_time
''', (start_time, end_time))

positions = cursor.fetchall()

# 按小时分组统计开仓
hourly_opens = defaultdict(lambda: {'count': 0, 'short': 0, 'long': 0})

print(f"总共 {len(positions)} 笔山寨币交易")
print()

# 显示前20笔开仓详情
print('【前20笔开仓详情】')
print('-' * 100)

for i, pos in enumerate(positions[:20], 1):
    side = '做空' if pos['position_side'] == 'SHORT' else '做多'
    open_time_utc8 = pos['open_time'] + timedelta(hours=8)
    close_time_utc8 = pos['close_time'] + timedelta(hours=8)
    open_str = open_time_utc8.strftime('%m-%d %H:%M')
    close_str = close_time_utc8.strftime('%m-%d %H:%M')

    # 统计开仓时间
    hour_key = open_time_utc8.strftime('%m-%d %H:00')
    hourly_opens[hour_key]['count'] += 1
    if pos['position_side'] == 'SHORT':
        hourly_opens[hour_key]['short'] += 1
    else:
        hourly_opens[hour_key]['long'] += 1

    score = pos['entry_score'] or 0
    reason = (pos['entry_reason'] or '无')[:40]
    pnl = pos['realized_pnl']

    print(f"{i:2d}. {open_str} | {pos['symbol']:12s} {side} | 评分{score:3.0f} | {close_str}平 | {pnl:+7.2f} | {reason}")

print()

# 统计每小时的开仓情况
print('【按小时统计开仓】')
print('-' * 100)

for hour in sorted(hourly_opens.keys()):
    stats = hourly_opens[hour]
    print(f"{hour} | 开仓{stats['count']:3d}笔 | 做多{stats['long']:2d}笔 | 做空{stats['short']:2d}笔")

print()

# 2. 查询是否有Big4的历史评分数据
print('【尝试查找Big4历史数据】')
print('-' * 100)

# 检查是否有存储Big4评分的表
cursor.execute("SHOW TABLES LIKE '%big4%'")
big4_tables = cursor.fetchall()

if big4_tables:
    print(f"找到相关表: {[t for t in big4_tables]}")
    # 如果有历史表，查询对应时间的数据
else:
    print("未找到Big4历史数据表")
    print()
    print("💡 建议：创建Big4历史数据表来记录每小时的趋势评分")

print()

# 3. 查询是否有BTC/ETH的持仓记录（包括未平仓的）
print('【查询Big4币种在相关时间段的持仓】')
print('-' * 100)

cursor.execute('''
    SELECT
        symbol,
        position_side,
        open_time,
        close_time,
        status,
        entry_score,
        realized_pnl,
        unrealized_pnl
    FROM futures_positions
    WHERE account_id = 2
        AND symbol IN ('BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT')
        AND (
            (open_time >= %s - INTERVAL 4 HOUR AND open_time < %s)
            OR (close_time >= %s AND close_time < %s)
            OR (status = 'open' AND open_time < %s)
        )
    ORDER BY open_time
''', (start_time, end_time, start_time, end_time, end_time))

big4_positions = cursor.fetchall()

if big4_positions:
    for pos in big4_positions:
        side = '做空' if pos['position_side'] == 'SHORT' else '做多'
        open_time_utc8 = pos['open_time'] + timedelta(hours=8)
        open_str = open_time_utc8.strftime('%m-%d %H:%M')

        if pos['close_time']:
            close_time_utc8 = pos['close_time'] + timedelta(hours=8)
            close_str = close_time_utc8.strftime('%m-%d %H:%M')
            pnl = pos['realized_pnl']
            status_str = f"{close_str}平 | {pnl:+7.2f}"
        else:
            pnl = pos['unrealized_pnl']
            status_str = f"持仓中 | 浮盈{pnl:+7.2f}"

        score = pos['entry_score'] or 0
        print(f"{open_str} | {pos['symbol']:12s} {side} | 评分{score:3.0f} | {status_str}")
else:
    print("❌ 在该时间段内没有Big4币种的持仓记录")
    print("   这意味着：系统没有捕捉到BTC/ETH等主流币的信号")
    print("   或者：即使有信号也没有开仓")

print()

# 4. 分析开仓的时间模式
print('【开仓时间模式分析】')
print('-' * 100)

# 计算所有开仓的时间范围
if positions:
    first_open = positions[0]['open_time'] + timedelta(hours=8)
    last_open = positions[-1]['open_time'] + timedelta(hours=8)

    print(f"第一笔开仓: {first_open.strftime('%m-%d %H:%M')}")
    print(f"最后一笔开仓: {last_open.strftime('%m-%d %H:%M')}")
    print(f"开仓时间跨度: {(last_open - first_open).total_seconds() / 3600:.1f} 小时")
    print()

    # 检查是否有集中开仓的时间段
    print("开仓密集时段:")
    for hour, stats in sorted(hourly_opens.items(), key=lambda x: x[1]['count'], reverse=True)[:5]:
        print(f"  {hour}: {stats['count']}笔 (做空{stats['short']}笔)")

print()

# 5. 查询close_time（被止损/平仓的时间）分析山寨币何时开始上涨
print('【平仓时间分布 - 推测山寨币上涨时间】')
print('-' * 100)

hourly_closes = defaultdict(int)
for pos in positions:
    close_time_utc8 = pos['close_time'] + timedelta(hours=8)
    hour_key = close_time_utc8.strftime('%m-%d %H:00')
    hourly_closes[hour_key] += 1

print("各时段平仓数量（可能是山寨币开始上涨的时间）:")
for hour in sorted(hourly_closes.keys()):
    count = hourly_closes[hour]
    print(f"  {hour}: {count}笔被平仓")

print()
print('=' * 100)

conn.close()
