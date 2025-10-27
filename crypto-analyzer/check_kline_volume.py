"""
检查K线表中的成交量数据
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

print("=" * 100)
print("检查 kline_data 表中的成交量字段")
print("=" * 100 + "\n")

try:
    # 1. 检查表结构
    print("1. 检查 kline_data 表结构:\n")
    sql_desc = text("DESCRIBE kline_data")
    columns = session.execute(sql_desc).fetchall()

    print(f"{'字段名':<20} {'类型':<25} {'允许NULL':<10} {'键':<10} {'默认值':<15}")
    print("-" * 100)
    for col in columns:
        print(f"{col[0]:<20} {col[1]:<25} {col[2]:<10} {col[3]:<10} {str(col[4]):<15}")

    # 2. 检查最近的K线数据
    print("\n" + "=" * 100)
    print("\n2. 检查最近1小时的K线数据 (每个币种最新1条):\n")

    sql_data = text("""
        SELECT
            t1.symbol,
            t1.timeframe,
            t1.close as price,
            t1.volume,
            t1.quote_volume,
            t1.timestamp
        FROM kline_data t1
        INNER JOIN (
            SELECT symbol, MAX(timestamp) as max_ts
            FROM kline_data
            WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
            AND timeframe = '5m'
            GROUP BY symbol
        ) t2 ON t1.symbol = t2.symbol AND t1.timestamp = t2.max_ts
        ORDER BY t1.symbol
    """)

    results = session.execute(sql_data).fetchall()

    if results:
        print(f"{'币种':<15} {'周期':<8} {'价格':<12} {'成交量(币)':<18} {'成交量(USDT)':<18} {'时间'}")
        print("-" * 100)

        has_volume = 0
        no_volume = 0

        for row in results:
            symbol = row[0]
            timeframe = row[1]
            price = row[2]
            volume = row[3] if row[3] else 0
            quote_volume = row[4] if row[4] else None
            timestamp = row[5]

            if quote_volume and quote_volume > 0:
                has_volume += 1
                status = "✅"
            else:
                no_volume += 1
                status = "❌"

            qv_str = f"${quote_volume:,.2f}" if quote_volume else "NULL"

            print(f"{status} {symbol:<13} {timeframe:<8} ${price:<10.2f} {volume:<17,.2f} {qv_str:<18} {timestamp}")

        print("\n" + "-" * 100)
        print(f"统计: ✅ 有数据: {has_volume} 个 | ❌ 无数据: {no_volume} 个")
    else:
        print("⚠️  最近1小时没有K线数据")

    # 3. 检查24小时K线数量
    print("\n" + "=" * 100)
    print("\n3. 检查各币种24小时K线数量:\n")

    sql_count = text("""
        SELECT
            symbol,
            timeframe,
            COUNT(*) as kline_count,
            MIN(timestamp) as first_time,
            MAX(timestamp) as last_time
        FROM kline_data
        WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
        GROUP BY symbol, timeframe
        ORDER BY symbol, timeframe
    """)

    count_results = session.execute(sql_count).fetchall()

    if count_results:
        print(f"{'币种':<15} {'周期':<8} {'K线数量':<10} {'最早时间':<22} {'最新时间'}")
        print("-" * 100)

        for row in count_results:
            symbol = row[0]
            timeframe = row[1]
            count = row[2]
            first_time = row[3]
            last_time = row[4]

            # 5m周期24小时应该有288根K线
            expected_5m = 288
            expected_1h = 24

            if timeframe == '5m':
                status = "✅" if count >= expected_5m * 0.9 else "⚠️"
            elif timeframe == '1h':
                status = "✅" if count >= expected_1h * 0.9 else "⚠️"
            else:
                status = "  "

            print(f"{status} {symbol:<13} {timeframe:<8} {count:<10} {first_time} {last_time}")
    else:
        print("⚠️  24小时内没有K线数据")

    # 4. 检查是否有任何K线有quote_volume
    print("\n" + "=" * 100)
    print("\n4. 检查是否有K线记录包含 quote_volume:\n")

    sql_has_qv = text("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN quote_volume IS NOT NULL AND quote_volume > 0 THEN 1 ELSE 0 END) as has_qv,
               SUM(CASE WHEN quote_volume IS NULL OR quote_volume = 0 THEN 1 ELSE 0 END) as no_qv
        FROM kline_data
        WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
    """)

    qv_stats = session.execute(sql_has_qv).fetchone()

    if qv_stats:
        total = qv_stats[0]
        has_qv = qv_stats[1]
        no_qv = qv_stats[2]

        print(f"24小时内K线总数: {total}")
        print(f"  ✅ 有 quote_volume: {has_qv} ({has_qv/total*100:.1f}%)" if total > 0 else "  ✅ 有 quote_volume: 0")
        print(f"  ❌ 无 quote_volume: {no_qv} ({no_qv/total*100:.1f}%)" if total > 0 else "  ❌ 无 quote_volume: 0")

        if no_qv == total and total > 0:
            print("\n⚠️⚠️⚠️  所有K线记录都没有 quote_volume 数据！")
            print("       这就是成交量显示为空的原因。")

finally:
    session.close()

print("\n" + "=" * 100)
