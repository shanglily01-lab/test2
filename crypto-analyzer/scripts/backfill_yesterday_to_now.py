"""
回填从昨天到现在所有数据
包括K线数据、价格数据等
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import asyncio
import yaml
from datetime import datetime, timedelta
from loguru import logger

# 配置日志
logger.remove()
logger.add(
    "logs/backfill_yesterday_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
)
logger.add(lambda msg: print(msg), level="INFO")


async def backfill_yesterday_to_now():
    """回填从昨天到现在所有数据"""
    
    logger.info("=" * 80)
    logger.info("🔄 开始回填从昨天到现在的数据")
    logger.info("=" * 80)
    
    # 加载配置
    config_path = project_root / "config.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    # 获取监控币种
    symbols = config.get('symbols', [])
    logger.info(f"📊 监控币种数量: {len(symbols)}")
    
    # 计算时间范围：昨天00:00到现在
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    start_time = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    end_time = now
    
    logger.info(f"⏰ 时间范围: {start_time.strftime('%Y-%m-%d %H:%M:%S')} ~ {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"📈 时间周期: 1m, 5m, 15m, 1h")
    logger.info(f"🏦 交易所: Binance, Gate.io")
    logger.info("=" * 80 + "\n")
    
    # 导入回填脚本
    from scripts.backfill_kline_data import KlineBackfiller
    
    # 创建回补器
    backfiller = KlineBackfiller()
    
    # 回补K线数据（所有时间周期）
    timeframes = ['1m', '5m', '15m', '1h']
    logger.info(f"📊 开始回补K线数据...")
    await backfiller.backfill_klines(start_time, end_time, timeframes)
    
    # 回补价格数据
    logger.info(f"\n📊 开始回补价格数据...")
    await backfiller.backfill_prices(start_time, end_time)
    
    logger.info("\n" + "=" * 80)
    logger.info("✅ 数据回补完成！")
    logger.info("=" * 80)
    
    # 更新缓存
    logger.info("\n🔄 更新缓存表...")
    try:
        from app.services.cache_update_service import CacheUpdateService
        cache_service = CacheUpdateService(config)
        await cache_service.update_all_caches(symbols)
        logger.info("✅ 缓存更新完成")
    except Exception as e:
        logger.error(f"⚠️  缓存更新失败: {e}")
        import traceback
        traceback.print_exc()
    
    logger.info("\n🎉 所有数据回补和缓存更新完成！")


if __name__ == '__main__':
    try:
        asyncio.run(backfill_yesterday_to_now())
    except KeyboardInterrupt:
        logger.warning("\n用户中断操作")
    except Exception as e:
        logger.error(f"\n回填过程出错: {e}")
        import traceback
        traceback.print_exc()

