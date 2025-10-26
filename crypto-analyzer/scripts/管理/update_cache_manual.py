"""
手动更新缓存脚本
用于测试缓存更新功能或手动触发缓存更新
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import asyncio
import yaml
from loguru import logger

from app.services.cache_update_service import CacheUpdateService


async def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("手动缓存更新工具")
    logger.info("=" * 60)

    # 加载配置
    config_path = project_root / "config.yaml"
    if not config_path.exists():
        logger.error(f"配置文件不存在: {config_path}")
        return

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    logger.info(f"配置文件加载成功: {config_path}")

    # 创建缓存更新服务
    cache_service = CacheUpdateService(config)

    # 获取监控币种
    symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT'])
    logger.info(f"监控币种: {', '.join(symbols)}")

    # 更新所有缓存
    logger.info("\n开始更新缓存...")
    await cache_service.update_all_caches(symbols)

    logger.info("\n" + "=" * 60)
    logger.info("缓存更新完成！")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
