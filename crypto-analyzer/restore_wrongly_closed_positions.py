#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
恢复因保证金过小误判而被平仓的订单
"""

import pymysql
from loguru import logger
from datetime import datetime, timedelta
from app.utils.config_loader import load_config

def main():
    """主函数"""
    logger.info("=" * 80)
    logger.info("查询因保证金过小误判而被平仓的订单")
    logger.info("=" * 80)

    # 加载配置
    config = load_config()
    mysql_config = config['database']['mysql']

    # 连接数据库
    conn = pymysql.connect(
        host=mysql_config['host'],
        port=mysql_config['port'],
        user=mysql_config['user'],
        password=mysql_config['password'],
        database=mysql_config['database'],
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

    cursor = conn.cursor()

    try:
        # 查询最近因"保证金过小"被平仓的持仓
        cursor.execute("""
            SELECT
                id, symbol, position_side, margin, quantity, entry_price,
                mark_price, realized_pnl, notes,
                created_at, close_time, status
            FROM futures_positions
            WHERE account_id = 2
            AND status = 'closed'
            AND notes LIKE '%保证金过小%'
            AND close_time >= NOW() - INTERVAL 2 HOUR
            ORDER BY close_time DESC
        """)

        wrongly_closed = cursor.fetchall()

        if not wrongly_closed:
            logger.info("✅ 没有找到因保证金过小被误平的订单")
            return

        logger.info(f"\n找到 {len(wrongly_closed)} 个被误平的订单:\n")

        total_pnl = 0
        for pos in wrongly_closed:
            margin = float(pos['margin']) if pos['margin'] else 0
            realized_pnl = float(pos['realized_pnl']) if pos['realized_pnl'] else 0
            total_pnl += realized_pnl

            logger.info(
                f"#{pos['id']:4d} {pos['symbol']:12s} {pos['position_side']:5s} | "
                f"保证金: ${margin:7.2f} | 盈亏: ${realized_pnl:+7.2f} | "
                f"开仓: {pos['created_at']} | 平仓: {pos['close_time']}"
            )

        logger.info(f"\n总盈亏: ${total_pnl:+.2f}")

        # 询问是否恢复
        logger.info("\n" + "=" * 80)
        logger.info("恢复选项:")
        logger.info("=" * 80)
        logger.info("""
选项1: 将这些持仓状态改回 'open'（恢复持仓）
选项2: 只修正realized_pnl和账户余额
选项3: 不做任何操作

注意：
- 选项1会恢复持仓，但市场价格已经变化，可能不是最佳选择
- 选项2只修正盈亏记录，持仓仍然保持关闭状态
- 建议选择选项2，然后手动重新开仓
        """)

        choice = input("\n请选择 (1/2/3): ").strip()

        if choice == '1':
            logger.info("\n开始恢复持仓...")
            for pos in wrongly_closed:
                cursor.execute("""
                    UPDATE futures_positions
                    SET status = 'open',
                        close_time = NULL,
                        notes = CONCAT(COALESCE(notes, ''), '\n[恢复] 因保证金检查bug被误平，已恢复')
                    WHERE id = %s
                """, (pos['id'],))
                logger.info(f"✅ 恢复持仓 #{pos['id']} {pos['symbol']}")

            conn.commit()
            logger.info(f"\n✅ 成功恢复 {len(wrongly_closed)} 个持仓")

        elif choice == '2':
            logger.info("\n只记录误平，不恢复持仓")
            for pos in wrongly_closed:
                cursor.execute("""
                    UPDATE futures_positions
                    SET notes = CONCAT(COALESCE(notes, ''), '\n[记录] 因保证金检查bug被误平')
                    WHERE id = %s
                """, (pos['id'],))

            conn.commit()
            logger.info(f"\n✅ 已标记 {len(wrongly_closed)} 个持仓")

        else:
            logger.info("\n不做任何操作")

    finally:
        cursor.close()
        conn.close()

    logger.info("\n" + "=" * 80)
    logger.info("完成")
    logger.info("=" * 80)

if __name__ == '__main__':
    main()
