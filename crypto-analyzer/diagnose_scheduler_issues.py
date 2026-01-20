"""
诊断 scheduler.py 的数据采集延迟问题
"""
import pymysql
from datetime import datetime, timedelta
from collections import defaultdict
import sys

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

# Database config
db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data',
    'charset': 'utf8mb4'
}

print("=" * 100)
print("数据采集延迟诊断报告")
print("=" * 100)

conn = pymysql.connect(**db_config)
cursor = conn.cursor(pymysql.cursors.DictCursor)

# 1. 检查最近1小时的K线数据新鲜度
print("\n1. K线数据新鲜度检查 (最近1小时)")
print("-" * 100)

cursor.execute("""
    SELECT
        symbol,
        timeframe,
        exchange,
        MAX(timestamp) as latest_timestamp,
        TIMESTAMPDIFF(MINUTE, MAX(timestamp), NOW()) as minutes_ago,
        COUNT(*) as kline_count
    FROM kline_data
    WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
    GROUP BY symbol, timeframe, exchange
    HAVING minutes_ago > 10  -- 超过10分钟没有新数据
    ORDER BY minutes_ago DESC
    LIMIT 20
""")

stale_klines = cursor.fetchall()

if stale_klines:
    print(f"⚠️  发现 {len(stale_klines)} 个交易对的K线数据不新鲜 (超过10分钟):")
    print()
    for row in stale_klines:
        print(f"  {row['symbol']:15s} {row['timeframe']:5s} [{row['exchange']:15s}]  "
              f"最新数据: {row['minutes_ago']:3d}分钟前  "
              f"(共{row['kline_count']}条)")
else:
    print("✅ 所有K线数据都是新鲜的")

# 2. 检查1m K线的采集间隔
print("\n\n2. 1分钟K线采集间隔分析 (应该每5秒一次)")
print("-" * 100)

# 选择几个代表性的交易对
test_symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']

for symbol in test_symbols:
    cursor.execute("""
        SELECT
            timestamp,
            LAG(timestamp) OVER (ORDER BY timestamp) as prev_timestamp,
            TIMESTAMPDIFF(SECOND, LAG(timestamp) OVER (ORDER BY timestamp), timestamp) as interval_seconds
        FROM kline_data
        WHERE symbol = %s
        AND timeframe = '1m'
        AND exchange = 'binance_futures'
        AND timestamp >= DATE_SUB(NOW(), INTERVAL 10 MINUTE)
        ORDER BY timestamp DESC
        LIMIT 50
    """, (symbol,))

    intervals = cursor.fetchall()

    if intervals:
        # 过滤掉NULL的间隔
        valid_intervals = [row['interval_seconds'] for row in intervals if row['interval_seconds'] is not None]

        if valid_intervals:
            avg_interval = sum(valid_intervals) / len(valid_intervals)
            max_interval = max(valid_intervals)
            min_interval = min(valid_intervals)

            # 统计间隔分布
            interval_dist = defaultdict(int)
            for interval in valid_intervals:
                if interval <= 10:
                    interval_dist['0-10秒'] += 1
                elif interval <= 60:
                    interval_dist['11-60秒'] += 1
                elif interval <= 120:
                    interval_dist['1-2分钟'] += 1
                else:
                    interval_dist['>2分钟'] += 1

            print(f"\n{symbol}:")
            print(f"  平均间隔: {avg_interval:.1f}秒  (预期: 5秒)")
            print(f"  最大间隔: {max_interval}秒")
            print(f"  最小间隔: {min_interval}秒")
            print(f"  间隔分布:")
            for range_name, count in sorted(interval_dist.items()):
                pct = count / len(valid_intervals) * 100
                print(f"    {range_name:12s}: {count:3d}次 ({pct:5.1f}%)")

            if avg_interval > 10:
                print(f"  ⚠️  平均间隔 {avg_interval:.1f}秒 远超预期的5秒!")
            elif max_interval > 60:
                print(f"  ⚠️  最大间隔 {max_interval}秒 过长!")

# 3. 检查所有交易对的最新数据时间
print("\n\n3. 所有交易对的最新K线数据 (1m, binance_futures)")
print("-" * 100)

cursor.execute("""
    SELECT
        symbol,
        MAX(timestamp) as latest_timestamp,
        TIMESTAMPDIFF(SECOND, MAX(timestamp), NOW()) as seconds_ago
    FROM kline_data
    WHERE timeframe = '1m'
    AND exchange = 'binance_futures'
    AND timestamp >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
    GROUP BY symbol
    ORDER BY seconds_ago DESC
""")

all_symbols_freshness = cursor.fetchall()

# 统计数据新鲜度
fresh_count = 0  # <10秒
acceptable_count = 0  # 10-60秒
stale_count = 0  # >60秒

freshness_groups = {
    '最新 (<10秒)': [],
    '可接受 (10-60秒)': [],
    '延迟 (1-5分钟)': [],
    '严重延迟 (>5分钟)': []
}

for row in all_symbols_freshness:
    seconds = row['seconds_ago']
    symbol = row['symbol']

    if seconds < 10:
        fresh_count += 1
        freshness_groups['最新 (<10秒)'].append(symbol)
    elif seconds < 60:
        acceptable_count += 1
        freshness_groups['可接受 (10-60秒)'].append(symbol)
    elif seconds < 300:
        stale_count += 1
        freshness_groups['延迟 (1-5分钟)'].append(symbol)
    else:
        freshness_groups['严重延迟 (>5分钟)'].append(symbol)

total_symbols = len(all_symbols_freshness)
print(f"总交易对数: {total_symbols}")
print()

for group_name, symbols in freshness_groups.items():
    count = len(symbols)
    pct = count / total_symbols * 100 if total_symbols > 0 else 0
    print(f"{group_name:20s}: {count:3d} 个 ({pct:5.1f}%)")

    if count > 0 and count <= 10:
        print(f"  {', '.join(symbols)}")

if stale_count > 0 or len(freshness_groups['严重延迟 (>5分钟)']) > 0:
    print("\n⚠️  发现数据延迟问题!")
else:
    print("\n✅ 所有交易对数据都是新鲜的")

# 4. 检查采集任务的执行频率
print("\n\n4. 价格数据采集频率 (price_data表)")
print("-" * 100)

cursor.execute("""
    SELECT
        COUNT(DISTINCT symbol) as symbol_count,
        COUNT(*) as total_records,
        MIN(timestamp) as earliest,
        MAX(timestamp) as latest,
        TIMESTAMPDIFF(SECOND, MIN(timestamp), MAX(timestamp)) as time_span_seconds,
        COUNT(*) / TIMESTAMPDIFF(SECOND, MIN(timestamp), MAX(timestamp)) as records_per_second
    FROM price_data
    WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 10 MINUTE)
    AND exchange = 'binance_futures'
""")

price_stats = cursor.fetchone()

if price_stats and price_stats['total_records'] > 0:
    time_span_min = price_stats['time_span_seconds'] / 60
    records_per_min = price_stats['records_per_second'] * 60 if price_stats['records_per_second'] else 0

    print(f"最近10分钟:")
    print(f"  交易对数: {price_stats['symbol_count']}")
    print(f"  总记录数: {price_stats['total_records']}")
    print(f"  时间跨度: {time_span_min:.1f}分钟")
    print(f"  采集频率: {records_per_min:.1f} 条/分钟")
    print()

    # 如果是39个交易对,每5秒采集一次,应该是 39 * 12 = 468 条/分钟
    expected_per_min = price_stats['symbol_count'] * 12
    print(f"  预期频率: {expected_per_min:.0f} 条/分钟 (基于 {price_stats['symbol_count']} 个交易对 × 12次/分钟)")

    if records_per_min < expected_per_min * 0.8:
        print(f"  ⚠️  实际采集频率低于预期! ({records_per_min / expected_per_min * 100:.1f}%)")

cursor.close()
conn.close()

print("\n" + "=" * 100)
print("诊断完成")
print("=" * 100)
