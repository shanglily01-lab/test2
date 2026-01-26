#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增强版现货交易系统 - 数据库表初始化脚本
Enhanced Spot Trading System - Database Setup Script

确保spot_positions表存在并可用
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pymysql
from loguru import logger
from app.utils.config_loader import load_config


def setup_spot_enhanced_tables():
    """创建/验证增强版现货交易所需的数据库表"""

    # 加载配置
    config = load_config()
    db_config = config['database']['mysql']

    logger.info("开始初始化增强版现货交易数据库表...")

    try:
        # 连接数据库
        conn = pymysql.connect(**db_config, autocommit=True)
        cursor = conn.cursor()

        # 创建现货持仓表
        logger.info("创建/验证表: spot_positions")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS spot_positions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL COMMENT '交易对 (如 BTC/USDT)',

                -- 持仓信息
                entry_price DECIMAL(20, 8) NOT NULL COMMENT '首次买入价格',
                avg_entry_price DECIMAL(20, 8) NOT NULL COMMENT '平均成本价',
                quantity DECIMAL(20, 8) NOT NULL COMMENT '持仓数量',
                total_cost DECIMAL(20, 4) NOT NULL COMMENT '总成本 (USDT)',

                -- 批次信息
                current_batch INT NOT NULL DEFAULT 1 COMMENT '当前批次 (1-5)',

                -- 目标价格
                take_profit_price DECIMAL(20, 8) COMMENT '止盈价格',
                stop_loss_price DECIMAL(20, 8) COMMENT '止损价格',

                -- 平仓信息
                exit_price DECIMAL(20, 8) COMMENT '平仓价格',
                pnl DECIMAL(20, 4) COMMENT '盈亏金额 (USDT)',
                pnl_pct DECIMAL(10, 6) COMMENT '盈亏百分比',
                close_reason VARCHAR(50) COMMENT '平仓原因 (止盈/止损/手动)',

                -- 信号信息
                signal_strength DECIMAL(5, 2) COMMENT '开仓信号强度 (0-100)',
                signal_details TEXT COMMENT '信号详情',

                -- 状态
                status VARCHAR(20) NOT NULL DEFAULT 'active' COMMENT '状态 (active/closed)',

                -- 时间戳
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
                closed_at TIMESTAMP NULL COMMENT '平仓时间',

                -- 索引
                INDEX idx_symbol (symbol),
                INDEX idx_status (status),
                INDEX idx_created (created_at),
                INDEX idx_pnl (pnl_pct)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            COMMENT='现货持仓表 - 增强版底部反转策略'
        """)
        logger.success("✓ 表 spot_positions 创建/验证成功")

        # 验证表结构
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
            AND TABLE_NAME = 'spot_positions'
        """)

        result = cursor.fetchone()

        logger.info("\n表信息:")
        logger.info("=" * 100)
        if result:
            logger.info(
                f"表名: {result[0]:25} | "
                f"说明: {result[1]:30} | "
                f"行数: {result[2]:6} | "
                f"数据: {result[3]:6}MB | "
                f"索引: {result[4]:6}MB"
            )
        logger.info("=" * 100)

        # 检查字段
        cursor.execute("""
            SELECT
                COLUMN_NAME,
                COLUMN_TYPE,
                IS_NULLABLE,
                COLUMN_DEFAULT,
                COLUMN_COMMENT
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
            AND TABLE_NAME = 'spot_positions'
            ORDER BY ORDINAL_POSITION
        """)

        columns = cursor.fetchall()
        logger.info(f"\n✅ 表字段验证: {len(columns)} 个字段")
        for col in columns[:10]:  # 显示前10个字段
            logger.debug(f"  {col[0]:20} {col[1]:20} {col[4]}")

        cursor.close()
        conn.close()

        logger.success("\n✅ 增强版现货交易数据库表初始化完成！")
        logger.info("\n下一步:")
        logger.info("1. 确认config.yaml中配置了监控交易对")
        logger.info("2. 运行增强版服务: python -m app.services.spot_trader_service_enhanced")
        logger.info("3. 该服务专注捕捉底部反转机会进行现货抄底")

    except Exception as e:
        logger.error(f"❌ 初始化失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == '__main__':
    setup_spot_enhanced_tables()
