#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
运行一次K线采集（使用修复后的采集器）
只采集最近3天的数据：5m(2根), 15m(2根), 1h(72根=3天), 1d(3根)
"""
import sys
import asyncio
from pathlib import Path
from datetime import datetime
from loguru import logger

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from app.collectors.smart_futures_collector import SmartFuturesCollector
from app.utils.config_loader import load_config


async def main():
    """运行一次完整采集"""
    # 配置日志
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | <level>{message}</level>",
        level="INFO"
    )

    # 加载配置
    config = load_config()
    db_config = config['database']['mysql']

    # 初始化采集器
    collector = SmartFuturesCollector(db_config)

    logger.info("=" * 80)
    logger.info("🚀 开始运行一次性完整K线采集（使用修复后的采集器）")
    logger.info("=" * 80)
    logger.info("")

    # 强制清空上次采集时间，确保所有周期都会被采集
    collector.last_collection_time = {}

    # 修改采集参数为只采集3天的数据
    # 5m: 864根(3天), 15m: 288根(3天), 1h: 72根(3天), 1d: 3根(3天)
    collector.intervals = [
        ('5m', 864),   # 3天 * 24小时 * 60分钟 / 5 = 864根
        ('15m', 288),  # 3天 * 24小时 * 60分钟 / 15 = 288根
        ('1h', 72),    # 3天 * 24小时 = 72根
        ('1d', 3)      # 3天 = 3根
    ]

    # 运行一次采集周期
    await collector.run_collection_cycle()

    logger.info("")
    logger.info("=" * 80)
    logger.info("✅ 一次性K线采集完成")
    logger.info("=" * 80)
    logger.info("")
    logger.info("修复说明：")
    logger.info("  ✅ 只保存已完成的K线（排除未完成的最后一根）")
    logger.info("  ✅ 基于K线整点时间判断采集时机")
    logger.info("  ✅ 确保数据准确性")
    logger.info("")


if __name__ == '__main__':
    asyncio.run(main())
