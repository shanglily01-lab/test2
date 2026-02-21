#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复旧持仓的 avg_entry_price 字段
将所有 NULL 的 avg_entry_price 设置为 entry_price
"""

import pymysql
from loguru import logger

# 数据库配置
db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def fix_old_positions():
    """修复旧持仓的 avg_entry_price 字段"""
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()

    try:
        # 查看需要修复的持仓数量
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM futures_positions
            WHERE avg_entry_price IS NULL
              AND status = 'open'
              AND account_id = 3
        """)
        result = cursor.fetchone()
        count_to_fix = result[0]

        if count_to_fix == 0:
            logger.info("✅ 没有需要修复的持仓")
            return

        logger.info(f"🔧 发现 {count_to_fix} 个需要修复的持仓")

        # 更新 avg_entry_price
        cursor.execute("""
            UPDATE futures_positions
            SET avg_entry_price = entry_price
            WHERE avg_entry_price IS NULL
              AND status = 'open'
              AND account_id = 3
        """)

        affected_rows = cursor.rowcount
        conn.commit()

        logger.info(f"✅ 已修复 {affected_rows} 个持仓的 avg_entry_price 字段")

        # 验证结果
        cursor.execute("""
            SELECT
                id,
                symbol,
                position_side,
                entry_price,
                avg_entry_price,
                status
            FROM futures_positions
            WHERE account_id = 3
              AND status = 'open'
            ORDER BY id DESC
            LIMIT 20
        """)

        positions = cursor.fetchall()
        logger.info(f"\n最新的持仓记录（前20个）：")
        for pos in positions:
            pos_id, symbol, side, entry_price, avg_entry_price, status = pos
            logger.info(
                f"  ID:{pos_id} | {symbol} {side} | "
                f"entry_price={entry_price} | avg_entry_price={avg_entry_price} | {status}"
            )

    except Exception as e:
        conn.rollback()
        logger.error(f"❌ 修复失败: {e}")
        raise

    finally:
        cursor.close()
        conn.close()


if __name__ == '__main__':
    logger.info("🚀 开始修复旧持仓的 avg_entry_price 字段...")
    fix_old_positions()
    logger.info("✅ 修复完成")
