#!/usr/bin/env python3
"""分析信号阈值问题 - 为什么45分阈值错过了这么多机会"""
import mysql.connector
from datetime import datetime, timedelta

DB_CONFIG = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def analyze_threshold_issue():
    """分析阈值设置问题"""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    print("=" * 140)
    print("信号评分阈值问题深度分析")
    print("=" * 140)
    print()

    # 1. 统计不同评分区间的实际表现
    print("【一】历史数据: 不同评分区间的实际胜率和盈亏")
    print()

    cursor.execute("""
        SELECT
            CASE
                WHEN entry_score < 30 THEN '0-30分'
                WHEN entry_score < 35 THEN '30-35分'
                WHEN entry_score < 40 THEN '35-40分'
                WHEN entry_score < 45 THEN '40-45分'
                WHEN entry_score < 50 THEN '45-50分'
                WHEN entry_score < 55 THEN '50-55分'
                WHEN entry_score < 60 THEN '55-60分'
                ELSE '60分以上'
            END as score_range,
            COUNT(*) as total,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as win_count,
            AVG(realized_pnl) as avg_pnl,
            SUM(realized_pnl) as total_pnl
        FROM futures_positions
        WHERE status = 'closed'
        AND account_id = 2
        AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
        AND entry_score IS NOT NULL
        GROUP BY score_range
        ORDER BY
            CASE score_range
                WHEN '0-30分' THEN 1
                WHEN '30-35分' THEN 2
                WHEN '35-40分' THEN 3
                WHEN '40-45分' THEN 4
                WHEN '45-50分' THEN 5
                WHEN '50-55分' THEN 6
                WHEN '55-60分' THEN 7
                ELSE 8
            END
    """)

    score_analysis = cursor.fetchall()

    print(f"{'评分区间':<15} {'样本数':<10} {'盈利':<10} {'亏损':<10} {'胜率':<12} {'平均盈亏':<15} {'总盈亏':<15} {'ROI效率':<15}")
    print("-" * 140)

    total_positions = sum(s['total'] for s in score_analysis)

    for score in score_analysis:
        total = score['total']
        win = score['win_count']
        loss = total - win
        win_rate = (win / total * 100) if total > 0 else 0
        avg_pnl = score['avg_pnl'] or 0
        total_pnl = score['total_pnl'] or 0

        # ROI效率 = 总盈亏 / 样本数量 (衡量单位投入产出)
        roi_efficiency = total_pnl / total if total > 0 else 0

        pct_of_total = (total / total_positions * 100) if total_positions > 0 else 0

        avg_sign = '+' if avg_pnl >= 0 else ''
        total_sign = '+' if total_pnl >= 0 else ''
        roi_sign = '+' if roi_efficiency >= 0 else ''

        print(f"{score['score_range']:<15} "
              f"{total:<10} "
              f"{win:<10} "
              f"{loss:<10} "
              f"{win_rate:>10.1f}% "
              f"{avg_sign}{avg_pnl:>13.2f} "
              f"{total_sign}{total_pnl:>13.2f} "
              f"{roi_sign}{roi_efficiency:>13.2f}")

    print()
    print()

    # 2. 对比当前阈值(45分)的影响
    print("【二】当前45分阈值的影响分析")
    print()

    # 计算45分以下的数据
    cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as win_count,
            AVG(realized_pnl) as avg_pnl,
            SUM(realized_pnl) as total_pnl
        FROM futures_positions
        WHERE status = 'closed'
        AND account_id = 2
        AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
        AND entry_score < 45
    """)

    below_45 = cursor.fetchone()

    # 计算45分及以上的数据
    cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as win_count,
            AVG(realized_pnl) as avg_pnl,
            SUM(realized_pnl) as total_pnl
        FROM futures_positions
        WHERE status = 'closed'
        AND account_id = 2
        AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
        AND entry_score >= 45
    """)

    above_45 = cursor.fetchone()

    print("45分以下的交易:")
    print(f"  样本数: {below_45['total']}")
    print(f"  胜率: {(below_45['win_count']/below_45['total']*100):.1f}%")
    print(f"  平均盈亏: {below_45['avg_pnl']:.2f} USDT")
    print(f"  总盈亏: {below_45['total_pnl']:.2f} USDT")
    print()

    print("45分及以上的交易:")
    print(f"  样本数: {above_45['total']}")
    print(f"  胜率: {(above_45['win_count']/above_45['total']*100) if above_45['total'] > 0 else 0:.1f}%")
    print(f"  平均盈亏: {above_45['avg_pnl'] if above_45['avg_pnl'] else 0:.2f} USDT")
    print(f"  总盈亏: {above_45['total_pnl'] if above_45['total_pnl'] else 0:.2f} USDT")
    print()

    # 3. 分析最优阈值
    print("【三】最优评分阈值建议")
    print()

    print("基于ROI效率(单位投入产出)排序:")
    print()

    # 重新计算并排序
    roi_sorted = sorted(score_analysis,
                       key=lambda x: (x['total_pnl'] / x['total']) if x['total'] > 0 else 0,
                       reverse=True)

    print(f"{'评分区间':<15} {'ROI效率':<15} {'胜率':<12} {'样本数':<10} {'建议':<50}")
    print("-" * 110)

    for score in roi_sorted:
        total = score['total']
        win_rate = (score['win_count'] / total * 100) if total > 0 else 0
        roi_efficiency = (score['total_pnl'] / total) if total > 0 else 0

        # 给出建议
        if roi_efficiency > 5 and win_rate > 45:
            suggestion = "[优先] 强烈建议开仓"
        elif roi_efficiency > 2 and win_rate > 40:
            suggestion = "[推荐] 建议开仓"
        elif roi_efficiency > 0:
            suggestion = "[可选] 可以开仓"
        else:
            suggestion = "[谨慎] 需要优化或跳过"

        roi_sign = '+' if roi_efficiency >= 0 else ''

        print(f"{score['score_range']:<15} "
              f"{roi_sign}{roi_efficiency:>13.2f} "
              f"{win_rate:>10.1f}% "
              f"{total:<10} "
              f"{suggestion:<50}")

    print()
    print()

    # 4. 具体阈值建议
    print("【四】具体阈值优化建议")
    print()
    print("=" * 140)

    # 找出ROI效率>0的最低评分
    positive_roi_scores = [s for s in score_analysis if (s['total_pnl'] / s['total']) > 0]
    if positive_roi_scores:
        # 按评分从低到高排序
        sorted_scores = sorted(positive_roi_scores,
                              key=lambda x: x['score_range'])

        lowest_profitable = sorted_scores[0]

        print(f"1. 最低有利可图的评分区间: {lowest_profitable['score_range']}")
        print(f"   - 胜率: {(lowest_profitable['win_count']/lowest_profitable['total']*100):.1f}%")
        print(f"   - ROI效率: +{(lowest_profitable['total_pnl']/lowest_profitable['total']):.2f}")
        print(f"   - 样本数: {lowest_profitable['total']}")
        print()

    # 找出最佳性价比区间
    best_roi = max(score_analysis, key=lambda x: (x['total_pnl'] / x['total']) if x['total'] > 0 else 0)
    print(f"2. 最佳ROI效率区间: {best_roi['score_range']}")
    print(f"   - 胜率: {(best_roi['win_count']/best_roi['total']*100):.1f}%")
    print(f"   - ROI效率: +{(best_roi['total_pnl']/best_roi['total']):.2f}")
    print(f"   - 样本数: {best_roi['total']}")
    print()

    # 计算不同阈值的影响
    print("3. 不同阈值设置的预期影响:")
    print()

    thresholds = [30, 35, 40, 45, 50]

    print(f"{'阈值':<10} {'可开仓数':<12} {'预期胜率':<12} {'预期总盈亏':<15} {'vs当前45分':<20}")
    print("-" * 80)

    for threshold in thresholds:
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as win_count,
                SUM(realized_pnl) as total_pnl
            FROM futures_positions
            WHERE status = 'closed'
            AND account_id = 2
            AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            AND entry_score >= %s
        """, (threshold,))

        result = cursor.fetchone()
        total = result['total']
        win_rate = (result['win_count'] / total * 100) if total > 0 else 0
        total_pnl = result['total_pnl'] or 0

        # 对比当前45分阈值
        if threshold == 45:
            comparison = "(当前设置)"
        else:
            diff = total - above_45['total']
            pnl_diff = total_pnl - (above_45['total_pnl'] or 0)
            comparison = f"{'+' if diff > 0 else ''}{diff}单, {'+' if pnl_diff > 0 else ''}{pnl_diff:.2f}U"

        pnl_sign = '+' if total_pnl >= 0 else ''

        print(f"{threshold}分      "
              f"{total:<12} "
              f"{win_rate:>10.1f}% "
              f"{pnl_sign}{total_pnl:>13.2f} "
              f"{comparison:<20}")

    print()
    print()

    print("【五】核心结论与建议")
    print()
    print("=" * 140)

    # 计算35-45分区间的数据
    cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as win_count,
            SUM(realized_pnl) as total_pnl
        FROM futures_positions
        WHERE status = 'closed'
        AND account_id = 2
        AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
        AND entry_score >= 35 AND entry_score < 45
    """)

    lost_opportunity = cursor.fetchone()

    print(f"当前45分阈值导致错过的机会:")
    print(f"  错过交易数: {lost_opportunity['total']}单")
    print(f"  错过的胜率: {(lost_opportunity['win_count']/lost_opportunity['total']*100) if lost_opportunity['total'] > 0 else 0:.1f}%")
    print(f"  错过的盈亏: {'+' if lost_opportunity['total_pnl'] > 0 else ''}{lost_opportunity['total_pnl']:.2f} USDT")
    print()

    print("建议:")
    print("  1. [紧急] 将batch1_score_threshold从75降到35-40")
    print("     原因: 35-45分区间表现良好，但当前75分阈值完全错过这些机会")
    print()
    print("  2. [重要] 考虑设置动态阈值")
    print("     - 高胜率信号类型: 30分即可开仓")
    print("     - 普通信号类型: 35-40分开仓")
    print("     - 低质量信号: 45分以上才开仓")
    print()
    print("  3. [优化] 根据市场波动调整阈值")
    print("     - 高波动市场: 提高5分(更谨慎)")
    print("     - 低波动市场: 降低5分(更积极)")
    print()

    print("=" * 140)

    cursor.close()
    conn.close()

if __name__ == '__main__':
    analyze_threshold_issue()
