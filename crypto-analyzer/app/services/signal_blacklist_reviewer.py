#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信号黑名单定期评估器 - 自动解除表现改善的信号
"""

from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta
from loguru import logger
import pymysql
import json


class SignalBlacklistReviewer:
    """信号黑名单定期评估器 - 实现动态升级/降级机制"""

    def __init__(self, db_config: dict):
        """
        初始化评估器

        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config
        self.connection = None

        # 解除条件配置（降低黑名单等级）
        self.upgrade_thresholds = {
            1: {  # Level 1 → Level 0（解除黑名单）
                'win_rate': 0.50,
                'min_orders': 10,
                'min_profit': 0
            },
            2: {  # Level 2 → Level 1
                'win_rate': 0.55,
                'min_orders': 15,
                'min_profit': 50
            },
            3: {  # Level 3 → Level 2
                'win_rate': 0.60,
                'min_orders': 20,
                'min_profit': 100
            }
        }

        # 降级条件配置（提高黑名单等级）
        self.downgrade_thresholds = {
            1: {  # Level 1 → Level 2
                'win_rate': 0.35,
                'max_loss': -100
            },
            2: {  # Level 2 → Level 3
                'win_rate': 0.30,
                'max_loss': -200
            }
        }

        # 评估周期配置
        self.review_periods = {
            1: 7,   # Level 1: 7天评估一次
            2: 14,  # Level 2: 14天评估一次
            3: 30   # Level 3: 30天评估一次
        }

        # 最大重试次数
        self.max_retry_count = 3

        logger.info("✅ 信号黑名单评估器已初始化")

    def _get_connection(self):
        """获取数据库连接"""
        if self.connection is None or not self.connection.open:
            self.connection = pymysql.connect(
                host=self.db_config.get('host', 'localhost'),
                port=self.db_config.get('port', 3306),
                user=self.db_config.get('user', 'root'),
                password=self.db_config.get('password', ''),
                database=self.db_config.get('database', 'binance-data'),
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
        return self.connection

    def review_all_blacklisted_signals(self) -> Dict:
        """
        评估所有黑名单信号

        Returns:
            评估结果统计
        """
        logger.info("=" * 80)
        logger.info("🔍 开始评估信号黑名单...")
        logger.info("=" * 80)

        conn = self._get_connection()
        cursor = conn.cursor()

        # 查询所有激活的黑名单信号
        cursor.execute("""
            SELECT *
            FROM signal_blacklist
            WHERE is_active = 1
        """)

        blacklist_signals = cursor.fetchall()

        results = {
            'total': len(blacklist_signals),
            'upgraded': [],     # 降低黑名单等级（变好）
            'downgraded': [],   # 提高黑名单等级（变差）
            'removed': [],      # 完全移除黑名单
            'unchanged': [],
            'skipped': []       # 跳过评估（未到评估周期）
        }

        for signal_info in blacklist_signals:
            signal_type = signal_info['signal_type']
            position_side = signal_info['position_side']
            current_level = signal_info.get('blacklist_level') or 3
            last_reviewed = signal_info.get('last_reviewed_at')
            retry_count = signal_info.get('retry_count') or 0

            # 检查是否到达评估周期
            if not self._should_review(current_level, last_reviewed):
                results['skipped'].append({
                    'signal': signal_type,
                    'side': position_side,
                    'level': current_level,
                    'reason': f'未到评估周期（L{current_level}每{self.review_periods[current_level]}天）'
                })
                continue

            # 检查重试次数
            if retry_count >= self.max_retry_count:
                logger.warning(
                    f"⚠️ {signal_type} {position_side} 已重试{retry_count}次，"
                    f"超过最大次数{self.max_retry_count}，永久禁用"
                )
                results['unchanged'].append({
                    'signal': signal_type,
                    'side': position_side,
                    'level': current_level,
                    'reason': f'已重试{retry_count}次，永久禁用'
                })
                continue

            # 查询该信号最近的表现
            performance = self._get_signal_recent_performance(
                cursor, signal_type, position_side, days=30
            )

            if not performance or performance['order_count'] < 5:
                results['unchanged'].append({
                    'signal': signal_type,
                    'side': position_side,
                    'level': current_level,
                    'reason': '数据不足（<5单），暂不调整'
                })
                # 更新last_reviewed_at
                self._update_last_reviewed(cursor, signal_info['id'])
                continue

            # 评估是否应该升级/降级/移除
            action, new_level, reason = self._evaluate_signal_level(
                current_level, performance
            )

            if action == 'upgrade':
                # 降低黑名单等级（变好）
                self._update_blacklist_level(
                    cursor, signal_info['id'], new_level, reason, performance
                )
                results['upgraded'].append({
                    'signal': signal_type,
                    'side': position_side,
                    'old_level': current_level,
                    'new_level': new_level,
                    'reason': reason,
                    'performance': performance
                })
                logger.info(
                    f"📈 降低黑名单等级: {signal_type[:50]} {position_side} | "
                    f"L{current_level}→L{new_level} | {reason}"
                )

            elif action == 'downgrade':
                # 提高黑名单等级（变差）
                self._update_blacklist_level(
                    cursor, signal_info['id'], new_level, reason, performance
                )
                results['downgraded'].append({
                    'signal': signal_type,
                    'side': position_side,
                    'old_level': current_level,
                    'new_level': new_level,
                    'reason': reason,
                    'performance': performance
                })
                logger.warning(
                    f"📉 提高黑名单等级: {signal_type[:50]} {position_side} | "
                    f"L{current_level}→L{new_level} | {reason}"
                )

            elif action == 'remove':
                # 完全移除黑名单（设为is_active=0）
                # 但保留记录，如果再次触发黑名单，retry_count+1
                cursor.execute("""
                    UPDATE signal_blacklist
                    SET is_active = 0,
                        blacklist_level = 0,
                        last_reviewed_at = NOW(),
                        updated_at = NOW(),
                        notes = CONCAT(IFNULL(notes, ''), ' | [', NOW(), '] 解除黑名单: ', %s)
                    WHERE id = %s
                """, (reason, signal_info['id']))

                results['removed'].append({
                    'signal': signal_type,
                    'side': position_side,
                    'reason': reason,
                    'performance': performance
                })
                logger.info(
                    f"✅ 解除黑名单: {signal_type[:50]} {position_side} | {reason}"
                )

            else:
                # 无变化，只更新评估时间
                self._update_last_reviewed(cursor, signal_info['id'])
                results['unchanged'].append({
                    'signal': signal_type,
                    'side': position_side,
                    'level': current_level,
                    'reason': '表现平稳，维持当前等级'
                })

        conn.commit()
        cursor.close()

        logger.info("=" * 80)
        logger.info("🔍 信号黑名单评估完成")
        logger.info("=" * 80)
        logger.info(f"总计: {results['total']}")
        logger.info(f"✅ 解除黑名单: {len(results['removed'])}")
        logger.info(f"📈 降低等级（变好）: {len(results['upgraded'])}")
        logger.info(f"📉 提高等级（变差）: {len(results['downgraded'])}")
        logger.info(f"⏸️ 跳过评估: {len(results['skipped'])}")
        logger.info(f"➖ 无变化: {len(results['unchanged'])}")

        return results

    def _should_review(self, level: int, last_reviewed: Optional[datetime]) -> bool:
        """
        判断是否应该评估

        Args:
            level: 当前黑名单等级
            last_reviewed: 上次评估时间

        Returns:
            是否应该评估
        """
        if last_reviewed is None:
            return True

        review_period = self.review_periods.get(level, 7)
        next_review_date = last_reviewed + timedelta(days=review_period)

        return datetime.now() >= next_review_date

    def _get_signal_recent_performance(
        self, cursor, signal_type: str, position_side: str, days: int = 30
    ) -> Optional[Dict]:
        """
        查询信号最近的表现

        Args:
            cursor: 数据库游标
            signal_type: 信号类型
            position_side: 持仓方向
            days: 查询天数

        Returns:
            表现统计字典
        """
        cutoff_date = datetime.now() - timedelta(days=days)

        cursor.execute("""
            SELECT
                COUNT(*) as order_count,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(realized_pnl) as total_pnl,
                AVG(realized_pnl) as avg_pnl
            FROM futures_positions
            WHERE entry_signal_type = %s
            AND position_side = %s
            AND status = 'CLOSED'
            AND close_time >= %s
        """, (signal_type, position_side, cutoff_date))

        result = cursor.fetchone()

        if not result or result['order_count'] == 0:
            return None

        return {
            'order_count': result['order_count'],
            'win_rate': result['wins'] / result['order_count'],
            'total_pnl': float(result['total_pnl'] or 0),
            'avg_pnl': float(result['avg_pnl'] or 0)
        }

    def _evaluate_signal_level(
        self, current_level: int, performance: Dict
    ) -> Tuple[str, int, str]:
        """
        评估信号应该升级/降级/移除/不变

        Args:
            current_level: 当前黑名单等级
            performance: 表现统计

        Returns:
            (action, new_level, reason)
            action: 'upgrade' | 'downgrade' | 'remove' | 'unchanged'
        """
        win_rate = performance['win_rate']
        order_count = performance['order_count']
        total_pnl = performance['total_pnl']

        # 检查是否应该升级（降低黑名单等级，变好）
        if current_level > 0:
            threshold = self.upgrade_thresholds.get(current_level)
            if threshold:
                if (win_rate >= threshold['win_rate'] and
                    order_count >= threshold['min_orders'] and
                    total_pnl >= threshold['min_profit']):

                    new_level = current_level - 1
                    reason = (
                        f"表现改善: 胜率{win_rate*100:.1f}%, "
                        f"盈利{total_pnl:.2f}U, {order_count}单"
                    )

                    if new_level == 0:
                        return ('remove', 0, reason)
                    else:
                        return ('upgrade', new_level, reason)

        # 检查是否应该降级（提高黑名单等级，变差）
        if current_level < 3:
            threshold = self.downgrade_thresholds.get(current_level)
            if threshold:
                if (win_rate < threshold['win_rate'] or
                    total_pnl < threshold['max_loss']):

                    new_level = current_level + 1
                    reason = (
                        f"表现恶化: 胜率{win_rate*100:.1f}%, "
                        f"亏损{total_pnl:.2f}U"
                    )
                    return ('downgrade', new_level, reason)

        return ('unchanged', current_level, '表现平稳')

    def _update_blacklist_level(
        self, cursor, blacklist_id: int, new_level: int,
        reason: str, performance: Dict
    ):
        """
        更新黑名单等级

        Args:
            cursor: 数据库游标
            blacklist_id: 黑名单记录ID
            new_level: 新等级
            reason: 调整原因
            performance: 表现统计
        """
        # 保存表现数据到JSON
        performance_json = json.dumps(performance, ensure_ascii=False)

        cursor.execute("""
            UPDATE signal_blacklist
            SET blacklist_level = %s,
                last_reviewed_at = NOW(),
                review_period_days = %s,
                performance_json = %s,
                updated_at = NOW(),
                notes = CONCAT(IFNULL(notes, ''), ' | [', NOW(), '] ', %s)
            WHERE id = %s
        """, (
            new_level,
            self.review_periods.get(new_level, 7),
            performance_json,
            reason,
            blacklist_id
        ))

    def _update_last_reviewed(self, cursor, blacklist_id: int):
        """
        更新最后评估时间

        Args:
            cursor: 数据库游标
            blacklist_id: 黑名单记录ID
        """
        cursor.execute("""
            UPDATE signal_blacklist
            SET last_reviewed_at = NOW()
            WHERE id = %s
        """, (blacklist_id,))

    def print_review_report(self, results: Dict):
        """打印评估报告"""
        print("\n" + "=" * 100)
        print("🔍 信号黑名单评估报告")
        print("=" * 100)

        print(f"\n📊 总计: {results['total']} 条黑名单记录")

        if results['removed']:
            print(f"\n✅ 解除黑名单 ({len(results['removed'])} 条):")
            for item in results['removed']:
                perf = item['performance']
                print(f"\n  信号: {item['signal'][:70]}")
                print(f"  方向: {item['side']}")
                print(f"  原因: {item['reason']}")
                print(f"  表现: 胜率{perf['win_rate']*100:.1f}% | "
                      f"盈利{perf['total_pnl']:.2f}U | {perf['order_count']}单")

        if results['upgraded']:
            print(f"\n📈 降低黑名单等级 ({len(results['upgraded'])} 条):")
            for item in results['upgraded']:
                perf = item['performance']
                print(f"\n  信号: {item['signal'][:70]}")
                print(f"  方向: {item['side']}")
                print(f"  等级: L{item['old_level']} → L{item['new_level']}")
                print(f"  原因: {item['reason']}")
                print(f"  表现: 胜率{perf['win_rate']*100:.1f}% | "
                      f"盈利{perf['total_pnl']:.2f}U | {perf['order_count']}单")

        if results['downgraded']:
            print(f"\n📉 提高黑名单等级 ({len(results['downgraded'])} 条):")
            for item in results['downgraded']:
                perf = item['performance']
                print(f"\n  信号: {item['signal'][:70]}")
                print(f"  方向: {item['side']}")
                print(f"  等级: L{item['old_level']} → L{item['new_level']}")
                print(f"  原因: {item['reason']}")
                print(f"  表现: 胜率{perf['win_rate']*100:.1f}% | "
                      f"亏损{perf['total_pnl']:.2f}U | {perf['order_count']}单")

        if results['skipped']:
            print(f"\n⏸️ 跳过评估 ({len(results['skipped'])} 条): 未到评估周期")

        if results['unchanged']:
            print(f"\n➖ 无变化 ({len(results['unchanged'])} 条): 表现平稳或数据不足")

        print("\n" + "=" * 100)

    def close(self):
        """关闭数据库连接"""
        if self.connection:
            self.connection.close()


def main():
    """测试主函数"""
    import os
    from dotenv import load_dotenv

    load_dotenv()

    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 3306)),
        'user': os.getenv('DB_USER', 'root'),
        'password': os.getenv('DB_PASSWORD', ''),
        'database': os.getenv('DB_NAME', 'binance-data')
    }

    reviewer = SignalBlacklistReviewer(db_config)
    results = reviewer.review_all_blacklisted_signals()
    reviewer.print_review_report(results)
    reviewer.close()


if __name__ == '__main__':
    main()
