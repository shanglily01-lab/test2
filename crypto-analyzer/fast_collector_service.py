#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
快速数据采集服务
专门采集超级大脑需要的5m K线数据
每5分钟运行一次，独立于其他采集器

注意：实时价格由 WebSocket 服务提供，不在此采集
"""

import sys
import asyncio
from pathlib import Path
from datetime import datetime
from loguru import logger

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from app.collectors.fast_futures_collector import FastFuturesCollector
from app.utils.config_loader import load_config


class FastCollectorService:
    """快速采集服务"""

    def __init__(self):
        """初始化服务"""
        # 配置日志
        logger.remove()
        logger.add(
            sys.stdout,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | <level>{message}</level>",
            level="INFO"
        )
        logger.add(
            "logs/fast_collector_{time:YYYY-MM-DD}.log",
            rotation="00:00",
            retention="7 days",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}",
            level="INFO"
        )

        # 加载配置
        config = load_config()
        db_config = config['database']['mysql']

        # 初始化采集器
        self.collector = FastFuturesCollector(db_config)

        # 采集间隔（秒）
        self.interval = 300  # 5分钟

        logger.info("快速数据采集服务初始化完成")
        logger.info(f"采集间隔: {self.interval}秒 (5分钟)")

    async def run_forever(self):
        """持续运行采集服务"""
        logger.info("=" * 60)
        logger.info("快速数据采集服务启动")
        logger.info("专门采集: 5m K线数据")
        logger.info("实时价格: 由 WebSocket 服务提供")
        logger.info("=" * 60)

        cycle_count = 0

        while True:
            try:
                cycle_count += 1
                logger.info(f"\n【第 {cycle_count} 次采集】")

                # 执行采集
                await self.collector.run_collection_cycle()

                # 等待下一次采集
                logger.info(f"等待 {self.interval} 秒...\n")
                await asyncio.sleep(self.interval)

            except KeyboardInterrupt:
                logger.info("收到停止信号，服务退出")
                break
            except Exception as e:
                logger.error(f"采集周期异常: {e}")
                logger.exception(e)
                # 出错后等待30秒再重试
                logger.info("30秒后重试...")
                await asyncio.sleep(30)


def main():
    """主函数"""
    service = FastCollectorService()

    try:
        asyncio.run(service.run_forever())
    except KeyboardInterrupt:
        logger.info("服务已停止")


if __name__ == '__main__':
    main()
