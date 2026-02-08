#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
详细分析典型波段 - 提取5M/15M/1H数据用于回测
"""

import pymysql
import os
import sys
import io
from dotenv import load_dotenv
from datetime import datetime

# Set console encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME')
}

def timestamp_to_datetime(ts):
    """将毫秒时间戳转换为可读时间"""
    return datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d %H:%M')

conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
cursor = conn.cursor()

print('='*100)
print('典型波段详细分析')
print('='*100)
print()

# 定义几个典型波段用于分析
test_cases = [
    {
        'name': 'BTC大跌波段',
        'symbol': 'BTC/USDT',
        'start_time': 1769882400000,  # 2026-01-10某时
        'type': 'DOWN',
        'description': '从高点跌-8.15%到$75500'
    },
    {
        'name': 'BTC反弹波段',
        'symbol': 'BTC/USDT',
        'start_time': 1770026400000,
        'type': 'UP',
        'description': '从低点涨+3.89%到$77815'
    },
    {
        'name': 'BNB持续下跌',
        'symbol': 'BNB/USDT',
        'start_time': 1769882400000,
        'type': 'DOWN',
        'description': '从高点跌-7.55%到$749'
    }
]

for case in test_cases:
    print(f'\n{"="*100}')
    print(f'案例: {case["name"]} - {case["description"]}')
    print(f'币种: {case["symbol"]} | 类型: {case["type"]} | 起始时间: {timestamp_to_datetime(case["start_time"])}')
    print('='*100)
    print()

    # 获取该时间点前后的1H数据 (前后各6根，共12小时)
    cursor.execute('''
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
        AND open_time >= %s - 3600000 * 6
        AND open_time <= %s + 3600000 * 6
        ORDER BY open_time ASC
    ''', (case['symbol'], case['start_time'], case['start_time']))

    klines_1h = cursor.fetchall()

    if not klines_1h:
        print('  无1H数据')
        continue

    print(f'1H K线数据 ({len(klines_1h)}根):')
    print(f'{"时间":<20} | {"开盘":<12} | {"最高":<12} | {"最低":<12} | {"收盘":<12} | {"涨跌幅":<10}')
    print('-' * 100)

    prev_close = None
    for k in klines_1h:
        time_str = timestamp_to_datetime(k['open_time'])
        open_p = float(k['open_price'])
        high_p = float(k['high_price'])
        low_p = float(k['low_price'])
        close_p = float(k['close_price'])

        # 计算涨跌幅
        if prev_close:
            change_pct = (close_p - prev_close) / prev_close * 100
            change_str = f'{change_pct:+.2f}%'
        else:
            change_str = '-'

        # 标记起始时间点
        marker = ' <== START' if k['open_time'] == case['start_time'] else ''

        print(f'{time_str:<20} | ${open_p:<11.2f} | ${high_p:<11.2f} | ${low_p:<11.2f} | ${close_p:<11.2f} | {change_str:<10}{marker}')

        prev_close = close_p

    print()

    # 获取该时间点的15M数据 (起始时间前后2小时，共8根15M)
    cursor.execute('''
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
        AND open_time >= %s - 900000 * 8
        AND open_time <= %s + 900000 * 8
        ORDER BY open_time ASC
    ''', (case['symbol'], case['start_time'], case['start_time']))

    klines_15m = cursor.fetchall()

    if klines_15m:
        print(f'15M K线数据 ({len(klines_15m)}根，前后各2小时):')
        print(f'{"时间":<20} | {"收盘":<12} | {"涨跌幅":<10}')
        print('-' * 50)

        prev_close = None
        for k in klines_15m:
            time_str = timestamp_to_datetime(k['open_time'])
            close_p = float(k['close_price'])

            if prev_close:
                change_pct = (close_p - prev_close) / prev_close * 100
                change_str = f'{change_pct:+.2f}%'
            else:
                change_str = '-'

            marker = ' <== START' if k['open_time'] == case['start_time'] else ''
            print(f'{time_str:<20} | ${close_p:<11.2f} | {change_str:<10}{marker}')

            prev_close = close_p
        print()

    # 分析信号质量
    print('信号分析:')

    # 找到起始K线
    start_kline = next((k for k in klines_1h if k['open_time'] == case['start_time']), None)
    if start_kline:
        start_price = float(start_kline['open_price'])

        # 找最近的高点和低点（用于判断趋势）
        recent_klines = [k for k in klines_1h if k['open_time'] < case['start_time']][-6:]
        if len(recent_klines) >= 3:
            recent_highs = [float(k['high_price']) for k in recent_klines]
            recent_lows = [float(k['low_price']) for k in recent_klines]

            # 判断趋势
            if recent_highs[-1] > recent_highs[-3] and recent_lows[-1] > recent_lows[-3]:
                trend = '上升趋势'
            elif recent_highs[-1] < recent_highs[-3] and recent_lows[-1] < recent_lows[-3]:
                trend = '下降趋势'
            else:
                trend = '震荡'

            print(f'  前期趋势: {trend}')
            print(f'  起始价格: ${start_price:.2f}')

            # 计算后续表现
            future_klines = [k for k in klines_1h if k['open_time'] > case['start_time']][:6]
            if future_klines:
                max_gain = max((float(k['high_price']) - start_price) / start_price * 100 for k in future_klines)
                max_loss = min((float(k['low_price']) - start_price) / start_price * 100 for k in future_klines)
                final_price = float(future_klines[-1]['close_price'])
                final_change = (final_price - start_price) / start_price * 100

                print(f'  后续6H表现:')
                print(f'    最大盈利: {max_gain:+.2f}%')
                print(f'    最大亏损: {max_loss:+.2f}%')
                print(f'    最终收益: {final_change:+.2f}% (${final_price:.2f})')

                # 判断超级大脑应该如何操作
                print(f'  超级大脑策略建议:')
                if case['type'] == 'DOWN':
                    print(f'    - 应该做空 (SHORT)')
                    if max_loss < -2:  # 如果最大亏损>2%,说明应该止损
                        print(f'    - 需要设置2%止损')
                    if max_gain > 5:  # 如果最大盈利>5%,说明可以获利
                        print(f'    - 可以获利{max_gain:.2f}%')
                else:  # UP
                    print(f'    - 应该做多 (LONG)')
                    if max_loss < -2:
                        print(f'    - 需要设置2%止损')
                    if max_gain > 5:
                        print(f'    - 可以获利{max_gain:.2f}%')

cursor.close()
conn.close()

print()
print('='*100)
print('典型波段分析完成')
print('='*100)
