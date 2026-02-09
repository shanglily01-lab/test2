#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证Big4最近4小时的实际波动
"""
import pymysql
import os
import sys
from dotenv import load_dotenv
from datetime import datetime, timedelta

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
EMERGENCY_DETECTION_HOURS = 4

print('=' * 120)
print(f'验证Big4最近{EMERGENCY_DETECTION_HOURS}小时的实际波动')
print('=' * 120)

hours_ago = datetime.now() - timedelta(hours=EMERGENCY_DETECTION_HOURS)

for symbol in BIG4:
    print(f'\n{symbol}:')
    print('-' * 120)

    # 获取最近4小时的1H K线
    cursor.execute("""
        SELECT open_price, close_price, low_price, high_price, open_time
        FROM kline_data
        WHERE symbol = %s
        AND timeframe = '1h'
        AND exchange = 'binance_futures'
        AND open_time >= %s
        ORDER BY open_time ASC
    """, (symbol, hours_ago))

    klines = cursor.fetchall()

    if not klines or len(klines) < 2:
        print('  数据不足')
        continue

    print(f'  获取到 {len(klines)} 根K线')

    # 计算期间的最高价和最低价
    period_high = max([float(k['high_price']) for k in klines])
    period_low = min([float(k['low_price']) for k in klines])
    latest_close = float(klines[-1]['close_price'])

    # 从最高点到最低点的跌幅
    drop_pct = (period_low - period_high) / period_high * 100
    # 从最低点到当前的涨幅
    rise_from_low = (latest_close - period_low) / period_low * 100
    # 从最高点到当前的跌幅
    drop_from_high = (latest_close - period_high) / period_high * 100
    # 从最低点到最高点的涨幅
    rise_pct = (period_high - period_low) / period_low * 100

    print(f'  期间最高价: {period_high:.2f}')
    print(f'  期间最低价: {period_low:.2f}')
    print(f'  当前收盘价: {latest_close:.2f}')
    print(f'  ')
    print(f'  📊 波动分析:')
    print(f'    最大跌幅 (高→低): {drop_pct:.2f}%')
    print(f'    最大涨幅 (低→高): {rise_pct:.2f}%')
    print(f'    从低点反弹: {rise_from_low:.2f}%')
    print(f'    从高点回落: {drop_from_high:.2f}%')

    # 触底判断
    BOTTOM_DROP_THRESHOLD = -5.0
    if drop_pct <= BOTTOM_DROP_THRESHOLD and rise_from_low > 0:
        print(f'  🔴 触底检测: ✅ 触发 (跌幅{drop_pct:.2f}% <= {BOTTOM_DROP_THRESHOLD}%, 已反弹{rise_from_low:.2f}%)')
    else:
        print(f'  🟢 触底检测: ❌ 未触发')
        if drop_pct > BOTTOM_DROP_THRESHOLD:
            print(f'     原因: 跌幅{drop_pct:.2f}% > {BOTTOM_DROP_THRESHOLD}%')
        elif rise_from_low <= 0:
            print(f'     原因: 尚未反弹 ({rise_from_low:.2f}%)')

    # 触顶判断
    TOP_RISE_THRESHOLD = 5.0
    if rise_pct >= TOP_RISE_THRESHOLD and drop_from_high < 0:
        print(f'  🔴 触顶检测: ✅ 触发 (涨幅{rise_pct:.2f}% >= {TOP_RISE_THRESHOLD}%, 已回落{drop_from_high:.2f}%)')
    else:
        print(f'  🟢 触顶检测: ❌ 未触发')
        if rise_pct < TOP_RISE_THRESHOLD:
            print(f'     原因: 涨幅{rise_pct:.2f}% < {TOP_RISE_THRESHOLD}%')
        elif drop_from_high >= 0:
            print(f'     原因: 尚未回落 ({drop_from_high:.2f}%)')

cursor.close()
conn.close()

print('\n' + '=' * 120)
print('验证完成')
print('=' * 120)
