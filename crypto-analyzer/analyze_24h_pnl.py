#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析最近24小时的交易盈亏情况
"""

import os
import pymysql
from datetime import datetime, timedelta
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

def analyze_24h_pnl():
    """分析最近24小时的交易表现"""

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

        # 1. 总体统计
        logger.info("=" * 80)
        logger.info("📊 最近24小时交易总览")
        logger.info("=" * 80)

        cursor.execute("""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) as losses,
                ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate,
                ROUND(SUM(realized_pnl), 2) as total_pnl,
                ROUND(AVG(realized_pnl), 2) as avg_pnl,
                ROUND(AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END), 2) as avg_win,
                ROUND(AVG(CASE WHEN realized_pnl <= 0 THEN realized_pnl END), 2) as avg_loss
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND account_id = 2
        """)

        stats = cursor.fetchone()

        logger.info(f"交易次数: {stats['total_trades']} 笔")
        logger.info(f"盈利次数: {stats['wins']} 笔 | 亏损次数: {stats['losses']} 笔")
        logger.info(f"胜率: {stats['win_rate']}%")
        logger.info(f"总盈亏: {stats['total_pnl']:+.2f} USDT")
        logger.info(f"平均盈亏: {stats['avg_pnl']:+.2f} USDT")
        logger.info(f"平均盈利: +{stats['avg_win']:.2f} USDT | 平均亏损: {stats['avg_loss']:.2f} USDT")

        if stats['avg_win'] and stats['avg_loss']:
            profit_loss_ratio = abs(stats['avg_win'] / stats['avg_loss'])
            logger.info(f"盈亏比: {profit_loss_ratio:.2f}:1")

        # 2. 按信号版本统计 (V2/V3对比)
        logger.info("\n" + "=" * 80)
        logger.info("🔥 V2 vs V3 信号对比 (最近24小时)")
        logger.info("=" * 80)

        cursor.execute("""
            SELECT
                signal_version,
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate,
                ROUND(SUM(realized_pnl), 2) as total_pnl,
                ROUND(AVG(realized_pnl), 2) as avg_pnl
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND account_id = 2
            GROUP BY signal_version
            ORDER BY total_pnl DESC
        """)

        version_stats = cursor.fetchall()

        for stat in version_stats:
            version = stat['signal_version'] or 'unknown'
            logger.info(f"\n{version.upper()}:")
            logger.info(f"  交易次数: {stat['total_trades']} 笔")
            logger.info(f"  胜率: {stat['win_rate']}% ({stat['wins']}/{stat['total_trades']})")
            logger.info(f"  总盈亏: {stat['total_pnl']:+.2f} USDT")
            logger.info(f"  平均盈亏: {stat['avg_pnl']:+.2f} USDT")

        # 3. 按交易对统计 (找出最赚钱和最亏钱的)
        logger.info("\n" + "=" * 80)
        logger.info("💰 最赚钱的交易对 TOP 10")
        logger.info("=" * 80)

        cursor.execute("""
            SELECT
                symbol,
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate,
                ROUND(SUM(realized_pnl), 2) as total_pnl
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND account_id = 2
            GROUP BY symbol
            HAVING total_pnl > 0
            ORDER BY total_pnl DESC
            LIMIT 10
        """)

        top_profitable = cursor.fetchall()

        for i, row in enumerate(top_profitable, 1):
            logger.info(f"{i}. {row['symbol']}: +{row['total_pnl']:.2f} USDT "
                       f"(胜率{row['win_rate']}%, {row['wins']}/{row['total_trades']}笔)")

        # 4. 最亏钱的交易对
        logger.info("\n" + "=" * 80)
        logger.info("📉 最亏钱的交易对 TOP 10")
        logger.info("=" * 80)

        cursor.execute("""
            SELECT
                symbol,
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate,
                ROUND(SUM(realized_pnl), 2) as total_pnl
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND account_id = 2
            GROUP BY symbol
            HAVING total_pnl < 0
            ORDER BY total_pnl ASC
            LIMIT 10
        """)

        top_losses = cursor.fetchall()

        for i, row in enumerate(top_losses, 1):
            logger.info(f"{i}. {row['symbol']}: {row['total_pnl']:.2f} USDT "
                       f"(胜率{row['win_rate']}%, {row['wins']}/{row['total_trades']}笔)")

        # 5. 按方向统计
        logger.info("\n" + "=" * 80)
        logger.info("📊 做多 vs 做空 表现对比")
        logger.info("=" * 80)

        cursor.execute("""
            SELECT
                position_side,
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate,
                ROUND(SUM(realized_pnl), 2) as total_pnl,
                ROUND(AVG(realized_pnl), 2) as avg_pnl
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND account_id = 2
            GROUP BY position_side
            ORDER BY total_pnl DESC
        """)

        side_stats = cursor.fetchall()

        for stat in side_stats:
            side = stat['position_side']
            logger.info(f"\n{side}:")
            logger.info(f"  交易次数: {stat['total_trades']} 笔")
            logger.info(f"  胜率: {stat['win_rate']}% ({stat['wins']}/{stat['total_trades']})")
            logger.info(f"  总盈亏: {stat['total_pnl']:+.2f} USDT")
            logger.info(f"  平均盈亏: {stat['avg_pnl']:+.2f} USDT")

        # 6. 平仓原因统计
        logger.info("\n" + "=" * 80)
        logger.info("📋 平仓原因分析")
        logger.info("=" * 80)

        cursor.execute("""
            SELECT
                SUBSTRING_INDEX(entry_signal_type, '_', 1) as exit_type,
                COUNT(*) as count,
                ROUND(AVG(realized_pnl), 2) as avg_pnl,
                ROUND(SUM(realized_pnl), 2) as total_pnl
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND account_id = 2
            GROUP BY exit_type
            ORDER BY count DESC
            LIMIT 10
        """)

        exit_reasons = cursor.fetchall()

        for reason in exit_reasons:
            logger.info(f"{reason['exit_type']}: {reason['count']}笔, "
                       f"平均{reason['avg_pnl']:+.2f} USDT, "
                       f"合计{reason['total_pnl']:+.2f} USDT")

        cursor.close()
        conn.close()

        logger.info("\n" + "=" * 80)
        logger.info("✅ 分析完成！")
        logger.info("=" * 80)

        return True

    except Exception as e:
        logger.error(f"❌ 分析失败: {e}")
        return False

if __name__ == '__main__':
    analyze_24h_pnl()
