#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日优化调度服务
定时执行超级大脑的自我优化任务
"""

import sys
import asyncio
import schedule
import time
from pathlib import Path
from datetime import datetime
from loguru import logger

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from app.services.auto_parameter_optimizer import AutoParameterOptimizer
from app.utils.config_loader import load_config


class DailyOptimizerService:
    """每日优化调度服务"""

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
            "logs/daily_optimizer_{time:YYYY-MM-DD}.log",
            rotation="00:00",
            retention="30 days",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}",
            level="INFO"
        )

        # 加载配置
        config = load_config()
        mysql_config = config['database']['mysql']
        self.db_config = {
            'host': mysql_config['host'],
            'port': mysql_config['port'],
            'user': mysql_config['user'],
            'password': mysql_config['password'],
            'database': mysql_config['database']
        }

        logger.info("=" * 100)
        logger.info("每日优化调度服务初始化完成")
        logger.info("=" * 100)

    def run_daily_optimization(self):
        """执行每日优化任务"""
        try:
            logger.info("")
            logger.info("=" * 100)
            logger.info(f"开始执行每日自我优化 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info("=" * 100)

            # 创建优化器
            optimizer = AutoParameterOptimizer(self.db_config)

            # 执行优化
            logger.info("📊 分析最近7天的交易数据...")
            result = optimizer.optimize_and_update(days=7)

            if result['success']:
                logger.info("")
                logger.info("=" * 100)
                logger.info("✅ 每日优化完成！")
                logger.info("=" * 100)
                logger.info(f"优化内容: {result['message']}")
                logger.info(f"胜率: {result['stats']['win_rate']:.1f}%")
                logger.info(f"平均盈亏比: {result['stats']['avg_profit_loss_ratio']:.2f}")
                logger.info(f"总盈亏: {result['stats']['total_pnl']:.2f} USDT")
                logger.info(f"建议调整: {len(result['recommendations'])}项")

                if result['recommendations']:
                    logger.info("")
                    logger.info("📋 优化建议:")
                    for i, rec in enumerate(result['recommendations'], 1):
                        logger.info(f"  {i}. {rec}")

                logger.info("=" * 100)
            else:
                logger.warning(f"⚠️ 优化失败: {result.get('error', '未知错误')}")

            # 关闭优化器
            optimizer.close()

        except Exception as e:
            logger.error(f"❌ 每日优化任务执行失败: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def schedule_tasks(self):
        """配置定时任务"""
        # 每天凌晨1点执行优化
        schedule.every().day.at("01:00").do(self.run_daily_optimization)

        # 启动后立即执行一次（可选，用于测试）
        # self.run_daily_optimization()

        logger.info("")
        logger.info("📅 定时任务配置:")
        logger.info("  - 每日优化: 每天 01:00 执行")
        logger.info("")
        logger.info("⏰ 调度服务已启动，等待执行...")

    def run(self):
        """运行服务"""
        try:
            # 配置定时任务
            self.schedule_tasks()

            # 运行调度循环
            while True:
                schedule.run_pending()
                time.sleep(60)  # 每分钟检查一次

        except KeyboardInterrupt:
            logger.info("")
            logger.info("=" * 100)
            logger.info("收到停止信号，服务正在关闭...")
            logger.info("=" * 100)
        except Exception as e:
            logger.error(f"服务运行异常: {e}")
            import traceback
            logger.error(traceback.format_exc())


def main():
    """主函数"""
    service = DailyOptimizerService()
    service.run()


if __name__ == '__main__':
    main()
