#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日复盘调度器 - Daily Review Scheduler

每天中午10点自动运行复盘分析

Author: Claude
Date: 2026-01-26
"""

import asyncio
import schedule
import time
from datetime import datetime
from loguru import logger
import yaml

from app.services.daily_review_analyzer import DailyReviewAnalyzer


class DailyReviewScheduler:
    """每日复盘调度器"""

    def __init__(self, config_path: str = 'config.yaml'):
        """
        初始化调度器

        Args:
            config_path: 配置文件路径
        """
        # 加载配置
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        # 初始化复盘分析器
        db_config = self.config['database']['mysql']
        self.analyzer = DailyReviewAnalyzer(db_config)

        # 获取监控的交易对
        self.symbols = self.config.get('symbols', [])

        logger.info(f"✅ 每日复盘调度器已初始化 | 监控{len(self.symbols)}个交易对")

    async def run_daily_review_task(self):
        """运行每日复盘任务"""
        try:
            logger.info(f"🚀 开始执行每日复盘任务 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            # 执行复盘分析
            report = await self.analyzer.run_daily_review(self.symbols)

            logger.info(
                f"✅ 每日复盘任务完成 | "
                f"捕获率: {report.capture_rate:.1f}% | "
                f"总机会: {report.total_opportunities} | "
                f"已捕获: {report.captured_count} | "
                f"已错过: {report.missed_count}"
            )

            # TODO: 可选 - 发送通知到Telegram
            # await self._send_notification(report)

        except Exception as e:
            logger.error(f"❌ 每日复盘任务失败: {e}")

    def _run_task_sync(self):
        """同步包装器（用于schedule库）"""
        asyncio.run(self.run_daily_review_task())

    def start(self):
        """启动调度器"""
        logger.info("📅 每日复盘调度器已启动")
        logger.info("   执行时间: 每天中午 10:00")

        # 每天中午10点执行
        schedule.every().day.at("10:00").do(self._run_task_sync)

        # 可选：启动时立即执行一次（用于测试）
        # logger.info("🔄 立即执行一次复盘...")
        # self._run_task_sync()

        # 主循环
        while True:
            schedule.run_pending()
            time.sleep(60)  # 每分钟检查一次


def main():
    """主函数"""
    scheduler = DailyReviewScheduler()
    scheduler.start()


if __name__ == '__main__':
    main()
