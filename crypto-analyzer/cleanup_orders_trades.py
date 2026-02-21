#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理 futures_orders 和 futures_trades 中的残留记录"""

import pymysql
from loguru import logger

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
cursor = conn.cursor()

try:
    # 删除 futures_orders 中14:00后的记录
    cursor.execute("""
        DELETE FROM futures_orders
        WHERE DATE(created_at) = '2026-02-21'
          AND TIME(created_at) >= '06:00:00'
    """)
    orders_deleted = cursor.rowcount
    logger.info(f"✅ 删除 {orders_deleted} 条 futures_orders 记录")

    # 删除 futures_trades 中14:00后的记录
    cursor.execute("""
        DELETE FROM futures_trades
        WHERE DATE(trade_time) = '2026-02-21'
          AND TIME(trade_time) >= '06:00:00'
    """)
    trades_deleted = cursor.rowcount
    logger.info(f"✅ 删除 {trades_deleted} 条 futures_trades 记录")

    conn.commit()

    logger.info(f"\n✅ 清理完成:")
    logger.info(f"   futures_orders: {orders_deleted} 条")
    logger.info(f"   futures_trades: {trades_deleted} 条")

except Exception as e:
    conn.rollback()
    logger.error(f"❌ 清理失败: {e}")
    raise

finally:
    cursor.close()
    conn.close()
