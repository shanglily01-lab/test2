"""
详细调试 quote_volume 数据
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
print("详细调试 quote_volume 数据")
print("=" * 120 + "\n")

# 选择一个币种进行详细分析
test_symbol = 'BTC/USDT'

print(f"分析币种: {test_symbol}\n")
print("=" * 120)

# 1. 检查最近1小时的5分钟K线
print("\n1. 检查最近1小时的5分钟K线 (应该有12根):\n")

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
        WHERE symbol = :symbol
        AND timeframe = '5m'
        AND timestamp >= :start_time
        ORDER BY timestamp DESC
        LIMIT 20
    """)

    start_time = datetime.now() - timedelta(hours=1)
    results = session.execute(sql, {"symbol": test_symbol, "start_time": start_time}).fetchall()

    if results:
        print(f"{'时间':<22} {'价格':<12} {'成交量(币)':<18} {'成交量(USDT)':<20} {'交易所':<12} {'状态'}")
        print("-" * 120)

        total_volume = 0
        total_quote_volume = 0
        has_qv_count = 0
        null_qv_count = 0

        for row in results:
            timestamp = row[2]
            price = row[3]
            volume = row[4] if row[4] else 0
            quote_volume = row[5]
            exchange = row[6]

            if quote_volume and quote_volume > 0:
                status = "✅ 有数据"
                has_qv_count += 1
                total_quote_volume += float(quote_volume)
            else:
                status = "❌ NULL/0"
                null_qv_count += 1

            total_volume += float(volume) if volume else 0

            qv_str = f"${quote_volume:,.2f}" if quote_volume else "NULL"

            print(f"{timestamp} ${price:<10.2f} {volume:<17,.4f} {qv_str:<20} {exchange:<12} {status}")

        print("\n" + "-" * 120)
        print(f"总计: {len(results)} 根K线")
        print(f"  ✅ 有 quote_volume: {has_qv_count} 根")
        print(f"  ❌ 无 quote_volume: {null_qv_count} 根")
        print(f"  📊 总成交量(币): {total_volume:,.4f}")
        print(f"  💰 总成交量(USDT): ${total_quote_volume:,.2f}")

        if has_qv_count == 0:
            print("\n⚠️⚠️⚠️  所有5分钟K线的 quote_volume 都是 NULL！")
            print("      说明调度器还没有采集到包含 quote_volume 的5分钟K线")
            print("      请检查:")
            print("      1. scheduler.py 是否已经重启（使用修复后的版本）")
            print("      2. 是否等待了至少5分钟让调度器采集新数据")

    else:
        print("⚠️  最近1小时没有5分钟K线数据")

except Exception as e:
    print(f"查询失败: {e}")
    import traceback
    traceback.print_exc()

# 2. 检查最近1小时的1分钟K线（应该更多）
print("\n" + "=" * 120)
print("\n2. 检查最近1小时的1分钟K线 (前15根):\n")

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
        WHERE symbol = :symbol
        AND timeframe = '1m'
        AND timestamp >= :start_time
        ORDER BY timestamp DESC
        LIMIT 15
    """)

    start_time = datetime.now() - timedelta(hours=1)
    results = session.execute(sql, {"symbol": test_symbol, "start_time": start_time}).fetchall()

    if results:
        print(f"{'时间':<22} {'价格':<12} {'成交量(币)':<18} {'成交量(USDT)':<20} {'交易所':<12} {'状态'}")
        print("-" * 120)

        has_qv_count = 0
        null_qv_count = 0

        for row in results[:15]:  # 只显示前15根
            timestamp = row[2]
            price = row[3]
            volume = row[4] if row[4] else 0
            quote_volume = row[5]
            exchange = row[6]

            if quote_volume and quote_volume > 0:
                status = "✅ 有数据"
                has_qv_count += 1
            else:
                status = "❌ NULL/0"
                null_qv_count += 1

            qv_str = f"${quote_volume:,.2f}" if quote_volume else "NULL"

            print(f"{timestamp} ${price:<10.2f} {volume:<17,.4f} {qv_str:<20} {exchange:<12} {status}")

        print("\n" + "-" * 120)
        print(f"显示: 前 15 根 (共 {len(results)} 根)")
        print(f"  ✅ 有 quote_volume: {has_qv_count} 根")
        print(f"  ❌ 无 quote_volume: {null_qv_count} 根")

        if has_qv_count > 0:
            print("\n✅ 1分钟K线有 quote_volume 数据")
            print("   但5分钟K线如果没有，说明5分钟K线还未采集或未包含该字段")
        else:
            print("\n❌ 1分钟K线也没有 quote_volume 数据")
            print("   需要重启 scheduler 并等待新数据采集")

    else:
        print("⚠️  最近1小时没有1分钟K线数据")

except Exception as e:
    print(f"查询失败: {e}")
    import traceback
    traceback.print_exc()

# 3. 检查调度器版本
print("\n" + "=" * 120)
print("\n3. 检查 scheduler.py 是否包含 quote_volume 修复:\n")

scheduler_path = Path(__file__).parent / 'app' / 'scheduler.py'
if scheduler_path.exists():
    with open(scheduler_path, 'r', encoding='utf-8') as f:
        content = f.read()
        if "'quote_volume': latest_kline.get('quote_volume')" in content:
            print("✅ scheduler.py 已包含 quote_volume 修复 (第260行)")
        else:
            print("❌ scheduler.py 未包含 quote_volume 修复")
            print("   请确认已经 git pull 拉取最新代码")
else:
    print("⚠️  找不到 scheduler.py 文件")

# 4. 检查调度器是否在运行
print("\n" + "=" * 120)
print("\n4. 下一步建议:\n")

print("如果所有K线都没有 quote_volume 数据:")
print("  1️⃣  确认已拉取最新代码: git pull")
print("  2️⃣  重启 scheduler: python app/scheduler.py")
print("  3️⃣  等待5-10分钟让调度器采集新数据")
print("  4️⃣  重新运行本脚本: python debug_quote_volume.py")

print("\n如果1分钟K线有数据但5分钟K线没有:")
print("  1️⃣  等待5分钟让5分钟K线生成")
print("  2️⃣  重新运行: python check_and_update_cache.py")

session.close()

print("\n" + "=" * 120)
