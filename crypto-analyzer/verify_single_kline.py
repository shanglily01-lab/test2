#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证单根K线数据"""
import sys
import os
import mysql.connector
from dotenv import load_dotenv
import ccxt

# 设置UTF-8输出
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

# 初始化交易所
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

# 获取历史K线（已完成的K线）
ohlcv = exchange.fetch_ohlcv('BTC/USDT', '15m', limit=50)

# 连接数据库
conn = mysql.connector.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME')
)

cursor = conn.cursor(dictionary=True)

print('=' * 140)
print('验证数据库K线价格准确性 (对比最近10根已完成的15M K线)')
print('=' * 140)
print(f'{"序号":>4} | {"时间戳(UTC)":>19} | {"数据库Open":>12} | {"实时Open":>12} | {"数据库Close":>12} | {"实时Close":>12} | {"DB方向":>4} | {"实时方向":>4} | {"价格匹配"}')
print('-' * 140)

# 对比最近10根K线（跳过最后一根，因为可能还未完成）
for i in range(10, 0, -1):
    rt_candle = ohlcv[-(i+1)]  # 跳过最后一根
    rt_timestamp_ms = rt_candle[0]
    rt_open = rt_candle[1]
    rt_close = rt_candle[4]
    rt_is_bull = rt_close > rt_open
    rt_direction = '阳' if rt_is_bull else '阴'

    # 查询数据库中对应时间戳的K线
    cursor.execute('''
        SELECT open_price, close_price, timestamp
        FROM kline_data
        WHERE symbol = 'BTC/USDT'
        AND timeframe = '15m'
        AND exchange = 'binance_futures'
        AND open_time = %s
        LIMIT 1
    ''', (rt_timestamp_ms,))

    db_kline = cursor.fetchone()

    if db_kline:
        db_open = float(db_kline['open_price'])
        db_close = float(db_kline['close_price'])
        db_is_bull = db_close > db_open
        db_direction = '阳' if db_is_bull else '阴'
        db_timestamp = db_kline['timestamp']

        # 检查价格是否匹配（允许0.1的误差）
        open_match = abs(db_open - rt_open) < 0.1
        close_match = abs(db_close - rt_close) < 0.1
        match_status = '✅' if (open_match and close_match and db_direction == rt_direction) else '❌'

        print(f'{11-i:>4} | {db_timestamp} | {db_open:>12.2f} | {rt_open:>12.2f} | {db_close:>12.2f} | {rt_close:>12.2f} | {db_direction:>4} | {rt_direction:>4} | {match_status}')
    else:
        print(f'{11-i:>4} | 时间戳{rt_timestamp_ms} | 数据库中无此K线记录 ❌')

print('=' * 140)

cursor.close()
conn.close()
