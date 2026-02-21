#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试采集BTC一天的数据"""
import sys
import os
import ccxt
import mysql.connector
from dotenv import load_dotenv
from datetime import datetime, timedelta
from decimal import Decimal

# 设置UTF-8输出
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

def test_collect_btc(target_date):
    """测试采集BTC指定日期的K线数据"""

    print('=' * 100)
    print(f'测试采集BTC {target_date} 的K线数据')
    print('=' * 100)

    # 初始化交易所
    print('初始化交易所...')
    exchange = ccxt.binance({
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })
    print('✅ 交易所初始化完成')

    # 连接数据库
    print('连接数据库...')
    conn = mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        port=int(os.getenv('DB_PORT', 3306)),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME')
    )
    cursor = conn.cursor()
    print('✅ 数据库连接成功')

    # 解析目标日期
    target_dt = datetime.strptime(target_date, '%Y-%m-%d')
    start_timestamp = int(target_dt.timestamp() * 1000)
    end_timestamp = int((target_dt + timedelta(days=1)).timestamp() * 1000)

    print(f'\n目标时间范围:')
    print(f'  开始: {target_dt}')
    print(f'  结束: {target_dt + timedelta(days=1)}')
    print(f'  开始时间戳: {start_timestamp}')
    print(f'  结束时间戳: {end_timestamp}')

    symbol = 'BTC/USDT'
    intervals = {
        '5m': 288,
        '15m': 96,
        '1h': 24,
        '1d': 1
    }

    print(f'\n开始采集 {symbol}')
    print('-' * 100)

    for interval, expected_count in intervals.items():
        print(f'\n采集 {interval} K线...')

        try:
            # 获取K线数据
            print(f'  请求API: symbol={symbol}, interval={interval}, since={start_timestamp}, limit={expected_count + 1}')
            ohlcv = exchange.fetch_ohlcv(
                symbol,
                interval,
                since=start_timestamp,
                limit=expected_count + 1
            )

            print(f'  ✅ 获取到 {len(ohlcv)} 根K线')

            if not ohlcv:
                print(f'  ⚠️  未获取到数据')
                continue

            # 显示获取到的K线时间范围
            first_time = datetime.fromtimestamp(ohlcv[0][0] / 1000)
            last_time = datetime.fromtimestamp(ohlcv[-1][0] / 1000)
            print(f'  时间范围: {first_time} -> {last_time}')

            # 过滤目标日期范围内的K线
            filtered_klines = []
            for kline in ohlcv:
                kline_time = kline[0]
                if start_timestamp <= kline_time < end_timestamp:
                    filtered_klines.append(kline)

            print(f'  过滤后: {len(filtered_klines)} 根在目标日期范围内')

            # 显示前3根K线信息
            if filtered_klines:
                print(f'  前3根K线:')
                for i, kline in enumerate(filtered_klines[:3]):
                    kline_dt = datetime.fromtimestamp(kline[0] / 1000)
                    print(f'    {i+1}. {kline_dt} | O:{kline[1]} H:{kline[2]} L:{kline[3]} C:{kline[4]} V:{kline[5]}')

            # 保存到数据库
            saved_count = 0
            for kline in filtered_klines:
                try:
                    cursor.execute('''
                        INSERT INTO kline_data (
                            exchange, symbol, timeframe, open_time, close_time, timestamp,
                            open_price, high_price, low_price, close_price, volume,
                            quote_volume, number_of_trades, taker_buy_base_volume, taker_buy_quote_volume
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        ) ON DUPLICATE KEY UPDATE
                            open_price = VALUES(open_price),
                            high_price = VALUES(high_price),
                            low_price = VALUES(low_price),
                            close_price = VALUES(close_price),
                            volume = VALUES(volume)
                    ''', (
                        'binance_futures',
                        symbol,
                        interval,
                        kline[0],
                        kline[0] + (int(interval[:-1]) * 60000 if 'm' in interval else int(interval[:-1]) * 3600000),
                        datetime.fromtimestamp(kline[0] / 1000),
                        Decimal(str(kline[1])),
                        Decimal(str(kline[2])),
                        Decimal(str(kline[3])),
                        Decimal(str(kline[4])),
                        Decimal(str(kline[5])),
                        Decimal(str(kline[6] if len(kline) > 6 else kline[5])),
                        0,
                        Decimal('0'),
                        Decimal('0')
                    ))
                    saved_count += 1
                except Exception as e:
                    print(f'  保存K线失败: {e}')

            conn.commit()
            print(f'  ✅ 成功保存 {saved_count} 条到数据库')

        except Exception as e:
            print(f'  ❌ 采集失败: {e}')
            import traceback
            traceback.print_exc()

    cursor.close()
    conn.close()

    print('\n' + '=' * 100)
    print('测试完成')
    print('=' * 100)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print('用法: python test_collect_btc.py YYYY-MM-DD')
        print('例如: python test_collect_btc.py 2026-02-19')
        sys.exit(1)

    test_collect_btc(sys.argv[1])
