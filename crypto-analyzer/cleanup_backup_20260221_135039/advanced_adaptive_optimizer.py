#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
高级自适应优化器
1. 动态优化每个交易对的止盈止损
2. 动态优化信号和交易对的仓位分配
"""

import pymysql
import json
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from loguru import logger
import numpy as np


class AdvancedAdaptiveOptimizer:
    """高级自适应优化器"""

    def __init__(self, db_config: dict):
        self.db_config = db_config
        self.connection = None

        # 优化参数
        self.min_trades_for_optimization = 10  # 最少交易次数
        self.position_multiplier_range = (0.5, 2.0)  # 仓位倍数范围
        self.tp_sl_range = (0.01, 0.10)  # 止盈止损范围 1%-10%

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

    def optimize_symbol_risk_params(self, days: int = 7, dry_run: bool = True) -> Dict:
        """
        优化每个交易对的止盈止损参数

        策略:
        1. 分析每个交易对的历史表现
        2. 计算最优止盈止损比例(基于历史价格波动)
        3. 根据胜率和盈亏调整止盈止损
        """
        logger.info(f"开始优化交易对止盈止损参数 (最近{days}天)")

        conn = self._get_connection()
        cursor = conn.cursor()

        # 分析每个交易对的历史表现
        cursor.execute("""
            SELECT
                symbol,
                position_side,
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                AVG(realized_pnl) as avg_pnl,
                SUM(realized_pnl) as total_pnl,
                AVG(ABS(unrealized_pnl_pct)) as avg_price_move,
                MAX(unrealized_pnl_pct) as max_profit_pct,
                MIN(unrealized_pnl_pct) as max_loss_pct,
                STDDEV(unrealized_pnl_pct) as volatility
            FROM futures_positions
            WHERE source = 'smart_trader'
            AND status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
            GROUP BY symbol, position_side
            HAVING total_trades >= %s
        """, (days, self.min_trades_for_optimization))

        symbol_stats = cursor.fetchall()

        adjustments = []

        for stat in symbol_stats:
            symbol = stat['symbol']
            side = stat['position_side']
            total_trades = stat['total_trades']
            win_rate = float(stat['wins']) / total_trades if total_trades > 0 else 0
            avg_pnl = float(stat['avg_pnl'] or 0)
            volatility = float(stat['volatility'] or 0.03)
            max_profit_pct = abs(float(stat['max_profit_pct'] or 0.05))
            max_loss_pct = abs(float(stat['max_loss_pct'] or 0.03))

            # 获取当前配置
            cursor.execute("""
                SELECT long_take_profit_pct, long_stop_loss_pct,
                       short_take_profit_pct, short_stop_loss_pct
                FROM symbol_risk_params
                WHERE symbol = %s
            """, (symbol,))

            current = cursor.fetchone()

            if not current:
                # 初始化默认值
                cursor.execute("""
                    INSERT INTO symbol_risk_params
                    (symbol, long_take_profit_pct, long_stop_loss_pct,
                     short_take_profit_pct, short_stop_loss_pct)
                    VALUES (%s, 0.05, 0.02, 0.05, 0.02)
                """, (symbol,))
                conn.commit()
                current = {'long_take_profit_pct': 0.05, 'long_stop_loss_pct': 0.02,
                          'short_take_profit_pct': 0.05, 'short_stop_loss_pct': 0.02}

            # 计算最优止盈止损
            if side == 'LONG':
                current_tp = float(current['long_take_profit_pct'])
                current_sl = float(current['long_stop_loss_pct'])
            else:
                current_tp = float(current['short_take_profit_pct'])
                current_sl = float(current['short_stop_loss_pct'])

            # 优化逻辑
            new_tp, new_sl = self._calculate_optimal_tp_sl(
                win_rate=win_rate,
                avg_pnl=avg_pnl,
                volatility=volatility,
                max_profit_pct=max_profit_pct,
                max_loss_pct=max_loss_pct,
                current_tp=current_tp,
                current_sl=current_sl
            )

            # 检查是否需要调整
            if abs(new_tp - current_tp) >= 0.005 or abs(new_sl - current_sl) >= 0.002:
                adjustment = {
                    'symbol': symbol,
                    'side': side,
                    'current_tp': current_tp,
                    'current_sl': current_sl,
                    'new_tp': new_tp,
                    'new_sl': new_sl,
                    'win_rate': win_rate,
                    'volatility': volatility,
                    'total_trades': total_trades,
                    'reason': self._get_adjustment_reason(
                        win_rate, volatility, current_tp, current_sl, new_tp, new_sl
                    )
                }
                adjustments.append(adjustment)

                if not dry_run:
                    # 更新数据库
                    if side == 'LONG':
                        cursor.execute("""
                            UPDATE symbol_risk_params
                            SET long_take_profit_pct = %s,
                                long_stop_loss_pct = %s,
                                last_optimized = NOW(),
                                optimization_count = optimization_count + 1
                            WHERE symbol = %s
                        """, (new_tp, new_sl, symbol))
                    else:
                        cursor.execute("""
                            UPDATE symbol_risk_params
                            SET short_take_profit_pct = %s,
                                short_stop_loss_pct = %s,
                                last_optimized = NOW(),
                                optimization_count = optimization_count + 1
                            WHERE symbol = %s
                        """, (new_tp, new_sl, symbol))

                    # 记录优化历史
                    cursor.execute("""
                        INSERT INTO optimization_history
                        (optimization_type, target_name, param_name, old_value, new_value,
                         change_amount, sample_size, win_rate, reason)
                        VALUES
                        ('symbol_risk', %s, %s, %s, %s, %s, %s, %s, %s),
                        ('symbol_risk', %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        f"{symbol}_{side}", 'take_profit_pct', current_tp, new_tp,
                        new_tp - current_tp, total_trades, win_rate, adjustment['reason'],
                        f"{symbol}_{side}", 'stop_loss_pct', current_sl, new_sl,
                        new_sl - current_sl, total_trades, win_rate, adjustment['reason']
                    ))

        if not dry_run and adjustments:
            conn.commit()
            logger.info(f"✅ 已优化 {len(adjustments)} 个交易对的止盈止损参数")

        cursor.close()

        return {
            'adjusted': adjustments,
            'total_analyzed': len(symbol_stats)
        }

    def _calculate_optimal_tp_sl(self, win_rate: float, avg_pnl: float,
                                  volatility: float, max_profit_pct: float,
                                  max_loss_pct: float, current_tp: float,
                                  current_sl: float) -> Tuple[float, float]:
        """
        计算最优止盈止损

        策略:
        1. 波动性高 -> 更大的止盈止损空间
        2. 胜率高 -> 可以收窄止损,扩大止盈
        3. 胜率低 -> 需要扩大止盈止损比例 (提高盈亏比)
        """

        # 基础止盈止损(基于波动性)
        base_tp = min(max(volatility * 2.0, 0.02), 0.10)  # 波动性的2倍,最小2%,最大10%
        base_sl = min(max(volatility * 1.0, 0.015), 0.05)  # 波动性的1倍,最小1.5%,最大5%

        # 根据胜率调整
        if win_rate > 0.40:
            # 胜率高,可以收窄止损
            tp_multiplier = 1.2
            sl_multiplier = 0.8
        elif win_rate > 0.30:
            # 胜率中等,保持平衡
            tp_multiplier = 1.0
            sl_multiplier = 1.0
        else:
            # 胜率低,扩大盈亏比
            tp_multiplier = 1.5
            sl_multiplier = 0.7

        new_tp = base_tp * tp_multiplier
        new_sl = base_sl * sl_multiplier

        # 确保盈亏比至少 2:1
        if new_tp / new_sl < 2.0:
            new_tp = new_sl * 2.5

        # 限制在合理范围
        new_tp = max(min(new_tp, 0.10), 0.02)
        new_sl = max(min(new_sl, 0.05), 0.01)

        # 平滑调整(避免剧烈变化)
        new_tp = current_tp * 0.5 + new_tp * 0.5
        new_sl = current_sl * 0.5 + new_sl * 0.5

        return round(new_tp, 4), round(new_sl, 4)

    def _get_adjustment_reason(self, win_rate: float, volatility: float,
                               old_tp: float, old_sl: float,
                               new_tp: float, new_sl: float) -> str:
        """生成调整原因说明"""
        reasons = []

        if volatility > 0.05:
            reasons.append(f"高波动({volatility*100:.1f}%)")
        elif volatility < 0.02:
            reasons.append(f"低波动({volatility*100:.1f}%)")

        if win_rate > 0.40:
            reasons.append(f"高胜率({win_rate*100:.1f}%)")
        elif win_rate < 0.30:
            reasons.append(f"低胜率({win_rate*100:.1f}%),提高盈亏比")

        if new_tp > old_tp:
            reasons.append(f"提高止盈")
        if new_sl < old_sl:
            reasons.append(f"收紧止损")

        return "; ".join(reasons) if reasons else "常规优化"

    def optimize_position_multipliers(self, days: int = 7, dry_run: bool = True) -> Dict:
        """
        优化仓位倍数

        策略:
        1. 分析每个交易对的表现 -> 调整交易对仓位倍数
        2. 分析每个信号组件的表现 -> 调整信号仓位倍数
        """
        logger.info(f"开始优化仓位倍数 (最近{days}天)")

        symbol_adjustments = self._optimize_symbol_positions(days, dry_run)
        signal_adjustments = self._optimize_signal_positions(days, dry_run)

        return {
            'symbol_adjustments': symbol_adjustments,
            'signal_adjustments': signal_adjustments
        }

    def _optimize_symbol_positions(self, days: int, dry_run: bool) -> List[Dict]:
        """优化交易对仓位倍数"""
        conn = self._get_connection()
        cursor = conn.cursor()

        # 分析交易对表现
        cursor.execute("""
            SELECT
                symbol,
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                AVG(realized_pnl) as avg_pnl,
                SUM(realized_pnl) as total_pnl,
                STDDEV(realized_pnl) as pnl_std
            FROM futures_positions
            WHERE source = 'smart_trader'
            AND status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
            GROUP BY symbol
            HAVING total_trades >= %s
        """, (days, self.min_trades_for_optimization))

        symbol_stats = cursor.fetchall()
        adjustments = []

        for stat in symbol_stats:
            symbol = stat['symbol']
            total_trades = stat['total_trades']
            win_rate = float(stat['wins']) / total_trades
            avg_pnl = float(stat['avg_pnl'] or 0)
            total_pnl = float(stat['total_pnl'] or 0)

            # 计算性能得分
            performance_score = self._calculate_performance_score(
                win_rate=win_rate,
                avg_pnl=avg_pnl,
                total_pnl=total_pnl,
                total_trades=total_trades
            )

            # 计算新的仓位倍数
            new_multiplier = self._calculate_position_multiplier(performance_score)

            # 获取当前倍数
            cursor.execute("""
                SELECT position_multiplier FROM symbol_risk_params
                WHERE symbol = %s
            """, (symbol,))

            current = cursor.fetchone()
            current_multiplier = float(current['position_multiplier']) if current else 1.0

            # 检查是否需要调整
            if abs(new_multiplier - current_multiplier) >= 0.1:
                adjustment = {
                    'symbol': symbol,
                    'current_multiplier': current_multiplier,
                    'new_multiplier': new_multiplier,
                    'performance_score': performance_score,
                    'win_rate': win_rate,
                    'total_pnl': total_pnl,
                    'reason': f"Performance: {performance_score:.2f}"
                }
                adjustments.append(adjustment)

                if not dry_run:
                    cursor.execute("""
                        UPDATE symbol_risk_params
                        SET position_multiplier = %s,
                            last_optimized = NOW()
                        WHERE symbol = %s
                    """, (new_multiplier, symbol))

        if not dry_run and adjustments:
            conn.commit()

        cursor.close()
        return adjustments

    def _optimize_signal_positions(self, days: int, dry_run: bool) -> List[Dict]:
        """优化信号组件仓位倍数"""
        conn = self._get_connection()
        cursor = conn.cursor()

        # 分析信号组件表现
        cursor.execute("""
            SELECT
                JSON_KEYS(signal_components) as components,
                position_side,
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                AVG(realized_pnl) as avg_pnl,
                SUM(realized_pnl) as total_pnl
            FROM futures_positions
            WHERE source = 'smart_trader'
            AND status = 'closed'
            AND signal_components IS NOT NULL
            AND close_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
            GROUP BY signal_components, position_side
        """, (days,))

        # 按组件统计
        component_stats = {}
        for pos in cursor.fetchall():
            try:
                components = json.loads(pos['components']) if isinstance(pos['components'], str) else pos['components']
                if not components:
                    continue

                for comp in components:
                    key = (comp, pos['position_side'])
                    if key not in component_stats:
                        component_stats[key] = {
                            'trades': 0,
                            'wins': 0,
                            'total_pnl': 0
                        }
                    component_stats[key]['trades'] += 1
                    if pos['realized_pnl'] > 0:
                        component_stats[key]['wins'] += 1
                    component_stats[key]['total_pnl'] += float(pos['realized_pnl'] or 0)
            except:
                continue

        adjustments = []

        for (component, side), stats in component_stats.items():
            if stats['trades'] < self.min_trades_for_optimization:
                continue

            win_rate = stats['wins'] / stats['trades']
            avg_pnl = stats['total_pnl'] / stats['trades']
            total_pnl = stats['total_pnl']

            performance_score = self._calculate_performance_score(
                win_rate=win_rate,
                avg_pnl=avg_pnl,
                total_pnl=total_pnl,
                total_trades=stats['trades']
            )

            new_multiplier = self._calculate_position_multiplier(performance_score)

            # 获取当前倍数
            cursor.execute("""
                SELECT position_multiplier FROM signal_position_multipliers
                WHERE component_name = %s AND position_side = %s
            """, (component, side))

            current = cursor.fetchone()
            current_multiplier = float(current['position_multiplier']) if current else 1.0

            if abs(new_multiplier - current_multiplier) >= 0.1:
                adjustment = {
                    'component': component,
                    'side': side,
                    'current_multiplier': current_multiplier,
                    'new_multiplier': new_multiplier,
                    'performance_score': performance_score,
                    'win_rate': win_rate,
                    'total_trades': stats['trades']
                }
                adjustments.append(adjustment)

                if not dry_run:
                    cursor.execute("""
                        INSERT INTO signal_position_multipliers
                        (component_name, position_side, position_multiplier)
                        VALUES (%s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            position_multiplier = VALUES(position_multiplier),
                            last_analyzed = NOW()
                    """, (component, side, new_multiplier))

        if not dry_run and adjustments:
            conn.commit()

        cursor.close()
        return adjustments

    def _calculate_performance_score(self, win_rate: float, avg_pnl: float,
                                     total_pnl: float, total_trades: int) -> float:
        """
        计算性能得分

        综合考虑:
        - 胜率 (40%)
        - 平均盈亏 (30%)
        - 总盈亏 (20%)
        - 交易次数 (10%)
        """
        win_rate_score = (win_rate - 0.3) * 100  # 基准30%
        avg_pnl_score = avg_pnl / 5  # 平均5美元为基准
        total_pnl_score = total_pnl / 100  # 总盈利100美元为基准
        trades_score = min(total_trades / 20, 1.0) * 10  # 最多20次交易满分

        performance_score = (
            win_rate_score * 0.4 +
            avg_pnl_score * 0.3 +
            total_pnl_score * 0.2 +
            trades_score * 0.1
        )

        return performance_score

    def _calculate_position_multiplier(self, performance_score: float) -> float:
        """
        根据性能得分计算仓位倍数

        得分 > 20: 2.0x (表现优秀)
        得分 10-20: 1.5x (表现良好)
        得分 5-10: 1.2x (表现中等)
        得分 0-5: 1.0x (表现一般)
        得分 -5-0: 0.8x (表现较差)
        得分 < -5: 0.5x (表现很差)
        """
        if performance_score > 20:
            return 2.0
        elif performance_score > 10:
            return 1.5
        elif performance_score > 5:
            return 1.2
        elif performance_score > 0:
            return 1.0
        elif performance_score > -5:
            return 0.8
        else:
            return 0.5
