"""
检查所有K线数据
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import yaml
from sqlalchemy import text
from datetime import datetime, timedelta
from app.database.db_service import DatabaseService

# 加载配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_service = DatabaseService(config.get('database', {}))
session = db_service.get_session()

print("=" * 100)
print("检查所有K线数据")
print("=" * 100 + "\n")

# 1. 检查最近的K线（不限时间）
print("1. 检查最近的K线数据 (不限时间，每个周期最新10条):\n")

for timeframe in ['1m', '5m', '1h']:
    try:
        sql = text("""
            SELECT
                symbol,
                timeframe,
                timestamp,
                close_price,
                volume,
                quote_volume,
                exchange
            FROM kline_data
            WHERE timeframe = :timeframe
            ORDER BY timestamp DESC
            LIMIT 10
        """)

        results = session.execute(sql, {"timeframe": timeframe}).fetchall()

        print(f"\n{timeframe} K线 - 最新10条:")
        print("-" * 100)

        if results:
            print(f"{'币种':<15} {'时间':<22} {'价格':<12} {'成交量':<15} {'成交额':<15} {'交易所'}")
            print("-" * 100)

            for row in results:
                symbol = row[0]
                timestamp = row[2]
                price = row[3]
                volume = row[4] if row[4] else 0
                quote_volume = row[5]
                exchange = row[6]

                status = "✅" if quote_volume and quote_volume > 0 else "❌"
                qv_str = f"${quote_volume:,.2f}" if quote_volume else "NULL"

                print(f"{status} {symbol:<13} {timestamp} ${price:<10.2f} {volume:<14,.4f} {qv_str:<15} {exchange}")
        else:
            print(f"  ⚠️  没有 {timeframe} K线数据")

    except Exception as e:
        print(f"查询 {timeframe} 失败: {e}")

# 2. 统计各币种的K线数量
print("\n\n" + "=" * 100)
print("\n2. 统计各币种的K线数量:\n")

try:
    sql = text("""
        SELECT
            symbol,
            timeframe,
            COUNT(*) as count,
            MIN(timestamp) as first_time,
            MAX(timestamp) as last_time,
            SUM(CASE WHEN quote_volume IS NOT NULL AND quote_volume > 0 THEN 1 ELSE 0 END) as has_qv
        FROM kline_data
        GROUP BY symbol, timeframe
        ORDER BY symbol, timeframe
    """)

    results = session.execute(sql).fetchall()

    if results:
        print(f"{'币种':<15} {'周期':<8} {'总数':<8} {'有QV':<8} {'最早时间':<22} {'最新时间'}")
        print("-" * 100)

        for row in results:
            symbol = row[0]
            timeframe = row[1]
            count = row[2]
            first_time = row[3]
            last_time = row[4]
            has_qv = row[5]

            qv_pct = (has_qv / count * 100) if count > 0 else 0
            status = "✅" if has_qv > 0 else "❌"

            print(f"{status} {symbol:<13} {timeframe:<8} {count:<8} {has_qv:<8} {first_time} {last_time}")

        print("\n总结:")
        print(f"  总K线记录: {sum(r[2] for r in results)} 条")
        print(f"  有quote_volume: {sum(r[5] for r in results)} 条")
    else:
        print("⚠️  没有任何K线数据")

except Exception as e:
    print(f"查询失败: {e}")
    import traceback
    traceback.print_exc()

# 3. 检查最近30分钟的数据
print("\n\n" + "=" * 100)
print("\n3. 检查最近30分钟的K线数据:\n")

try:
    sql = text("""
        SELECT
            symbol,
            timeframe,
            COUNT(*) as count
        FROM kline_data
        WHERE timestamp >= :start_time
        GROUP BY symbol, timeframe
        ORDER BY symbol, timeframe
    """)

    start_time = datetime.now() - timedelta(minutes=30)
    results = session.execute(sql, {"start_time": start_time}).fetchall()

    if results:
        print(f"{'币种':<15} {'周期':<8} {'数量':<8}")
        print("-" * 100)

        for row in results:
            print(f"  {row[0]:<13} {row[1]:<8} {row[2]:<8}")

        print(f"\n最近30分钟共有 {sum(r[2] for r in results)} 条K线数据")
    else:
        print("⚠️⚠️⚠️  最近30分钟没有任何K线数据")
        print("      这说明 scheduler 可能:")
        print("      1. 没有在运行")
        print("      2. 遇到错误无法采集数据")
        print("      3. 数据库连接有问题")
        print("\n      请检查 scheduler 的运行状态和日志")

except Exception as e:
    print(f"查询失败: {e}")

session.close()

print("\n" + "=" * 100)
