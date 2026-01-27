#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信号分析调度器 - Signal Analysis Scheduler

每6小时运行一次，分析24H K线强度 + 信号捕捉情况

Author: Claude
Date: 2026-01-27
"""

import schedule
import time
from datetime import datetime
from loguru import logger
import yaml
import os
from dotenv import load_dotenv

from app.services.signal_analysis_service import SignalAnalysisService


class SignalAnalysisScheduler:
    """信号分析调度器"""

    def __init__(self, config_path: str = 'config.yaml'):
        """
        初始化调度器

        Args:
            config_path: 配置文件路径
        """
        # 加载环境变量
        load_dotenv()

        # 数据库配置
        self.db_config = {
            'host': os.getenv('DB_HOST'),
            'port': int(os.getenv('DB_PORT', 3306)),
            'user': os.getenv('DB_USER'),
            'password': os.getenv('DB_PASSWORD'),
            'database': os.getenv('DB_NAME'),
            'charset': 'utf8mb4',
            'cursorclass': None
        }

        # 加载配置
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        # 获取监控的交易对
        self.symbols = self.config.get('symbols', [])

        # 初始化信号分析服务
        self.service = SignalAnalysisService(self.db_config)

        logger.info(f"✅ 信号分析调度器已初始化 | 监控{len(self.symbols)}个交易对")

    def run_signal_analysis_task(self):
        """运行信号分析任务"""
        try:
            logger.info(f"📊 开始执行信号分析任务 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            # 执行分析
            report = self.service.analyze_all_symbols(self.symbols, hours=24)

            # 打印简要统计
            stats = report['statistics']
            logger.info(
                f"✅ 信号分析任务完成 | "
                f"捕获率: {stats['capture_rate']:.1f}% | "
                f"机会数: {stats['should_trade']} | "
                f"已开仓: {stats['has_position']} | "
                f"错过: {stats['missed']}"
            )

            # 保存到数据库
            self._save_to_database(report)

            # 打印Top错过机会
            missed = report['missed_opportunities']
            if missed:
                logger.info(f"⚠️  错过的高质量机会 (Top 5):")
                for i, opp in enumerate(missed[:5], 1):
                    logger.info(
                        f"   {i}. {opp['symbol']:12s} {opp['side']:5s} | "
                        f"1H净力量{opp['net_power_1h']:+3d} | {opp['reason']}"
                    )

        except Exception as e:
            logger.error(f"❌ 信号分析任务失败: {e}", exc_info=True)

    def _save_to_database(self, report: dict):
        """保存分析结果到数据库"""
        import pymysql
        import json

        # 修复db_config，添加cursorclass
        config = self.db_config.copy()
        config['cursorclass'] = pymysql.cursors.DictCursor

        conn = pymysql.connect(**config)
        cursor = conn.cursor()

        stats = report['statistics']
        analysis_time = report['analysis_time']

        try:
            # 创建表（如果不存在）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS signal_analysis_reports (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    analysis_time DATETIME NOT NULL,
                    total_analyzed INT NOT NULL,
                    has_position INT NOT NULL,
                    should_trade INT NOT NULL,
                    missed_opportunities INT NOT NULL,
                    wrong_direction INT NOT NULL,
                    correct_captures INT NOT NULL,
                    capture_rate DECIMAL(5,2) NOT NULL,
                    report_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_analysis_time (analysis_time)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # 序列化完整报告
            report_json = json.dumps({
                'top_opportunities': report['results'][:30],
                'missed_opportunities': report['missed_opportunities'][:20]
            }, ensure_ascii=False, default=str)

            cursor.execute('''
                INSERT INTO signal_analysis_reports
                (analysis_time, total_analyzed, has_position, should_trade,
                 missed_opportunities, wrong_direction, correct_captures, capture_rate, report_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                analysis_time,
                stats['total_analyzed'],
                stats['has_position'],
                stats['should_trade'],
                stats['missed'],
                stats['wrong_direction'],
                stats['correct_captures'],
                stats['capture_rate'],
                report_json
            ))

            conn.commit()
            logger.debug(f"✅ 分析报告已保存到数据库")

        except Exception as e:
            logger.error(f"保存报告到数据库失败: {e}")
            conn.rollback()
        finally:
            cursor.close()
            conn.close()

    def start(self):
        """启动调度器"""
        logger.info("📅 信号分析调度器已启动")
        logger.info("   执行时间: 每6小时执行一次 (00:00, 06:00, 12:00, 18:00)")

        # 每6小时执行一次
        schedule.every().day.at("00:00").do(self.run_signal_analysis_task)
        schedule.every().day.at("06:00").do(self.run_signal_analysis_task)
        schedule.every().day.at("12:00").do(self.run_signal_analysis_task)
        schedule.every().day.at("18:00").do(self.run_signal_analysis_task)

        # 可选：启动时立即执行一次（用于测试）
        # logger.info("🔄 立即执行一次信号分析...")
        # self.run_signal_analysis_task()

        # 主循环
        while True:
            schedule.run_pending()
            time.sleep(60)  # 每分钟检查一次

    def close(self):
        """关闭服务"""
        if self.service:
            self.service.close()


def main():
    """主函数"""
    scheduler = SignalAnalysisScheduler()
    try:
        scheduler.start()
    finally:
        scheduler.close()


if __name__ == '__main__':
    main()
