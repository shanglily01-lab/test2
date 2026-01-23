#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合约K线历史数据回填脚本
用于首次部署时补充历史K线数据
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.collectors.fast_futures_collector import FastFuturesCollector
from app.utils.config_loader import load_config
from loguru import logger

async def backfill_historical_klines():
    """回填历史K线数据"""

    logger.info("=" * 80)
    logger.info("合约K线历史数据回填")
    logger.info("=" * 80)

    # 加载配置
    config = load_config()
    db_config = config['database']['mysql']

    # 创建采集器
    collector = FastFuturesCollector(db_config)

    # 获取交易对列表
    symbols = collector.get_trading_symbols()
    if not symbols:
        logger.error("未找到交易对列表")
        return

    logger.info(f"准备回填 {len(symbols)} 个交易对的历史数据")
    logger.info(f"回填周期: 1h (100条), 1d (50条)")
    logger.info("")

    # 定义需要回填的时间周期
    intervals = [
        ('1h', 100),  # 1小时K线，回填100条（约4天）
        ('1d', 50)    # 1天K线，回填50条（50天）
    ]

    total_klines = 0

    for interval, limit in intervals:
        logger.info(f"开始回填 {interval} K线 (每个交易对{limit}条)...")

        try:
            # 批量采集
            klines = await collector.collect_batch(symbols, interval, limit)
            logger.info(f"成功获取 {len(klines)} 条 {interval} K线")

            # 保存
            if klines:
                saved = collector.save_klines(klines)
                total_klines += len(klines)
                logger.info(f"保存 {saved} 条记录")

            # 延迟避免API限流
            logger.info(f"等待5秒...")
            await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"回填 {interval} K线失败: {e}")
            import traceback
            traceback.print_exc()

    logger.info("")
    logger.info("=" * 80)
    logger.info(f"回填完成! 总计回填 {total_klines} 条K线数据")
    logger.info("=" * 80)

if __name__ == '__main__':
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | <level>{message}</level>",
        level="INFO"
    )

    asyncio.run(backfill_historical_klines())
