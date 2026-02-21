#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
删除所有旧的分批建仓持仓（来源为 smart_trader_batch_v2）
"""

import pymysql
from loguru import logger

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def delete_old_batch_positions():
    """删除所有 smart_trader_batch_v2 来源的持仓"""
    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    try:
        # 1. 查看需要删除的持仓
        cursor.execute("""
            SELECT
                id,
                symbol,
                position_side,
                margin,
                open_time,
                status,
                source
            FROM futures_positions
            WHERE source = 'smart_trader_batch_v2'
              AND status = 'open'
              AND account_id = 2
            ORDER BY open_time DESC
        """)

        positions = cursor.fetchall()

        if not positions:
            logger.info("✅ 没有需要删除的旧批次持仓")
            return

        logger.info(f"🔍 找到 {len(positions)} 个旧批次持仓:\n")
        print("=" * 100)

        total_frozen = 0
        position_ids = []

        for pos in positions:
            position_ids.append(pos['id'])
            if pos['status'] == 'open':
                total_frozen += float(pos['margin'])

            print(f"ID:{pos['id']:5d} | {pos['symbol']:15s} {pos['position_side']:5s} | "
                  f"保证金: ${pos['margin']:6.2f} | "
                  f"{pos['open_time']} | {pos['source']}")

        print("=" * 100)
        logger.info(f"\n统计信息:")
        logger.info(f"  总持仓数: {len(positions)}")
        logger.info(f"  需释放保证金: ${total_frozen:.2f}")

        # 确认删除
        confirm = input("\n确认删除以上持仓？(yes/no): ")
        if confirm.lower() != 'yes':
            logger.info("❌ 取消删除操作")
            return

        # 2. 删除相关的 futures_trades 记录
        if position_ids:
            placeholders = ','.join(['%s'] * len(position_ids))
            cursor.execute(f"""
                DELETE FROM futures_trades
                WHERE position_id IN ({placeholders})
            """, position_ids)
            trades_deleted = cursor.rowcount
            logger.info(f"✅ 删除 {trades_deleted} 条交易记录")

            # 3. 删除相关的 futures_orders 记录
            cursor.execute(f"""
                DELETE FROM futures_orders
                WHERE position_id IN ({placeholders})
            """, position_ids)
            orders_deleted = cursor.rowcount
            logger.info(f"✅ 删除 {orders_deleted} 条订单记录")

            # 4. 释放保证金
            if total_frozen > 0:
                cursor.execute("""
                    UPDATE futures_trading_accounts
                    SET current_balance = current_balance + %s,
                        frozen_balance = frozen_balance - %s,
                        updated_at = NOW()
                    WHERE id = 2
                """, (total_frozen, total_frozen))

                logger.info(f"✅ U本位账户释放保证金: ${total_frozen:.2f}")

            # 5. 删除持仓记录
            cursor.execute(f"""
                DELETE FROM futures_positions
                WHERE id IN ({placeholders})
            """, position_ids)
            positions_deleted = cursor.rowcount

            conn.commit()

            logger.info(f"\n✅ 删除完成:")
            logger.info(f"   持仓记录: {positions_deleted}")
            logger.info(f"   订单记录: {orders_deleted}")
            logger.info(f"   交易记录: {trades_deleted}")
            logger.info(f"   释放保证金: ${total_frozen:.2f}")

    except Exception as e:
        conn.rollback()
        logger.error(f"❌ 删除失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise

    finally:
        cursor.close()
        conn.close()


if __name__ == '__main__':
    logger.info("🚀 开始清理旧的分批建仓持仓...")
    delete_old_batch_positions()
    logger.info("✅ 清理完成")
