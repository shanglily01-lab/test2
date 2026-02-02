#!/usr/bin/env python3
"""
分析最近24小时的开仓信号统计
重点关注: Big4状态, momentum信号, 胜率
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import pymysql
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta
from collections import defaultdict

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

# 计算24小时前的时间
time_24h_ago = datetime.now() - timedelta(hours=24)

print('=' * 100)
print(f'最近24小时开仓信号分析 (从 {time_24h_ago.strftime("%Y-%m-%d %H:%M")} 到现在)')
print('=' * 100)

# 1. 总体统计
cursor.execute('''
    SELECT
        COUNT(*) as total_orders,
        SUM(CASE WHEN position_side = 'LONG' THEN 1 ELSE 0 END) as long_count,
        SUM(CASE WHEN position_side = 'SHORT' THEN 1 ELSE 0 END) as short_count,
        SUM(CASE WHEN status = 'CLOSED' THEN 1 ELSE 0 END) as closed_count,
        SUM(CASE WHEN status = 'CLOSED' AND realized_pnl > 0 THEN 1 ELSE 0 END) as win_count,
        SUM(CASE WHEN status = 'CLOSED' AND realized_pnl <= 0 THEN 1 ELSE 0 END) as loss_count,
        SUM(CASE WHEN status = 'CLOSED' THEN realized_pnl ELSE 0 END) as total_pnl,
        AVG(entry_score) as avg_score
    FROM paper_trading_orders
    WHERE created_at >= %s
''', (time_24h_ago,))

stats = cursor.fetchone()

print('\n【总体统计】')
print(f'总开仓数: {stats["total_orders"]}笔')
print(f'  - LONG: {stats["long_count"]}笔 ({stats["long_count"]/stats["total_orders"]*100:.1f}%)')
print(f'  - SHORT: {stats["short_count"]}笔 ({stats["short_count"]/stats["total_orders"]*100:.1f}%)')
print(f'\n已平仓: {stats["closed_count"]}笔')
if stats["closed_count"] > 0:
    win_rate = stats["win_count"] / stats["closed_count"] * 100
    print(f'  - 盈利: {stats["win_count"]}笔')
    print(f'  - 亏损: {stats["loss_count"]}笔')
    print(f'  - 胜率: {win_rate:.1f}%')
    print(f'  - 盈亏: ${stats["total_pnl"]:.2f}')
print(f'\n平均入场评分: {stats["avg_score"]:.1f}分')

# 2. 按信号组合统计
print('\n' + '=' * 100)
print('【信号组合统计】(已平仓订单)')
print('=' * 100)

cursor.execute('''
    SELECT
        entry_signal_type,
        position_side,
        COUNT(*) as total,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) as losses,
        SUM(realized_pnl) as total_pnl,
        AVG(entry_score) as avg_score
    FROM paper_trading_orders
    WHERE created_at >= %s
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
        losses = sig['losses']
        pnl = float(sig['total_pnl'])
        win_rate = wins / total * 100 if total > 0 else 0
        avg_score = sig['avg_score']

        # 标记包含momentum的信号
        has_momentum = 'momentum' in signal_type.lower() if signal_type != '未知' else False
        momentum_mark = ' 🔴 MOMENTUM' if has_momentum else ''

        # 标记胜率
        if win_rate >= 60:
            rate_mark = '✅'
        elif win_rate >= 45:
            rate_mark = '⚠️'
        else:
            rate_mark = '❌'

        print(f'\n{rate_mark} {signal_type[:60]:60} ({side}){momentum_mark}')
        print(f'   数量: {total}笔 | 胜率: {win_rate:.1f}% ({wins}赢{losses}输) | 盈亏: ${pnl:+.2f} | 评分: {avg_score:.1f}')
else:
    print('无已平仓订单')

# 3. 检查是否有big4_market_signal字段
cursor.execute('''
    SHOW COLUMNS FROM orders_futures LIKE 'big4_market_signal'
''')
has_big4_field = cursor.fetchone()

if has_big4_field:
    print('\n' + '=' * 100)
    print('【Big4市场信号统计】')
    print('=' * 100)

    cursor.execute('''
        SELECT
            big4_market_signal,
            COUNT(*) as total,
            SUM(CASE WHEN status = 'CLOSED' THEN 1 ELSE 0 END) as closed,
            SUM(CASE WHEN status = 'CLOSED' AND realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN status = 'CLOSED' THEN realized_pnl ELSE 0 END) as total_pnl
        FROM orders_futures
        WHERE account_id = 2
        AND created_at >= %s
        GROUP BY big4_market_signal
        ORDER BY total DESC
    ''', (time_24h_ago,))

    big4_stats = cursor.fetchall()

    for stat in big4_stats:
        signal = stat['big4_market_signal'] or 'NULL/未记录'
        total = stat['total']
        closed = stat['closed']
        wins = stat['wins']
        pnl = float(stat['total_pnl'])

        if closed > 0:
            win_rate = wins / closed * 100
            print(f'\n{signal:15} | 总数: {total:3}笔 | 已平: {closed:3}笔 | 胜率: {win_rate:5.1f}% | 盈亏: ${pnl:+8.2f}')
        else:
            print(f'\n{signal:15} | 总数: {total:3}笔 | 已平: 0笔 (全部持仓中)')
else:
    print('\n⚠️  数据库中没有big4_market_signal字段，无法统计Big4信号')

# 4. momentum信号专项统计
print('\n' + '=' * 100)
print('【Momentum信号专项统计】')
print('=' * 100)

cursor.execute('''
    SELECT
        position_side,
        COUNT(*) as total,
        SUM(CASE WHEN status = 'CLOSED' THEN 1 ELSE 0 END) as closed,
        SUM(CASE WHEN status = 'CLOSED' AND realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN status = 'CLOSED' THEN realized_pnl ELSE 0 END) as total_pnl,
        AVG(entry_score) as avg_score
    FROM paper_trading_orders
    WHERE created_at >= %s
    AND (entry_signal_type LIKE '%momentum%' OR entry_signal_type LIKE '%涨势%' OR entry_signal_type LIKE '%跌势%')
    GROUP BY position_side
''', (time_24h_ago,))

momentum_stats = cursor.fetchall()

if momentum_stats:
    for stat in momentum_stats:
        side = stat['position_side']
        total = stat['total']
        closed = stat['closed']
        wins = stat['wins']
        pnl = float(stat['total_pnl'])
        avg_score = stat['avg_score']

        if closed > 0:
            win_rate = wins / closed * 100
            losses = closed - wins
            print(f'\nMomentum ({side}):')
            print(f'  总数: {total}笔 | 已平: {closed}笔 ({wins}赢{losses}输)')
            print(f'  胜率: {win_rate:.1f}% | 盈亏: ${pnl:+.2f} | 平均评分: {avg_score:.1f}')
        else:
            print(f'\nMomentum ({side}): {total}笔 (全部持仓中)')
else:
    print('最近24小时无momentum相关信号')

# 5. 按小时统计开仓频率
print('\n' + '=' * 100)
print('【24小时开仓频率分布】')
print('=' * 100)

cursor.execute('''
    SELECT
        DATE_FORMAT(created_at, '%Y-%m-%d %H:00') as hour,
        COUNT(*) as count,
        SUM(CASE WHEN status = 'CLOSED' AND realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN status = 'CLOSED' AND realized_pnl <= 0 THEN 1 ELSE 0 END) as losses
    FROM paper_trading_orders
    WHERE created_at >= %s
    GROUP BY hour
    ORDER BY hour DESC
    LIMIT 24
''', (time_24h_ago,))

hourly_stats = cursor.fetchall()

if hourly_stats:
    print('\n小时         | 开仓数 | 盈利 | 亏损')
    print('-' * 50)
    for stat in hourly_stats:
        hour = stat['hour']
        count = stat['count']
        wins = stat['wins']
        losses = stat['losses']
        bar = '█' * min(count, 50)
        print(f'{hour} | {count:3}笔 {bar:10} | {wins:2}赢 | {losses:2}输')

# 6. 最差表现的交易对
print('\n' + '=' * 100)
print('【24小时最差交易对 TOP10】(已平仓)')
print('=' * 100)

cursor.execute('''
    SELECT
        symbol,
        COUNT(*) as total,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl
    FROM paper_trading_orders
    WHERE created_at >= %s
    AND status = 'CLOSED'
    GROUP BY symbol
    HAVING total >= 2
    ORDER BY total_pnl ASC
    LIMIT 10
''', (time_24h_ago,))

worst_symbols = cursor.fetchall()

if worst_symbols:
    for i, sym in enumerate(worst_symbols, 1):
        symbol = sym['symbol']
        total = sym['total']
        wins = sym['wins']
        pnl = float(sym['total_pnl'])
        losses = total - wins
        win_rate = wins / total * 100 if total > 0 else 0

        print(f'{i:2}. {symbol:15} | {total}笔 ({wins}赢{losses}输) | 胜率{win_rate:.1f}% | 亏损${pnl:.2f}')

cursor.close()
conn.close()

print('\n' + '=' * 100)
print('分析完成')
print('=' * 100)
