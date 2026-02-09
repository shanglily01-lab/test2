#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
深度分析LONG vs SHORT的开仓评分和亏损关系
"""

import os
import pymysql
from datetime import datetime, timedelta
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

def analyze_long_vs_short():
    """分析LONG和SHORT的开仓评分与盈亏关系"""

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

        logger.info("=" * 120)
        logger.info("🔍 LONG vs SHORT 开仓评分与盈亏深度分析 (最近24小时)")
        logger.info("=" * 120)

        # 1. LONG和SHORT的开仓评分分布对比
        logger.info("\n📊 开仓评分分布对比")
        logger.info("-" * 120)

        for side in ['LONG', 'SHORT']:
            cursor.execute("""
                SELECT
                    position_side,
                    COUNT(*) as total,
                    ROUND(AVG(entry_score), 2) as avg_score,
                    ROUND(MIN(entry_score), 2) as min_score,
                    ROUND(MAX(entry_score), 2) as max_score,
                    COUNT(CASE WHEN realized_pnl > 0 THEN 1 END) as wins,
                    COUNT(CASE WHEN realized_pnl <= 0 THEN 1 END) as losses,
                    ROUND(COUNT(CASE WHEN realized_pnl > 0 THEN 1 END) / COUNT(*) * 100, 2) as win_rate,
                    ROUND(SUM(realized_pnl), 2) as total_pnl,
                    ROUND(AVG(realized_pnl), 2) as avg_pnl
                FROM futures_positions
                WHERE status = 'closed'
                AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
                AND position_side = %s
                AND account_id = 2
            """, (side,))

            result = cursor.fetchone()

            logger.info(f"\n{side}:")
            logger.info(f"  总交易: {result['total']} 笔 | 胜率: {result['win_rate']}%")
            logger.info(f"  开仓评分: 平均{result['avg_score']} | 范围[{result['min_score']}, {result['max_score']}]")
            logger.info(f"  盈亏: 总{result['total_pnl']:+.2f}U | 平均{result['avg_pnl']:+.2f}U")

        # 2. 按评分区间对比LONG和SHORT的表现
        logger.info("\n" + "=" * 120)
        logger.info("📈 不同评分区间的LONG vs SHORT表现对比")
        logger.info("=" * 120)

        score_ranges = [
            ("低分", 0, 20),
            ("中低分", 20, 25),
            ("中高分", 25, 30),
            ("高分", 30, 100)
        ]

        for range_name, min_score, max_score in score_ranges:
            logger.info(f"\n{range_name} ({min_score}-{max_score}分):")
            logger.info("-" * 120)

            for side in ['LONG', 'SHORT']:
                cursor.execute("""
                    SELECT
                        COUNT(*) as count,
                        COUNT(CASE WHEN realized_pnl > 0 THEN 1 END) as wins,
                        ROUND(COUNT(CASE WHEN realized_pnl > 0 THEN 1 END) / COUNT(*) * 100, 2) as win_rate,
                        ROUND(SUM(realized_pnl), 2) as total_pnl,
                        ROUND(AVG(realized_pnl), 2) as avg_pnl,
                        ROUND(AVG(entry_score), 2) as avg_score
                    FROM futures_positions
                    WHERE status = 'closed'
                    AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
                    AND position_side = %s
                    AND entry_score >= %s AND entry_score < %s
                    AND account_id = 2
                """, (side, min_score, max_score))

                result = cursor.fetchone()

                if result['count'] > 0:
                    logger.info(f"  {side}: {result['count']}笔 | 胜率{result['win_rate']}% | "
                               f"平均评分{result['avg_score']} | 平均盈亏{result['avg_pnl']:+.2f}U | "
                               f"总盈亏{result['total_pnl']:+.2f}U")
                else:
                    logger.info(f"  {side}: 无数据")

        # 3. 分析LONG亏损单的评分特征
        logger.info("\n" + "=" * 120)
        logger.info("🔴 LONG亏损单的评分特征分析")
        logger.info("=" * 120)

        cursor.execute("""
            SELECT
                CASE
                    WHEN entry_score < 20 THEN '低分(<20)'
                    WHEN entry_score < 25 THEN '中低分(20-25)'
                    WHEN entry_score < 30 THEN '中高分(25-30)'
                    ELSE '高分(>=30)'
                END as score_range,
                COUNT(*) as count,
                ROUND(AVG(entry_score), 2) as avg_score,
                ROUND(SUM(realized_pnl), 2) as total_loss,
                ROUND(AVG(realized_pnl), 2) as avg_loss,
                ROUND(AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)), 0) as avg_holding
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND position_side = 'LONG'
            AND realized_pnl < 0
            AND account_id = 2
            GROUP BY score_range
            ORDER BY
                CASE score_range
                    WHEN '低分(<20)' THEN 1
                    WHEN '中低分(20-25)' THEN 2
                    WHEN '中高分(25-30)' THEN 3
                    ELSE 4
                END
        """)

        long_losses = cursor.fetchall()

        for ll in long_losses:
            logger.info(f"\n{ll['score_range']}:")
            logger.info(f"  亏损笔数: {ll['count']} 笔")
            logger.info(f"  平均评分: {ll['avg_score']}")
            logger.info(f"  总亏损: {ll['total_loss']:.2f} USDT")
            logger.info(f"  平均亏损: {ll['avg_loss']:.2f} USDT")
            logger.info(f"  平均持仓: {ll['avg_holding']:.0f} 分钟")

        # 4. 分析SHORT亏损单的评分特征
        logger.info("\n" + "=" * 120)
        logger.info("🔴 SHORT亏损单的评分特征分析")
        logger.info("=" * 120)

        cursor.execute("""
            SELECT
                CASE
                    WHEN entry_score < 20 THEN '低分(<20)'
                    WHEN entry_score < 25 THEN '中低分(20-25)'
                    WHEN entry_score < 30 THEN '中高分(25-30)'
                    ELSE '高分(>=30)'
                END as score_range,
                COUNT(*) as count,
                ROUND(AVG(entry_score), 2) as avg_score,
                ROUND(SUM(realized_pnl), 2) as total_loss,
                ROUND(AVG(realized_pnl), 2) as avg_loss,
                ROUND(AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)), 0) as avg_holding
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND position_side = 'SHORT'
            AND realized_pnl < 0
            AND account_id = 2
            GROUP BY score_range
            ORDER BY
                CASE score_range
                    WHEN '低分(<20)' THEN 1
                    WHEN '中低分(20-25)' THEN 2
                    WHEN '中高分(25-30)' THEN 3
                    ELSE 4
                END
        """)

        short_losses = cursor.fetchall()

        for sl in short_losses:
            logger.info(f"\n{sl['score_range']}:")
            logger.info(f"  亏损笔数: {sl['count']} 笔")
            logger.info(f"  平均评分: {sl['avg_score']}")
            logger.info(f"  总亏损: {sl['total_loss']:.2f} USDT")
            logger.info(f"  平均亏损: {sl['avg_loss']:.2f} USDT")
            logger.info(f"  平均持仓: {sl['avg_holding']:.0f} 分钟")

        # 5. 评分阈值建议
        logger.info("\n" + "=" * 120)
        logger.info("💡 评分阈值优化建议")
        logger.info("=" * 120)

        # 计算不同阈值下的表现
        thresholds = [18, 20, 22, 25, 28]

        for threshold in thresholds:
            for side in ['LONG', 'SHORT']:
                cursor.execute("""
                    SELECT
                        COUNT(*) as count,
                        COUNT(CASE WHEN realized_pnl > 0 THEN 1 END) as wins,
                        ROUND(COUNT(CASE WHEN realized_pnl > 0 THEN 1 END) / COUNT(*) * 100, 2) as win_rate,
                        ROUND(SUM(realized_pnl), 2) as total_pnl,
                        ROUND(AVG(realized_pnl), 2) as avg_pnl
                    FROM futures_positions
                    WHERE status = 'closed'
                    AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
                    AND position_side = %s
                    AND entry_score >= %s
                    AND account_id = 2
                """, (side, threshold))

                result = cursor.fetchone()

                if result['count'] > 0:
                    logger.info(f"阈值>={threshold}分 {side}: {result['count']}笔 | "
                               f"胜率{result['win_rate']}% | 总盈亏{result['total_pnl']:+.2f}U | "
                               f"平均{result['avg_pnl']:+.2f}U")

        # 6. 最差LONG交易对分析
        logger.info("\n" + "=" * 120)
        logger.info("🔍 LONG方向最差交易对 (亏损最多)")
        logger.info("=" * 120)

        cursor.execute("""
            SELECT
                symbol,
                COUNT(*) as count,
                ROUND(AVG(entry_score), 2) as avg_score,
                COUNT(CASE WHEN realized_pnl > 0 THEN 1 END) as wins,
                ROUND(COUNT(CASE WHEN realized_pnl > 0 THEN 1 END) / COUNT(*) * 100, 2) as win_rate,
                ROUND(SUM(realized_pnl), 2) as total_pnl,
                ROUND(AVG(realized_pnl), 2) as avg_pnl
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND position_side = 'LONG'
            AND account_id = 2
            GROUP BY symbol
            HAVING total_pnl < 0
            ORDER BY total_pnl ASC
            LIMIT 10
        """)

        worst_longs = cursor.fetchall()

        for i, wl in enumerate(worst_longs, 1):
            logger.info(f"{i}. {wl['symbol']}: {wl['count']}笔 | 平均评分{wl['avg_score']} | "
                       f"胜率{wl['win_rate']}% | 总盈亏{wl['total_pnl']:.2f}U | 平均{wl['avg_pnl']:.2f}U")

        # 7. 总结建议
        logger.info("\n" + "=" * 120)
        logger.info("📋 结论与建议")
        logger.info("=" * 120)

        cursor.execute("""
            SELECT
                position_side,
                ROUND(AVG(entry_score), 2) as avg_score,
                ROUND(AVG(CASE WHEN realized_pnl > 0 THEN entry_score END), 2) as avg_score_win,
                ROUND(AVG(CASE WHEN realized_pnl <= 0 THEN entry_score END), 2) as avg_score_loss
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND account_id = 2
            GROUP BY position_side
        """)

        score_summary = cursor.fetchall()

        for ss in score_summary:
            logger.info(f"\n{ss['position_side']}:")
            logger.info(f"  总体平均评分: {ss['avg_score']}")
            logger.info(f"  盈利单平均评分: {ss['avg_score_win']}")
            logger.info(f"  亏损单平均评分: {ss['avg_score_loss']}")
            score_diff = ss['avg_score_win'] - ss['avg_score_loss']
            logger.info(f"  评分差异: {score_diff:+.2f} (盈利单 - 亏损单)")

        cursor.close()
        conn.close()

        return True

    except Exception as e:
        logger.error(f"❌ 分析失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    analyze_long_vs_short()
