#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
紧急平仓所有下架合约的持仓

危险情况:
- 43个下架合约的持仓，总未实现盈亏 -208,706 USDT
- 这些价格全是错的，不能继续持有
- 必须立即平仓止损
"""

import pymysql
from loguru import logger
import os
from dotenv import load_dotenv

load_dotenv()

# 数据库配置
DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4'
}

# 下架的交易对列表
DELISTED_SYMBOLS = [
    'ALPACA/USDT', 'BNX/USDT', 'ALPHA/USDT', 'PORT3/USDT', 'UXLINK/USDT',
    'VIDT/USDT', 'SXP/USDT', 'AGIX/USDT', 'LINA/USDT', 'MEMEFI/USDT',
    'LEVER/USDT', 'NEIROETH/USDT', 'FTM/USDT', 'WAVES/USDT', 'OMNI/USDT',
    'AMB/USDT', 'BSW/USDT', 'OCEAN/USDT', 'STRAX/USDT', 'REN/USDT',
    'UNFI/USDT', 'DGB/USDT', 'TROY/USDT', 'HIFI/USDT', 'SNT/USDT', 'MKR/USDT'
]

def delete_positions():
    """删除所有下架合约的持仓"""
    logger.info("=" * 60)
    logger.info("紧急删除: 下架合约持仓(错误数据)")
    logger.info("=" * 60)

    conn = pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    try:
        # 查询所有open/building状态且是下架合约的持仓
        placeholders = ','.join(['%s'] * len(DELISTED_SYMBOLS))
        cursor.execute(f'''
            SELECT id, symbol, position_side, entry_price, quantity, margin,
                   unrealized_pnl, unrealized_pnl_pct, status
            FROM futures_positions
            WHERE status IN ('open', 'building')
            AND symbol IN ({placeholders})
            ORDER BY id
        ''', DELISTED_SYMBOLS)

        positions = cursor.fetchall()

        if not positions:
            logger.success("✅ 没有需要删除的持仓")
            return

        logger.warning(f"发现 {len(positions)} 个需要删除的持仓")

        total_loss = sum(float(p['unrealized_pnl'] or 0) for p in positions)
        logger.warning(f"总未实现盈亏: {total_loss:.2f} USDT (全是错误数据)")

        # 显示将要删除的持仓
        logger.info("\n准备删除以下持仓:")
        for p in positions:
            pnl = float(p['unrealized_pnl'] or 0)
            pnl_pct = float(p['unrealized_pnl_pct'] or 0)
            logger.info(
                f"  [{p['id']}] {p['symbol']} {p['position_side']} {p['status']} "
                f"价格:{p['entry_price']} 盈亏: {pnl:+.2f} ({pnl_pct:+.2f}%)"
            )

        # 直接删除持仓记录
        position_ids = [p['id'] for p in positions]

        cursor.execute(f"""
            DELETE FROM futures_positions
            WHERE id IN ({','.join(map(str, position_ids))})
        """)

        conn.commit()

        logger.success(f"\n✅ 已删除 {len(positions)} 个错误的持仓记录")

        logger.info("\n⚠️  说明:")
        logger.info("1. 这些持仓的价格全是错误的(下架合约)")
        logger.info("2. 实际没有真实的交易发生")
        logger.info("3. 删除这些数据不影响任何真实资金")
        logger.info("4. 这些只是系统的模拟持仓,不是实盘")

        # 统计
        logger.info("\n=== 删除统计 ===")
        for symbol in sorted(set(p['symbol'] for p in positions)):
            symbol_positions = [p for p in positions if p['symbol'] == symbol]
            symbol_loss = sum(float(p['unrealized_pnl'] or 0) for p in symbol_positions)
            logger.info(f"{symbol}: {len(symbol_positions)} 个持仓, 错误盈亏 {symbol_loss:+.2f}")

    except Exception as e:
        logger.error(f"平仓失败: {e}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    delete_positions()
