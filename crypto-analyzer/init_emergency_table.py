#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
初始化紧急干预表
"""
import os
import pymysql
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

def init_emergency_table():
    """创建emergency_intervention表"""

    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', '3306')),
        'user': os.getenv('DB_USER', 'root'),
        'password': os.getenv('DB_PASSWORD', ''),
        'database': os.getenv('DB_NAME', 'binance-data')
    }

    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()

    logger.info("=" * 80)
    logger.info("🔧 初始化紧急干预表")
    logger.info("=" * 80)

    # 创建表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS emergency_intervention (
            id INT AUTO_INCREMENT PRIMARY KEY,
            account_id INT NOT NULL COMMENT '账户ID',
            trading_type VARCHAR(50) NOT NULL COMMENT '交易类型 (usdt_futures/coin_futures)',
            intervention_type VARCHAR(50) NOT NULL COMMENT '干预类型 (BOTTOM_BOUNCE/TOP_REVERSAL)',
            block_long BOOLEAN DEFAULT FALSE COMMENT '是否阻止做多',
            block_short BOOLEAN DEFAULT FALSE COMMENT '是否阻止做空',
            trigger_reason TEXT COMMENT '触发原因',
            expires_at DATETIME NOT NULL COMMENT '失效时间',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            INDEX idx_account_type (account_id, trading_type),
            INDEX idx_expires (expires_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='紧急干预记录表'
    """)

    conn.commit()

    logger.info("✅ emergency_intervention表创建成功")

    # 检查表结构
    cursor.execute("DESCRIBE emergency_intervention")
    columns = cursor.fetchall()

    logger.info("\n表结构:")
    for col in columns:
        logger.info(f"  {col[0]:20} {col[1]:20} {col[2]:10} {col[3]:10}")

    logger.info("\n" + "=" * 80)
    logger.info("初始化完成")
    logger.info("=" * 80)

    cursor.close()
    conn.close()

if __name__ == '__main__':
    init_emergency_table()
