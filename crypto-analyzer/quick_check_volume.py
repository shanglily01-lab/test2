"""
快速检查成交量显示问题
"""
import yaml
from sqlalchemy import create_engine, text
from urllib.parse import quote_plus

# 加载配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

# 获取数据库配置
database_config = config.get('database', {})
db_type = database_config.get('type', 'mysql')

if db_type != 'mysql':
    print(f"❌ 不支持的数据库类型: {db_type}")
    print("此脚本仅支持 MySQL 数据库")
    exit(1)

db_config = database_config.get('mysql', {})

# URL 编码密码（处理特殊字符）
password = quote_plus(db_config.get('password', ''))

# 创建数据库连接
engine = create_engine(
    f"mysql+pymysql://{db_config.get('user', 'root')}:{password}@"
    f"{db_config.get('host', 'localhost')}:{db_config.get('port', 3306)}/"
    f"{db_config.get('database', 'binance-data')}",
    echo=False
)

print("\n" + "="*80)
print("快速检查：price_stats_24h 缓存表的数据")
print("="*80 + "\n")

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

    if not results:
        print("❌ price_stats_24h 表中没有数据！")
        print("\n可能原因:")
        print("  1. scheduler 没有运行")
        print("  2. 缓存更新服务没有执行")
        print("\n解决方案:")
        print("  确保 scheduler.py 正在运行")
    else:
        print(f"找到 {len(results)} 条数据:\n")

        for row in results:
            print(f"{row.symbol}:")
            print(f"  当前价格: ${row.current_price:,.2f}")
            print(f"  volume_24h (应该是数量): {row.volume_24h:,.4f}")
            print(f"  quote_volume_24h (应该是金额): ${row.quote_volume_24h:,.2f}")
            print(f"  更新时间: {row.updated_at}")

            # 判断数据是否合理
            if row.symbol == 'BTC/USDT':
                # BTC 的 24h 成交量应该是几百到几千个 BTC
                if row.volume_24h < 100:
                    print(f"  ⚠️ volume_24h 太小了 ({row.volume_24h:.2f})，可能数据不足")
                elif row.volume_24h > 100000:
                    print(f"  ⚠️ volume_24h 太大了 ({row.volume_24h:,.0f})，可能是金额而不是数量")
                else:
                    print(f"  ✓ volume_24h 看起来合理")

                # BTC 的 24h 成交额应该是几千万到几亿美元
                if row.quote_volume_24h < 1000000:
                    print(f"  ⚠️ quote_volume_24h 太小了 (${row.quote_volume_24h:,.0f})")
                elif row.quote_volume_24h > 10000000000:
                    print(f"  ⚠️ quote_volume_24h 太大了 (${row.quote_volume_24h:,.0f})")
                else:
                    print(f"  ✓ quote_volume_24h 看起来合理")

                # 验证一致性
                if row.volume_24h > 0 and row.current_price > 0:
                    implied_quote = row.volume_24h * row.current_price
                    ratio = row.quote_volume_24h / implied_quote if implied_quote > 0 else 0
                    print(f"  理论成交额: ${implied_quote:,.2f}")
                    if 0.5 < ratio < 2.0:
                        print(f"  ✓ 数据一致性良好 (ratio: {ratio:.2f})")
                    else:
                        print(f"  ⚠️ 数据不一致 (ratio: {ratio:.2f})")

            print()

print("="*80)
print("检查完成")
print("="*80)
print("\n说明：")
print("  - volume_24h 应该显示为 '1.23K' 这样的数量")
print("  - quote_volume_24h 应该显示为 '$52.35M' 这样的金额")
print("\n如果数据不对，请:")
print("  1. 确保 scheduler.py 正在运行")
print("  2. 等待几分钟让缓存更新")
print("  3. 或者手动触发: 访问 http://localhost:8000/api/update-cache")
print()
