#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""展示最近的AI优化记录"""

import pymysql
from dotenv import load_dotenv
import os
import sys
import io
from datetime import datetime, timedelta

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

print('=' * 120)
print('最近7天的超级大脑AI优化记录')
print('=' * 120)
print()

# 1. 信号黑名单优化（最近新增的）
print('【信号自优化 - 新增黑名单】')
print('-' * 120)

cursor.execute('''
    SELECT
        created_at,
        signal_type,
        position_side,
        reason,
        total_loss,
        win_rate,
        order_count,
        blacklist_level
    FROM signal_blacklist
    WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
        AND is_active = 1
    ORDER BY created_at DESC
    LIMIT 20
''')

blacklist = cursor.fetchall()

if blacklist:
    for i, item in enumerate(blacklist, 1):
        time_str = item['created_at'].strftime('%m-%d %H:%M')
        signal = item['signal_type']
        side = '做多' if item['position_side'] == 'LONG' else '做空'
        reason = item['reason']
        loss = item['total_loss']
        win_rate = (item['win_rate'] * 100) if item['win_rate'] is not None else 0
        count = item['order_count']
        level = item['blacklist_level']

        level_emoji = {1: '⚠️', 2: '🔴', 3: '🚫'}.get(level, '📝')

        print(f"{i:2d}. {time_str} | {level_emoji} 黑名单级别{level} | {side}")
        print(f"    信号: {signal}")
        print(f"    原因: {reason}")
        print(f"    统计: {count}笔交易，胜率{win_rate:.1f}%，亏损{loss:.2f}U")
        print()
else:
    print('最近7天没有新增黑名单信号')
    print()

# 2. 每日信号分析（优秀和较差的信号）
print()
print('【信号自优化 - 每日评级分析】')
print('-' * 120)

cursor.execute('''
    SELECT
        review_date,
        signal_type,
        total_trades,
        win_trades,
        loss_trades,
        win_rate,
        avg_pnl,
        best_trade,
        worst_trade,
        avg_holding_minutes,
        rating,
        score
    FROM daily_review_signal_analysis
    WHERE review_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
        AND (score >= 80 OR score <= 30)
    ORDER BY review_date DESC, score DESC
    LIMIT 30
''')

signal_analysis = cursor.fetchall()

if signal_analysis:
    current_date = None
    for item in signal_analysis:
        review_date = item['review_date']
        if review_date != current_date:
            if current_date is not None:
                print()
            current_date = review_date
            print(f"\n📅 {review_date.strftime('%Y-%m-%d')}")
            print('-' * 100)

        signal = item['signal_type']
        trades = item['total_trades']
        win_rate = item['win_rate']
        avg_pnl = item['avg_pnl']
        rating = item['rating']
        score = item['score']

        print(f"  {rating} 评分{score} | {signal[:60]:60s}")
        print(f"      {trades}笔交易，胜率{win_rate:.1f}%，平均{avg_pnl:+.2f}U")
else:
    print('暂无信号分析数据')

print()

# 3. 所有活跃的黑名单信号
print()
print('【当前所有活跃黑名单信号】')
print('-' * 120)

cursor.execute('''
    SELECT
        signal_type,
        position_side,
        reason,
        total_loss,
        win_rate,
        order_count,
        blacklist_level,
        created_at
    FROM signal_blacklist
    WHERE is_active = 1
    ORDER BY blacklist_level DESC, total_loss DESC
''')

all_blacklist = cursor.fetchall()

if all_blacklist:
    print(f"共 {len(all_blacklist)} 个活跃黑名单信号\n")

    for i, item in enumerate(all_blacklist, 1):
        signal = item['signal_type']
        side = '做多' if item['position_side'] == 'LONG' else '做空'
        reason = item['reason']
        loss = item['total_loss']
        win_rate = (item['win_rate'] * 100) if item['win_rate'] is not None else 0
        count = item['order_count']
        level = item['blacklist_level']
        created = item['created_at'].strftime('%m-%d')

        level_emoji = {1: '⚠️', 2: '🔴', 3: '🚫'}.get(level, '📝')

        print(f"{i:2d}. {level_emoji} L{level} | {side} | {created} | {signal[:50]:50s}")
        print(f"    {reason} | {count}笔，胜率{win_rate:.1f}%，亏损{loss:.2f}U")
else:
    print('暂无黑名单信号')

print()
print('=' * 120)

conn.close()
