"""
测试 Paper Trading API 性能
找出卡顿的具体位置
"""
import time
import yaml
import pymysql
from decimal import Decimal

print("=" * 60)
print("Paper Trading API 性能诊断")
print("=" * 60)

# 步骤 1: 加载配置
print("\n[步骤 1] 加载配置文件...")
start = time.time()
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)
db_config = config.get('database', {}).get('mysql', {})
print(f"   ✓ 耗时: {time.time() - start:.3f}秒")

# 步骤 2: 创建数据库连接
print("\n[步骤 2] 创建数据库连接...")
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
print(f"   ✓ 耗时: {time.time() - start:.3f}秒")

# 步骤 3: 查询 price_data
print("\n[步骤 3] 查询 price_data 表...")
start = time.time()
with conn.cursor() as cursor:
    cursor.execute(
        """SELECT price FROM price_data
        WHERE symbol = %s
        ORDER BY timestamp DESC
        LIMIT 1""",
        ('BTC/USDT',)
    )
    result = cursor.fetchone()
    price = Decimal(str(result['price'])) if result else None
print(f"   ✓ 耗时: {time.time() - start:.3f}秒")
print(f"   价格: {price}")

# 步骤 4: 关闭连接
print("\n[步骤 4] 关闭数据库连接...")
start = time.time()
conn.close()
print(f"   ✓ 耗时: {time.time() - start:.3f}秒")

# 步骤 5: 模拟完整流程（多次）
print("\n[步骤 5] 模拟完整 API 调用流程（10次）...")
times = []
for i in range(10):
    start = time.time()

    # 连接
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

    # 查询
    with conn.cursor() as cursor:
        cursor.execute(
            """SELECT price FROM price_data
            WHERE symbol = %s
            ORDER BY timestamp DESC
            LIMIT 1""",
            ('BTC/USDT',)
        )
        result = cursor.fetchone()

    # 关闭
    conn.close()

    elapsed = time.time() - start
    times.append(elapsed)
    print(f"   第 {i+1} 次: {elapsed:.3f}秒")

print(f"\n   平均耗时: {sum(times)/len(times):.3f}秒")
print(f"   最快: {min(times):.3f}秒")
print(f"   最慢: {max(times):.3f}秒")

# 步骤 6: 测试索引性能
print("\n[步骤 6] 检查数据库索引...")
conn = pymysql.connect(
    host=db_config.get('host', 'localhost'),
    port=db_config.get('port', 3306),
    user=db_config.get('user', 'root'),
    password=db_config.get('password', ''),
    database=db_config.get('database', 'binance-data'),
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

with conn.cursor() as cursor:
    # 检查 price_data 表的索引
    cursor.execute("SHOW INDEX FROM price_data")
    indexes = cursor.fetchall()
    print(f"\n   price_data 表的索引:")
    for idx in indexes:
        print(f"      - {idx['Key_name']}: {idx['Column_name']}")

    # 分析查询性能
    cursor.execute(
        """EXPLAIN SELECT price FROM price_data
        WHERE symbol = 'BTC/USDT'
        ORDER BY timestamp DESC
        LIMIT 1"""
    )
    explain = cursor.fetchone()
    print(f"\n   查询分析:")
    print(f"      类型: {explain.get('type')}")
    print(f"      可能的索引: {explain.get('possible_keys')}")
    print(f"      使用的索引: {explain.get('key')}")
    print(f"      扫描行数: {explain.get('rows')}")

conn.close()

print("\n" + "=" * 60)
print("诊断完成！")
print("=" * 60)

# 性能建议
print("\n【性能分析】")
if sum(times)/len(times) > 0.1:
    print("⚠️  数据库查询较慢（>100ms）")
    print("\n可能的原因：")
    print("1. price_data 表缺少索引")
    print("   建议：CREATE INDEX idx_symbol_timestamp ON price_data(symbol, timestamp);")
    print("2. 表数据量过大")
    print("   建议：定期清理旧数据")
    print("3. MySQL 配置不佳")
    print("   建议：检查 my.ini 配置文件")
else:
    print("✓ 数据库查询性能正常")

print("\n按任意键退出...")
input()
