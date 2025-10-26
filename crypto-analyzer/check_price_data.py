#!/usr/bin/env python3
"""
检查数据库中有哪些交易对的价格数据
"""

import pymysql
import yaml
from datetime import datetime, timedelta

def check_price_data():
    """检查 price_data 表中的数据"""

    print("=" * 80)
    print("检查数据库中的价格数据")
    print("=" * 80)
    print()

    # 读取配置
    try:
        with open('config.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"❌ 无法读取 config.yaml: {e}")
        return

    db_config = config.get('database', {}).get('mysql', {})

    # 连接数据库
    try:
        conn = pymysql.connect(
            host=db_config.get('host', 'localhost'),
            port=db_config.get('port', 3306),
            user=db_config.get('user', 'root'),
            password=db_config.get('password', ''),
            database=db_config.get('database', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5
        )
        print(f"✅ 成功连接到数据库: {db_config.get('database', 'binance-data')}")
        print()
    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        return

    try:
        with conn.cursor() as cursor:
            # 1. 检查 price_data 表
            print("=" * 80)
            print("1. price_data 表统计")
            print("=" * 80)

            sql = """
            SELECT
                symbol,
                COUNT(*) as count,
                MAX(timestamp) as latest_time,
                MIN(timestamp) as earliest_time,
                ROUND(AVG(price), 2) as avg_price,
                ROUND(MIN(price), 2) as min_price,
                ROUND(MAX(price), 2) as max_price
            FROM price_data
            GROUP BY symbol
            ORDER BY latest_time DESC
            """

            cursor.execute(sql)
            results = cursor.fetchall()

            if not results:
                print("⚠️  price_data 表为空！")
                print()
            else:
                print(f"找到 {len(results)} 个交易对的数据：")
                print()
                print(f"{'交易对':<15} {'数据条数':<10} {'最新时间':<20} {'平均价格':<15} {'价格范围'}")
                print("-" * 80)

                for row in results:
                    symbol = row['symbol']
                    count = row['count']
                    latest = row['latest_time']
                    avg_price = row['avg_price']
                    min_price = row['min_price']
                    max_price = row['max_price']

                    # 检查数据是否新鲜（最近5分钟）
                    now = datetime.now()
                    if isinstance(latest, datetime):
                        age = (now - latest).total_seconds()
                        if age < 300:  # 5分钟
                            freshness = "✅"
                        elif age < 3600:  # 1小时
                            freshness = "⚠️"
                        else:
                            freshness = "❌"
                    else:
                        freshness = "?"

                    latest_str = latest.strftime('%Y-%m-%d %H:%M:%S') if isinstance(latest, datetime) else str(latest)

                    print(f"{symbol:<15} {count:<10} {latest_str:<20} ${avg_price:<14} ${min_price} - ${max_price} {freshness}")

                print()
                print("新鲜度标识: ✅ <5分钟  ⚠️ <1小时  ❌ >1小时")

            print()
            print("=" * 80)
            print("2. kline_data 表统计（备用价格数据）")
            print("=" * 80)

            # 2. 检查 kline_data 表
            sql = """
            SELECT
                symbol,
                COUNT(*) as count,
                MAX(open_time) as latest_time,
                ROUND(AVG(close_price), 2) as avg_price
            FROM kline_data
            WHERE timeframe = '1m'
            GROUP BY symbol
            ORDER BY latest_time DESC
            LIMIT 20
            """

            cursor.execute(sql)
            results = cursor.fetchall()

            if not results:
                print("⚠️  kline_data 表为空或没有 1m 数据！")
                print()
            else:
                print(f"找到 {len(results)} 个交易对的 K线数据（仅显示前20个）：")
                print()
                print(f"{'交易对':<15} {'数据条数':<10} {'最新时间':<20} {'平均收盘价'}")
                print("-" * 80)

                for row in results:
                    symbol = row['symbol']
                    count = row['count']
                    latest = row['latest_time']
                    avg_price = row['avg_price']

                    latest_str = latest.strftime('%Y-%m-%d %H:%M:%S') if isinstance(latest, datetime) else str(latest)

                    print(f"{symbol:<15} {count:<10} {latest_str:<20} ${avg_price}")

            print()
            print("=" * 80)
            print("3. config.yaml 中配置的交易对")
            print("=" * 80)

            config_symbols = config.get('symbols', [])
            print(f"配置文件中有 {len(config_symbols)} 个交易对：")
            print()

            # 获取 price_data 中有数据的交易对
            cursor.execute("SELECT DISTINCT symbol FROM price_data")
            price_symbols = set(row['symbol'] for row in cursor.fetchall())

            # 获取 kline_data 中有数据的交易对
            cursor.execute("SELECT DISTINCT symbol FROM kline_data WHERE timeframe = '1m'")
            kline_symbols = set(row['symbol'] for row in cursor.fetchall())

            for symbol in config_symbols:
                has_price = symbol in price_symbols
                has_kline = symbol in kline_symbols

                if has_price and has_kline:
                    status = "✅ price_data + kline_data"
                elif has_price:
                    status = "⚠️ 仅 price_data"
                elif has_kline:
                    status = "⚠️ 仅 kline_data"
                else:
                    status = "❌ 无数据"

                print(f"  {symbol:<15} {status}")

            print()
            print("=" * 80)
            print("4. 测试查询速度")
            print("=" * 80)

            # 测试几个常见交易对的查询速度
            test_symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'HYPE/USDT']

            for symbol in test_symbols:
                # 测试 price_data 查询
                start = datetime.now()
                sql = """
                SELECT price, timestamp
                FROM price_data
                WHERE symbol = %s
                ORDER BY timestamp DESC
                LIMIT 1
                """
                cursor.execute(sql, (symbol,))
                result = cursor.fetchone()
                elapsed_price = (datetime.now() - start).total_seconds() * 1000

                # 测试 kline_data 查询（备用）
                start = datetime.now()
                sql = """
                SELECT close_price, open_time
                FROM kline_data
                WHERE symbol = %s AND timeframe = '1m'
                ORDER BY open_time DESC
                LIMIT 1
                """
                cursor.execute(sql, (symbol,))
                result_kline = cursor.fetchone()
                elapsed_kline = (datetime.now() - start).total_seconds() * 1000

                # 显示结果
                if result:
                    price = result['price']
                    timestamp = result['timestamp']
                    print(f"  {symbol:<15} price_data: ${price:<10} ({elapsed_price:.1f}ms) ✅")
                elif result_kline:
                    price = result_kline['close_price']
                    timestamp = result_kline['open_time']
                    print(f"  {symbol:<15} kline_data: ${price:<10} ({elapsed_kline:.1f}ms) ⚠️")
                else:
                    print(f"  {symbol:<15} 无数据 (price: {elapsed_price:.1f}ms, kline: {elapsed_kline:.1f}ms) ❌")

            print()

    except Exception as e:
        print(f"❌ 查询失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

    print()
    print("=" * 80)
    print("建议:")
    print("=" * 80)
    print()
    print("1. 如果某些交易对显示 ❌ 无数据，说明数据采集器没有采集这些币种")
    print("2. 解决方法：")
    print("   a) 在 config.yaml 中添加这些交易对")
    print("   b) 运行数据采集器: python scripts/init/collect_realtime_price.py")
    print("   c) 或者从 Paper Trading 界面中移除这些交易对")
    print()
    print("3. 如果查询速度 >100ms，考虑添加数据库索引：")
    print("   CREATE INDEX idx_symbol_timestamp ON price_data(symbol, timestamp DESC);")
    print("   CREATE INDEX idx_symbol_time ON kline_data(symbol, timeframe, open_time DESC);")
    print()

if __name__ == "__main__":
    check_price_data()
