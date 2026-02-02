#!/usr/bin/env python3
"""
分析最近24小时的合约开仓信号（基于futures_positions表）
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
print(f'最近24小时合约开仓信号分析 (从 {time_24h_ago.strftime("%Y-%m-%d %H:%M")} 到现在)')
print('=' * 100)

# 1. 总体统计
cursor.execute('''
    SELECT
        COUNT(*) as total,
        SUM(CASE WHEN position_side = 'LONG' THEN 1 ELSE 0 END) as long_count,
        SUM(CASE WHEN position_side = 'SHORT' THEN 1 ELSE 0 END) as short_count,
        SUM(CASE WHEN status = 'CLOSED' THEN 1 ELSE 0 END) as closed,
        SUM(CASE WHEN status = 'CLOSED' AND realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN status = 'CLOSED' THEN realized_pnl ELSE 0 END) as total_pnl,
        AVG(entry_score) as avg_score
    FROM futures_positions
    WHERE account_id = 2
    AND open_time >= %s
''', (time_24h_ago,))

stats = cursor.fetchone()

print(f'\n【总体统计】')
print(f'总开仓数: {stats["total"]}笔')
if stats["total"] > 0:
    print(f'  - LONG: {stats["long_count"]}笔 ({stats["long_count"]/stats["total"]*100:.1f}%)')
    print(f'  - SHORT: {stats["short_count"]}笔 ({stats["short_count"]/stats["total"]*100:.1f}%)')
    print(f'\n已平仓: {stats["closed"]}笔')

    if stats["closed"] and stats["closed"] > 0:
        win_rate = stats["wins"] / stats["closed"] * 100
        losses = stats["closed"] - stats["wins"]
        print(f'  - 盈利: {stats["wins"]}笔')
        print(f'  - 亏损: {losses}笔')
        print(f'  - 胜率: {win_rate:.1f}%')
        print(f'  - 盈亏: ${stats["total_pnl"]:.2f}')

    if stats["avg_score"]:
        print(f'\n平均入场评分: {stats["avg_score"]:.1f}分')
else:
    print('  无开仓记录')

# 2. 按信号类型统计
print(f'\n{"=" * 100}')
print(f'【信号类型统计】(已平仓)')
print(f'{"=" * 100}')

cursor.execute('''
    SELECT
        entry_signal_type,
        position_side,
        COUNT(*) as total,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl,
        AVG(entry_score) as avg_score
    FROM futures_positions
    WHERE account_id = 2
    AND open_time >= %s
    AND status = 'CLOSED'
    GROUP BY entry_signal_type, position_side
    ORDER BY total DESC
    LIMIT 20
''', (time_24h_ago,))

signals = cursor.fetchall()

if signals:
    for sig in signals:
        signal_type = sig['entry_signal_type'] or '未知'
        side = sig['position_side']
        total = sig['total']
        wins = sig['wins']
        pnl = float(sig['total_pnl'] or 0)
        score = sig['avg_score'] or 0

        losses = total - wins
        win_rate = (wins / total * 100) if total > 0 else 0

        # 标记momentum信号
        has_momentum = 'momentum' in signal_type.lower() or '涨势' in signal_type or '跌势' in signal_type
        momentum_mark = ' 🔴 MOMENTUM' if has_momentum else ''

        # 标记胜率
        if win_rate >= 60:
            rate_mark = '✅'
        elif win_rate >= 45:
            rate_mark = '⚠️'
        else:
            rate_mark = '❌'

        print(f'\n{rate_mark} {signal_type[:65]:65} ({side}){momentum_mark}')
        print(f'   {total}笔 | 胜率{win_rate:.1f}% ({wins}赢{losses}输) | 盈亏${pnl:+.2f} | 评分{score:.1f}')
else:
    print('\n无已平仓订单')

# 3. Momentum信号统计
print(f'\n{"=" * 100}')
print(f'【Momentum信号统计】')
print(f'{"=" * 100}')

cursor.execute('''
    SELECT
        position_side,
        COUNT(*) as total,
        SUM(CASE WHEN status = 'CLOSED' THEN 1 ELSE 0 END) as closed,
        SUM(CASE WHEN status = 'CLOSED' AND realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN status = 'CLOSED' THEN realized_pnl ELSE 0 END) as total_pnl
    FROM futures_positions
    WHERE account_id = 2
    AND open_time >= %s
    AND (entry_signal_type LIKE '%momentum%' OR entry_signal_type LIKE '%涨势%' OR entry_signal_type LIKE '%跌势%')
    GROUP BY position_side
''', (time_24h_ago,))

momentum_stats = cursor.fetchall()

if momentum_stats:
    for stat in momentum_stats:
        side = stat['position_side']
        total = stat['total']
        closed = stat['closed']
        wins = stat['wins'] or 0
        pnl = float(stat['total_pnl'] or 0)

        if closed > 0:
            win_rate = wins / closed * 100
            print(f'\nMomentum ({side}):')
            print(f'  总数: {total}笔 | 已平: {closed}笔 ({wins}赢{closed-wins}输)')
            print(f'  胜率: {win_rate:.1f}% | 盈亏: ${pnl:+.2f}')
        else:
            print(f'\nMomentum ({side}): {total}笔 (全部持仓中)')
else:
    print('\n最近24小时无momentum信号')

# 4. 按小时统计
print(f'\n{"=" * 100}')
print(f'【每小时开仓分布】')
print(f'{"=" * 100}')

cursor.execute('''
    SELECT
        DATE_FORMAT(open_time, '%%Y-%%m-%%d %%H:00') as hour,
        COUNT(*) as count,
        SUM(CASE WHEN status = 'CLOSED' AND realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN status = 'CLOSED' AND realized_pnl <= 0 THEN 1 ELSE 0 END) as losses
    FROM futures_positions
    WHERE account_id = 2
    AND open_time >= %s
    GROUP BY hour
    ORDER BY hour DESC
    LIMIT 24
''', (time_24h_ago,))

hourly = cursor.fetchall()

if hourly:
    print(f'\n小时             | 开仓数 | 盈利 | 亏损')
    print(f'{"-" * 50}')
    for h in hourly:
        hour = h['hour']
        count = h['count']
        wins = h['wins'] or 0
        losses = h['losses'] or 0
        bar = '█' * min(count, 30)
        print(f'{hour} | {count:3}笔 {bar:15} | {wins:2}赢 | {losses:2}输')

# 5. 最差交易对
print(f'\n{"=" * 100}')
print(f'【24小时最差交易对 TOP10】(已平仓)')
print(f'{"=" * 100}')

cursor.execute('''
    SELECT
        symbol,
        COUNT(*) as total,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl
    FROM futures_positions
    WHERE account_id = 2
    AND open_time >= %s
    AND status = 'CLOSED'
    GROUP BY symbol
    HAVING total >= 2
    ORDER BY total_pnl ASC
    LIMIT 10
''', (time_24h_ago,))

worst = cursor.fetchall()

if worst:
    for i, w in enumerate(worst, 1):
        symbol = w['symbol']
        total = w['total']
        wins = w['wins'] or 0
        pnl = float(w['total_pnl'] or 0)
        losses = total - wins
        win_rate = (wins / total * 100) if total > 0 else 0

        print(f'{i:2}. {symbol:15} | {total}笔 ({wins}赢{losses}输) | 胜率{win_rate:.1f}% | ${pnl:+.2f}')
else:
    print('\n数据不足')

cursor.close()
conn.close()

print(f'\n{"=" * 100}')
print('分析完成')
print(f'{"=" * 100}')
