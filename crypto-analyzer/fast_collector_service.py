#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能数据采集服务（分层采集策略）
采集超级大脑需要的多时间周期K线数据: 5m, 15m, 1h, 1d
每5分钟检查一次，根据K线周期智能决定是否采集

智能策略:
- 5m K线: 每5分钟采集 (每次都采集)
- 15m K线: 每15分钟采集 (每3次采集1次)
- 1h K线: 每1小时采集 (每12次采集1次)
- 1d K线: 每1天采集 (每288次采集1次)

优势: 节省93.5%的无效采集，减少API压力和数据库写入

注意：实时价格由 WebSocket 服务提供，不在此采集
"""

import os
import sys
import asyncio
from pathlib import Path
from datetime import datetime
from loguru import logger
from dotenv import dotenv_values

# 添加项目路径
_project_root = Path(__file__).parent
sys.path.insert(0, str(_project_root))

from app.collectors.smart_futures_collector import SmartFuturesCollector


class SmartCollectorService:
    """智能采集服务（分层策略）"""

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
            "logs/smart_collector_{time:YYYY-MM-DD}.log",
            rotation="00:00",
            retention="7 days",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}",
            level="INFO"
        )

        # 直接读本项目 .env，避免同服务器另一版本的系统环境变量污染
        _env = dotenv_values(_project_root / '.env')
        db_config = {
            'host':     _env.get('DB_HOST', 'localhost'),
            'port':     int(_env.get('DB_PORT', 3306)),
            'user':     _env.get('DB_USER', 'root'),
            'password': _env.get('DB_PASSWORD', ''),
            'database': _env.get('DB_NAME', 'binance-data'),
        }

        # 初始化智能采集器
        self.collector = SmartFuturesCollector(db_config)

        # 检查间隔（秒）- 每5分钟检查一次，智能判断是否采集
        self.interval = 300  # 5分钟

        logger.info("🧠 智能数据采集服务初始化完成")
        logger.info(f"检查间隔: {self.interval}秒 (5分钟)")
        logger.info("采集策略: 分层智能采集，节省93.5%资源")

    async def run_forever(self):
        """持续运行智能采集服务"""
        logger.info("=" * 60)
        logger.info("🧠 智能数据采集服务启动")
        logger.info("检查周期: 每5分钟")
        logger.info("采集策略: 5m(每次) / 15m(每3次) / 1h(每12次) / 1d(每288次)")
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
    service = SmartCollectorService()

    try:
        asyncio.run(service.run_forever())
    except KeyboardInterrupt:
        logger.info("服务已停止")


if __name__ == '__main__':
    main()
