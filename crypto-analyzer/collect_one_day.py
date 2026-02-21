#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集指定日期的K线数据"""
import sys
import os
import ccxt
import mysql.connector
import yaml
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime, timedelta
from decimal import Decimal

# 设置UTF-8输出
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

def collect_day_data(target_date):
    """采集指定日期的K线数据

    Args:
        target_date: 目标日期字符串，格式：YYYY-MM-DD
    """
    # 初始化交易所
    exchange = ccxt.binance({
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })

    # 连接数据库
    conn = mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        port=int(os.getenv('DB_PORT', 3306)),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME')
    )
    cursor = conn.cursor()

    # 解析目标日期
    target_dt = datetime.strptime(target_date, '%Y-%m-%d')
    start_timestamp = int(target_dt.timestamp() * 1000)
    end_timestamp = int((target_dt + timedelta(days=1)).timestamp() * 1000)

    print('=' * 100)
    print(f'采集 {target_date} 的K线数据')
    print('=' * 100)
    print()

    # 从配置文件读取交易对列表
    config_path = Path(__file__).parent / 'config.yaml'
    if not config_path.exists():
        print(f'❌ 配置文件不存在: {config_path}')
        return

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
        futures_symbols = config.get('symbols', [])

    if not futures_symbols:
        print('❌ 配置文件中没有找到交易对列表')
        return

    print(f'目标: {len(futures_symbols)} 个交易对')
    print()

    # 定义时间周期和数量
    intervals_config = {
        '5m': 288,   # 1天 = 24 * 60 / 5 = 288根
        '15m': 96,   # 1天 = 24 * 60 / 15 = 96根
        '1h': 24,    # 1天 = 24根
        '1d': 1      # 1天 = 1根
    }

    total_saved = {'5m': 0, '15m': 0, '1h': 0, '1d': 0}
    total_symbols = len(futures_symbols)

    for interval, expected_count in intervals_config.items():
        print(f'开始采集 {interval} K线...')
        interval_count = 0

        for idx, symbol in enumerate(futures_symbols, 1):
            try:
                # 获取K线数据
                ohlcv = exchange.fetch_ohlcv(
                    symbol,
                    interval,
                    since=start_timestamp,
                    limit=expected_count + 1  # 多取1根以防万一
                )

                if not ohlcv:
                    continue

                # 过滤出目标日期范围内的K线
                filtered_klines = []
                for kline in ohlcv:
                    kline_time = kline[0]
                    if start_timestamp <= kline_time < end_timestamp:
                        filtered_klines.append(kline)

                # 保存到数据库
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
                                volume = VALUES(volume),
                                quote_volume = VALUES(quote_volume),
                                number_of_trades = VALUES(number_of_trades),
                                taker_buy_base_volume = VALUES(taker_buy_base_volume),
                                taker_buy_quote_volume = VALUES(taker_buy_quote_volume)
                        ''', (
                            'binance_futures',
                            symbol,
                            interval,
                            kline[0],  # open_time
                            kline[0] + (int(interval[:-1]) * 60000 if 'm' in interval else int(interval[:-1]) * 3600000),  # close_time
                            datetime.fromtimestamp(kline[0] / 1000),
                            Decimal(str(kline[1])),  # open
                            Decimal(str(kline[2])),  # high
                            Decimal(str(kline[3])),  # low
                            Decimal(str(kline[4])),  # close
                            Decimal(str(kline[5])),  # volume
                            Decimal(str(kline[5]) if len(kline) < 7 else str(kline[6])),  # quote_volume
                            0,  # number_of_trades
                            Decimal('0'),  # taker_buy_base_volume
                            Decimal('0')   # taker_buy_quote_volume
                        ))
                        total_saved[interval] += 1
                        interval_count += 1
                    except Exception as e:
                        pass  # 忽略重复键错误

                # 显示进度
                if idx % 10 == 0 or idx == total_symbols:
                    print(f'  进度: {idx}/{total_symbols} | 已保存: {interval_count} 条', end='\r')
                    sys.stdout.flush()

            except Exception as e:
                print(f'  ❌ {symbol} {interval} 失败: {e}')
                continue

        conn.commit()
        print(f'\n  ✅ {interval} 完成，共保存 {total_saved[interval]} 条')

    print()
    print('=' * 100)
    print('采集完成统计：')
    for interval, count in total_saved.items():
        print(f'  {interval}: {count} 条')
    print('=' * 100)

    cursor.close()
    conn.close()


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print('用法: python collect_one_day.py YYYY-MM-DD')
        print('例如: python collect_one_day.py 2026-02-19')
        sys.exit(1)

    collect_day_data(sys.argv[1])
