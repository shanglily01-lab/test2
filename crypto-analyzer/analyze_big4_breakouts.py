#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析Big4在2月1号之后的破位点
"""
import pymysql
import os
import sys
from dotenv import load_dotenv
from datetime import datetime
from collections import defaultdict

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

print('=' * 120)
print('Big4破位点分析 - 2月1号之后')
print('=' * 120)

# 分析起始时间：2026-02-01 00:00:00
start_time_ms = int(datetime(2026, 2, 1, 0, 0, 0).timestamp() * 1000)

for symbol in BIG4:
    print(f'\n{"=" * 120}')
    print(f'{symbol} 破位分析')
    print(f'{"=" * 120}')

    # 获取1H K线数据
    cursor.execute("""
        SELECT
            open_time,
            open_price,
            high_price,
            low_price,
            close_price,
            volume
        FROM kline_data
        WHERE symbol = %s
        AND timeframe = '1h'
        AND exchange = 'binance_futures'
        AND open_time >= %s
        ORDER BY open_time ASC
    """, (symbol, start_time_ms))

    klines_1h = cursor.fetchall()

    if not klines_1h or len(klines_1h) < 20:
        print(f'  数据不足')
        continue

    print(f'\n📊 1H K线数据: {len(klines_1h)} 根')

    # 分析支撑阻力位
    print(f'\n🔍 支撑阻力位识别（滚动窗口分析）')
    print('-' * 120)

    breakouts = []

    # 使用滚动窗口（每20根K线为一个窗口）
    for i in range(20, len(klines_1h)):
        window = klines_1h[i-20:i]
        current = klines_1h[i]

        # 找前20根的最高点和最低点
        highs = [float(k['high_price']) for k in window]
        lows = [float(k['low_price']) for k in window]

        resistance = max(highs)  # 阻力位
        support = min(lows)      # 支撑位

        current_close = float(current['close_price'])
        current_high = float(current['high_price'])
        current_low = float(current['low_price'])

        # 检测向上破位
        if current_high > resistance * 1.001:  # 突破阻力位0.1%
            breakout_strength = (current_high - resistance) / resistance * 100

            # 转换时间戳为日期时间
            breakout_time = datetime.fromtimestamp(current['open_time'] / 1000)

            breakouts.append({
                'time': breakout_time,
                'type': '向上破位',
                'level': resistance,
                'current_price': current_close,
                'breakout_price': current_high,
                'strength': breakout_strength,
                'volume': float(current['volume'])
            })

        # 检测向下破位
        if current_low < support * 0.999:  # 跌破支撑位0.1%
            breakdown_strength = (support - current_low) / support * 100

            breakdown_time = datetime.fromtimestamp(current['open_time'] / 1000)

            breakouts.append({
                'time': breakdown_time,
                'type': '向下破位',
                'level': support,
                'current_price': current_close,
                'breakout_price': current_low,
                'strength': breakdown_strength,
                'volume': float(current['volume'])
            })

    # 去重和过滤（同一方向连续破位只保留第一次）
    filtered_breakouts = []
    last_breakout_time = None
    last_breakout_type = None

    for b in breakouts:
        # 如果距离上次破位超过4小时，或者方向不同，则保留
        if (last_breakout_time is None or
            (b['time'] - last_breakout_time).total_seconds() > 4 * 3600 or
            b['type'] != last_breakout_type):
            filtered_breakouts.append(b)
            last_breakout_time = b['time']
            last_breakout_type = b['type']

    print(f'\n🎯 发现 {len(filtered_breakouts)} 个关键破位点:\n')

    if filtered_breakouts:
        for idx, b in enumerate(filtered_breakouts, 1):
            print(f'{idx}. {b["time"].strftime("%Y-%m-%d %H:%M")} - {b["type"]}')
            print(f'   破位位置: {b["level"]:.2f}')
            print(f'   破位价格: {b["breakout_price"]:.2f}')
            print(f'   收盘价格: {b["current_price"]:.2f}')
            print(f'   破位强度: {b["strength"]:.2f}%')
            print(f'   成交量: {b["volume"]:.2f}')
            print()

        # 对每个破位点，获取前后的15M K线数据
        print(f'\n📈 破位点详细K线数据')
        print('=' * 120)

        for idx, b in enumerate(filtered_breakouts, 1):
            print(f'\n破位点 #{idx}: {b["time"].strftime("%Y-%m-%d %H:%M")} - {b["type"]}')
            print('-' * 120)

            # 获取破位时间前后2小时的15M K线（8根15M = 2小时）
            breakout_time_ms = int(b['time'].timestamp() * 1000)
            before_time_ms = breakout_time_ms - 2 * 3600 * 1000  # 前2小时
            after_time_ms = breakout_time_ms + 2 * 3600 * 1000   # 后2小时

            cursor.execute("""
                SELECT
                    open_time,
                    open_price,
                    high_price,
                    low_price,
                    close_price,
                    volume
                FROM kline_data
                WHERE symbol = %s
                AND timeframe = '15m'
                AND exchange = 'binance_futures'
                AND open_time >= %s
                AND open_time <= %s
                ORDER BY open_time ASC
            """, (symbol, before_time_ms, after_time_ms))

            klines_15m = cursor.fetchall()

            print(f'\n  15M K线数据 (破位前后2小时):')
            print(f'  {"时间":<20} {"开盘":<12} {"最高":<12} {"最低":<12} {"收盘":<12} {"成交量":<15} {"备注"}')
            print(f'  {"-" * 110}')

            for k in klines_15m:
                k_time = datetime.fromtimestamp(k['open_time'] / 1000)
                time_str = k_time.strftime('%Y-%m-%d %H:%M')

                # 标记破位K线
                remark = ''
                if abs((k['open_time'] - breakout_time_ms) / 1000) < 15 * 60:  # 15分钟内
                    remark = '← 破位K线'

                print(f'  {time_str:<20} '
                      f'{float(k["open_price"]):<12.2f} '
                      f'{float(k["high_price"]):<12.2f} '
                      f'{float(k["low_price"]):<12.2f} '
                      f'{float(k["close_price"]):<12.2f} '
                      f'{float(k["volume"]):<15.2f} '
                      f'{remark}')

            # 获取破位时刻的1H K线
            cursor.execute("""
                SELECT
                    open_time,
                    open_price,
                    high_price,
                    low_price,
                    close_price,
                    volume
                FROM kline_data
                WHERE symbol = %s
                AND timeframe = '1h'
                AND exchange = 'binance_futures'
                AND open_time >= %s
                AND open_time <= %s
                ORDER BY open_time ASC
            """, (symbol, before_time_ms, after_time_ms))

            klines_1h_detail = cursor.fetchall()

            print(f'\n  1H K线数据 (破位前后2小时):')
            print(f'  {"时间":<20} {"开盘":<12} {"最高":<12} {"最低":<12} {"收盘":<12} {"成交量":<15} {"备注"}')
            print(f'  {"-" * 110}')

            for k in klines_1h_detail:
                k_time = datetime.fromtimestamp(k['open_time'] / 1000)
                time_str = k_time.strftime('%Y-%m-%d %H:%M')

                # 标记破位K线
                remark = ''
                if abs((k['open_time'] - breakout_time_ms) / 1000) < 60 * 60:  # 1小时内
                    remark = '← 破位K线'

                print(f'  {time_str:<20} '
                      f'{float(k["open_price"]):<12.2f} '
                      f'{float(k["high_price"]):<12.2f} '
                      f'{float(k["low_price"]):<12.2f} '
                      f'{float(k["close_price"]):<12.2f} '
                      f'{float(k["volume"]):<15.2f} '
                      f'{remark}')

    else:
        print('  未发现明显的破位点')

cursor.close()
conn.close()

print('\n' + '=' * 120)
print('分析完成')
print('=' * 120)
