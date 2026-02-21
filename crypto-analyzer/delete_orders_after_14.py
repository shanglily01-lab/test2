#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
删除今天14:00之后的所有订单（垃圾数据清理）
"""

import pymysql
from loguru import logger
from datetime import datetime

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def delete_orders_after_14():
    """删除今天14:00之后的所有订单"""
    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    try:
        # 1. 查看需要删除的订单
        # 北京时间14:00 = UTC 06:00
        cursor.execute("""
            SELECT
                id,
                symbol,
                position_side,
                margin,
                entry_price,
                open_time,
                close_time,
                status,
                source
            FROM futures_positions
            WHERE DATE(open_time) = '2026-02-21'
              AND TIME(open_time) >= '06:00:00'
            ORDER BY open_time DESC
        """)

        positions = cursor.fetchall()

        if not positions:
            logger.info("✅ 没有需要删除的订单")
            return

        logger.info(f"🔍 找到 {len(positions)} 个需要删除的订单:\n")
        print("=" * 120)

        total_frozen = 0
        position_ids = []

        for pos in positions:
            position_ids.append(pos['id'])
            if pos['status'] == 'open':
                total_frozen += float(pos['margin'])

            print(f"ID: {pos['id']} | {pos['symbol']} {pos['position_side']} | "
                  f"保证金: ${pos['margin']:.2f} | "
                  f"开仓: {pos['open_time']} | "
                  f"状态: {pos['status']} | "
                  f"来源: {pos['source']}")

        print("=" * 120)
        logger.info(f"\n统计信息:")
        logger.info(f"  总订单数: {len(positions)}")
        logger.info(f"  需释放保证金: ${total_frozen:.2f}")

        # 确认删除
        confirm = input("\n确认删除以上订单？(yes/no): ")
        if confirm.lower() != 'yes':
            logger.info("❌ 取消删除操作")
            return

        # 2. 删除相关的 futures_trades 记录
        cursor.execute("""
            DELETE FROM futures_trades
            WHERE position_id IN (%s)
        """ % ','.join(map(str, position_ids)))
        trades_deleted = cursor.rowcount
        logger.info(f"✅ 删除 {trades_deleted} 条交易记录")

        # 3. 删除相关的 futures_orders 记录
        cursor.execute("""
            DELETE FROM futures_orders
            WHERE position_id IN (%s)
        """ % ','.join(map(str, position_ids)))
        orders_deleted = cursor.rowcount
        logger.info(f"✅ 删除 {orders_deleted} 条订单记录")

        # 4. 释放未平仓订单的冻结保证金（只针对 open 状态）
        if total_frozen > 0:
            # U本位账户ID=2, 币本位账户ID=3
            # 检查这些订单属于哪个账户
            cursor.execute("""
                SELECT DISTINCT account_id
                FROM futures_positions
                WHERE id IN (%s)
            """ % ','.join(map(str, position_ids)))

            account_ids = [row['account_id'] for row in cursor.fetchall()]

            for account_id in account_ids:
                # 计算该账户需要释放的保证金
                cursor.execute("""
                    SELECT SUM(margin) as frozen_margin
                    FROM futures_positions
                    WHERE id IN (%s)
                      AND account_id = %s
                      AND status = 'open'
                """ % (','.join(map(str, position_ids)), account_id))

                result = cursor.fetchone()
                account_frozen = float(result['frozen_margin']) if result['frozen_margin'] else 0

                if account_frozen > 0:
                    cursor.execute("""
                        UPDATE futures_trading_accounts
                        SET current_balance = current_balance + %s,
                            frozen_balance = frozen_balance - %s,
                            updated_at = NOW()
                        WHERE id = %s
                    """, (account_frozen, account_frozen, account_id))

                    logger.info(f"✅ 账户 {account_id} 释放保证金: ${account_frozen:.2f}")

        # 5. 删除持仓记录
        cursor.execute("""
            DELETE FROM futures_positions
            WHERE id IN (%s)
        """ % ','.join(map(str, position_ids)))
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
        raise

    finally:
        cursor.close()
        conn.close()


if __name__ == '__main__':
    logger.info("🚀 开始清理今天14:00之后的订单...")
    delete_orders_after_14()
    logger.info("✅ 清理完成")
