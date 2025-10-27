"""
调试缓存计算 - 查看为什么 quote_volume_24h 是 0
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import yaml
from datetime import datetime, timedelta
from app.database.db_service import DatabaseService

# 加载配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_service = DatabaseService(config.get('database', {}))

print("=" * 100)
print("调试缓存计算 - 查看 quote_volume 数据")
print("=" * 100 + "\n")

# 测试币种
test_symbol = 'BTC/USDT'

print(f"测试币种: {test_symbol}")
print(f"当前时间: {datetime.now()}")
print(f"查询范围: 最近1小时的5分钟K线\n")

# 获取K线数据（和缓存更新服务使用相同的方法）
start_time = datetime.now() - timedelta(hours=1)
klines = db_service.get_klines(test_symbol, '5m', start_time=start_time, limit=12)

print(f"查询到 {len(klines)} 条K线数据\n")

if not klines:
    print("❌ 没有查询到K线数据！")
    sys.exit(1)

print("K线详细信息:")
print("-" * 100)
print(f"{'时间':<22} {'开盘':<12} {'收盘':<12} {'成交量(币)':<18} {'成交量(USDT)':<20} {'状态'}")
print("-" * 100)

total_volume = 0
total_quote_volume = 0
has_qv_count = 0
no_qv_count = 0

for k in klines:
    timestamp = k.timestamp
    open_price = float(k.open_price) if k.open_price else 0
    close_price = float(k.close_price) if k.close_price else 0
    volume = float(k.volume) if k.volume else 0
    quote_volume = float(k.quote_volume) if k.quote_volume else None

    total_volume += volume

    if quote_volume:
        total_quote_volume += quote_volume
        status = "✅"
        has_qv_count += 1
    else:
        status = "❌ NULL"
        no_qv_count += 1

    qv_str = f"${quote_volume:,.2f}" if quote_volume else "NULL"

    print(f"{timestamp} ${open_price:<10.2f} ${close_price:<10.2f} {volume:<17,.4f} {qv_str:<20} {status}")

print("-" * 100)
print(f"\n统计:")
print(f"  总K线数: {len(klines)}")
print(f"  ✅ 有 quote_volume: {has_qv_count} 条")
print(f"  ❌ 无 quote_volume: {no_qv_count} 条")
print(f"\n  总成交量(币): {total_volume:,.4f}")
print(f"  总成交量(USDT): ${total_quote_volume:,.2f}")

print("\n" + "=" * 100)
print("\n模拟缓存更新服务的计算:")
print("-" * 100)

# 模拟 cache_update_service.py 第113行的计算
volume_24h = sum(float(k.volume) for k in klines)
quote_volume_24h = sum(float(k.quote_volume) for k in klines if k.quote_volume)

print(f"volume_24h = sum(float(k.volume) for k in klines)")
print(f"  结果: {volume_24h:,.4f}\n")

print(f"quote_volume_24h = sum(float(k.quote_volume) for k in klines if k.quote_volume)")
print(f"  结果: ${quote_volume_24h:,.2f}\n")

if quote_volume_24h > 0:
    print("✅✅✅ quote_volume_24h 计算正确，有数据！")
    print(f"       缓存应该会显示: ${quote_volume_24h:,.2f}")
else:
    print("❌ quote_volume_24h 计算结果为 0")
    print(f"\n可能原因:")
    print(f"  1. K线的 quote_volume 字段都是 NULL ({no_qv_count}/{len(klines)} 条)")
    print(f"  2. if k.quote_volume 条件过滤掉了所有K线")
    print(f"  3. K线数据本身有问题")

print("\n" + "=" * 100)
print("\n详细检查每个K线对象:")
print("-" * 100)

for i, k in enumerate(klines, 1):
    print(f"\nK线 #{i}:")
    print(f"  timestamp: {k.timestamp}")
    print(f"  volume: {k.volume} (类型: {type(k.volume).__name__})")
    print(f"  quote_volume: {k.quote_volume} (类型: {type(k.quote_volume).__name__})")
    print(f"  k.quote_volume 是否为真: {bool(k.quote_volume)}")

    if k.quote_volume:
        print(f"  ✅ 会被包含在计算中")
        try:
            qv_float = float(k.quote_volume)
            print(f"  转换为 float: {qv_float:,.2f}")
        except Exception as e:
            print(f"  ❌ 转换为 float 失败: {e}")
    else:
        print(f"  ❌ 会被 if k.quote_volume 过滤掉")

print("\n" + "=" * 100)
