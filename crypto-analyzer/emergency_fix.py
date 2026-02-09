#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
紧急修复脚本 - 2026-02-09
1. 检查服务状态
2. 提供修复建议
"""

import os
import pymysql
from dotenv import load_dotenv
from loguru import logger
from datetime import datetime, timedelta

load_dotenv()

def check_service_status():
    """检查关键问题"""

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
    logger.info("🚨 紧急状态检查 (2026-02-09)")
    logger.info("=" * 80)

    # 1. 检查止损BUG是否修复
    logger.info("\n1️⃣ 检查止损BUG状态")
    logger.info("-" * 80)

    cursor.execute("""
        SELECT COUNT(*) as total,
               COUNT(CASE WHEN stop_loss_pct IS NULL THEN 1 END) as null_count
        FROM futures_positions
        WHERE created_at >= DATE_SUB(NOW(), INTERVAL 2 HOUR)
        AND account_id = 2
    """)

    stop_loss_check = cursor.fetchone()

    if stop_loss_check['null_count'] > 0:
        logger.error(f"❌ 止损BUG未修复: {stop_loss_check['null_count']}/{stop_loss_check['total']} 笔持仓stop_loss_pct为NULL")
        logger.error("   原因: 服务未重启,仍在运行旧代码!")
        logger.error("   解决: 立即重启 smart_trader_service.py")
    else:
        logger.success(f"✅ 止损BUG已修复: 最近2小时{stop_loss_check['total']}笔持仓都有止损")

    # 2. 检查LONG阈值优化是否生效
    logger.info("\n2️⃣ 检查LONG阈值优化状态")
    logger.info("-" * 80)

    cursor.execute("""
        SELECT COUNT(*) as long_count,
               MIN(entry_score) as min_score,
               MAX(entry_score) as max_score,
               AVG(entry_score) as avg_score
        FROM futures_positions
        WHERE position_side = 'LONG'
        AND signal_version = 'v3'
        AND created_at >= DATE_SUB(NOW(), INTERVAL 2 HOUR)
        AND account_id = 2
    """)

    long_check = cursor.fetchone()

    if long_check['long_count'] > 0:
        if long_check['min_score'] < 28:
            logger.error(f"❌ LONG阈值优化未生效: 最低分{long_check['min_score']:.1f} < 28分")
            logger.error(f"   最近2小时开了{long_check['long_count']}笔LONG,评分{long_check['min_score']:.1f}-{long_check['max_score']:.1f}")
            logger.error("   原因: 服务未重启!")
        else:
            logger.success(f"✅ LONG阈值已生效: 最近2小时{long_check['long_count']}笔LONG,最低{long_check['min_score']:.1f}分 ≥ 28分")
    else:
        logger.warning("⚠️ 最近2小时无LONG开仓,无法验证阈值")

    # 3. 检查当前持仓情况
    logger.info("\n3️⃣ 当前持仓状况")
    logger.info("-" * 80)

    cursor.execute("""
        SELECT
            position_side,
            COUNT(*) as count,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            ROUND(AVG(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 100, 2) as win_rate,
            ROUND(SUM(realized_pnl), 2) as total_pnl
        FROM futures_positions
        WHERE status = 'open'
        AND account_id = 2
        GROUP BY position_side
    """)

    open_positions = cursor.fetchall()

    total_open = 0
    for pos in open_positions:
        total_open += pos['count']
        logger.info(f"{pos['position_side']}: {pos['count']}笔持仓")

    if total_open == 0:
        logger.info("无持仓")

    # 4. 检查最近1小时盈亏
    logger.info("\n4️⃣ 最近1小时盈亏")
    logger.info("-" * 80)

    cursor.execute("""
        SELECT
            COUNT(*) as count,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            ROUND(AVG(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 100, 2) as win_rate,
            ROUND(SUM(realized_pnl), 2) as total_pnl,
            ROUND(AVG(realized_pnl), 2) as avg_pnl
        FROM futures_positions
        WHERE status = 'closed'
        AND close_time >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
        AND account_id = 2
    """)

    recent_pnl = cursor.fetchone()

    if recent_pnl['count'] > 0:
        logger.info(f"交易笔数: {recent_pnl['count']}笔")
        logger.info(f"胜率: {recent_pnl['win_rate']}%")
        logger.info(f"总盈亏: {recent_pnl['total_pnl']:+.2f} USDT")
        logger.info(f"平均盈亏: {recent_pnl['avg_pnl']:+.2f} USDT")

        if recent_pnl['total_pnl'] < -100:
            logger.error("🚨 最近1小时亏损超过100U,情况紧急!")
        elif recent_pnl['total_pnl'] < 0:
            logger.warning("⚠️ 最近1小时亏损中")
        else:
            logger.success("✅ 最近1小时盈利中")
    else:
        logger.info("最近1小时无平仓")

    # 5. 检查交易控制开关
    logger.info("\n5️⃣ 交易控制开关")
    logger.info("-" * 80)

    cursor.execute("""
        SELECT trading_enabled
        FROM trading_control
        WHERE account_id = 2 AND trading_type = 'usdt_futures'
    """)

    trading_control = cursor.fetchone()

    if trading_control:
        if trading_control['trading_enabled']:
            logger.info("✅ 交易已启用")
        else:
            logger.warning("⚠️ 交易已停止")
    else:
        logger.warning("⚠️ 无交易控制记录")

    # 总结建议
    logger.info("\n" + "=" * 80)
    logger.info("📋 紧急修复建议")
    logger.info("=" * 80)

    needs_restart = False

    if stop_loss_check['null_count'] > 0:
        logger.error("\n🔥 立即执行:")
        logger.error("1. 重启交易服务:")
        logger.error("   sudo systemctl restart smart-trader")
        logger.error("   或: pkill -f smart_trader_service.py && python smart_trader_service.py &")
        needs_restart = True

    if long_check['long_count'] > 0 and long_check['min_score'] < 28:
        if not needs_restart:
            logger.error("\n🔥 立即执行:")
        logger.error("2. LONG阈值优化未生效,需要重启服务")
        needs_restart = True

    if recent_pnl['count'] > 0 and recent_pnl['total_pnl'] < -100:
        logger.error("\n🚨 紧急止血:")
        logger.error("3. 考虑暂时停止交易:")
        logger.error("   UPDATE trading_control SET trading_enabled = 0 WHERE account_id = 2;")
        logger.error("4. 或立即平掉所有LONG持仓")

    if not needs_restart and (recent_pnl['count'] == 0 or recent_pnl['total_pnl'] >= 0):
        logger.success("\n✅ 系统状态正常,继续观察")

    logger.info("\n" + "=" * 80)
    logger.info("检查完成")
    logger.info("=" * 80)

    cursor.close()
    conn.close()

if __name__ == '__main__':
    check_service_status()
