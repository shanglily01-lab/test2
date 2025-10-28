"""
诊断 K线查询问题
检查 kline_data 表中的数据格式和字段值
"""

import mysql.connector
import yaml
from pathlib import Path

def load_config():
    """加载配置文件"""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def diagnose_kline_query():
    """诊断 K线查询"""
    print("=" * 80)
    print("🔍 K线查询诊断工具")
    print("=" * 80 + "\n")

    # 加载配置
    config = load_config()
    mysql_config = config.get('database', {}).get('mysql', {})

    # 连接数据库
    try:
        conn = mysql.connector.connect(
            host=mysql_config.get('host', 'localhost'),
            port=mysql_config.get('port', 3306),
            user=mysql_config.get('user', 'root'),
            password=mysql_config.get('password', ''),
            database=mysql_config.get('database', 'binance-data')
        )
        cursor = conn.cursor(dictionary=True)

        print(f"✅ 数据库连接成功\n")

    except mysql.connector.Error as e:
        print(f"❌ 数据库连接失败: {e}\n")
        return

    # 检查 kline_data 表结构
    print("=" * 80)
    print("📋 kline_data 表结构")
    print("=" * 80 + "\n")

    cursor.execute("DESCRIBE kline_data")
    columns = cursor.fetchall()

    print(f"{'字段名':<25} {'类型':<20} {'允许NULL':<10} {'键':<10} {'默认值'}")
    print("-" * 80)
    for col in columns:
        print(f"{col['Field']:<25} {col['Type']:<20} {col['Null']:<10} {col['Key']:<10} {str(col['Default'])}")

    print()

    # 检查 symbol 字段的值格式
    print("=" * 80)
    print("📊 symbol 字段值示例")
    print("=" * 80 + "\n")

    cursor.execute("""
        SELECT DISTINCT symbol
        FROM kline_data
        ORDER BY symbol
        LIMIT 20
    """)
    symbols = cursor.fetchall()

    print("数据库中的 symbol 格式:")
    for s in symbols:
        print(f"   - {s['symbol']}")

    print()

    # 检查 timeframe 字段的值
    print("=" * 80)
    print("⏰ timeframe 字段值")
    print("=" * 80 + "\n")

    cursor.execute("""
        SELECT DISTINCT timeframe, COUNT(*) as count
        FROM kline_data
        GROUP BY timeframe
        ORDER BY
            CASE timeframe
                WHEN '1m' THEN 1
                WHEN '5m' THEN 2
                WHEN '15m' THEN 3
                WHEN '1h' THEN 4
                WHEN '4h' THEN 5
                WHEN '1d' THEN 6
                ELSE 7
            END
    """)
    timeframes = cursor.fetchall()

    print("数据库中的 timeframe 值:")
    for tf in timeframes:
        print(f"   - '{tf['timeframe']}': {tf['count']:,} 条记录")

    print()

    # 检查 exchange 字段
    cursor.execute("SHOW COLUMNS FROM kline_data LIKE 'exchange'")
    has_exchange = cursor.fetchone() is not None

    if has_exchange:
        print("=" * 80)
        print("🏦 exchange 字段值")
        print("=" * 80 + "\n")

        cursor.execute("""
            SELECT DISTINCT exchange, COUNT(*) as count
            FROM kline_data
            GROUP BY exchange
        """)
        exchanges = cursor.fetchall()

        print("数据库中的 exchange 值:")
        for ex in exchanges:
            print(f"   - '{ex['exchange']}': {ex['count']:,} 条记录")

        print()
    else:
        print("⚠️  kline_data 表没有 exchange 字段\n")

    # 模拟查询测试
    print("=" * 80)
    print("🧪 模拟查询测试")
    print("=" * 80 + "\n")

    test_symbols = ['BTCUSDT', 'BTC', 'BTC/USDT', 'ETHUSDT', 'ETH', 'ETH/USDT']

    for test_symbol in test_symbols:
        # 测试查询1：包含 exchange 条件
        if has_exchange:
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM kline_data
                WHERE symbol = %s
                AND timeframe = '1h'
                AND exchange = 'binance'
            """, (test_symbol,))
        else:
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM kline_data
                WHERE symbol = %s
                AND timeframe = '1h'
            """, (test_symbol,))

        result = cursor.fetchone()
        count = result['count']

        if count > 0:
            status = f"✅ 找到 {count} 条记录"
        else:
            status = "❌ 未找到数据"

        print(f"   symbol='{test_symbol}', timeframe='1h': {status}")

    print()

    # 显示实际查询示例
    print("=" * 80)
    print("💡 正确的查询示例")
    print("=" * 80 + "\n")

    if symbols and len(symbols) > 0:
        actual_symbol = symbols[0]['symbol']

        if has_exchange:
            query_example = f"""
SELECT open_time, close_price, volume
FROM kline_data
WHERE symbol = '{actual_symbol}'
AND timeframe = '1h'
AND exchange = 'binance'
ORDER BY open_time DESC
LIMIT 31
"""
        else:
            query_example = f"""
SELECT open_time, close_price, volume
FROM kline_data
WHERE symbol = '{actual_symbol}'
AND timeframe = '1h'
ORDER BY open_time DESC
LIMIT 31
"""

        print("推荐的查询SQL:")
        print(query_example)

        # 执行这个查询
        cursor.execute(query_example.strip())
        results = cursor.fetchall()

        print(f"\n✅ 查询结果: {len(results)} 条记录")

        if results and len(results) > 0:
            print(f"\n最新的3条数据:")
            for i, row in enumerate(results[:3]):
                print(f"   {i+1}. {row['open_time']} | 收盘价: ${row['close_price']} | 成交量: {row['volume']}")

    cursor.close()
    conn.close()

    print("\n" + "=" * 80)
    print("诊断完成！")
    print("=" * 80 + "\n")

if __name__ == "__main__":
    try:
        diagnose_kline_query()
    except Exception as e:
        print(f"\n❌ 诊断过程出错: {e}")
        import traceback
        traceback.print_exc()
