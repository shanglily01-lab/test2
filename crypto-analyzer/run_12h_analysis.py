#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
12小时复盘分析 - 定时任务调度器
每12小时(00:00和12:00)自动运行分析
"""

import schedule
import time
import subprocess
import logging
from datetime import datetime
from pathlib import Path

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).parent


def run_analysis():
    """执行12小时复盘分析"""
    try:
        logger.info("=" * 80)
        logger.info("开始执行12小时复盘分析...")
        logger.info("=" * 80)

        # 运行分析脚本
        result = subprocess.run(
            ['python', str(PROJECT_ROOT / 'scripts' / '12h_retrospective_analysis.py')],
            capture_output=True,
            text=True,
            timeout=300,  # 5分钟超时
            encoding='utf-8',
            errors='ignore'
        )

        if result.returncode == 0:
            logger.info("✅ 12小时复盘分析完成")
            # 输出分析结果
            print(result.stdout)

            # 保存分析结果到文件
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            report_dir = PROJECT_ROOT / 'logs' / 'retrospective'
            report_dir.mkdir(parents=True, exist_ok=True)

            report_file = report_dir / f'analysis_{timestamp}.txt'
            with open(report_file, 'w', encoding='utf-8') as f:
                f.write(result.stdout)

            logger.info(f"分析报告已保存: {report_file}")

        else:
            logger.error(f"❌ 分析失败: {result.stderr}")

    except subprocess.TimeoutExpired:
        logger.error("❌ 分析超时(超过5分钟)")
    except Exception as e:
        logger.error(f"❌ 执行分析时出错: {e}")


def main():
    """主函数 - 配置定时任务"""

    logger.info("🚀 12小时复盘分析调度器启动")
    logger.info(f"项目目录: {PROJECT_ROOT}")
    logger.info("分析时间: 每天 00:00 和 12:00")

    # 配置定时任务
    schedule.every().day.at("00:00").do(run_analysis)
    schedule.every().day.at("12:00").do(run_analysis)

    # 可选: 启动时立即执行一次
    logger.info("立即执行一次分析...")
    run_analysis()

    # 主循环
    logger.info("等待下次调度...")
    while True:
        schedule.run_pending()
        time.sleep(60)  # 每分钟检查一次


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\n⏹️  调度器已停止")
