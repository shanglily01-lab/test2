#!/usr/bin/env python3
"""
直接测试数据库连接
"""

import pymysql
import yaml
import time

def test_db():
    print("=" * 80)
    print("测试数据库连接")
    print("=" * 80)
    print()

    # 读取配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    db_config = config.get('database', {}).get('mysql', {})

    print(f"数据库配置:")
    print(f"  Host: {db_config.get('host', 'localhost')}")
    print(f"  Port: {db_config.get('port', 3306)}")
    print(f"  Database: {db_config.get('database', 'binance-data')}")
    print(f"  User: {db_config.get('user', 'root')}")
    print()

    # 测试连接
    for i in range(5):
        print(f"测试 {i+1}/5:")
        try:
            start = time.time()
            conn = pymysql.connect(
                host=db_config.get('host', 'localhost'),
                port=db_config.get('port', 3306),
                user=db_config.get('user', 'root'),
                password=db_config.get('password', ''),
                database=db_config.get('database', 'binance-data'),
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=5,
                read_timeout=10,
                write_timeout=10
            )
            elapsed_connect = (time.time() - start) * 1000

            # 查询价格
            start = time.time()
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT price FROM price_data WHERE symbol = %s ORDER BY timestamp DESC LIMIT 1",
                    ('BTC/USDT',)
                )
                result = cursor.fetchone()
            elapsed_query = (time.time() - start) * 1000

            conn.close()

            if result:
                print(f"  ✅ 成功 - 连接: {elapsed_connect:.1f}ms, 查询: {elapsed_query:.1f}ms, 价格: ${result['price']}")
            else:
                print(f"  ⚠️  连接成功但无数据 - 连接: {elapsed_connect:.1f}ms")

        except pymysql.err.OperationalError as e:
            print(f"  ❌ 数据库操作错误: {e}")
        except Exception as e:
            print(f"  ❌ 错误: {e}")

        time.sleep(0.5)

    print()
    print("=" * 80)
    print("测试完成")
    print("=" * 80)

if __name__ == "__main__":
    test_db()
