"""
调试 API 返回数据
"""
import asyncio
import yaml
from app.api.enhanced_dashboard_cached import EnhancedDashboardCached

async def debug():
    # 加载配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 创建 dashboard 实例
    dashboard = EnhancedDashboardCached(config)

    # 只查询 BTC
    symbols = ['BTC/USDT']

    print("\n" + "="*80)
    print("调试 API 返回数据")
    print("="*80 + "\n")

    # 调用 _get_prices_from_cache 方法
    prices = await dashboard._get_prices_from_cache(symbols)

    if prices:
        btc = prices[0]
        print(f"API 返回的 BTC 数据:")
        print(f"  symbol: {btc.get('symbol')}")
        print(f"  full_symbol: {btc.get('full_symbol')}")
        print(f"  price: {btc.get('price')}")
        print(f"  volume_24h: {btc.get('volume_24h')}")
        print(f"  quote_volume_24h: {btc.get('quote_volume_24h')}")
        print()

        # 对比期望值
        print("期望值（从数据库直接查询）:")
        print("  volume_24h: 33,130.53 (BTC数量)")
        print("  quote_volume_24h: 3,345,403,872.44 (美元金额)")
        print()

        # 判断
        if btc.get('volume_24h', 0) > 1000000:
            print("❌ 问题：volume_24h 的值太大，可能是金额而不是数量")
            print(f"   实际值: {btc.get('volume_24h')}")
        else:
            print("✓ volume_24h 看起来正确")

        if btc.get('quote_volume_24h', 0) > 1000000000:
            print("✓ quote_volume_24h 看起来正确")
        else:
            print(f"❌ 问题：quote_volume_24h 的值太小: {btc.get('quote_volume_24h')}")

    else:
        print("❌ API 没有返回数据")

if __name__ == '__main__':
    asyncio.run(debug())
