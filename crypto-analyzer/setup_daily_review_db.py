#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日复盘系统 - 数据库表初始化脚本
Daily Review System - Database Table Setup Script

执行此脚本来创建所需的数据库表
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pymysql
from loguru import logger
from app.utils.config_loader import load_config


def setup_daily_review_tables():
    """创建每日复盘系统所需的数据库表"""

    # 加载配置
    config = load_config()
    db_config = config['database']['mysql']

    logger.info("开始创建每日复盘系统数据库表...")

    try:
        # 连接数据库
        conn = pymysql.connect(**db_config, autocommit=True)
        cursor = conn.cursor()

        # 1. 创建复盘报告主表
        logger.info("创建表: daily_review_reports")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_review_reports (
                id INT AUTO_INCREMENT PRIMARY KEY,
                date DATE NOT NULL,
                report_json MEDIUMTEXT NOT NULL,
                total_opportunities INT,
                captured_count INT,
                missed_count INT,
                capture_rate FLOAT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY unique_date (date),
                INDEX idx_date (date),
                INDEX idx_capture_rate (capture_rate)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            COMMENT='每日复盘报告主表'
        """)
        logger.success("✓ 表 daily_review_reports 创建成功")

        # 2. 创建机会详情表
        logger.info("创建表: daily_review_opportunities")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_review_opportunities (
                id INT AUTO_INCREMENT PRIMARY KEY,
                review_date DATE NOT NULL,
                symbol VARCHAR(20) NOT NULL,
                timeframe VARCHAR(10) NOT NULL,
                move_type VARCHAR(10) NOT NULL,
                start_time DATETIME NOT NULL,
                end_time DATETIME NOT NULL,
                price_change_pct FLOAT NOT NULL,
                volume_ratio FLOAT NOT NULL,
                captured BOOLEAN NOT NULL,
                capture_delay_minutes INT,
                signal_type VARCHAR(50),
                position_pnl_pct FLOAT,
                miss_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_review_date (review_date),
                INDEX idx_symbol (symbol),
                INDEX idx_captured (captured),
                INDEX idx_timeframe (timeframe)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            COMMENT='大行情机会详情表'
        """)
        logger.success("✓ 表 daily_review_opportunities 创建成功")

        # 3. 创建信号分析表
        logger.info("创建表: daily_review_signal_analysis")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_review_signal_analysis (
                id INT AUTO_INCREMENT PRIMARY KEY,
                review_date DATE NOT NULL,
                signal_type VARCHAR(50) NOT NULL,
                total_trades INT NOT NULL,
                win_trades INT NOT NULL,
                loss_trades INT NOT NULL,
                win_rate FLOAT NOT NULL,
                avg_pnl FLOAT NOT NULL,
                best_trade FLOAT,
                worst_trade FLOAT,
                long_trades INT NOT NULL,
                short_trades INT NOT NULL,
                avg_holding_minutes FLOAT,
                captured_opportunities INT NOT NULL,
                rating VARCHAR(20),
                score INT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_review_signal (review_date, signal_type),
                INDEX idx_review_date (review_date),
                INDEX idx_score (score)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            COMMENT='信号分析表'
        """)
        logger.success("✓ 表 daily_review_signal_analysis 创建成功")

        # 4. 创建参数调整历史表
        logger.info("创建表: parameter_adjustments")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS parameter_adjustments (
                id INT AUTO_INCREMENT PRIMARY KEY,
                adjustment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                param_group VARCHAR(100),
                param_name VARCHAR(100),
                old_value VARCHAR(100),
                new_value VARCHAR(100),
                reason TEXT,
                applied BOOLEAN DEFAULT TRUE,
                INDEX idx_adjustment_date (adjustment_date),
                INDEX idx_param_group (param_group)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            COMMENT='参数调整历史表'
        """)
        logger.success("✓ 表 parameter_adjustments 创建成功")

        # 验证表是否创建成功
        logger.info("\n检查表结构...")
        cursor.execute("""
            SELECT
                TABLE_NAME as table_name,
                TABLE_COMMENT as table_comment,
                TABLE_ROWS as row_count,
                ROUND(DATA_LENGTH / 1024 / 1024, 2) as data_size_mb,
                ROUND(INDEX_LENGTH / 1024 / 1024, 2) as index_size_mb,
                CREATE_TIME as create_time
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
            AND TABLE_NAME IN (
                'daily_review_reports',
                'daily_review_opportunities',
                'daily_review_signal_analysis',
                'parameter_adjustments'
            )
            ORDER BY TABLE_NAME
        """)

        results = cursor.fetchall()

        logger.info("\n表信息:")
        logger.info("=" * 100)
        for row in results:
            logger.info(
                f"表名: {row[0]:35} | "
                f"说明: {row[1]:20} | "
                f"行数: {row[2]:6} | "
                f"数据: {row[3]:6}MB | "
                f"索引: {row[4]:6}MB"
            )
        logger.info("=" * 100)

        cursor.close()
        conn.close()

        logger.success("\n✅ 所有表创建成功！")
        logger.info("\n下一步:")
        logger.info("1. 运行每日复盘: python run_daily_review_and_optimize.py")
        logger.info("2. 启动定时任务: python app/schedulers/daily_review_scheduler.py")
        logger.info("3. 访问前端页面查看结果: http://localhost:9020/futures_review")

    except Exception as e:
        logger.error(f"❌ 创建表失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == '__main__':
    setup_daily_review_tables()
