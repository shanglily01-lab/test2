#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动参数优化器 - Auto Parameter Optimizer

基于复盘结果自动优化信号检测参数

Author: Claude
Date: 2026-01-26
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from loguru import logger
import pymysql
import json
from app.database.connection_pool import get_global_pool


class AutoParameterOptimizer:
    """自动参数优化器"""

    def __init__(self, db_config: dict):
        """
        初始化优化器

        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config
        self.db_pool = get_global_pool(db_config, pool_size=5)

        # 当前参数（初始值）
        self.current_params = {
            'BOTTOM_REVERSAL_LONG': {
                'min_score_threshold': 50,  # 最低评分阈值
                'min_hammer_count': 3,      # 最少锤头线数量
                'min_volume_ratio': 2.0,    # 最小成交量倍数
                'min_lower_shadow_pct': 0.6  # 最小下影线百分比
            },
            'WEAK_RALLY_SHORT': {
                'min_score_threshold': 50,
                'min_drop_pct': 0.4,         # 最小下跌百分比
                'min_volume_ratio': 2.0,
                'weak_rally_hours': 6        # 上涨无力小时数
            },
            'batch_entry': {
                'batch1_score_threshold': 80,  # 第1批评分阈值
                'batch1_timeout_minutes': 15,  # 第1批超时
                'sampling_window_seconds': 300  # 采样窗口（5分钟）
            },
            'batch_exit': {
                'batch1_score_threshold': 95,  # 第1批平仓评分
                'batch2_score_threshold': 95,
                'monitoring_window_minutes': 30
            }
        }

        # 参数调整历史
        self.adjustment_history = []

        logger.info("✅ 自动参数优化器已初始化")


    async def optimize_based_on_review(self, review_date: str) -> Dict[str, any]:
        """
        基于复盘结果优化参数

        Args:
            review_date: 复盘日期 (YYYY-MM-DD)

        Returns:
            优化结果
        """
        logger.info(f"🔧 开始自动参数优化 | 基于日期: {review_date}")

        # 1. 加载复盘报告
        report = await self._load_review_report(review_date)

        if not report:
            logger.warning(f"未找到复盘报告: {review_date}")
            return {'success': False, 'error': '未找到复盘报告'}

        # 2. 分析性能指标
        metrics = self._analyze_performance_metrics(report)

        # 3. 决定参数调整
        adjustments = self._decide_adjustments(metrics, report)

        # 4. 应用调整
        if adjustments:
            await self._apply_adjustments(adjustments)

            logger.info(f"✅ 参数优化完成 | 调整了{len(adjustments)}个参数")
        else:
            logger.info("ℹ️  当前参数表现良好，无需调整")

        return {
            'success': True,
            'metrics': metrics,
            'adjustments': adjustments,
            'current_params': self.current_params
        }

    async def _load_review_report(self, review_date: str) -> Optional[Dict]:
        """
        加载复盘报告

        Args:
            review_date: 复盘日期

        Returns:
            复盘报告字典
        """
        with self.db_pool.get_connection() as conn:
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            cursor.execute("""
                SELECT report_json
                FROM daily_review_reports
                WHERE date = %s
            """, (review_date,))

            result = cursor.fetchone()
            cursor.close()

            if result:
                return json.loads(result['report_json'])

            return None

    def _analyze_performance_metrics(self, report: Dict) -> Dict[str, float]:
        """
        分析性能指标

        Args:
            report: 复盘报告

        Returns:
            性能指标字典
        """
        metrics = {
            'capture_rate': report['capture_rate'],
            'total_opportunities': report['total_opportunities'],
            'missed_count': report['missed_count']
        }

        # 分析错过的机会类型
        missed_opportunities = report.get('missed_opportunities', [])

        missed_by_type = {'pump': 0, 'dump': 0}
        missed_by_timeframe = {'5m': 0, '15m': 0, '1h': 0}

        for opp in missed_opportunities:
            missed_by_type[opp['move_type']] = missed_by_type.get(opp['move_type'], 0) + 1
            missed_by_timeframe[opp['timeframe']] = missed_by_timeframe.get(opp['timeframe'], 0) + 1

        metrics['missed_pumps'] = missed_by_type.get('pump', 0)
        metrics['missed_dumps'] = missed_by_type.get('dump', 0)
        metrics['missed_5m'] = missed_by_timeframe.get('5m', 0)
        metrics['missed_15m'] = missed_by_timeframe.get('15m', 0)
        metrics['missed_1h'] = missed_by_timeframe.get('1h', 0)

        # 分析信号表现
        signal_performances = report.get('signal_performances', [])

        for perf in signal_performances:
            signal_type = perf['signal_type']
            metrics[f'{signal_type}_win_rate'] = perf['win_rate']
            metrics[f'{signal_type}_avg_pnl'] = perf['avg_pnl_pct']

        return metrics

    def _decide_adjustments(self, metrics: Dict[str, float], report: Dict) -> List[Dict]:
        """
        决定参数调整

        Args:
            metrics: 性能指标
            report: 复盘报告

        Returns:
            调整列表
        """
        adjustments = []

        # 规则1: 如果捕获率 < 60%，降低所有阈值
        if metrics['capture_rate'] < 60:
            adjustments.append({
                'param_group': 'BOTTOM_REVERSAL_LONG',
                'param_name': 'min_score_threshold',
                'old_value': self.current_params['BOTTOM_REVERSAL_LONG']['min_score_threshold'],
                'new_value': max(40, self.current_params['BOTTOM_REVERSAL_LONG']['min_score_threshold'] - 5),
                'reason': f'捕获率过低({metrics["capture_rate"]:.1f}%)'
            })

            adjustments.append({
                'param_group': 'WEAK_RALLY_SHORT',
                'param_name': 'min_score_threshold',
                'old_value': self.current_params['WEAK_RALLY_SHORT']['min_score_threshold'],
                'new_value': max(40, self.current_params['WEAK_RALLY_SHORT']['min_score_threshold'] - 5),
                'reason': f'捕获率过低({metrics["capture_rate"]:.1f}%)'
            })

        # 规则2: 如果错过的pump机会 >= 5个，降低BOTTOM_REVERSAL_LONG阈值
        if metrics.get('missed_pumps', 0) >= 5:
            adjustments.append({
                'param_group': 'BOTTOM_REVERSAL_LONG',
                'param_name': 'min_score_threshold',
                'old_value': self.current_params['BOTTOM_REVERSAL_LONG']['min_score_threshold'],
                'new_value': max(40, self.current_params['BOTTOM_REVERSAL_LONG']['min_score_threshold'] - 5),
                'reason': f'错过{metrics["missed_pumps"]}个上涨机会'
            })

            adjustments.append({
                'param_group': 'BOTTOM_REVERSAL_LONG',
                'param_name': 'min_hammer_count',
                'old_value': self.current_params['BOTTOM_REVERSAL_LONG']['min_hammer_count'],
                'new_value': max(2, self.current_params['BOTTOM_REVERSAL_LONG']['min_hammer_count'] - 1),
                'reason': f'降低锤头线要求以提高捕获率'
            })

        # 规则3: 如果错过的dump机会 >= 5个，降低WEAK_RALLY_SHORT阈值
        if metrics.get('missed_dumps', 0) >= 5:
            adjustments.append({
                'param_group': 'WEAK_RALLY_SHORT',
                'param_name': 'min_score_threshold',
                'old_value': self.current_params['WEAK_RALLY_SHORT']['min_score_threshold'],
                'new_value': max(40, self.current_params['WEAK_RALLY_SHORT']['min_score_threshold'] - 5),
                'reason': f'错过{metrics["missed_dumps"]}个下跌机会'
            })

            adjustments.append({
                'param_group': 'WEAK_RALLY_SHORT',
                'param_name': 'min_drop_pct',
                'old_value': self.current_params['WEAK_RALLY_SHORT']['min_drop_pct'],
                'new_value': max(0.3, self.current_params['WEAK_RALLY_SHORT']['min_drop_pct'] - 0.05),
                'reason': f'降低下跌幅度要求'
            })

        # 规则4: 如果某个信号胜率 < 45%，提高该信号的阈值（收紧）
        for perf in report.get('signal_performances', []):
            win_rate = float(perf['win_rate']) if perf.get('win_rate') is not None else 0
            total_trades = int(perf.get('total_trades', 0))
            if win_rate < 45 and total_trades >= 10:
                signal_type = perf['signal_type']

                if signal_type == 'BOTTOM_REVERSAL_LONG':
                    adjustments.append({
                        'param_group': 'BOTTOM_REVERSAL_LONG',
                        'param_name': 'min_score_threshold',
                        'old_value': self.current_params['BOTTOM_REVERSAL_LONG']['min_score_threshold'],
                        'new_value': min(70, self.current_params['BOTTOM_REVERSAL_LONG']['min_score_threshold'] + 5),
                        'reason': f'{signal_type}胜率过低({win_rate:.1f}%)，收紧条件'
                    })

                elif signal_type == 'WEAK_RALLY_SHORT':
                    adjustments.append({
                        'param_group': 'WEAK_RALLY_SHORT',
                        'param_name': 'min_score_threshold',
                        'old_value': self.current_params['WEAK_RALLY_SHORT']['min_score_threshold'],
                        'new_value': min(70, self.current_params['WEAK_RALLY_SHORT']['min_score_threshold'] + 5),
                        'reason': f'{signal_type}胜率过低({win_rate:.1f}%)，收紧条件'
                    })

        # 规则5: 如果5M机会很多但错过率高，降低第1批建仓阈值
        if metrics.get('missed_5m', 0) >= 8:
            adjustments.append({
                'param_group': 'batch_entry',
                'param_name': 'batch1_score_threshold',
                'old_value': self.current_params['batch_entry']['batch1_score_threshold'],
                'new_value': max(70, self.current_params['batch_entry']['batch1_score_threshold'] - 5),
                'reason': f'错过{metrics["missed_5m"]}个5M机会，加快建仓速度'
            })

        # 去重（同一参数只保留最后一次调整）
        unique_adjustments = {}
        for adj in adjustments:
            key = f"{adj['param_group']}.{adj['param_name']}"
            unique_adjustments[key] = adj

        return list(unique_adjustments.values())

    async def _apply_adjustments(self, adjustments: List[Dict]):
        """
        应用参数调整

        Args:
            adjustments: 调整列表
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # 创建参数调整历史表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS parameter_adjustments (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    adjustment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    param_group VARCHAR(100),
                    param_name VARCHAR(100),
                    old_value VARCHAR(100),
                    new_value VARCHAR(100),
                    reason TEXT,
                    applied BOOLEAN DEFAULT TRUE
                )
            """)

            for adj in adjustments:
                # 更新内存中的参数
                if adj['param_group'] in self.current_params:
                    if adj['param_name'] in self.current_params[adj['param_group']]:
                        self.current_params[adj['param_group']][adj['param_name']] = adj['new_value']

                # 记录到数据库
                cursor.execute("""
                    INSERT INTO parameter_adjustments
                    (param_group, param_name, old_value, new_value, reason)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    adj['param_group'],
                    adj['param_name'],
                    str(adj['old_value']),
                    str(adj['new_value']),
                    adj['reason']
                ))

                logger.info(
                    f"✏️  调整参数: {adj['param_group']}.{adj['param_name']} | "
                    f"{adj['old_value']} -> {adj['new_value']} | "
                    f"原因: {adj['reason']}"
                )

            conn.commit()

            # 保存当前参数到文件（可选）
            self._save_params_to_file()

        except Exception as e:
            logger.error(f"应用参数调整失败: {e}")
            conn.rollback()
        finally:
            cursor.close()

    def _save_params_to_file(self):
        """保存参数到文件"""
        import yaml

        try:
            with open('optimized_params.yaml', 'w', encoding='utf-8') as f:
                yaml.dump(self.current_params, f, allow_unicode=True, default_flow_style=False)

            logger.info("💾 优化后的参数已保存到 optimized_params.yaml")

        except Exception as e:
            logger.error(f"保存参数文件失败: {e}")

    async def get_current_params(self) -> Dict:
        """
        获取当前参数

        Returns:
            当前参数字典
        """
        return self.current_params

    async def get_adjustment_history(self, days: int = 7) -> List[Dict]:
        """
        获取参数调整历史

        Args:
            days: 查询天数

        Returns:
            调整历史列表
        """
        with self.db_pool.get_connection() as conn:
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            cursor.execute("""
            SELECT *
            FROM parameter_adjustments
            WHERE adjustment_date >= DATE_SUB(NOW(), INTERVAL %s DAY)
            ORDER BY adjustment_date DESC
        """, (days,))

        results = cursor.fetchall()
        cursor.close()

        return results


async def main():
    """测试主函数"""
    db_config = {
        'host': 'localhost',
        'port': 3306,
        'user': 'root',
        'password': '',
        'database': 'binance-data'
    }

    optimizer = AutoParameterOptimizer(db_config)

    # 测试：基于昨天的复盘优化参数
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    result = await optimizer.optimize_based_on_review(yesterday)

    if result['success']:
        print("\n当前参数:")
        import json
        print(json.dumps(result['current_params'], indent=2))


if __name__ == '__main__':
    asyncio.run(main())
