#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
深度分析亏损单的模式和原因
"""

import os
import pymysql
from datetime import datetime, timedelta
from dotenv import load_dotenv
from loguru import logger
from collections import defaultdict

load_dotenv()

def analyze_loss_patterns():
    """深度分析亏损单的模式"""

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

        # 1. 按持仓时间段统计亏损分布
        logger.info("=" * 120)
        logger.info("📊 按持仓时间段统计亏损分布 (最近24小时)")
        logger.info("=" * 120)

        cursor.execute("""
            SELECT
                CASE
                    WHEN TIMESTAMPDIFF(MINUTE, open_time, close_time) <= 30 THEN '0-30分钟'
                    WHEN TIMESTAMPDIFF(MINUTE, open_time, close_time) <= 60 THEN '30-60分钟'
                    WHEN TIMESTAMPDIFF(MINUTE, open_time, close_time) <= 120 THEN '1-2小时'
                    WHEN TIMESTAMPDIFF(MINUTE, open_time, close_time) <= 180 THEN '2-3小时'
                    ELSE '>3小时'
                END as time_range,
                COUNT(*) as count,
                ROUND(SUM(realized_pnl), 2) as total_loss,
                ROUND(AVG(realized_pnl), 2) as avg_loss,
                ROUND(MIN(realized_pnl), 2) as max_single_loss
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND realized_pnl < 0
            AND account_id = 2
            GROUP BY time_range
            ORDER BY
                CASE time_range
                    WHEN '0-30分钟' THEN 1
                    WHEN '30-60分钟' THEN 2
                    WHEN '1-2小时' THEN 3
                    WHEN '2-3小时' THEN 4
                    ELSE 5
                END
        """)

        time_ranges = cursor.fetchall()

        for tr in time_ranges:
            logger.info(f"\n{tr['time_range']}")
            logger.info(f"  交易笔数: {tr['count']} 笔")
            logger.info(f"  总亏损: {tr['total_loss']:.2f} USDT")
            logger.info(f"  平均亏损: {tr['avg_loss']:.2f} USDT")
            logger.info(f"  最大单笔: {tr['max_single_loss']:.2f} USDT")

        # 2. 分析不同平仓原因的效果
        logger.info("\n" + "=" * 120)
        logger.info("📋 不同平仓原因的亏损统计")
        logger.info("=" * 120)

        cursor.execute("""
            SELECT
                CASE
                    WHEN notes LIKE '%快速止损%' THEN '快速止损'
                    WHEN notes LIKE '%止损%' AND notes NOT LIKE '%快速%' THEN '固定止损'
                    WHEN notes LIKE '%反转止损%' THEN '反转止损'
                    WHEN notes LIKE '%移动止盈%' THEN '移动止盈'
                    WHEN notes LIKE '%超时%' THEN '动态超时'
                    WHEN notes LIKE '%熔断%' THEN '熔断机制'
                    ELSE '其他/未知'
                END as exit_type,
                COUNT(*) as count,
                ROUND(SUM(realized_pnl), 2) as total_pnl,
                ROUND(AVG(realized_pnl), 2) as avg_pnl,
                ROUND(AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)), 0) as avg_holding_mins
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND realized_pnl < 0
            AND account_id = 2
            GROUP BY exit_type
            ORDER BY total_pnl ASC
        """)

        exit_types = cursor.fetchall()

        for et in exit_types:
            logger.info(f"\n{et['exit_type']}")
            logger.info(f"  交易笔数: {et['count']} 笔")
            logger.info(f"  总亏损: {et['total_pnl']:.2f} USDT")
            logger.info(f"  平均亏损: {et['avg_pnl']:.2f} USDT")
            logger.info(f"  平均持仓: {et['avg_holding_mins']:.0f} 分钟")

        # 3. 按交易对统计亏损频率
        logger.info("\n" + "=" * 120)
        logger.info("🔥 高频亏损交易对 TOP 15 (亏损次数最多)")
        logger.info("=" * 120)

        cursor.execute("""
            SELECT
                symbol,
                COUNT(*) as loss_count,
                ROUND(SUM(realized_pnl), 2) as total_loss,
                ROUND(AVG(realized_pnl), 2) as avg_loss,
                COUNT(DISTINCT position_side) as directions
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND realized_pnl < 0
            AND account_id = 2
            GROUP BY symbol
            ORDER BY loss_count DESC, total_loss ASC
            LIMIT 15
        """)

        high_freq_losses = cursor.fetchall()

        for i, row in enumerate(high_freq_losses, 1):
            direction_text = "双向" if row['directions'] == 2 else "单向"
            logger.info(f"{i}. {row['symbol']}: {row['loss_count']}笔亏损 ({direction_text}) | "
                       f"总亏损{row['total_loss']:.2f}U | 平均{row['avg_loss']:.2f}U")

        # 4. 分析LONG vs SHORT的亏损差异
        logger.info("\n" + "=" * 120)
        logger.info("📊 LONG vs SHORT 亏损对比 (最近24小时)")
        logger.info("=" * 120)

        cursor.execute("""
            SELECT
                position_side,
                COUNT(*) as count,
                ROUND(SUM(realized_pnl), 2) as total_loss,
                ROUND(AVG(realized_pnl), 2) as avg_loss,
                ROUND(AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)), 0) as avg_holding_mins
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND realized_pnl < 0
            AND account_id = 2
            GROUP BY position_side
        """)

        direction_stats = cursor.fetchall()

        for ds in direction_stats:
            logger.info(f"\n{ds['position_side']}:")
            logger.info(f"  亏损笔数: {ds['count']} 笔")
            logger.info(f"  总亏损: {ds['total_loss']:.2f} USDT")
            logger.info(f"  平均亏损: {ds['avg_loss']:.2f} USDT")
            logger.info(f"  平均持仓: {ds['avg_holding_mins']:.0f} 分钟")

        # 5. 分析开仓评分与亏损的关系
        logger.info("\n" + "=" * 120)
        logger.info("🎯 开仓评分与亏损关系分析")
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
                ROUND(SUM(realized_pnl), 2) as total_loss,
                ROUND(AVG(realized_pnl), 2) as avg_loss
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND realized_pnl < 0
            AND account_id = 2
            AND entry_score IS NOT NULL
            GROUP BY score_range
            ORDER BY
                CASE score_range
                    WHEN '低分(<20)' THEN 1
                    WHEN '中低分(20-25)' THEN 2
                    WHEN '中高分(25-30)' THEN 3
                    ELSE 4
                END
        """)

        score_analysis = cursor.fetchall()

        for sa in score_analysis:
            logger.info(f"\n{sa['score_range']}:")
            logger.info(f"  亏损笔数: {sa['count']} 笔")
            logger.info(f"  总亏损: {sa['total_loss']:.2f} USDT")
            logger.info(f"  平均亏损: {sa['avg_loss']:.2f} USDT")

        # 6. 找出"曾经盈利但最终亏损"的单子
        logger.info("\n" + "=" * 120)
        logger.info("😢 曾盈利但最终亏损的单子 (利润回吐)")
        logger.info("=" * 120)

        cursor.execute("""
            SELECT
                symbol,
                position_side,
                ROUND(max_profit_pct * 100, 2) as max_profit_pct,
                ROUND(realized_pnl, 2) as final_loss,
                TIMESTAMPDIFF(MINUTE, open_time, close_time) as holding_mins,
                notes
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND realized_pnl < 0
            AND max_profit_pct > 0.01
            AND account_id = 2
            ORDER BY max_profit_pct DESC
            LIMIT 10
        """)

        profit_giveback = cursor.fetchall()

        if profit_giveback:
            logger.info(f"找到 {len(profit_giveback)} 笔利润回吐的单子:")
            for pg in profit_giveback:
                logger.info(f"\n  {pg['symbol']} {pg['position_side']}")
                logger.info(f"    曾达最高: +{pg['max_profit_pct']:.2f}%")
                logger.info(f"    最终亏损: {pg['final_loss']:.2f} USDT")
                logger.info(f"    持仓时间: {pg['holding_mins']}分钟")
                if pg['notes']:
                    logger.info(f"    平仓原因: {pg['notes'][:80]}")
        else:
            logger.info("未发现利润回吐情况")

        # 7. 总结建议
        logger.info("\n" + "=" * 120)
        logger.info("💡 优化建议")
        logger.info("=" * 120)

        # 计算总体数据
        cursor.execute("""
            SELECT
                COUNT(*) as total_losses,
                ROUND(SUM(realized_pnl), 2) as total_loss,
                ROUND(AVG(realized_pnl), 2) as avg_loss,
                ROUND(AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)), 0) as avg_holding
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND realized_pnl < 0
            AND account_id = 2
        """)

        summary = cursor.fetchone()

        logger.info(f"\n24小时亏损总览:")
        logger.info(f"  总亏损笔数: {summary['total_losses']} 笔")
        logger.info(f"  总亏损金额: {summary['total_loss']:.2f} USDT")
        logger.info(f"  平均单笔亏损: {summary['avg_loss']:.2f} USDT")
        logger.info(f"  平均持仓时间: {summary['avg_holding']:.0f} 分钟")

        logger.info("\n关键发现:")
        logger.info("1. stop_loss_pct字段为NULL导致固定止损失效 ✅ 已修复")
        logger.info("2. 需要重点关注持仓时间>2小时的亏损单")
        logger.info("3. 高频亏损交易对应加入黑名单")
        logger.info("4. 利润回吐问题需要优化移动止盈策略")

        cursor.close()
        conn.close()

        return True

    except Exception as e:
        logger.error(f"❌ 分析失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    analyze_loss_patterns()
