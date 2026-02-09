#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析今日交易表现
"""

import pymysql
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4'
}

conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
cursor = conn.cursor()

# 今天的订单
today = datetime.now().strftime('%Y-%m-%d')
cursor.execute('''
    SELECT
        id,
        symbol,
        position_side,
        entry_price,
        exit_price,
        realized_pnl,
        open_time,
        close_time,
        close_reason,
        notes
    FROM futures_positions
    WHERE account_id = 2
    AND DATE(open_time) = %s
    AND status = 'closed'
    ORDER BY open_time DESC
    LIMIT 30
''', (today,))

orders = cursor.fetchall()

print(f'\n今日交易记录 ({today}):')
print('=' * 120)

if orders:
    total_pnl = sum(float(o['realized_pnl']) for o in orders)
    win_count = sum(1 for o in orders if float(o['realized_pnl']) > 0)

    print(f'\n📊 统计数据:')
    print(f'  总订单数: {len(orders)}')
    print(f'  盈利订单: {win_count}')
    print(f'  亏损订单: {len(orders) - win_count}')
    print(f'  胜率: {win_count}/{len(orders)} = {win_count/len(orders)*100:.1f}%')
    print(f'  总盈亏: ${total_pnl:.2f}')
    print()

    # 按止损原因分类
    stop_loss_reasons = {}
    for o in orders:
        reason = o['close_reason']
        if reason not in stop_loss_reasons:
            stop_loss_reasons[reason] = {'count': 0, 'pnl': 0}
        stop_loss_reasons[reason]['count'] += 1
        stop_loss_reasons[reason]['pnl'] += float(o['realized_pnl'])

    print('📋 平仓原因统计:')
    for reason, data in sorted(stop_loss_reasons.items(), key=lambda x: x[1]['count'], reverse=True):
        print(f'  {reason}: {data["count"]}次, 盈亏${data["pnl"]:.2f}')
    print()

    print('📝 详细订单:')
    print('-' * 120)

    for i, o in enumerate(orders, 1):
        pnl = float(o['realized_pnl'])
        entry = float(o['entry_price'])
        exit_price = float(o['exit_price'])

        if o['position_side'] == 'LONG':
            pnl_pct = (exit_price - entry) / entry * 100
        else:
            pnl_pct = (entry - exit_price) / entry * 100

        status_icon = '✅' if pnl > 0 else '❌'

        print(f'\n{i}. {status_icon} {o["symbol"]} {o["position_side"]} (ID:{o["id"]})')
        print(f'   开仓: {o["open_time"].strftime("%H:%M:%S")} @ ${entry:.6f}')
        print(f'   平仓: {o["close_time"].strftime("%H:%M:%S")} @ ${exit_price:.6f}')
        print(f'   盈亏: ${pnl:+.2f} ({pnl_pct:+.2f}%)')
        print(f'   原因: {o["close_reason"]}')

        if o.get('notes'):
            notes = o['notes']
            if len(notes) > 200:
                notes = notes[:200] + '...'
            print(f'   备注: {notes}')

        # 持仓时长
        duration = (o['close_time'] - o['open_time']).total_seconds() / 60
        print(f'   时长: {duration:.1f}分钟')
else:
    print('\n今日暂无交易记录')

# 查看当前市场趋势
print('\n\n' + '=' * 120)
print('📈 当前市场状态分析:')
print('=' * 120)

# 查询Big4趋势
cursor.execute('''
    SELECT symbol, signal, strength, created_at
    FROM big4_signals
    WHERE created_at >= NOW() - INTERVAL 1 HOUR
    ORDER BY created_at DESC
    LIMIT 4
''')

big4 = cursor.fetchall()
if big4:
    print('\n🔱 Big4信号 (最近1小时):')
    for b in big4:
        print(f'  {b["symbol"]}: {b["signal"]} (强度:{b["strength"]}) @ {b["created_at"].strftime("%H:%M:%S")}')

# 查询破位状态
cursor.execute('''
    SELECT * FROM breakout_sessions
    WHERE expires_at > NOW()
    ORDER BY created_at DESC
    LIMIT 1
''')

breakout = cursor.fetchone()
if breakout:
    print(f'\n💥 活跃破位会话:')
    print(f'  方向: {breakout["direction"]}')
    print(f'  强度: {breakout["strength"]}')
    print(f'  创建: {breakout["created_at"].strftime("%H:%M:%S")}')
    print(f'  失效: {breakout["expires_at"].strftime("%H:%M:%S")}')
else:
    print('\n无活跃破位会话')

# 查询紧急干预
cursor.execute('''
    SELECT * FROM emergency_intervention
    WHERE expires_at > NOW()
    ORDER BY created_at DESC
    LIMIT 3
''')

interventions = cursor.fetchall()
if interventions:
    print(f'\n🚨 活跃紧急干预:')
    for inter in interventions:
        print(f'  类型: {inter["intervention_type"]}')
        print(f'  禁止做多: {"是" if inter["block_long"] else "否"} | 禁止做空: {"是" if inter["block_short"] else "否"}')
        print(f'  原因: {inter["trigger_reason"]}')
        print(f'  失效: {inter["expires_at"].strftime("%H:%M:%S")}')
        print()

cursor.close()
conn.close()

print('\n' + '=' * 120)
print('分析完成!')
print('=' * 120)
