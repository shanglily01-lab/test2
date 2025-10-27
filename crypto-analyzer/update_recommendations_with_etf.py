#!/usr/bin/env python3
"""
手动更新投资建议缓存（包含ETF因素）
"""

import asyncio
import yaml
from app.services.cache_update_service import CacheUpdateService

async def main():
    # 加载配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    print('=' * 80)
    print('手动更新投资建议缓存（包含ETF因素）')
    print('=' * 80)
    print()

    # 创建缓存更新服务
    cache_service = CacheUpdateService(config)

    # 只更新BTC和ETH（有ETF数据的币种）
    symbols = ['BTC/USDT', 'ETH/USDT']

    print(f"准备更新 {len(symbols)} 个币种的投资建议...")
    print()

    # 先检查ETF数据
    print("步骤 1: 检查ETF数据是否存在")
    print("-" * 80)
    for symbol in symbols:
        etf_data = cache_service._get_cached_etf_data(symbol)
        if etf_data:
            details = etf_data.get('details', {})
            print(f"✅ {symbol}: ETF评分={etf_data.get('score', 0):.1f}, "
                  f"信号={etf_data.get('signal')}, "
                  f"最新流入=${details.get('total_net_inflow', 0):,.0f}")
        else:
            print(f"❌ {symbol}: 没有ETF数据")
    print()

    # 更新投资建议缓存
    print("步骤 2: 更新投资建议缓存")
    print("-" * 80)
    await cache_service.update_recommendations_cache(symbols)
    print()

    # 验证更新结果
    print("步骤 3: 验证更新结果")
    print("-" * 80)

    from app.database.db_service import DatabaseService
    from sqlalchemy import text

    db_service = DatabaseService(config.get('database', {}))
    session = db_service.get_session()

    try:
        for symbol in symbols:
            sql = text("SELECT * FROM investment_recommendations_cache WHERE symbol = :symbol")
            result = session.execute(sql, {"symbol": symbol}).fetchone()

            if result:
                result_dict = dict(result._mapping) if hasattr(result, '_mapping') else dict(result)
                print(f"\n{symbol}:")
                print(f"  信号: {result_dict.get('signal')}")
                print(f"  置信度: {result_dict.get('confidence', 0):.1f}%")
                print(f"  综合评分: {result_dict.get('score', 0):.1f}")

                # 显示建议理由（检查是否包含ETF）
                reasons = result_dict.get('reasons', '')
                if reasons:
                    print(f"  建议理由:")
                    for line in reasons.split('\n')[:10]:  # 只显示前10行
                        if line.strip():
                            print(f"    {line}")

                    # 检查是否包含ETF信息
                    if 'ETF' in reasons or '🏦' in reasons:
                        print(f"  ✅ 包含ETF信息")
                    else:
                        print(f"  ⚠️  未包含ETF信息")
            else:
                print(f"❌ {symbol}: 缓存中没有投资建议")

    finally:
        session.close()

    print()
    print('=' * 80)
    print('更新完成！请刷新Dashboard查看效果')
    print('=' * 80)

if __name__ == '__main__':
    asyncio.run(main())
