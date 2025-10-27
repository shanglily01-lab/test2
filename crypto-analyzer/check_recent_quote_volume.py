"""
检查最近采集的K线是否有 quote_volume 数据
专门查看最近30分钟的数据，判断修复是否生效
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

print("=" * 120)
print("检查最近30分钟采集的K线 quote_volume 数据")
print(f"当前UTC时间: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 120 + "\n")

# 检查最近30分钟的1分钟K线
print("1. 检查最近30分钟的1分钟K线 (最新15条):\n")

try:
    sql = text("""
        SELECT
            symbol,
            timestamp,
            close_price,
            volume,
            quote_volume,
            exchange
        FROM kline_data
        WHERE timeframe = '1m'
        AND timestamp >= :start_time
        ORDER BY timestamp DESC
        LIMIT 15
    """)

    start_time = datetime.utcnow() - timedelta(minutes=30)
    results = session.execute(sql, {"start_time": start_time}).fetchall()

    if results:
        print(f"{'时间 (UTC)':<22} {'币种':<15} {'价格':<12} {'成交量(币)':<18} {'成交量(USDT)':<20} {'交易所':<12} {'状态'}")
        print("-" * 120)

        has_qv = 0
        no_qv = 0

        for row in results:
            symbol = row[0]
            timestamp = row[1]
            price = row[2]
            volume = row[3] if row[3] else 0
            quote_volume = row[4]
            exchange = row[5]

            if quote_volume and quote_volume > 0:
                status = "✅ 有数据"
                has_qv += 1
            else:
                status = "❌ NULL/0"
                no_qv += 1

            qv_str = f"${quote_volume:,.2f}" if quote_volume else "NULL"

            print(f"{timestamp} {symbol:<15} ${price:<10.2f} {volume:<17,.4f} {qv_str:<20} {exchange:<12} {status}")

        print("\n" + "-" * 120)
        print(f"统计: 总共 {len(results)} 条")
        print(f"  ✅ 有 quote_volume: {has_qv} 条")
        print(f"  ❌ 无 quote_volume: {no_qv} 条")

        if has_qv > 0:
            print(f"\n✅✅✅ 成功！最近的K线已经有 quote_volume 数据！")
            print(f"       修复已生效，新采集的数据包含 quote_volume")
        else:
            print(f"\n❌ 最近30分钟的K线仍然没有 quote_volume")
            print(f"   可能原因:")
            print(f"   1. scheduler 还在使用旧代码 (需要重启)")
            print(f"   2. 交易所API没有返回 quote_volume")
    else:
        print("⚠️  最近30分钟没有1分钟K线数据")
        print("   这说明 scheduler 可能没有在运行")

except Exception as e:
    print(f"查询失败: {e}")
    import traceback
    traceback.print_exc()

# 检查最近30分钟的5分钟K线
print("\n" + "=" * 120)
print("\n2. 检查最近30分钟的5分钟K线:\n")

try:
    sql = text("""
        SELECT
            symbol,
            timestamp,
            close_price,
            volume,
            quote_volume,
            exchange
        FROM kline_data
        WHERE timeframe = '5m'
        AND timestamp >= :start_time
        ORDER BY timestamp DESC
        LIMIT 10
    """)

    start_time = datetime.utcnow() - timedelta(minutes=30)
    results = session.execute(sql, {"start_time": start_time}).fetchall()

    if results:
        print(f"{'时间 (UTC)':<22} {'币种':<15} {'价格':<12} {'成交量(币)':<18} {'成交量(USDT)':<20} {'交易所':<12} {'状态'}")
        print("-" * 120)

        has_qv = 0
        no_qv = 0

        for row in results:
            symbol = row[0]
            timestamp = row[1]
            price = row[2]
            volume = row[3] if row[3] else 0
            quote_volume = row[4]
            exchange = row[5]

            if quote_volume and quote_volume > 0:
                status = "✅ 有数据"
                has_qv += 1
            else:
                status = "❌ NULL/0"
                no_qv += 1

            qv_str = f"${quote_volume:,.2f}" if quote_volume else "NULL"

            print(f"{timestamp} {symbol:<15} ${price:<10.2f} {volume:<17,.4f} {qv_str:<20} {exchange:<12} {status}")

        print("\n" + "-" * 120)
        print(f"统计: 总共 {len(results)} 条")
        print(f"  ✅ 有 quote_volume: {has_qv} 条")
        print(f"  ❌ 无 quote_volume: {no_qv} 条")

        if has_qv > 0:
            print(f"\n✅ 5分钟K线有 quote_volume 数据")
        else:
            print(f"\n❌ 5分钟K线没有 quote_volume 数据")
    else:
        print("⚠️  最近30分钟没有5分钟K线数据")

except Exception as e:
    print(f"查询失败: {e}")
    import traceback
    traceback.print_exc()

# 检查最近10分钟的数据（更精确）
print("\n" + "=" * 120)
print("\n3. 检查最近10分钟的所有K线 (任何周期):\n")

try:
    sql = text("""
        SELECT
            timestamp,
            symbol,
            timeframe,
            close_price,
            quote_volume,
            exchange
        FROM kline_data
        WHERE timestamp >= :start_time
        ORDER BY timestamp DESC
        LIMIT 20
    """)

    start_time = datetime.utcnow() - timedelta(minutes=10)
    results = session.execute(sql, {"start_time": start_time}).fetchall()

    if results:
        print(f"{'时间 (UTC)':<22} {'币种':<15} {'周期':<8} {'价格':<12} {'成交量(USDT)':<20} {'交易所':<12} {'状态'}")
        print("-" * 120)

        has_qv = 0
        no_qv = 0

        for row in results:
            timestamp = row[0]
            symbol = row[1]
            timeframe = row[2]
            price = row[3]
            quote_volume = row[4]
            exchange = row[5]

            if quote_volume and quote_volume > 0:
                status = "✅"
                has_qv += 1
            else:
                status = "❌"
                no_qv += 1

            qv_str = f"${quote_volume:,.2f}" if quote_volume else "NULL"

            print(f"{timestamp} {symbol:<15} {timeframe:<8} ${price:<10.2f} {qv_str:<20} {exchange:<12} {status}")

        print("\n" + "-" * 120)
        print(f"最近10分钟: 总共 {len(results)} 条K线")
        print(f"  ✅ 有 quote_volume: {has_qv} 条 ({has_qv/len(results)*100:.1f}%)")
        print(f"  ❌ 无 quote_volume: {no_qv} 条 ({no_qv/len(results)*100:.1f}%)")

        if has_qv > 0:
            print(f"\n🎉🎉🎉 太好了！scheduler 正在采集包含 quote_volume 的数据！")
            print(f"       现在只需要:")
            print(f"       1. 等待1小时让足够的数据积累")
            print(f"       2. 运行: python check_and_update_cache.py")
            print(f"       3. 刷新 Dashboard 查看成交量")
        elif no_qv == len(results):
            print(f"\n⚠️  最近10分钟采集的数据仍然没有 quote_volume")
            print(f"    需要重启 scheduler 以加载修复后的代码")
        else:
            print(f"\n⚠️  部分数据有 quote_volume，部分没有")
            print(f"    可能是 scheduler 在运行过程中被更新了代码")
    else:
        print("⚠️  最近10分钟没有任何K线数据")
        print("   scheduler 可能没有在运行")

except Exception as e:
    print(f"查询失败: {e}")
    import traceback
    traceback.print_exc()

session.close()

print("\n" + "=" * 120)
print("\n总结:")
print("- 如果最近的K线有 ✅ quote_volume，说明修复成功")
print("- 如果最近的K线都是 ❌ NULL，说明需要重启 scheduler")
print("- 时间显示为 UTC (伦敦时间)，这是 Binance 的标准时间")
print("\n" + "=" * 120)
