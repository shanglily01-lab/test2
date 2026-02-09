#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
添加交易对到黑名单
"""

import os
import pymysql
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

def add_to_blacklist(symbols: list, level: int = 2, reason: str = ""):
    """
    添加交易对到黑名单

    Args:
        symbols: 交易对列表
        level: 黑名单等级 (1=保证金25%, 2=保证金12.5%, 3=禁止交易)
        reason: 原因说明
    """

    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', '3306')),
        'user': os.getenv('DB_USER', 'root'),
        'password': os.getenv('DB_PASSWORD', ''),
        'database': os.getenv('DB_NAME', 'binance-data')
    }

    try:
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 保证金倍数映射
        margin_multipliers = {
            1: 0.25,  # 25%
            2: 0.125, # 12.5%
            3: 0.0    # 禁止交易
        }

        margin_multiplier = margin_multipliers.get(level, 0.125)

        for symbol in symbols:
            # 检查是否已存在
            cursor.execute("""
                SELECT symbol, rating_level, margin_multiplier
                FROM trading_symbol_rating
                WHERE symbol = %s
            """, (symbol,))

            existing = cursor.fetchone()

            if existing:
                # 更新现有记录
                old_level = existing['rating_level']
                cursor.execute("""
                    UPDATE trading_symbol_rating
                    SET rating_level = %s,
                        margin_multiplier = %s,
                        updated_at = NOW()
                    WHERE symbol = %s
                """, (level, margin_multiplier, symbol))

                logger.info(f"✅ {symbol} 黑名单等级更新: L{old_level} → L{level} (保证金{margin_multiplier*100}%)")
            else:
                # 插入新记录
                cursor.execute("""
                    INSERT INTO trading_symbol_rating
                    (symbol, rating_level, margin_multiplier, created_at, updated_at)
                    VALUES (%s, %s, %s, NOW(), NOW())
                """, (symbol, level, margin_multiplier))

                logger.info(f"✅ {symbol} 加入黑名单 L{level} (保证金{margin_multiplier*100}%)")

        conn.commit()

        # 显示当前黑名单统计
        cursor.execute("""
            SELECT rating_level, COUNT(*) as count
            FROM trading_symbol_rating
            GROUP BY rating_level
            ORDER BY rating_level
        """)

        stats = cursor.fetchall()

        logger.info("=" * 60)
        logger.info("📊 黑名单统计:")
        for stat in stats:
            level_name = f"L{stat['rating_level']}"
            if stat['rating_level'] == 1:
                level_name += " (保证金25%)"
            elif stat['rating_level'] == 2:
                level_name += " (保证金12.5%)"
            elif stat['rating_level'] == 3:
                level_name += " (禁止交易)"
            logger.info(f"   {level_name}: {stat['count']} 个交易对")
        logger.info("=" * 60)

        cursor.close()
        conn.close()

        logger.info("🎉 黑名单更新完成！重启服务后生效。")
        return True

    except Exception as e:
        logger.error(f"❌ 黑名单更新失败: {e}")
        return False

if __name__ == '__main__':
    # 要添加到黑名单2级的交易对
    blacklist_symbols = [
        'DUSK/USDT'    # 频繁止损，表现不稳定
    ]

    reason = "表现不稳定，加入黑名单L2 (2026-02-09)"

    logger.info("=" * 60)
    logger.info(f"准备将以下交易对加入黑名单L2:")
    for symbol in blacklist_symbols:
        logger.info(f"  - {symbol}")
    logger.info(f"原因: {reason}")
    logger.info("=" * 60)

    success = add_to_blacklist(blacklist_symbols, level=2, reason=reason)

    if success:
        logger.info("\n✅ 操作完成！")
        logger.info("黑名单L2效果:")
        logger.info("  - 开仓保证金降低到12.5% (原来的1/8)")
        logger.info("  - 降低这些币种的仓位风险")
        logger.info("  - 如果后续表现更差，可升级到L3禁止交易")
    else:
        logger.error("\n❌ 操作失败，请检查错误信息")
