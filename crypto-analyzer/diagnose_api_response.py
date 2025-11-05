"""
测试 API 返回的数据
"""
import asyncio
import yaml
from app.api.enhanced_dashboard_cached import get_cached_prices

async def test_api():
    # 加载配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']

    print("\n" + "="*80)
    print("测试 API 返回数据")
    print("="*80 + "\n")

    prices = await get_cached_prices(symbols, config)

    if not prices:
        print("❌ API 没有返回数据！")
        return

    print(f"找到 {len(prices)} 个币种数据:\n")

    for p in prices:
        print(f"{p['full_symbol']}:")
        print(f"  price: {p['price']}")
        print(f"  volume_24h: {p['volume_24h']}")
        print(f"  quote_volume_24h: {p['quote_volume_24h']}")
        print(f"  change_24h: {p['change_24h']}")
        print()

if __name__ == '__main__':
    asyncio.run(test_api())
