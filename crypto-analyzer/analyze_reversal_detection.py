#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析当前的紧急反转检测逻辑 vs 实际市场数据
"""
import pymysql
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4'
}

conn = pymysql.connect(**db_config)
cursor = conn.cursor(pymysql.cursors.DictCursor)

BIG4 = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']

print('=' * 100)
print('Big4 紧急反转检测分析')
print('=' * 100)

# 配置参数
DETECTION_HOURS = 4
BOTTOM_THRESHOLD = -5.0
TOP_THRESHOLD = 5.0

for symbol in BIG4:
    print(f'\n{symbol}:')
    print('-' * 80)

    # 获取最近N小时的1H K线
    cursor.execute("""
        SELECT
            open_time,
            ROUND(open_price, 2) as open,
            ROUND(high_price, 2) as high,
            ROUND(low_price, 2) as low,
            ROUND(close_price, 2) as close
        FROM kline_data
        WHERE symbol = %s
        AND timeframe = '1h'
        AND exchange = 'binance_futures'
        ORDER BY open_time DESC
        LIMIT %s
    """, (symbol, DETECTION_HOURS))

    rows = cursor.fetchall()

    if not rows or len(rows) < 2:
        print('  数据不足')
        continue

    # 计算波动
    highs = [float(r['high']) for r in rows]
    lows = [float(r['low']) for r in rows]

    max_high = max(highs)
    min_low = min(lows)
    latest_close = float(rows[0]['close'])

    # 跌幅
    drop_pct = (min_low - max_high) / max_high * 100
    # 从最低点反弹
    rise_from_low = (latest_close - min_low) / min_low * 100

    # 涨幅
    rise_pct = (max_high - min_low) / min_low * 100
    # 从最高点回调
    drop_from_high = (latest_close - max_high) / max_high * 100

    print(f'  最近{DETECTION_HOURS}小时:')
    print(f'    最高: {max_high:.2f}')
    print(f'    最低: {min_low:.2f}')
    print(f'    当前: {latest_close:.2f}')
    print(f'    跌幅: {drop_pct:.2f}%')
    print(f'    从最低反弹: {rise_from_low:.2f}%')
    print(f'    涨幅: {rise_pct:.2f}%')
    print(f'    从最高回调: {drop_from_high:.2f}%')

    # 检测触底
    bottom_detected = drop_pct <= BOTTOM_THRESHOLD and rise_from_low > 0
    # 检测触顶
    top_detected = rise_pct >= TOP_THRESHOLD and drop_from_high < 0

    if bottom_detected:
        print(f'    >>> 触底反转检测: 是 (跌{drop_pct:.1f}% > {BOTTOM_THRESHOLD}%, 反弹{rise_from_low:.1f}%)')
        print(f'    >>> 建议: 禁止做空')
    elif top_detected:
        print(f'    >>> 触顶回调检测: 是 (涨{rise_pct:.1f}% > {TOP_THRESHOLD}%, 回调{drop_from_high:.1f}%)')
        print(f'    >>> 建议: 禁止做多')
    else:
        print(f'    >>> 无紧急反转')

# 综合判断
print('\n' + '=' * 100)
print('Big4综合判断:')
print('=' * 100)

bottom_count = 0
top_count = 0

for symbol in BIG4:
    cursor.execute("""
        SELECT
            ROUND(high_price, 2) as high,
            ROUND(low_price, 2) as low,
            ROUND(close_price, 2) as close
        FROM kline_data
        WHERE symbol = %s
        AND timeframe = '1h'
        AND exchange = 'binance_futures'
        ORDER BY open_time DESC
        LIMIT %s
    """, (symbol, DETECTION_HOURS))

    rows = cursor.fetchall()
    if not rows or len(rows) < 2:
        continue

    highs = [float(r['high']) for r in rows]
    lows = [float(r['low']) for r in rows]
    max_high = max(highs)
    min_low = min(lows)
    latest_close = float(rows[0]['close'])

    drop_pct = (min_low - max_high) / max_high * 100
    rise_from_low = (latest_close - min_low) / min_low * 100
    rise_pct = (max_high - min_low) / min_low * 100
    drop_from_high = (latest_close - max_high) / max_high * 100

    if drop_pct <= BOTTOM_THRESHOLD and rise_from_low > 0:
        bottom_count += 1
    if rise_pct >= TOP_THRESHOLD and drop_from_high < 0:
        top_count += 1

print(f'\n触底币种数: {bottom_count}/4')
print(f'触顶币种数: {top_count}/4')

if bottom_count >= 2:
    print('\n 紧急干预: 禁止做空 (至少2个币种触底)')
elif top_count >= 2:
    print('\n 紧急干预: 禁止做多 (至少2个币种触顶)')
else:
    print('\n 无紧急干预')

# 查看2月9日早上7点的剧烈波动
print('\n' + '=' * 100)
print('2月9日早上7点 BTC剧烈波动分析 (72300 -> 69913):')
print('=' * 100)

cursor.execute("""
    SELECT
        open_time,
        ROUND(open_price, 2) as open,
        ROUND(high_price, 2) as high,
        ROUND(low_price, 2) as low,
        ROUND(close_price, 2) as close
    FROM kline_data
    WHERE symbol = 'BTC/USDT'
    AND timeframe = '15m'
    AND exchange = 'binance_futures'
    AND open_time >= 1770577200000  # 2026-02-09 03:00
    AND open_time <= 1770595200000  # 2026-02-09 08:00
    ORDER BY open_time ASC
""")

rows = cursor.fetchall()

if rows:
    print('\n15M K线 (03:00-08:00):')
    print(f"{'时间':20} {'开盘':>10} {'最高':>10} {'最低':>10} {'收盘':>10} {'涨跌%':>10}")
    print('-' * 80)

    for row in rows:
        ts = int(row['open_time']) / 1000
        dt = datetime.fromtimestamp(ts)
        open_p = float(row['open'])
        close_p = float(row['close'])
        change = ((close_p - open_p) / open_p * 100) if open_p else 0

        marker = ''
        if change < -1.0:
            marker = ' <<<< 大跌'
        elif change > 1.0:
            marker = ' >>>> 大涨'

        print(f"{dt.strftime('%Y-%m-%d %H:%M'):20} {row['open']:>10} {row['high']:>10} {row['low']:>10} {row['close']:>10} {change:>9.2f}%{marker}")

# 5M K线看更细节
cursor.execute("""
    SELECT
        open_time,
        ROUND(open_price, 2) as open,
        ROUND(high_price, 2) as high,
        ROUND(low_price, 2) as low,
        ROUND(close_price, 2) as close
    FROM kline_data
    WHERE symbol = 'BTC/USDT'
    AND timeframe = '5m'
    AND exchange = 'binance_futures'
    AND open_time >= 1770588000000  # 2026-02-09 06:00
    AND open_time <= 1770595200000  # 2026-02-09 08:00
    ORDER BY open_time ASC
""")

rows = cursor.fetchall()

if rows:
    print('\n5M K线 (06:00-08:00 精确反转点):')
    print(f"{'时间':20} {'开盘':>10} {'最高':>10} {'最低':>10} {'收盘':>10} {'涨跌%':>10}")
    print('-' * 80)

    min_price = min([float(r['low']) for r in rows])
    max_price = max([float(r['high']) for r in rows])

    for row in rows:
        ts = int(row['open_time']) / 1000
        dt = datetime.fromtimestamp(ts)
        open_p = float(row['open'])
        close_p = float(row['close'])
        low_p = float(row['low'])
        high_p = float(row['high'])
        change = ((close_p - open_p) / open_p * 100) if open_p else 0

        marker = ''
        if low_p == min_price:
            marker = ' [最低点]'
        if high_p == max_price:
            marker = ' [最高点]'
        if change < -0.5:
            marker += ' <<<'
        elif change > 0.5:
            marker += ' >>>'

        print(f"{dt.strftime('%Y-%m-%d %H:%M'):20} {row['open']:>10} {row['high']:>10} {row['low']:>10} {row['close']:>10} {change:>9.2f}%{marker}")

    print(f'\n该时段波动: 最高 {max_price:.2f}, 最低 {min_price:.2f}, 波动 {(max_price-min_price)/min_price*100:.2f}%')

cursor.close()
conn.close()

print('\n' + '=' * 100)
print('分析完成')
print('=' * 100)
