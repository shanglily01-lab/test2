"""
手动触发缓存更新
"""
import asyncio
import yaml
from loguru import logger

logger.remove()
logger.add(lambda msg: print(msg), level="INFO")

async def main():
    # 加载配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT'])

    print("\n" + "="*80)
    print("手动触发缓存更新")
    print("="*80 + "\n")

    # 导入缓存更新服务
    from app.services.cache_update_service import CacheUpdateService

    cache_service = CacheUpdateService(config)

    logger.info(f"开始更新 {len(symbols)} 个币种的缓存...")

    # 执行更新
    await cache_service.update_all_caches(symbols)

    logger.info("\n✅ 缓存更新完成！")
    logger.info("现在可以查询数据库查看结果")

if __name__ == '__main__':
    asyncio.run(main())
