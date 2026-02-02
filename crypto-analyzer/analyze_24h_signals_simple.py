#!/usr/bin/env python3
"""
分析最近24小时的开仓信号（基于pending_positions表）
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import pymysql
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST', '13.212.252.171'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'admin'),
    'password': os.getenv('DB_PASSWORD', 'Tonny@1000'),
    'database': os.getenv('DB_NAME', 'binance-data'),
    'charset': 'utf8mb4'
}

conn = pymysql.connect(**db_config)
cursor = conn.cursor(pymysql.cursors.DictCursor)

time_24h_ago = datetime.now() - timedelta(hours=24)

print('=' * 100)
print(f'最近24小时开仓信号分析 (从 {time_24h_ago.strftime("%Y-%m-%d %H:%M")} 到现在)')
print('=' * 100)

# 1. 总体统计
cursor.execute('''
    SELECT
        COUNT(*) as total,
        SUM(CASE WHEN direction = 'LONG' THEN 1 ELSE 0 END) as long_count,
        SUM(CASE WHEN direction = 'SHORT' THEN 1 ELSE 0 END) as short_count,
        SUM(CASE WHEN status = 'opened' THEN 1 ELSE 0 END) as opened,
        SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected
    FROM pending_positions
    WHERE created_at >= %s
''', (time_24h_ago,))

stats = cursor.fetchone()

print(f'\n【总体统计】')
print(f'总信号数: {stats["total"]}个')
print(f'  - LONG: {stats["long_count"]}个')
print(f'  - SHORT: {stats["short_count"]}个')
print(f'  - 已开仓: {stats["opened"]}个')
print(f'  - 被拒绝: {stats["rejected"]}个')

# 2. 按信号类型统计
print(f'\n{"=" * 100}')
print(f'【信号类型统计】')
print(f'{"=" * 100}')

cursor.execute('''
    SELECT
        signal_type,
        direction,
        COUNT(*) as count,
        SUM(CASE WHEN status = 'opened' THEN 1 ELSE 0 END) as opened,
        SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected
    FROM pending_positions
    WHERE created_at >= %s
    GROUP BY signal_type, direction
    ORDER BY count DESC
    LIMIT 30
''', (time_24h_ago,))

signals = cursor.fetchall()

for sig in signals:
    signal_type = sig['signal_type'] or '未知'
    direction = sig['direction']
    count = sig['count']
    opened = sig['opened']
    rejected = sig['rejected']

    # 标记momentum信号
    has_momentum = 'momentum' in signal_type.lower() or '涨势' in signal_type or '跌势' in signal_type
    momentum_mark = ' 🔴' if has_momentum else ''

    # 计算开仓率
    open_rate = (opened / count * 100) if count > 0 else 0

    print(f'\n{signal_type[:70]:70} ({direction}){momentum_mark}')
    print(f'  总数: {count:3}个 | 开仓: {opened:3}个 | 拒绝: {rejected:3}个 | 开仓率: {open_rate:.1f}%')

# 3. Momentum信号专项统计
print(f'\n{"=" * 100}')
print(f'【Momentum信号统计】')
print(f'{"=" * 100}')

cursor.execute('''
    SELECT
        direction,
        COUNT(*) as total,
        SUM(CASE WHEN status = 'opened' THEN 1 ELSE 0 END) as opened
    FROM pending_positions
    WHERE created_at >= %s
    AND (signal_type LIKE '%momentum%' OR signal_type LIKE '%涨势%' OR signal_type LIKE '%跌势%')
    GROUP BY direction
''', (time_24h_ago,))

momentum_stats = cursor.fetchall()

if momentum_stats:
    total_momentum = sum(s['total'] for s in momentum_stats)
    total_opened = sum(s['opened'] for s in momentum_stats)
    print(f'\nMomentum信号总数: {total_momentum}个 (已开仓: {total_opened}个)')

    for stat in momentum_stats:
        direction = stat['direction']
        total = stat['total']
        opened = stat['opened']
        rate = (opened / total * 100) if total > 0 else 0
        print(f'  {direction}: {total}个信号, {opened}个开仓, 开仓率{rate:.1f}%')
else:
    print('\n最近24小时无momentum信号')

# 4. 按小时统计
print(f'\n{"=" * 100}')
print(f'【每小时信号分布】')
print(f'{"=" * 100}')

cursor.execute('''
    SELECT
        DATE_FORMAT(created_at, '%Y-%m-%d %H:00') as hour,
        COUNT(*) as count
    FROM pending_positions
    WHERE created_at >= %s
    GROUP BY hour
    ORDER BY hour DESC
    LIMIT 24
''', (time_24h_ago,))

hourly = cursor.fetchall()

if hourly:
    print(f'\n小时             | 信号数')
    print(f'{"-" * 40}')
    for h in hourly:
        hour = h['hour']
        count = h['count']
        bar = '█' * min(count, 50)
        print(f'{hour} | {count:3}个 {bar}')

# 5. 被拒绝的信号（含原因）
print(f'\n{"=" * 100}')
print(f'【被拒绝的信号 TOP 10】')
print(f'{"=" * 100}')

cursor.execute('''
    SELECT
        symbol,
        signal_type,
        direction,
        rejection_reason,
        created_at
    FROM pending_positions
    WHERE created_at >= %s
    AND status = 'rejected'
    ORDER BY created_at DESC
    LIMIT 10
''', (time_24h_ago,))

rejected = cursor.fetchall()

if rejected:
    for r in rejected:
        symbol = r['symbol']
        signal_type = r['signal_type'] or '未知'
        direction = r['direction']
        reason = r['rejection_reason'] or '无原因'
        time = r['created_at'].strftime('%H:%M:%S')

        print(f'\n{time} | {symbol:15} | {direction:5} | {signal_type[:40]}')
        print(f'  拒绝原因: {reason[:80]}')
else:
    print('\n无被拒绝的信号')

cursor.close()
conn.close()

print(f'\n{"=" * 100}')
print('分析完成')
print(f'{"=" * 100}')
