#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
添加 signal_version 字段到 futures_positions 表
用于V2/V3并行测试
"""

import os
import pymysql
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

def add_signal_version_field():
    """添加 signal_version 字段"""

    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', '3306')),
        'user': os.getenv('DB_USER', 'root'),
        'password': os.getenv('DB_PASSWORD', ''),
        'database': os.getenv('DB_NAME', 'binance-data')
    }

    try:
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()

        # 1. 检查字段是否存在
        cursor.execute("""
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s
            AND TABLE_NAME = 'futures_positions'
            AND COLUMN_NAME = 'signal_version'
        """, (db_config['database'],))

        exists = cursor.fetchone()[0]

        if exists:
            logger.info("✅ signal_version 字段已存在，跳过添加")
        else:
            # 2. 添加字段
            logger.info("正在添加 signal_version 字段...")
            cursor.execute("""
                ALTER TABLE futures_positions
                ADD COLUMN signal_version VARCHAR(20) DEFAULT 'traditional'
                COMMENT '信号版本: v2/v3/traditional'
                AFTER entry_signal_type
            """)
            conn.commit()
            logger.info("✅ signal_version 字段添加成功")

        # 3. 为已有数据补充默认值
        cursor.execute("""
            UPDATE futures_positions
            SET signal_version = 'traditional'
            WHERE signal_version IS NULL OR signal_version = ''
        """)
        updated = cursor.rowcount
        if updated > 0:
            logger.info(f"✅ 更新了 {updated} 条记录的默认值")
        conn.commit()

        # 4. 为V3模式的记录更新版本标识
        cursor.execute("""
            UPDATE futures_positions
            SET signal_version = 'v3'
            WHERE signal_components LIKE '%v3_%'
            AND signal_version = 'traditional'
        """)
        v3_updated = cursor.rowcount
        if v3_updated > 0:
            logger.info(f"✅ 识别并更新了 {v3_updated} 条V3记录")
        conn.commit()

        # 5. 显示统计信息
        cursor.execute("""
            SELECT signal_version, COUNT(*) as count
            FROM futures_positions
            GROUP BY signal_version
        """)
        results = cursor.fetchall()

        logger.info("=" * 60)
        logger.info("📊 signal_version 分布统计:")
        for version, count in results:
            version_name = version if version else 'NULL'
            logger.info(f"   {version_name}: {count} 条记录")
        logger.info("=" * 60)

        cursor.close()
        conn.close()

        logger.info("🎉 数据库升级完成！")
        return True

    except Exception as e:
        logger.error(f"❌ 数据库升级失败: {e}")
        return False

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("开始添加 signal_version 字段")
    logger.info("=" * 60)

    success = add_signal_version_field()

    if success:
        logger.info("\n✅ 可以启动服务进行V2/V3并行测试了！")
        logger.info("设置环境变量:")
        logger.info("  USE_V2_MODE=true")
        logger.info("  USE_V3_MODE=true")
    else:
        logger.error("\n❌ 升级失败，请检查错误信息")
