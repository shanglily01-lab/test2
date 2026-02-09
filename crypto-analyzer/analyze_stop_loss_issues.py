#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析亏损单的止损问题
"""

import os
import pymysql
from datetime import datetime, timedelta
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

def analyze_stop_loss_issues():
    """分析亏损单是否及时止损"""

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

        # 查询最近24小时亏损超过20U的交易
        cursor.execute("""
            SELECT
                symbol,
                position_side,
                entry_price,
                mark_price,
                realized_pnl,
                open_time,
                close_time,
                TIMESTAMPDIFF(MINUTE, open_time, close_time) as holding_minutes,
                notes,
                stop_loss_price,
                stop_loss_pct,
                leverage,
                signal_version,
                max_profit_pct,
                max_profit_price,
                trailing_stop_activated
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND realized_pnl < -20
            AND account_id = 2
            ORDER BY realized_pnl ASC
            LIMIT 30
        """)

        losses = cursor.fetchall()

        logger.info("=" * 120)
        logger.info(f"最近24小时亏损超过20U的交易分析 (共{len(losses)}笔)")
        logger.info("=" * 120)

        issues_count = 0
        no_stop_loss_count = 0
        exceeded_stop_loss_count = 0

        for i, loss in enumerate(losses, 1):
            symbol = loss['symbol']
            side = loss['position_side']
            entry = float(loss['entry_price'])
            mark = float(loss['mark_price']) if loss['mark_price'] else entry
            pnl = float(loss['realized_pnl'])
            leverage = int(loss['leverage'])
            stop_loss = float(loss['stop_loss_price']) if loss['stop_loss_price'] else None
            stop_loss_pct = float(loss['stop_loss_pct']) if loss['stop_loss_pct'] else None
            holding = loss['holding_minutes']
            notes = loss['notes'] or ''
            signal_version = loss['signal_version'] or 'unknown'
            max_profit_pct = float(loss['max_profit_pct']) if loss['max_profit_pct'] else 0

            # 计算价格变动百分比
            if side == 'LONG':
                price_change_pct = (mark - entry) / entry * 100
            else:  # SHORT
                price_change_pct = (entry - mark) / entry * 100

            logger.info(f"\n[{i}] {symbol} {side} [{signal_version}]")
            logger.info(f"  亏损: {pnl:.2f} USDT | 持仓: {holding}分钟 | 杠杆: {leverage}x")
            logger.info(f"  开仓价: {entry:.6f} -> 平仓价: {mark:.6f}")
            logger.info(f"  价格变动: {price_change_pct:+.2f}%")

            if max_profit_pct:
                logger.info(f"  曾达最高盈利: {max_profit_pct*100:.2f}%")

            has_issue = False

            if stop_loss and stop_loss_pct:
                logger.info(f"  止损价: {stop_loss:.6f} (设置 {stop_loss_pct:+.2f}%)")

                # 判断实际亏损是否超过止损设置
                if abs(price_change_pct) > abs(stop_loss_pct) * 1.1:  # 允许10%误差
                    logger.warning(f"  ⚠️ 实际亏损 {abs(price_change_pct):.2f}% > 止损设置 {abs(stop_loss_pct):.2f}% -> 止损未及时触发！")
                    exceeded_stop_loss_count += 1
                    has_issue = True
                else:
                    logger.info(f"  ✅ 实际亏损 {abs(price_change_pct):.2f}% <= 止损设置 {abs(stop_loss_pct):.2f}%")
            elif stop_loss:
                logger.info(f"  止损价: {stop_loss:.6f} (但止损百分比字段为空)")
                has_issue = True
            else:
                logger.warning(f"  ❌ 没有设置止损价！")
                no_stop_loss_count += 1
                has_issue = True

            # 提取平仓原因
            if notes:
                # 截取关键信息
                if '止损' in notes:
                    logger.info(f"  平仓原因: {notes[:150]}")
                elif '熔断' in notes:
                    logger.info(f"  平仓原因: {notes[:150]}")
                else:
                    logger.info(f"  备注: {notes[:150]}")

            if has_issue:
                issues_count += 1

        # 统计摘要
        logger.info("\n" + "=" * 120)
        logger.info("📊 问题统计:")
        logger.info(f"  总计大额亏损: {len(losses)} 笔")
        logger.info(f"  存在问题的: {issues_count} 笔")
        logger.info(f"  - 没有设置止损: {no_stop_loss_count} 笔")
        logger.info(f"  - 超过止损设置: {exceeded_stop_loss_count} 笔")
        logger.info("=" * 120)

        # 分析快速止损的效果
        logger.info("\n" + "=" * 120)
        logger.info("🔍 快速止损机制检查 (持仓30分钟内的亏损单)")
        logger.info("=" * 120)

        cursor.execute("""
            SELECT
                symbol,
                position_side,
                realized_pnl,
                TIMESTAMPDIFF(MINUTE, open_time, close_time) as holding_minutes,
                notes
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND realized_pnl < 0
            AND TIMESTAMPDIFF(MINUTE, open_time, close_time) <= 30
            AND account_id = 2
            ORDER BY holding_minutes ASC
            LIMIT 20
        """)

        quick_losses = cursor.fetchall()

        logger.info(f"30分钟内平仓的亏损单: {len(quick_losses)} 笔")

        quick_stop_triggered = 0
        for loss in quick_losses:
            notes = loss['notes'] or ''
            if '快速止损' in notes:
                quick_stop_triggered += 1
                logger.info(f"  ✅ {loss['symbol']} {loss['position_side']} | "
                          f"{loss['holding_minutes']}分钟 | {loss['realized_pnl']:.2f}U | "
                          f"触发快速止损")
            elif loss['realized_pnl'] < -20:
                logger.warning(f"  ⚠️ {loss['symbol']} {loss['position_side']} | "
                             f"{loss['holding_minutes']}分钟 | {loss['realized_pnl']:.2f}U | "
                             f"未触发快速止损但亏损超过20U")

        logger.info(f"\n快速止损触发: {quick_stop_triggered}/{len(quick_losses)} 笔")
        logger.info("=" * 120)

        cursor.close()
        conn.close()

        return True

    except Exception as e:
        logger.error(f"❌ 分析失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    analyze_stop_loss_issues()
