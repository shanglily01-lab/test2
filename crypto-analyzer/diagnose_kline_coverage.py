"""
检查K线数据的时间覆盖范围
"""
import yaml
from sqlalchemy import create_engine, text
from urllib.parse import quote_plus

# 加载配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

database_config = config.get('database', {})
db_config = database_config.get('mysql', {})
password = quote_plus(db_config.get('password', ''))

engine = create_engine(
    f"mysql+pymysql://{db_config.get('user', 'root')}:{password}@"
    f"{db_config.get('host', 'localhost')}:{db_config.get('port', 3306)}/"
    f"{db_config.get('database', 'binance-data')}",
    echo=False
)

print("\n" + "="*80)
print("检查 K线数据的时间覆盖范围")
print("="*80 + "\n")

with engine.connect() as conn:
    # 检查不同时间范围的数据量
    for hours in [1, 6, 12, 24]:
        query = text(f"""
            SELECT
                symbol,
                COUNT(*) as kline_count,
                MIN(timestamp) as earliest,
                MAX(timestamp) as latest,
                TIMESTAMPDIFF(HOUR, MIN(timestamp), MAX(timestamp)) as hours_covered
            FROM kline_data
            WHERE symbol IN ('BTC/USDT', 'ETH/USDT', 'SOL/USDT')
              AND timeframe = '5m'
              AND timestamp >= DATE_SUB(NOW(), INTERVAL {hours} HOUR)
            GROUP BY symbol
        """)

        results = conn.execute(query).fetchall()

        print(f"最近 {hours} 小时的数据：")
        print("-" * 80)

        if not results:
            print(f"  ❌ 没有数据\n")
            continue

        for row in results:
            expected_count = hours * 12  # 每小时12根5分钟K线
            coverage_pct = (row.kline_count / expected_count * 100) if expected_count > 0 else 0

            status = "✓" if coverage_pct >= 80 else "⚠️"

            print(f"  {status} {row.symbol}: {row.kline_count} 根K线 "
                  f"(预期: {expected_count}, 覆盖率: {coverage_pct:.1f}%)")

        print()

    # 推荐使用哪个时间范围
    print("="*80)
    print("建议：")
    print("-" * 80)

    query = text("""
        SELECT
            symbol,
            COUNT(*) as count_24h
        FROM kline_data
        WHERE symbol = 'BTC/USDT'
          AND timeframe = '5m'
          AND timestamp >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
    """)

    result = conn.execute(query).fetchone()

    if result and result.count_24h >= 240:  # 至少80%的数据 (288 * 0.8)
        print("✓ 有足够的24小时数据，建议修改缓存服务使用24小时数据")
        print(f"  - 将 cache_update_service.py 第102行改为: timedelta(hours=24)")
        print(f"  - 将 cache_update_service.py 第103行改为: limit=288  # 5分钟 * 288 = 24小时")
    else:
        print(f"⚠️ 24小时数据不足 (只有 {result.count_24h if result else 0} 根)，建议：")
        print("  1. 运行历史数据回填脚本: python backfill_historical_data.py")
        print("  2. 或者继续使用1小时数据，但要理解这不是真正的24小时统计")

    print("="*80 + "\n")
