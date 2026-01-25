"""
获取币安合约交易额前100的交易对
"""
from binance.client import Client
import os

api_key = os.getenv('BINANCE_API_KEY', 'iIPpaIv5nUGVUJFJc5UVTEU02rMt7KXMbevJKTPxWCxm88yoVieMEUvSTOmMrV1N')
api_secret = os.getenv('BINANCE_API_SECRET', 'pQXcz2v66R2tLYZDan2FC2swTjm1G1ML5FyzfpCk3DsQrRe2bewgrfvPE2fQOZbB')

client = Client(api_key, api_secret)

# 获取24小时ticker数据
tickers = client.futures_ticker()

# 过滤USDT合约，并按交易额排序
usdt_pairs = []
for ticker in tickers:
    symbol = ticker['symbol']
    if symbol.endswith('USDT'):
        quote_volume = float(ticker['quoteVolume'])
        usdt_pairs.append({
            'symbol': symbol,
            'quote_volume': quote_volume,
            'price_change_pct': float(ticker['priceChangePercent']),
            'volume': float(ticker['volume'])
        })

# 按交易额排序
usdt_pairs.sort(key=lambda x: x['quote_volume'], reverse=True)

# 取前100个
top_100 = usdt_pairs[:100]

print(f"币安合约交易额前100的USDT交易对:\n")
print(f"{'排名':<5} {'交易对':<15} {'24h交易额(USDT)':<20} {'24h涨跌幅':<12}")
print("=" * 60)

for i, pair in enumerate(top_100, 1):
    symbol_formatted = pair['symbol'].replace('USDT', '/USDT')
    volume_str = f"${pair['quote_volume']:,.0f}"
    change_str = f"{pair['price_change_pct']:+.2f}%"
    print(f"{i:<5} {symbol_formatted:<15} {volume_str:<20} {change_str:<12}")

# 生成Python列表格式
print("\n\n生成的Python列表（可直接复制到配置文件）:\n")
print("top_100_symbols = [")
for pair in top_100:
    symbol_formatted = pair['symbol'].replace('USDT', '/USDT')
    print(f"    '{symbol_formatted}',")
print("]")

# 统计信息
total_volume = sum(p['quote_volume'] for p in top_100)
print(f"\n\n统计信息:")
print(f"交易对数量: {len(top_100)}")
print(f"总交易额: ${total_volume:,.0f}")
print(f"平均交易额: ${total_volume/len(top_100):,.0f}")
