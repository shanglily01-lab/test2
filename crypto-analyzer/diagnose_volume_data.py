"""
检查成交量数据
用于诊断 volume_24h 和 quote_volume_24h 的实际值
"""

import yaml
from sqlalchemy import create_engine, text
from datetime import datetime

# 加载配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config.get('database', {}).get('mysql', {})

# 创建数据库连接
engine = create_engine(
    f"mysql+pymysql://{db_config['user']}:{db_config['password']}@"
    f"{db_config['host']}:{db_config['port']}/{db_config['database']}",
    echo=False
)

print("\n" + "="*80)
print("检查数据库中的成交量数据")
print("="*80 + "\n")

# 1. 检查 price_stats_24h 表
print("📊 1. 检查 price_stats_24h 表（缓存数据）")
print("-"*80)

with engine.connect() as conn:
    query = text("""
        SELECT
            symbol,
            current_price,
            volume_24h,
            quote_volume_24h,
            updated_at
        FROM price_stats_24h
        WHERE symbol IN ('BTC/USDT', 'ETH/USDT', 'SOL/USDT')
        ORDER BY symbol
    """)

    results = conn.execute(query).fetchall()

    if results:
        for row in results:
            print(f"\n{row.symbol}:")
            print(f"  当前价格: ${row.current_price:,.2f}")
            print(f"  volume_24h: {row.volume_24h:,.2f}")
            print(f"  quote_volume_24h: {row.quote_volume_24h:,.2f}")
            print(f"  更新时间: {row.updated_at}")

            # 分析数据
            if row.volume_24h > 1000000:
                print(f"  ⚠️ volume_24h 值很大 ({row.volume_24h:,.0f})，可能是 USDT 金额而不是币的数量")
            if row.quote_volume_24h == 0:
                print(f"  ⚠️ quote_volume_24h 为 0，可能数据有问题")

            # 计算理论值
            if row.volume_24h > 0 and row.current_price > 0:
                theoretical_quote = row.volume_24h * row.current_price
                print(f"  理论成交额 (volume_24h × price): ${theoretical_quote:,.2f}")
    else:
        print("❌ price_stats_24h 表中没有数据")

# 2. 检查 kline_data 表
print("\n" + "="*80)
print("📈 2. 检查 kline_data 表（原始K线数据）")
print("-"*80)

with engine.connect() as conn:
    query = text("""
        SELECT
            symbol,
            timeframe,
            COUNT(*) as count,
            SUM(volume) as total_volume,
            SUM(quote_volume) as total_quote_volume,
            AVG(close) as avg_price,
            MAX(timestamp) as latest_time
        FROM kline_data
        WHERE symbol IN ('BTC/USDT', 'ETH/USDT', 'SOL/USDT')
          AND timeframe = '15m'
          AND timestamp >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
        GROUP BY symbol, timeframe
        ORDER BY symbol
    """)

    results = conn.execute(query).fetchall()

    if results:
        for row in results:
            print(f"\n{row.symbol} (15分钟K线, 最近24小时):")
            print(f"  K线数量: {row.count}")
            print(f"  总 volume: {row.total_volume:,.2f}")
            print(f"  总 quote_volume: {row.total_quote_volume:,.2f}")
            print(f"  平均价格: ${row.avg_price:,.2f}")
            print(f"  最新时间: {row.latest_time}")

            # 分析数据
            if row.total_volume > 1000000:
                print(f"  ⚠️ total_volume 很大 ({row.total_volume:,.0f})，可能是金额")
            if row.total_quote_volume == 0:
                print(f"  ⚠️ total_quote_volume 为 0")

            # 检查数据合理性
            if row.total_volume > 0 and row.avg_price > 0:
                theoretical_quote = row.total_volume * row.avg_price
                print(f"  理论成交额: ${theoretical_quote:,.2f}")

                if row.total_quote_volume > 0:
                    ratio = row.total_quote_volume / theoretical_quote
                    if 0.8 < ratio < 1.2:
                        print(f"  ✓ 数据合理 (ratio: {ratio:.2f})")
                    else:
                        print(f"  ⚠️ 数据可能有问题 (ratio: {ratio:.2f})")
    else:
        print("❌ kline_data 表中没有最近24小时的数据")

# 3. 查看几条原始 K线数据样本
print("\n" + "="*80)
print("🔍 3. BTC/USDT 原始K线数据样本 (最近5条)")
print("-"*80)

with engine.connect() as conn:
    query = text("""
        SELECT
            timestamp,
            open,
            high,
            low,
            close,
            volume,
            quote_volume
        FROM kline_data
        WHERE symbol = 'BTC/USDT'
          AND timeframe = '15m'
        ORDER BY timestamp DESC
        LIMIT 5
    """)

    results = conn.execute(query).fetchall()

    if results:
        for i, row in enumerate(results, 1):
            print(f"\nK线 {i} - {row.timestamp}:")
            print(f"  价格: O:{row.open:,.2f} H:{row.high:,.2f} L:{row.low:,.2f} C:{row.close:,.2f}")
            print(f"  volume: {row.volume:,.4f}")
            print(f"  quote_volume: {row.quote_volume:,.2f}")

            # 验证数据
            if row.volume > 0 and row.close > 0:
                expected_quote = row.volume * row.close
                print(f"  预期 quote_volume (volume × close): {expected_quote:,.2f}")

                if row.quote_volume > 0:
                    ratio = row.quote_volume / expected_quote
                    if 0.8 < ratio < 1.2:
                        print(f"  ✓ 数据匹配良好 (ratio: {ratio:.2f})")
                    else:
                        print(f"  ⚠️ 数据不匹配 (ratio: {ratio:.2f})")
    else:
        print("❌ 没有找到 BTC/USDT 的K线数据")

print("\n" + "="*80)
print("诊断完成")
print("="*80 + "\n")

print("📝 说明:")
print("  - volume: 应该是基础货币的数量（BTC、ETH的个数）")
print("  - quote_volume: 应该是计价货币的金额（USDT的金额）")
print("  - 正常情况: quote_volume ≈ volume × price")
print()
