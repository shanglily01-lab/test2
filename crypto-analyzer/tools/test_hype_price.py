"""
测试 HYPE 价格采集
验证 Gate.io 是否能够正确获取 HYPE/USDT 价格
"""

import asyncio
import yaml
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.collectors.price_collector import MultiExchangeCollector


async def test_hype_price():
    """测试 HYPE 价格采集"""

    print("=" * 80)
    print("  HYPE/USDT 价格采集测试")
    print("=" * 80)
    print()

    # 加载配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 创建多交易所采集器
    collector = MultiExchangeCollector(config)

    print(f"已启用的交易所: {list(collector.collectors.keys())}")
    print()

    # 测试 HYPE/USDT
    symbol = 'HYPE/USDT'

    print(f"1. 测试从所有交易所获取 {symbol} 价格...")
    print("-" * 80)

    prices = await collector.fetch_price(symbol)

    if not prices:
        print(f"❌ 未获取到任何价格数据!")
        print()
        print("可能的原因:")
        print("1. Binance 没有 HYPE/USDT 交易对")
        print("2. Gate.io 获取失败（网络问题或API限制）")
        print()

        # 单独测试每个交易所
        print("2. 分别测试各交易所...")
        print("-" * 80)

        for exchange_id, exchange_collector in collector.collectors.items():
            print(f"\n测试 {exchange_id}:")
            try:
                price = await exchange_collector.fetch_ticker(symbol)
                if price:
                    print(f"  ✅ 成功获取价格: ${price['price']:,.4f}")
                    print(f"     交易所: {price.get('exchange', 'unknown')}")
                    print(f"     24h涨跌: {price.get('change_24h', 0):+.2f}%")
                    print(f"     24h成交量: {price.get('volume', 0):,.0f}")
                else:
                    print(f"  ❌ 未获取到价格（交易对可能不存在）")
            except Exception as e:
                print(f"  ❌ 获取失败: {e}")
    else:
        print(f"✅ 成功获取到 {len(prices)} 个交易所的价格:")
        print()

        for price in prices:
            exchange = price.get('exchange', 'unknown')
            print(f"  【{exchange}】")
            print(f"    价格: ${price['price']:,.4f}")
            print(f"    24h涨跌: {price.get('change_24h', 0):+.2f}%")
            print(f"    24h成交量: {price.get('volume', 0):,.0f}")
            print()

    print()
    print("3. 测试获取最优价格（聚合）...")
    print("-" * 80)

    best_price = await collector.fetch_best_price(symbol)

    if best_price:
        print(f"✅ 聚合价格:")
        print(f"  平均价格: ${best_price['price']:,.4f}")
        print(f"  最高价格: ${best_price['max_price']:,.4f}")
        print(f"  最低价格: ${best_price['min_price']:,.4f}")
        print(f"  价差: ${best_price.get('spread', 0):,.4f} ({best_price.get('spread_pct', 0):.2f}%)")
        print(f"  数据来源: {best_price.get('exchanges', 0)} 个交易所")
        print(f"  总成交量: {best_price.get('total_volume', 0):,.0f}")
    else:
        print(f"❌ 无法获取聚合价格")

    print()
    print("=" * 80)
    print("测试完成")
    print("=" * 80)
    print()

    # 诊断建议
    if not prices:
        print("⚠️  诊断建议:")
        print()
        print("1. 检查 Gate.io 配置:")
        print("   - config.yaml 中 gate.enabled 是否为 true")
        print("   - Gate.io API key 是否正确（公开数据不需要key）")
        print()
        print("2. 检查网络连接:")
        print("   - 能否访问 api.gateio.ws")
        print("   - 是否需要配置代理")
        print()
        print("3. 验证交易对:")
        print("   - 访问 https://www.gate.io/zh/trade/HYPE_USDT")
        print("   - 确认 HYPE/USDT 在 Gate.io 存在")
        print()
        print("4. 查看日志:")
        print("   - logs/app.log")
        print("   - logs/scheduler.log")


if __name__ == "__main__":
    asyncio.run(test_hype_price())
