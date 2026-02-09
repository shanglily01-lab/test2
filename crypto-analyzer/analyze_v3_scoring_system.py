#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V3评分系统全面分析
分析评分的有效性、各维度贡献、盈亏关系
"""

import os
import pymysql
from datetime import datetime, timedelta
from dotenv import load_dotenv
from loguru import logger
import json

load_dotenv()

def analyze_v3_scoring_system():
    """全面分析V3评分系统"""

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
        logger.info("🔍 V3评分系统全面分析 (最近24小时)")
        logger.info("=" * 120)

        # 1. 评分分布统计
        logger.info("\n📊 评分分布统计")
        logger.info("-" * 120)

        cursor.execute("""
            SELECT
                FLOOR(entry_score / 5) * 5 as score_bucket,
                COUNT(*) as count,
                COUNT(CASE WHEN realized_pnl > 0 THEN 1 END) as wins,
                ROUND(COUNT(CASE WHEN realized_pnl > 0 THEN 1 END) / COUNT(*) * 100, 2) as win_rate,
                ROUND(SUM(realized_pnl), 2) as total_pnl,
                ROUND(AVG(realized_pnl), 2) as avg_pnl,
                ROUND(AVG(entry_score), 2) as avg_score
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND signal_version = 'v3'
            AND account_id = 2
            GROUP BY score_bucket
            ORDER BY score_bucket
        """)

        score_distribution = cursor.fetchall()

        logger.info("\n评分段 | 笔数 | 胜率 | 平均评分 | 总盈亏 | 平均盈亏")
        logger.info("-" * 120)
        for sd in score_distribution:
            bucket_start = int(sd['score_bucket'])
            bucket_end = bucket_start + 4
            logger.info(f"{bucket_start:2d}-{bucket_end:2d}分 | {sd['count']:3d}笔 | "
                       f"{sd['win_rate']:5.1f}% | {sd['avg_score']:5.2f} | "
                       f"{sd['total_pnl']:+8.2f}U | {sd['avg_pnl']:+7.2f}U")

        # 2. 评分与盈亏的相关性分析
        logger.info("\n" + "=" * 120)
        logger.info("📈 评分与盈亏相关性分析")
        logger.info("-" * 120)

        cursor.execute("""
            SELECT
                ROUND(AVG(CASE WHEN realized_pnl > 0 THEN entry_score END), 2) as avg_score_win,
                ROUND(AVG(CASE WHEN realized_pnl <= 0 THEN entry_score END), 2) as avg_score_loss,
                ROUND(STDDEV(entry_score), 2) as score_stddev,
                ROUND(MIN(entry_score), 2) as min_score,
                ROUND(MAX(entry_score), 2) as max_score
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND signal_version = 'v3'
            AND account_id = 2
        """)

        correlation = cursor.fetchone()

        score_diff = correlation['avg_score_win'] - correlation['avg_score_loss']

        logger.info(f"盈利单平均评分: {correlation['avg_score_win']:.2f}")
        logger.info(f"亏损单平均评分: {correlation['avg_score_loss']:.2f}")
        logger.info(f"评分差异: {score_diff:+.2f} (盈利-亏损)")
        logger.info(f"评分标准差: {correlation['score_stddev']:.2f}")
        logger.info(f"评分范围: [{correlation['min_score']:.2f}, {correlation['max_score']:.2f}]")

        # 判断评分有效性
        if score_diff >= 2.0:
            logger.info("\n✅ 评分系统有效性: 优秀 (差异≥2分)")
        elif score_diff >= 1.0:
            logger.info("\n⚠️ 评分系统有效性: 一般 (差异1-2分)")
        else:
            logger.warning("\n❌ 评分系统有效性: 较差 (差异<1分)")

        # 3. 各评分维度分析
        logger.info("\n" + "=" * 120)
        logger.info("🎯 各评分维度贡献分析")
        logger.info("-" * 120)

        cursor.execute("""
            SELECT
                signal_components,
                realized_pnl,
                position_side
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND signal_version = 'v3'
            AND signal_components IS NOT NULL
            AND signal_components != ''
            AND account_id = 2
        """)

        positions = cursor.fetchall()

        # 统计各维度得分
        dimensions = {
            'big4': {'wins': [], 'losses': []},
            '5h_trend': {'wins': [], 'losses': []},
            '15m_signal': {'wins': [], 'losses': []},
            'volume_price': {'wins': [], 'losses': []},
            'technical': {'wins': [], 'losses': []}
        }

        for pos in positions:
            try:
                components = json.loads(pos['signal_components'])
                is_win = pos['realized_pnl'] > 0

                for dim in dimensions.keys():
                    if dim in components:
                        score = float(components[dim])
                        if is_win:
                            dimensions[dim]['wins'].append(score)
                        else:
                            dimensions[dim]['losses'].append(score)
            except:
                continue

        logger.info("\n维度 | 盈利单均分 | 亏损单均分 | 差异 | 有效性")
        logger.info("-" * 120)

        for dim, data in dimensions.items():
            if data['wins'] and data['losses']:
                avg_win = sum(data['wins']) / len(data['wins'])
                avg_loss = sum(data['losses']) / len(data['losses'])
                diff = avg_win - avg_loss

                effectiveness = "✅ 有效" if diff > 0.3 else "⚠️ 一般" if diff > 0.1 else "❌ 无效"

                logger.info(f"{dim:15s} | {avg_win:10.2f} | {avg_loss:10.2f} | {diff:+6.2f} | {effectiveness}")

        # 4. 高分单vs低分单对比
        logger.info("\n" + "=" * 120)
        logger.info("⚖️ 高分单(≥25分) vs 低分单(<25分)对比")
        logger.info("-" * 120)

        for threshold in [25]:
            cursor.execute("""
                SELECT
                    CASE WHEN entry_score >= %s THEN '高分(≥25)' ELSE '低分(<25)' END as category,
                    COUNT(*) as count,
                    COUNT(CASE WHEN realized_pnl > 0 THEN 1 END) as wins,
                    ROUND(COUNT(CASE WHEN realized_pnl > 0 THEN 1 END) / COUNT(*) * 100, 2) as win_rate,
                    ROUND(AVG(entry_score), 2) as avg_score,
                    ROUND(SUM(realized_pnl), 2) as total_pnl,
                    ROUND(AVG(realized_pnl), 2) as avg_pnl
                FROM futures_positions
                WHERE status = 'closed'
                AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
                AND signal_version = 'v3'
                AND account_id = 2
                GROUP BY category
                ORDER BY avg_score DESC
            """, (threshold,))

            comparison = cursor.fetchall()

            for comp in comparison:
                logger.info(f"\n{comp['category']}:")
                logger.info(f"  交易笔数: {comp['count']} 笔")
                logger.info(f"  胜率: {comp['win_rate']}%")
                logger.info(f"  平均评分: {comp['avg_score']}")
                logger.info(f"  总盈亏: {comp['total_pnl']:+.2f} USDT")
                logger.info(f"  平均盈亏: {comp['avg_pnl']:+.2f} USDT")

        # 5. 最佳评分区间推荐
        logger.info("\n" + "=" * 120)
        logger.info("💡 最佳评分阈值推荐")
        logger.info("-" * 120)

        thresholds_to_test = [18, 20, 22, 24, 26, 28, 30]

        logger.info("\n阈值 | 笔数 | 胜率 | 总盈亏 | 平均盈亏 | 建议")
        logger.info("-" * 120)

        best_threshold = None
        best_total_pnl = -float('inf')

        for threshold in thresholds_to_test:
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
                AND signal_version = 'v3'
                AND entry_score >= %s
                AND account_id = 2
            """, (threshold,))

            result = cursor.fetchone()

            if result['count'] > 0:
                # 综合评估: 总盈亏为主,胜率为辅
                recommendation = ""
                if result['total_pnl'] > best_total_pnl and result['count'] >= 10:
                    best_total_pnl = result['total_pnl']
                    best_threshold = threshold
                    recommendation = "⭐ 最佳"
                elif result['win_rate'] >= 70 and result['total_pnl'] > 0:
                    recommendation = "✅ 推荐"
                elif result['total_pnl'] > 0:
                    recommendation = "👍  可用"
                else:
                    recommendation = "❌ 不推荐"

                logger.info(f"{threshold:2d}分 | {result['count']:3d}笔 | "
                           f"{result['win_rate']:5.1f}% | {result['total_pnl']:+8.2f}U | "
                           f"{result['avg_pnl']:+7.2f}U | {recommendation}")

        # 6. 问题交易对分析
        logger.info("\n" + "=" * 120)
        logger.info("🔍 评分异常交易对分析 (高分低效)")
        logger.info("-" * 120)

        cursor.execute("""
            SELECT
                symbol,
                COUNT(*) as count,
                ROUND(AVG(entry_score), 2) as avg_score,
                COUNT(CASE WHEN realized_pnl > 0 THEN 1 END) as wins,
                ROUND(COUNT(CASE WHEN realized_pnl > 0 THEN 1 END) / COUNT(*) * 100, 2) as win_rate,
                ROUND(SUM(realized_pnl), 2) as total_pnl
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND signal_version = 'v3'
            AND entry_score >= 23
            AND account_id = 2
            GROUP BY symbol
            HAVING total_pnl < -30
            ORDER BY avg_score DESC
        """)

        anomalies = cursor.fetchall()

        if anomalies:
            logger.info("\n高分但亏损的交易对 (评分≥23但亏损>30U):")
            for ano in anomalies:
                logger.info(f"  {ano['symbol']}: {ano['count']}笔 | 平均{ano['avg_score']:.1f}分 | "
                           f"胜率{ano['win_rate']:.1f}% | 亏损{ano['total_pnl']:.2f}U")
        else:
            logger.info("\n✅ 未发现高分低效的异常交易对")

        # 7. 总结建议
        logger.info("\n" + "=" * 120)
        logger.info("📋 评分系统诊断报告")
        logger.info("=" * 120)

        cursor.execute("""
            SELECT
                COUNT(*) as total,
                ROUND(AVG(entry_score), 2) as avg_score,
                COUNT(CASE WHEN realized_pnl > 0 THEN 1 END) as wins,
                ROUND(COUNT(CASE WHEN realized_pnl > 0 THEN 1 END) / COUNT(*) * 100, 2) as win_rate,
                ROUND(SUM(realized_pnl), 2) as total_pnl
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            AND signal_version = 'v3'
            AND account_id = 2
        """)

        summary = cursor.fetchone()

        logger.info(f"\n24小时V3交易总览:")
        logger.info(f"  总交易: {summary['total']} 笔")
        logger.info(f"  平均评分: {summary['avg_score']:.2f}")
        logger.info(f"  胜率: {summary['win_rate']:.2f}%")
        logger.info(f"  总盈亏: {summary['total_pnl']:+.2f} USDT")

        logger.info(f"\n评分系统有效性:")
        logger.info(f"  盈利单vs亏损单评分差异: {score_diff:+.2f}分")
        if score_diff >= 2.0:
            logger.info(f"  结论: ✅ 评分系统有效,有较好区分度")
        elif score_diff >= 1.0:
            logger.info(f"  结论: ⚠️ 评分系统一般,区分度不足")
        else:
            logger.info(f"  结论: ❌ 评分系统较差,需要优化")

        if best_threshold:
            logger.info(f"\n推荐评分阈值: {best_threshold}分")
            logger.info(f"  该阈值下总盈亏: {best_total_pnl:+.2f} USDT")

        logger.info("\n优化建议:")
        if score_diff < 1.0:
            logger.info("  1. 评分系统区分度不足,建议调整权重")
            logger.info("  2. 考虑增加新的评分维度(如位置评分)")
            logger.info("  3. 分析高分低效交易对,识别评分盲区")

        if summary['win_rate'] < 60:
            logger.info("  4. 胜率偏低,考虑提高开仓阈值")

        if summary['total_pnl'] < 0:
            logger.info("  5. 整体亏损,需要全面审查评分逻辑")

        cursor.close()
        conn.close()

        return True

    except Exception as e:
        logger.error(f"❌ 分析失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    analyze_v3_scoring_system()
