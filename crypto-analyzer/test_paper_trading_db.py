"""
测试 Paper Trading 数据库连接和查询
"""
import yaml
import pymysql
from datetime import datetime

# 加载配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config.get('database', {}).get('mysql', {})

print("=" * 60)
print("Paper Trading 数据库连接诊断")
print("=" * 60)

print("\n1. 数据库配置:")
print(f"   Host: {db_config.get('host')}")
print(f"   Port: {db_config.get('port')}")
print(f"   User: {db_config.get('user')}")
print(f"   Database: {db_config.get('database')}")

print("\n2. 尝试连接数据库...")
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
    print("   ✓ 数据库连接成功!")

    print("\n3. 检查 price_data 表...")
    with conn.cursor() as cursor:
        start = datetime.now()
        cursor.execute(
            """SELECT symbol, price, timestamp
            FROM price_data
            WHERE symbol = 'BTC/USDT'
            ORDER BY timestamp DESC
            LIMIT 1"""
        )
        result = cursor.fetchone()
        elapsed = (datetime.now() - start).total_seconds()

        if result:
            print(f"   ✓ 查询成功 (耗时: {elapsed:.2f}秒)")
            print(f"   Symbol: {result['symbol']}")
            print(f"   Price: {result['price']}")
            print(f"   Timestamp: {result['timestamp']}")
        else:
            print("   ✗ price_data 表中没有 BTC/USDT 数据")

    print("\n4. 检查 kline_data 表...")
    with conn.cursor() as cursor:
        start = datetime.now()
        cursor.execute(
            """SELECT symbol, close_price, open_time
            FROM kline_data
            WHERE symbol = 'BTC/USDT'
            ORDER BY open_time DESC
            LIMIT 1"""
        )
        result = cursor.fetchone()
        elapsed = (datetime.now() - start).total_seconds()

        if result:
            print(f"   ✓ 查询成功 (耗时: {elapsed:.2f}秒)")
            print(f"   Symbol: {result['symbol']}")
            print(f"   Close Price: {result['close_price']}")
            print(f"   Open Time: {result['open_time']}")
        else:
            print("   ✗ kline_data 表中没有 BTC/USDT 数据")

    print("\n5. 检查所有表中的数据...")
    with conn.cursor() as cursor:
        # price_data 总行数
        cursor.execute("SELECT COUNT(*) as cnt FROM price_data")
        price_count = cursor.fetchone()['cnt']
        print(f"   price_data 表: {price_count} 条记录")

        # kline_data 总行数
        cursor.execute("SELECT COUNT(*) as cnt FROM kline_data")
        kline_count = cursor.fetchone()['cnt']
        print(f"   kline_data 表: {kline_count} 条记录")

        # price_data 有哪些交易对
        cursor.execute("SELECT DISTINCT symbol FROM price_data LIMIT 10")
        symbols = [row['symbol'] for row in cursor.fetchall()]
        print(f"   price_data 中的交易对: {', '.join(symbols)}")

    print("\n6. 测试 paper_trading_api.py 的配置...")
    print(f"   从 config.yaml 读取: config.get('database', {{}}).get('mysql', {{}})")
    print(f"   结果: {db_config}")

    conn.close()
    print("\n" + "=" * 60)
    print("诊断完成！")
    print("=" * 60)

except pymysql.err.OperationalError as e:
    print(f"   ✗ 数据库连接失败: {e}")
    print("\n可能的原因:")
    print("  1. MySQL 服务未启动")
    print("  2. 用户名或密码错误")
    print("  3. 数据库不存在")
    print("  4. 防火墙阻止连接")
except Exception as e:
    print(f"   ✗ 发生错误: {e}")
    import traceback
    traceback.print_exc()

print("\n按任意键退出...")
input()
