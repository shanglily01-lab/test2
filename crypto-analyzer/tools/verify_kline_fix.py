"""
验证 K线多交易所支持修复
测试系统是否能从 Gate.io 获取 HYPE K线数据
"""

import asyncio
import yaml
import sys
from pathlib import Path
from datetime import datetime

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.collectors.price_collector import MultiExchangeCollector


async def verify_kline_fix():
    """验证 K线数据采集修复"""

    print("=" * 80)
    print("  K线多交易所支持修复验证")
    print("=" * 80)
    print()

    # 加载配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 创建采集器
    collector = MultiExchangeCollector(config)

    print(f"已启用的交易所: {list(collector.collectors.keys())}")
    print()

    # 测试币种列表
    test_symbols = [
        ('BTC/USDT', 'binance'),   # 预期: Binance 有
        ('HYPE/USDT', 'gate'),     # 预期: 只有 Gate.io
        ('ETH/USDT', 'binance'),   # 预期: Binance 有
    ]

    print("测试 K线数据采集（智能交易所选择）:")
    print("-" * 80)
    print()

    success_count = 0
    fail_count = 0

    for symbol, expected_exchange in test_symbols:
        print(f"测试 {symbol}:")

        # 模拟调度器的逻辑
        enabled_exchanges = list(collector.collectors.keys())
        priority_exchanges = ['binance', 'gate'] + [e for e in enabled_exchanges if e not in ['binance', 'gate']]

        df = None
        used_exchange = None

        for exchange in priority_exchanges:
            if exchange not in enabled_exchanges:
                continue

            try:
                df = await collector.fetch_ohlcv(
                    symbol,
                    timeframe='5m',
                    exchange=exchange
                )

                if df is not None and len(df) > 0:
                    used_exchange = exchange
                    print(f"  ✅ 从 {exchange} 获取成功")

                    latest = df.iloc[-1]
                    print(f"     收盘价: ${latest['close']:,.4f}")
                    print(f"     时间戳: {latest['timestamp']}")

                    # 验证是否符合预期
                    if expected_exchange and used_exchange == expected_exchange:
                        print(f"     ✓ 符合预期（应该从 {expected_exchange} 获取）")
                    elif expected_exchange:
                        print(f"     ⚠️  预期从 {expected_exchange}，实际从 {used_exchange}")

                    success_count += 1
                    break
            except Exception as e:
                print(f"  ⊗ {exchange} 失败: {e}")
                continue

        if df is None or len(df) == 0:
            print(f"  ❌ 所有交易所均失败")
            fail_count += 1

        print()

    print("=" * 80)
    print("测试总结:")
    print(f"  成功: {success_count}/{len(test_symbols)}")
    print(f"  失败: {fail_count}/{len(test_symbols)}")
    print("=" * 80)
    print()

    # 给出结论
    if success_count == len(test_symbols):
        print("✅ 所有测试通过！K线多交易所支持修复成功！")
        print()
        print("预期效果:")
        print("  • BTC/USDT → 从 Binance 获取（优先级高）")
        print("  • HYPE/USDT → 从 Gate.io 获取（Binance 没有，自动回退）")
        print("  • ETH/USDT → 从 Binance 获取（优先级高）")
    else:
        print("⚠️  部分测试失败，请检查:")
        print("  1. config.yaml 中 gate.enabled 是否为 true")
        print("  2. 网络连接是否正常")
        print("  3. API 密钥配置是否正确（如果需要）")

    print()
    print("下一步:")
    print("  1. 重启调度器: python app/scheduler.py")
    print("  2. 等待 1-2 分钟")
    print("  3. 运行: python check_hype_in_db.py")
    print("  4. 验证数据库中有 Gate.io 的 HYPE K线数据")


if __name__ == "__main__":
    asyncio.run(verify_kline_fix())
