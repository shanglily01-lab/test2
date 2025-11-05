"""
直接查询 BTC 的数据
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

# 创建数据库连接
engine = create_engine(
    f"mysql+pymysql://{db_config.get('user', 'root')}:{password}@"
    f"{db_config.get('host', 'localhost')}:{db_config.get('port', 3306)}/"
    f"{db_config.get('database', 'binance-data')}",
    echo=False
)

print("\n直接查询 BTC/USDT 在 price_stats_24h 表中的原始数据：\n")

with engine.connect() as conn:
    query = text("""
        SELECT
            symbol,
            current_price,
            volume_24h,
            quote_volume_24h,
            change_24h,
            updated_at
        FROM price_stats_24h
        WHERE symbol = 'BTC/USDT'
    """)

    result = conn.execute(query).fetchone()

    if result:
        print(f"symbol: {result.symbol}")
        print(f"current_price: {result.current_price}")
        print(f"volume_24h: {result.volume_24h}")
        print(f"quote_volume_24h: {result.quote_volume_24h}")
        print(f"change_24h: {result.change_24h}")
        print(f"updated_at: {result.updated_at}")
        print()
        print(f"API 返回的 volume_24h: 15243036.99")
        print(f"数据库中的 volume_24h: {result.volume_24h}")
        print()
        if abs(result.volume_24h - 15243036.99) < 0.01:
            print("✓ 数据库值和 API 返回值一致 - 问题在数据库写入时")
        else:
            print("✗ 数据库值和 API 返回值不一致 - 问题在 API 处理时")
    else:
        print("❌ 数据库中没有找到 BTC/USDT 的记录")
