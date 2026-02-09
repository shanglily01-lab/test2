#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
强制TREND模式 - 禁用震荡市策略
"""

import os
import pymysql
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

def force_trend_mode():
    """强制启用TREND模式，禁用自动切换"""

    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', '3306')),
        'user': os.getenv('DB_USER', 'root'),
        'password': os.getenv('DB_PASSWORD', ''),
        'database': os.getenv('DB_NAME', 'binance-data')
    }

    conn = pymysql.connect(**db_config)
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    logger.info("=" * 80)
    logger.info("🔥 强制TREND模式配置")
    logger.info("=" * 80)

    # 检查当前配置
    cursor.execute("""
        SELECT mode_type, auto_switch_enabled
        FROM trading_mode_config
        WHERE account_id = 2 AND trading_type = 'usdt_futures'
    """)

    current = cursor.fetchone()

    if current:
        logger.info(f"\n当前配置:")
        logger.info(f"  模式: {current['mode_type']}")
        logger.info(f"  自动切换: {'启用' if current['auto_switch_enabled'] else '禁用'}")
    else:
        logger.info("\n当前无配置记录")

    # 强制TREND模式
    cursor.execute("""
        INSERT INTO trading_mode_config
        (account_id, trading_type, mode_type, auto_switch_enabled, updated_at)
        VALUES (2, 'usdt_futures', 'trend', 0, NOW())
        ON DUPLICATE KEY UPDATE
            mode_type = 'trend',
            auto_switch_enabled = 0,
            updated_at = NOW()
    """)

    conn.commit()

    logger.info(f"\n✅ 已设置:")
    logger.info(f"  模式: trend (强制)")
    logger.info(f"  自动切换: 禁用")
    logger.info(f"\n说明:")
    logger.info(f"  ✅ 只在Big4趋势明确时交易")
    logger.info(f"  ✅ NEUTRAL时完全停止交易")
    logger.info(f"  ❌ 不再使用震荡市策略")

    logger.info("\n" + "=" * 80)
    logger.info("配置完成，重启服务后生效")
    logger.info("=" * 80)

    cursor.close()
    conn.close()

if __name__ == '__main__':
    force_trend_mode()
