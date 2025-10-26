"""
测试缓存更新调度器
验证缓存更新任务是否正确配置
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import asyncio
import yaml
from loguru import logger
from app.services.cache_update_service import CacheUpdateService


async def test_cache_updates():
    """测试所有缓存更新功能"""

    logger.info("=" * 80)
    logger.info("测试缓存更新功能")
    logger.info("=" * 80 + "\n")

    # 加载配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT'])
    logger.info(f"测试币种: {', '.join(symbols)}\n")

    # 初始化缓存服务
    cache_service = CacheUpdateService(config)

    # 测试1: 价格统计缓存
    logger.info("测试 1/3: 价格统计缓存更新...")
    try:
        await cache_service.update_price_stats_cache(symbols)
        logger.info("  ✅ 价格统计缓存更新成功\n")
    except Exception as e:
        logger.error(f"  ❌ 价格统计缓存更新失败: {e}\n")

    # 测试2: 分析缓存（并发测试）
    logger.info("测试 2/3: 分析缓存更新（技术指标+新闻+资金费率+投资建议）...")
    try:
        results = await asyncio.gather(
            cache_service.update_technical_indicators_cache(symbols),
            cache_service.update_news_sentiment_aggregation(symbols),
            cache_service.update_funding_rate_stats(symbols),
            cache_service.update_recommendations_cache(symbols),
            return_exceptions=True
        )

        errors = [r for r in results if isinstance(r, Exception)]
        if errors:
            logger.warning(f"  ⚠️  部分缓存更新有错误: {len(errors)} 个")
            for e in errors:
                logger.error(f"    - {e}")
        else:
            logger.info("  ✅ 所有分析缓存更新成功\n")
    except Exception as e:
        logger.error(f"  ❌ 分析缓存更新失败: {e}\n")

    # 测试3: Hyperliquid缓存
    logger.info("测试 3/3: Hyperliquid聚合缓存更新...")
    try:
        await cache_service.update_hyperliquid_aggregation(symbols)
        logger.info("  ✅ Hyperliquid聚合缓存更新成功\n")
    except Exception as e:
        logger.error(f"  ❌ Hyperliquid聚合缓存更新失败: {e}\n")

    # 验证缓存表数据
    logger.info("\n" + "=" * 80)
    logger.info("验证缓存表数据")
    logger.info("=" * 80 + "\n")

    from app.database.db_service import DatabaseService
    from sqlalchemy import text

    db_service = DatabaseService(config.get('database', {}))
    session = db_service.get_session()

    cache_tables = [
        'price_stats_24h',
        'technical_indicators_cache',
        'news_sentiment_aggregation',
        'funding_rate_stats',
        'investment_recommendations_cache',
        'hyperliquid_symbol_aggregation'
    ]

    try:
        for table_name in cache_tables:
            try:
                result = session.execute(text(f"SELECT COUNT(*) as count FROM {table_name}"))
                count = result.fetchone()[0]

                if count > 0:
                    logger.info(f"  ✅ {table_name:40s}: {count:4d} 条记录")
                else:
                    logger.warning(f"  ⚠️  {table_name:40s}: {count:4d} 条记录 (空表)")
            except Exception as e:
                logger.error(f"  ❌ {table_name:40s}: 查询失败 ({e})")
    finally:
        session.close()

    logger.info("\n" + "=" * 80)
    logger.info("测试完成")
    logger.info("=" * 80 + "\n")


if __name__ == '__main__':
    asyncio.run(test_cache_updates())
