#!/usr/bin/env python3
"""
手动更新缓存脚本
用于立即触发缓存更新，无需等待 scheduler 的定时任务

使用方法:
python scripts/manual_update_cache.py --all
python scripts/manual_update_cache.py --analysis
python scripts/manual_update_cache.py --recommendations
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import asyncio
import yaml
from datetime import datetime
from loguru import logger

from app.services.cache_update_service import CacheUpdateService


async def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='手动更新缓存')
    parser.add_argument(
        '--all',
        action='store_true',
        help='更新所有缓存（价格、技术指标、新闻、资金费率、Hyperliquid、投资建议）'
    )
    parser.add_argument(
        '--price',
        action='store_true',
        help='仅更新价格统计缓存'
    )
    parser.add_argument(
        '--technical',
        action='store_true',
        help='仅更新技术指标缓存'
    )
    parser.add_argument(
        '--news',
        action='store_true',
        help='仅更新新闻情绪缓存'
    )
    parser.add_argument(
        '--funding',
        action='store_true',
        help='仅更新资金费率缓存'
    )
    parser.add_argument(
        '--hyperliquid',
        action='store_true',
        help='仅更新 Hyperliquid 缓存'
    )
    parser.add_argument(
        '--recommendations',
        action='store_true',
        help='仅更新投资建议缓存（包含 ETF 因素）'
    )
    parser.add_argument(
        '--analysis',
        action='store_true',
        help='更新分析缓存（技术指标+新闻+资金费率+投资建议）'
    )
    parser.add_argument(
        '--symbols',
        type=str,
        help='指定币种，逗号分隔（如: BTC/USDT,ETH/USDT）'
    )

    args = parser.parse_args()

    # 加载配置
    logger.info("加载配置文件...")
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 获取币种列表
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(',')]
    else:
        symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT'])

    logger.info(f"币种列表: {', '.join(symbols)}")

    # 创建缓存更新服务
    logger.info("初始化缓存更新服务...")
    cache_service = CacheUpdateService(config)

    start_time = datetime.now()

    try:
        # 根据参数执行相应的更新
        if args.all:
            logger.info("\n🔄 更新所有缓存...")
            await cache_service.update_all_caches(symbols)

        elif args.analysis:
            logger.info("\n🔄 更新分析缓存...")
            tasks = [
                cache_service.update_technical_indicators_cache(symbols),
                cache_service.update_news_sentiment_aggregation(symbols),
                cache_service.update_funding_rate_stats(symbols),
            ]
            await asyncio.gather(*tasks)
            await cache_service.update_recommendations_cache(symbols)

        else:
            # 单独更新
            if args.price:
                logger.info("\n📊 更新价格统计缓存...")
                await cache_service.update_price_stats_cache(symbols)

            if args.technical:
                logger.info("\n📈 更新技术指标缓存...")
                await cache_service.update_technical_indicators_cache(symbols)

            if args.news:
                logger.info("\n📰 更新新闻情绪缓存...")
                await cache_service.update_news_sentiment_aggregation(symbols)

            if args.funding:
                logger.info("\n💰 更新资金费率缓存...")
                await cache_service.update_funding_rate_stats(symbols)

            if args.hyperliquid:
                logger.info("\n🚀 更新 Hyperliquid 缓存...")
                await cache_service.update_hyperliquid_aggregation(symbols)

            if args.recommendations:
                logger.info("\n🎯 更新投资建议缓存（含 ETF 因素）...")
                await cache_service.update_recommendations_cache(symbols)

        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"\n✅ 缓存更新完成！耗时: {elapsed:.2f}秒")
        logger.info(f"现在可以刷新 Dashboard 查看最新的投资分析")

    except Exception as e:
        logger.error(f"\n❌ 缓存更新失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    # 如果没有任何参数，默认更新投资建议
    if len(sys.argv) == 1:
        logger.info("未指定参数，默认更新投资建议缓存（含 ETF 因素）")
        logger.info("使用 --help 查看所有选项")
        sys.argv.append('--recommendations')

    asyncio.run(main())