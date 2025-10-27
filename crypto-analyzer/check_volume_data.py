"""
检查价格缓存表中的成交量数据
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import yaml
from sqlalchemy import text
from app.database.db_service import DatabaseService

# 加载配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_service = DatabaseService(config.get('database', {}))
session = db_service.get_session()

print("=" * 80)
print("检查 price_stats_24h 表中的成交量数据")
print("=" * 80 + "\n")

try:
    # 查询所有币种的成交量数据
    sql = text("""
        SELECT
            symbol,
            current_price,
            volume_24h,
            quote_volume_24h,
            updated_at
        FROM price_stats_24h
        ORDER BY symbol
    """)

    results = session.execute(sql).fetchall()

    if results:
        print(f"找到 {len(results)} 条记录:\n")
        print(f"{'币种':<15} {'价格':<12} {'成交量(币)':<15} {'成交量(USDT)':<15} {'更新时间'}")
        print("-" * 80)

        for row in results:
            symbol = row[0]
            price = row[1]
            volume = row[2] if row[2] else 0
            quote_volume = row[3] if row[3] else 0
            updated_at = row[4]

            print(f"{symbol:<15} ${price:<11.2f} {volume:<14.2f} ${quote_volume:<14.2f} {updated_at}")
    else:
        print("⚠️  表中没有数据")

    print("\n" + "=" * 80)

    # 检查哪些币种的成交量为0
    sql_zero = text("""
        SELECT symbol
        FROM price_stats_24h
        WHERE quote_volume_24h IS NULL OR quote_volume_24h = 0
    """)

    zero_results = session.execute(sql_zero).fetchall()
    if zero_results:
        print(f"\n⚠️  以下 {len(zero_results)} 个币种的成交量为0或NULL:")
        for row in zero_results:
            print(f"  - {row[0]}")
    else:
        print("\n✅ 所有币种都有成交量数据")

    print("\n" + "=" * 80)

    # 检查原始表中是否有成交量数据
    print("\n检查原始 price_data 表:")
    sql_raw = text("""
        SELECT
            symbol,
            exchange,
            price,
            volume,
            quote_volume,
            timestamp
        FROM price_data
        WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
        ORDER BY symbol, timestamp DESC
        LIMIT 20
    """)

    raw_results = session.execute(sql_raw).fetchall()
    if raw_results:
        print(f"\n最近1小时的原始数据 (前20条):\n")
        print(f"{'币种':<15} {'交易所':<10} {'价格':<12} {'成交量(币)':<15} {'成交量(USDT)':<15}")
        print("-" * 80)

        for row in raw_results:
            symbol = row[0]
            exchange = row[1]
            price = row[2]
            volume = row[3] if row[3] else 0
            quote_volume = row[4] if row[4] else 0

            print(f"{symbol:<15} {exchange:<10} ${price:<11.2f} {volume:<14.2f} ${quote_volume:<14.2f}")
    else:
        print("\n⚠️  最近1小时没有原始数据")

finally:
    session.close()

print("\n" + "=" * 80)
