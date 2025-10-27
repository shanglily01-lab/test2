"""
直接查询数据库 - 检查最近1小时的5分钟K线是否有 quote_volume
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
print("检查最近1小时的5分钟K线 quote_volume 数据")
print(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 120 + "\n")

# 查询最近1小时的5分钟K线
sql = text("""
    SELECT
        symbol,
        timestamp,
        close_price,
        volume,
        quote_volume
    FROM kline_data
    WHERE timeframe = '5m'
    AND timestamp >= :start_time
    ORDER BY symbol, timestamp DESC
    LIMIT 100
""")

start_time = datetime.now() - timedelta(hours=1)
results = session.execute(sql, {"start_time": start_time}).fetchall()

if not results:
    print("❌ 没有找到最近1小时的5分钟K线数据")
    session.close()
    sys.exit(1)

print(f"找到 {len(results)} 条5分钟K线\n")

# 按币种分组统计
from collections import defaultdict
symbol_data = defaultdict(list)

for row in results:
    symbol = row[0]
    symbol_data[symbol].append(row)

print(f"涉及 {len(symbol_data)} 个币种\n")

# 逐个币种显示
for symbol in sorted(symbol_data.keys()):
    rows = symbol_data[symbol]

    print(f"\n{'='*120}")
    print(f"{symbol} - {len(rows)} 条K线")
    print(f"{'='*120}")
    print(f"{'时间':<22} {'价格':<12} {'成交量(币)':<18} {'成交量(USDT)':<25} {'状态'}")
    print("-" * 120)

    total_qv = 0
    has_qv = 0
    no_qv = 0

    for row in rows:
        timestamp = row[1]
        price = float(row[2]) if row[2] else 0
        volume = float(row[3]) if row[3] else 0
        quote_volume = row[4]

        if quote_volume and float(quote_volume) > 0:
            qv_float = float(quote_volume)
            qv_str = f"${qv_float:,.2f}"
            status = "✅"
            total_qv += qv_float
            has_qv += 1
        else:
            qv_str = "NULL or 0"
            status = "❌"
            no_qv += 1

        print(f"{timestamp} ${price:<10.2f} {volume:<17,.4f} {qv_str:<25} {status}")

    print("-" * 120)
    print(f"统计: ✅ {has_qv} 条有数据 | ❌ {no_qv} 条无数据")
    print(f"总计: ${total_qv:,.2f}")

    if total_qv > 0:
        print(f"✅ {symbol} 的5分钟K线有 quote_volume，缓存应该能计算出成交量")
    else:
        print(f"❌ {symbol} 的5分钟K线没有 quote_volume，缓存会显示 $0.00")

session.close()

print("\n" + "=" * 120)
print("\n结论:")
print("- 如果所有币种都显示 ✅ 和总计金额，说明数据正常，问题在缓存更新逻辑")
print("- 如果显示 ❌ 和 NULL，说明5分钟K线还没有 quote_volume 数据")
print("=" * 120)
