"""
检查缓存表数据并手动触发更新
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import asyncio
import yaml
from sqlalchemy import text
from app.database.db_service import DatabaseService
from app.services.cache_update_service import CacheUpdateService

# 加载配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

symbols = config.get('symbols', [])

print("=" * 100)
print("检查缓存表数据并手动更新")
print("=" * 100 + "\n")

# 1. 检查当前缓存表数据
db_service = DatabaseService(config.get('database', {}))
session = db_service.get_session()

print("1. 检查 price_stats_24h 缓存表当前数据:\n")

try:
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
        print(f"{'币种':<15} {'价格':<12} {'成交量(币)':<18} {'成交量(USDT)':<18} {'更新时间'}")
        print("-" * 100)

        for row in results:
            symbol = row[0]
            price = row[1]
            volume = row[2] if row[2] else 0
            quote_volume = row[3] if row[3] else 0
            updated_at = row[4]

            status = "✅" if quote_volume > 0 else "❌"
            qv_str = f"${quote_volume:,.2f}" if quote_volume > 0 else "0.00"

            print(f"{status} {symbol:<13} ${price:<10.2f} {volume:<17,.2f} {qv_str:<18} {updated_at}")
    else:
        print("⚠️  缓存表为空")

finally:
    session.close()

# 2. 手动触发缓存更新
print("\n" + "=" * 100)
print("\n2. 手动触发缓存更新 (这可能需要几秒钟):\n")

async def update_cache():
    cache_service = CacheUpdateService(config)

    print("   🔄 更新价格统计缓存...")
    await cache_service.update_price_stats_cache(symbols)
    print("   ✅ 价格统计缓存更新完成\n")

try:
    asyncio.run(update_cache())
except Exception as e:
    print(f"   ❌ 缓存更新失败: {e}")
    import traceback
    traceback.print_exc()

# 3. 再次检查缓存表数据
print("=" * 100)
print("\n3. 更新后的缓存表数据:\n")

session = db_service.get_session()

try:
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
        print(f"{'币种':<15} {'价格':<12} {'成交量(币)':<18} {'成交量(USDT)':<18} {'更新时间'}")
        print("-" * 100)

        has_volume_count = 0
        no_volume_count = 0

        for row in results:
            symbol = row[0]
            price = row[1]
            volume = row[2] if row[2] else 0
            quote_volume = row[3] if row[3] else 0
            updated_at = row[4]

            if quote_volume > 0:
                status = "✅"
                has_volume_count += 1
            else:
                status = "❌"
                no_volume_count += 1

            qv_str = f"${quote_volume:,.2f}" if quote_volume > 0 else "0.00"

            print(f"{status} {symbol:<13} ${price:<10.2f} {volume:<17,.2f} {qv_str:<18} {updated_at}")

        print("\n" + "-" * 100)
        print(f"统计: ✅ 有成交量: {has_volume_count} 个 | ❌ 无成交量: {no_volume_count} 个")

        if has_volume_count > 0:
            print("\n✅✅✅ 缓存表已有成交量数据！")
            print("      现在刷新 Dashboard 应该能看到成交量了")
        else:
            print("\n⚠️⚠️⚠️  缓存表仍然没有成交量数据")
            print("      可能原因:")
            print("      1. K线数据的 quote_volume 字段仍为 NULL (需要等待新数据)")
            print("      2. 24小时内的K线数据不足 (需要等待更多数据)")
    else:
        print("⚠️  缓存表为空")

finally:
    session.close()

print("\n" + "=" * 100)
