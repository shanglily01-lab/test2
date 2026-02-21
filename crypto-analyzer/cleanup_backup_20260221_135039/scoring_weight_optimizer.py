#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信号评分权重优化器
根据历史交易表现，动态调整各评分组件的权重
"""

import pymysql
import json
from datetime import datetime, timedelta
from loguru import logger


class ScoringWeightOptimizer:
    """信号评分权重优化器"""

    def __init__(self, db_config: dict):
        self.db_config = db_config
        self.connection = None

    def _get_connection(self):
        """获取数据库连接"""
        if self.connection is None or not self.connection.open:
            self.connection = pymysql.connect(
                **self.db_config,
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
        else:
            try:
                self.connection.ping(reconnect=True)
            except:
                self.connection = pymysql.connect(
                    **self.db_config,
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor
                )
        return self.connection

    def analyze_component_performance(self, days: int = 7):
        """
        分析各信号组件的历史表现

        Args:
            days: 分析最近N天的数据

        Returns:
            dict: 各组件的表现统计
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 获取所有已平仓且有signal_components的订单
            cursor.execute("""
                SELECT
                    position_side,
                    signal_components,
                    realized_pnl,
                    CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END as is_win
                FROM futures_positions
                WHERE status = 'closed'
                AND close_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
                AND signal_components IS NOT NULL
                AND signal_components != ''
            """, (days,))

            positions = cursor.fetchall()
            cursor.close()

            if not positions:
                logger.warning(f"最近{days}天没有包含signal_components的订单")
                return {}

            # 统计各组件表现
            # 结构: {component_name: {side: {'total': 0, 'wins': 0, 'total_pnl': 0}}}
            component_stats = {}

            for pos in positions:
                side = pos['position_side']
                pnl = float(pos['realized_pnl'])
                is_win = pos['is_win']

                # 解析signal_components
                try:
                    components = json.loads(pos['signal_components'])
                except:
                    continue

                # 统计每个组件
                for component_name, weight in components.items():
                    if component_name not in component_stats:
                        component_stats[component_name] = {
                            'LONG': {'total': 0, 'wins': 0, 'total_pnl': 0.0, 'orders': []},
                            'SHORT': {'total': 0, 'wins': 0, 'total_pnl': 0.0, 'orders': []}
                        }

                    stats = component_stats[component_name][side]
                    stats['total'] += 1
                    stats['wins'] += is_win
                    stats['total_pnl'] += pnl
                    stats['orders'].append(pnl)

            # 计算综合指标
            component_performance = {}
            for component_name, sides in component_stats.items():
                component_performance[component_name] = {}

                for side, stats in sides.items():
                    if stats['total'] == 0:
                        continue

                    win_rate = stats['wins'] / stats['total']
                    avg_pnl = stats['total_pnl'] / stats['total']

                    # 计算表现评分 (-100 到 +100)
                    # 基于: 胜率 (60%权重) + 平均盈亏 (40%权重)
                    win_rate_score = (win_rate - 0.50) * 100  # 胜率50%为基准
                    pnl_score = avg_pnl / 5 * 100  # $5为基准，归一化到100

                    performance_score = win_rate_score * 0.6 + pnl_score * 0.4

                    component_performance[component_name][side] = {
                        'total_orders': stats['total'],
                        'win_orders': stats['wins'],
                        'win_rate': win_rate,
                        'total_pnl': stats['total_pnl'],
                        'avg_pnl': avg_pnl,
                        'performance_score': performance_score
                    }

            logger.info(f"分析了最近{days}天的 {len(positions)} 个订单")
            return component_performance

        except Exception as e:
            logger.error(f"分析组件表现失败: {e}")
            return {}

    def calculate_weight_adjustment(self, current_weight: float, performance_score: float) -> tuple:
        """
        根据表现评分计算权重调整

        Args:
            current_weight: 当前权重
            performance_score: 表现评分 (-100 到 +100)

        Returns:
            (new_weight, adjustment): 新权重和调整量
        """
        # 调整策略
        if performance_score > 10:
            adjustment = +3
        elif performance_score > 5:
            adjustment = +2
        elif performance_score < -10:
            adjustment = -3
        elif performance_score < -5:
            adjustment = -2
        else:
            adjustment = 0

        # 应用调整，限制在 5-30 范围内
        new_weight = max(5, min(30, current_weight + adjustment))

        return new_weight, adjustment

    def adjust_weights(self, dry_run: bool = True):
        """
        调整权重

        Args:
            dry_run: 如果为True，只模拟调整不实际写入数据库

        Returns:
            dict: 调整结果
        """
        try:
            # 1. 分析组件表现
            component_performance = self.analyze_component_performance(days=7)

            if not component_performance:
                logger.info("没有足够的数据进行权重调整")
                return {'adjusted': [], 'skipped': []}

            # 2. 获取当前权重
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT signal_component, weight_long, weight_short, base_weight
                FROM signal_scoring_weights
                WHERE is_active = TRUE
            """)
            current_weights = {row['signal_component']: row for row in cursor.fetchall()}

            # 3. 计算调整
            adjustments = []
            skipped = []

            for component_name, performance in component_performance.items():
                if component_name not in current_weights:
                    logger.warning(f"组件 {component_name} 不在权重表中，跳过")
                    skipped.append(component_name)
                    continue

                current = current_weights[component_name]

                # 调整LONG权重
                if 'LONG' in performance:
                    perf = performance['LONG']
                    if perf['total_orders'] >= 5:  # 至少5个订单才调整
                        new_weight, adjustment = self.calculate_weight_adjustment(
                            float(current['weight_long']),
                            perf['performance_score']
                        )

                        if adjustment != 0:
                            adjustments.append({
                                'component': component_name,
                                'side': 'LONG',
                                'old_weight': float(current['weight_long']),
                                'new_weight': new_weight,
                                'adjustment': adjustment,
                                'performance_score': perf['performance_score'],
                                'win_rate': perf['win_rate'],
                                'avg_pnl': perf['avg_pnl'],
                                'orders': perf['total_orders']
                            })

                            if not dry_run:
                                cursor.execute("""
                                    UPDATE signal_scoring_weights
                                    SET weight_long = %s,
                                        performance_score = %s,
                                        last_adjusted = NOW(),
                                        adjustment_count = adjustment_count + 1
                                    WHERE signal_component = %s
                                """, (new_weight, perf['performance_score'], component_name))

                # 调整SHORT权重
                if 'SHORT' in performance:
                    perf = performance['SHORT']
                    if perf['total_orders'] >= 5:  # 至少5个订单才调整
                        new_weight, adjustment = self.calculate_weight_adjustment(
                            float(current['weight_short']),
                            perf['performance_score']
                        )

                        if adjustment != 0:
                            adjustments.append({
                                'component': component_name,
                                'side': 'SHORT',
                                'old_weight': float(current['weight_short']),
                                'new_weight': new_weight,
                                'adjustment': adjustment,
                                'performance_score': perf['performance_score'],
                                'win_rate': perf['win_rate'],
                                'avg_pnl': perf['avg_pnl'],
                                'orders': perf['total_orders']
                            })

                            if not dry_run:
                                cursor.execute("""
                                    UPDATE signal_scoring_weights
                                    SET weight_short = %s,
                                        performance_score = %s,
                                        last_adjusted = NOW(),
                                        adjustment_count = adjustment_count + 1
                                    WHERE signal_component = %s
                                """, (new_weight, perf['performance_score'], component_name))

            # 4. 更新signal_component_performance表 (记录统计)
            if not dry_run:
                for component_name, performance in component_performance.items():
                    for side, perf in performance.items():
                        cursor.execute("""
                            INSERT INTO signal_component_performance
                            (component_name, position_side, total_orders, win_orders,
                             total_pnl, avg_pnl, win_rate, contribution_score, last_analyzed)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                            ON DUPLICATE KEY UPDATE
                                total_orders = VALUES(total_orders),
                                win_orders = VALUES(win_orders),
                                total_pnl = VALUES(total_pnl),
                                avg_pnl = VALUES(avg_pnl),
                                win_rate = VALUES(win_rate),
                                contribution_score = VALUES(contribution_score),
                                last_analyzed = NOW()
                        """, (
                            component_name, side, perf['total_orders'], perf['win_orders'],
                            perf['total_pnl'], perf['avg_pnl'], perf['win_rate'],
                            perf['performance_score']
                        ))

                conn.commit()
                logger.info(f"✅ 权重调整已保存到数据库")

            cursor.close()

            return {
                'adjusted': adjustments,
                'skipped': skipped
            }

        except Exception as e:
            logger.error(f"调整权重失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {'adjusted': [], 'skipped': [], 'error': str(e)}

    def print_adjustment_report(self, results: dict):
        """打印调整报告"""
        adjustments = results.get('adjusted', [])
        skipped = results.get('skipped', [])

        if not adjustments:
            logger.info("📊 无需调整权重")
            return

        logger.info("=" * 100)
        logger.info("📊 信号评分权重调整报告")
        logger.info("=" * 100)

        # 按调整幅度排序
        adjustments.sort(key=lambda x: abs(x['adjustment']), reverse=True)

        for adj in adjustments:
            direction = "↑" if adj['adjustment'] > 0 else "↓"
            logger.info(
                f"{direction} {adj['component']:<20} {adj['side']:<5} | "
                f"权重: {adj['old_weight']:>4.0f} → {adj['new_weight']:>4.0f} ({adj['adjustment']:+.0f}) | "
                f"表现: {adj['performance_score']:>6.1f} | "
                f"胜率: {adj['win_rate']*100:>5.1f}% | "
                f"平均: ${adj['avg_pnl']:>6.2f} | "
                f"订单: {adj['orders']}"
            )

        logger.info("=" * 100)
        logger.info(f"✅ 共调整 {len(adjustments)} 个权重")

        if skipped:
            logger.info(f"⚠️ 跳过 {len(skipped)} 个组件: {', '.join(skipped)}")


if __name__ == '__main__':
    # 测试代码
    import os
    from dotenv import load_dotenv

    load_dotenv()

    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', '3306')),
        'user': os.getenv('DB_USER', 'root'),
        'password': os.getenv('DB_PASSWORD', ''),
        'database': os.getenv('DB_NAME', 'binance-data')
    }

    optimizer = ScoringWeightOptimizer(db_config)

    # 模拟运行
    print("\n🧪 模拟运行 (dry_run=True):")
    results = optimizer.adjust_weights(dry_run=True)
    optimizer.print_adjustment_report(results)

    # 询问是否实际应用
    if results['adjusted']:
        response = input("\n是否实际应用这些调整? (yes/no): ")
        if response.lower() == 'yes':
            print("\n✅ 应用调整...")
            results = optimizer.adjust_weights(dry_run=False)
            optimizer.print_adjustment_report(results)
        else:
            print("❌ 已取消")
