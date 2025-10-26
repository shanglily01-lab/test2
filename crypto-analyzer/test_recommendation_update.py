#!/usr/bin/env python3
"""
测试投资建议更新功能 - 显示详细的错误信息
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
import yaml
from loguru import logger

from app.services.cache_update_service import CacheUpdateService

async def test_recommendation_update():
    """测试更新单个币种的投资建议"""

    # 加载配置
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    print("=" * 80)
    print("测试投资建议更新功能")
    print("=" * 80)
    print()

    # 创建缓存更新服务
    cache_service = CacheUpdateService(config)

    # 测试单个币种
    test_symbol = 'BTC/USDT'
    print(f"测试币种: {test_symbol}")
    print()

    try:
        print("=" * 80)
        print(f"步骤1: 获取缓存数据")
        print("=" * 80)

        # 获取各维度缓存数据
        technical_data = cache_service._get_cached_technical_data(test_symbol)
        print(f"✅ 技术指标数据: {technical_data is not None}")
        if technical_data:
            print(f"   包含指标: {list(technical_data.keys())}")

        news_data = cache_service._get_cached_news_data(test_symbol)
        print(f"✅ 新闻情绪数据: {news_data is not None}")
        if news_data:
            print(f"   新闻评分: {news_data.get('news_score')}")

        funding_data = cache_service._get_cached_funding_data(test_symbol)
        print(f"✅ 资金费率数据: {funding_data is not None}")
        if funding_data:
            print(f"   资金费率: {funding_data.get('current_rate_pct')}%")

        hyperliquid_data = cache_service._get_cached_hyperliquid_data(test_symbol)
        print(f"✅ Hyperliquid数据: {hyperliquid_data is not None}")

        price_stats = cache_service._get_cached_price_stats(test_symbol)
        print(f"✅ 价格统计数据: {price_stats is not None}")
        if price_stats:
            print(f"   当前价格: ${price_stats.get('current_price')}")

        print()

        # 获取当前价格
        current_price = price_stats.get('current_price', 0) if price_stats else 0

        if current_price == 0:
            print(f"❌ 错误: 无法获取{test_symbol}的当前价格")
            print(f"   price_stats 内容: {price_stats}")
            return

        print("=" * 80)
        print(f"步骤2: 生成投资分析")
        print("=" * 80)

        # 使用投资分析器生成综合分析
        analysis = cache_service.investment_analyzer.analyze(
            symbol=test_symbol,
            technical_data=technical_data,
            news_data=news_data,
            funding_data=funding_data,
            hyperliquid_data=hyperliquid_data,
            ethereum_data=None,
            current_price=current_price
        )

        print(f"✅ 分析完成")
        print(f"   信号: {analysis.get('signal')}")
        print(f"   信心度: {analysis.get('confidence')}")
        print(f"   总分: {analysis['score'].get('total')}")
        print(f"   技术分: {analysis['score'].get('technical')}")
        print(f"   新闻分: {analysis['score'].get('news')}")
        print(f"   资金费率分: {analysis['score'].get('funding')}")
        print()

        print("=" * 80)
        print(f"步骤3: 写入数据库")
        print("=" * 80)

        # 写入投资建议缓存
        cache_service._upsert_recommendation(test_symbol, analysis)

        print(f"✅ 投资建议已写入数据库")
        print()

        print("=" * 80)
        print(f"步骤4: 验证数据")
        print("=" * 80)

        # 验证数据是否成功写入
        from app.database.db_service import DatabaseService
        from sqlalchemy import text

        db_service = DatabaseService(config.get('database', {}))
        session = db_service.get_session()

        try:
            result = session.execute(text("""
                SELECT symbol, `signal`, confidence, total_score
                FROM investment_recommendations_cache
                WHERE symbol = :symbol
            """), {"symbol": test_symbol})

            row = result.fetchone()
            if row:
                print(f"✅ 数据验证成功")
                print(f"   币种: {row[0]}")
                print(f"   信号: {row[1]}")
                print(f"   信心度: {row[2]}")
                print(f"   总分: {row[3]}")
            else:
                print(f"❌ 数据验证失败: 数据库中没有找到记录")
        finally:
            session.close()

        print()
        print("=" * 80)
        print("✅ 测试完成！")
        print("=" * 80)

    except Exception as e:
        print()
        print("=" * 80)
        print(f"❌ 测试失败: {e}")
        print("=" * 80)
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_recommendation_update())