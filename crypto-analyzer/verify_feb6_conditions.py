#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证2月6日08:00是否满足过滤条件
"""
import pymysql
import os
import sys
from dotenv import load_dotenv
from datetime import datetime

# Fix Windows encoding
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

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
print('验证2月6日08:00的Big4是否满足过滤条件')
print('=' * 100)

for symbol in BIG4:
    print(f'\n{symbol}:')
    print('-' * 100)

    # 获取2月6日08:00及之前的72根1H K线
    cursor.execute("""
        SELECT open_price, close_price, low_price, high_price, open_time
        FROM kline_data
        WHERE symbol = %s
        AND timeframe = '1h'
        AND exchange = 'binance_futures'
        ORDER BY open_time DESC
        LIMIT 200
    """, (symbol,))

    all_candles = cursor.fetchall()

    # 找到2月6日08:00的K线
    target_candle = None
    target_index = None

    for i, candle in enumerate(all_candles):
        ts = int(candle['open_time']) / 1000 if int(candle['open_time']) > 9999999999 else int(candle['open_time'])
        dt = datetime.fromtimestamp(ts)

        if dt.month == 2 and dt.day == 6 and dt.hour == 8:
            target_candle = candle
            target_index = i
            break

    if not target_candle:
        print('  未找到2月6日08:00的K线')
        continue

    open_p = float(target_candle['open_price'])
    close_p = float(target_candle['close_price'])
    high_p = float(target_candle['high_price'])
    low_p = float(target_candle['low_price'])

    # 计算下影线
    body_low = min(open_p, close_p)
    lower_shadow_pct = (body_low - low_p) / low_p * 100

    print(f'  【当前K线】2月6日08:00')
    print(f'    开: {open_p:.2f}, 高: {high_p:.2f}, 低: {low_p:.2f}, 收: {close_p:.2f}')
    print(f'    下影线: {lower_shadow_pct:.2f}%')
    print(f'    条件1 - 下影线>=3%: {"✅" if lower_shadow_pct >= 3.0 else "❌"}')

    # 获取前72H和24H的数据
    history_72h = all_candles[target_index:target_index + 72]
    history_24h = all_candles[target_index:target_index + 24]

    if len(history_72h) < 72:
        print(f'  数据不足: 只有{len(history_72h)}根1H K线')
        continue

    # 计算72H最高点和跌幅
    high_72h = max([float(k['high_price']) for k in history_72h])
    drop_72h = (low_p - high_72h) / high_72h * 100

    # 计算24H最高点和跌幅
    high_24h = max([float(k['high_price']) for k in history_24h])
    drop_24h = (low_p - high_24h) / high_24h * 100

    print(f'\n  【大周期背景】')
    print(f'    前72H最高: {high_72h:.2f}')
    print(f'    前24H最高: {high_24h:.2f}')
    print(f'    当前最低: {low_p:.2f}')
    print(f'    72H跌幅: {drop_72h:.2f}%')
    print(f'    24H跌幅: {drop_24h:.2f}%')
    print(f'    条件2 - 72H跌幅<=-8%: {"✅" if drop_72h <= -8.0 else "❌"}')
    print(f'    条件3 - 24H跌幅<=-4%: {"✅" if drop_24h <= -4.0 else "❌"}')

    # 检查24H内是否首次触底
    is_first_bottom = True
    prev_shadows = []

    for i, k in enumerate(history_24h[1:], 1):  # 跳过当前K线
        k_open = float(k['open_price'])
        k_close = float(k['close_price'])
        k_low = float(k['low_price'])
        k_body_low = min(k_open, k_close)
        k_shadow = (k_body_low - k_low) / k_low * 100 if k_low > 0 else 0

        if k_shadow >= 3.0:
            is_first_bottom = False
            k_ts = int(k['open_time']) / 1000 if int(k['open_time']) > 9999999999 else int(k['open_time'])
            k_dt = datetime.fromtimestamp(k_ts)
            prev_shadows.append((k_dt, k_shadow))

    print(f'\n  【首次触底判断】')
    print(f'    24H内其他长下影线: {len(prev_shadows)}个')
    if prev_shadows:
        for dt, shadow in prev_shadows:
            print(f'      - {dt.strftime("%m-%d %H:%M")}: 下影{shadow:.2f}%')
    print(f'    条件4 - 首次触底: {"✅" if is_first_bottom else "❌"}')

    # 综合判断
    is_true_deep_v = (
        lower_shadow_pct >= 3.0 and
        drop_72h <= -8.0 and
        drop_24h <= -4.0 and
        is_first_bottom
    )

    print(f'\n  【综合判断】')
    print(f'    真深V反转: {"🚀✅" if is_true_deep_v else "❌"}')

    if not is_true_deep_v:
        print(f'    不满足条件:')
        if lower_shadow_pct < 3.0:
            print(f'      - 下影线{lower_shadow_pct:.2f}% < 3%')
        if drop_72h > -8.0:
            print(f'      - 72H跌幅{drop_72h:.2f}% > -8%')
        if drop_24h > -4.0:
            print(f'      - 24H跌幅{drop_24h:.2f}% > -4%')
        if not is_first_bottom:
            print(f'      - 不是首次触底 (24H内已有{len(prev_shadows)}次长下影线)')

cursor.close()
conn.close()

print('\n' + '=' * 100)
print('验证完成')
print('=' * 100)
print('\n结论:')
print('  如果2月6日08:00不满足条件，说明过滤条件太严格')
print('  需要调整阈值参数')
