#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查看当前分批建仓任务"""

import pymysql
from dotenv import load_dotenv
import os
import sys
import io
from datetime import datetime, timedelta
import json

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
print('分批建仓任务查询')
print('=' * 120)
print()

# 查询正在分批建仓的任务
print('【正在进行的分批建仓任务 (status=building)】')
print('-' * 120)

cursor.execute('''
    SELECT
        id,
        symbol,
        position_side,
        quantity,
        entry_price,
        margin,
        leverage,
        entry_signal_time,
        created_at,
        entry_signal_type,
        batch_plan,
        batch_filled
    FROM futures_positions
    WHERE account_id = 2
        AND status = 'building'
        AND source = 'smart_trader_batch'
    ORDER BY entry_signal_time DESC
''')

building_tasks = cursor.fetchall()

if building_tasks:
    print(f'共 {len(building_tasks)} 个进行中的任务:\n')

    for task in building_tasks:
        symbol = task['symbol']
        side = '做多' if task['position_side'] == 'LONG' else '做空'
        signal_time = (task['entry_signal_time'] + timedelta(hours=8))
        created = (task['created_at'] + timedelta(hours=8))
        strategy = task['entry_signal_type']

        # 计算已用时间和剩余时间
        now = datetime.now()
        elapsed = (now - (task['entry_signal_time'] + timedelta(hours=8))).total_seconds() / 60
        remaining = 60 - elapsed

        print(f'🔄 ID:{task["id"]} | {symbol} {side} | 策略:{strategy}')
        print(f'   信号时间: {signal_time.strftime("%m-%d %H:%M:%S")} | 创建:{created.strftime("%m-%d %H:%M:%S")}')
        print(f'   当前持仓: {task["quantity"]:.4f} @ {task["entry_price"]:.6f} | 保证金{task["margin"]:.2f}U')
        print(f'   时间: 已用{elapsed:.1f}分钟 | 剩余{remaining:.1f}分钟')

        # 解析batch_filled查看进度
        if task['batch_filled']:
            batch_filled = json.loads(task['batch_filled'])
            batches = batch_filled.get('batches', [])
            print(f'   进度: {len(batches)}/3批')
            for b in batches:
                batch_time = datetime.fromisoformat(b['time']) + timedelta(hours=8)
                print(f'     ✅ 第{b["batch_num"]+1}批: {b["ratio"]*100:.0f}% @ {b["price"]:.6f} | {batch_time.strftime("%H:%M:%S")} | {b.get("reason", "")}')

        print()
else:
    print('  当前没有进行中的分批建仓任务')
    print()

# 查询最近完成的分批建仓任务
print()
print('【最近24小时完成的分批建仓任务】')
print('-' * 120)

cursor.execute('''
    SELECT
        id,
        symbol,
        position_side,
        status,
        quantity,
        entry_price,
        margin,
        realized_pnl,
        unrealized_pnl,
        created_at,
        close_time,
        entry_signal_type,
        batch_filled
    FROM futures_positions
    WHERE account_id = 2
        AND source = 'smart_trader_batch'
        AND created_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
    ORDER BY created_at DESC
    LIMIT 20
''')

recent_tasks = cursor.fetchall()

if recent_tasks:
    print(f'共 {len(recent_tasks)} 个任务:\n')

    for task in recent_tasks:
        symbol = task['symbol']
        side = '做多' if task['position_side'] == 'LONG' else '做空'
        status = task['status']
        created = (task['created_at'] + timedelta(hours=8))
        strategy = task['entry_signal_type']

        status_emoji = {
            'building': '🔄',
            'open': '🟢',
            'closed': '⚪'
        }.get(status, '📝')

        print(f'{status_emoji} ID:{task["id"]} | {created.strftime("%m-%d %H:%M:%S")} | {symbol} {side} | {status}')
        print(f'   策略:{strategy} | 数量{task["quantity"]:.4f} @ {task["entry_price"]:.6f}')

        # 解析batch_filled
        if task['batch_filled']:
            batch_filled = json.loads(task['batch_filled'])
            batches = batch_filled.get('batches', [])
            print(f'   完成批次: {len(batches)}/3')

        # 盈亏
        if status == 'closed':
            pnl = task['realized_pnl']
            close_time = (task['close_time'] + timedelta(hours=8)) if task['close_time'] else None
            if close_time:
                print(f'   平仓: {close_time.strftime("%m-%d %H:%M:%S")} | 盈亏{pnl:+.2f}U')
        elif status == 'open':
            pnl = task['unrealized_pnl'] or 0
            print(f'   浮盈: {pnl:+.2f}U')

        print()
else:
    print('  最近24小时没有分批建仓任务')

print('=' * 120)

conn.close()
