#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自适应优化器 - 超级大脑的自我学习和优化模块
根据实盘表现动态调整策略参数
"""

from typing import Dict, List, Tuple
from datetime import datetime, timedelta
from loguru import logger
import pymysql
import yaml
from .scoring_weight_optimizer import ScoringWeightOptimizer


class AdaptiveOptimizer:
    """自适应优化器 - 让超级大脑自我学习和改进"""

    # 黑名单白名单 - 四大天王永不拉黑
    BLACKLIST_WHITELIST = {
        'BTC/USDT',
        'ETH/USDT',
        'SOL/USDT',
        'BNB/USDT'
    }

    def __init__(self, db_config: dict, config_path: str = 'config.yaml'):
        """
        初始化优化器

        Args:
            db_config: 数据库配置
            config_path: 配置文件路径
        """
        self.db_config = db_config
        self.config_path = config_path
        self.connection = None

        # 优化阈值
        self.thresholds = {
            'min_orders_for_analysis': 5,      # 最少订单数才进行分析
            'blacklist_loss_threshold': -20,    # 单个交易对亏损超过20 USDT加入黑名单
            'blacklist_win_rate_threshold': 0.1, # 胜率低于10%加入黑名单
            'signal_direction_loss_threshold': -100,  # 信号+方向亏损超过100 USDT需要调整
            'long_stop_loss_multiplier': 2.0,    # 做多止损倍数
            'min_holding_time_long': 120,        # 做多最小持仓时间(分钟)
        }

        # 初始化评分权重优化器
        self.weight_optimizer = ScoringWeightOptimizer(db_config)

        logger.info("✅ 自适应优化器已初始化 (包含评分权重优化器)")

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
        else:
            try:
                self.connection.ping(reconnect=True)
            except:
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

    def analyze_recent_performance(self, hours: int = 24) -> Dict:
        """
        分析最近的交易表现

        Args:
            hours: 分析最近多少小时的数据

        Returns:
            分析结果字典
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cutoff_time = datetime.utcnow() - timedelta(hours=hours)

        logger.info(f"📊 开始分析最近{hours}小时的交易表现...")

        # 1. 按交易对分析
        cursor.execute("""
            SELECT
                symbol,
                COUNT(*) as order_count,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
                SUM(realized_pnl) as total_pnl,
                AVG(realized_pnl) as avg_pnl
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= %s
            GROUP BY symbol
            HAVING order_count >= %s
        """, (cutoff_time, self.thresholds['min_orders_for_analysis']))

        symbol_performance = cursor.fetchall()

        # 2. 按信号类型和方向分析
        cursor.execute("""
            SELECT
                entry_signal_type,
                position_side,
                COUNT(*) as order_count,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
                SUM(realized_pnl) as total_pnl,
                AVG(realized_pnl) as avg_pnl,
                AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)) as avg_hold_minutes
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= %s
            AND entry_signal_type IS NOT NULL
            GROUP BY entry_signal_type, position_side
            HAVING order_count >= %s
        """, (cutoff_time, self.thresholds['min_orders_for_analysis']))

        signal_performance = cursor.fetchall()

        cursor.close()

        return {
            'symbol_performance': symbol_performance,
            'signal_performance': signal_performance,
            'analysis_hours': hours,
            'cutoff_time': cutoff_time
        }

    def identify_blacklist_symbols(self, analysis: Dict) -> List[str]:
        """
        识别需要加入黑名单的交易对

        Args:
            analysis: 分析结果

        Returns:
            需要加入黑名单的交易对列表
        """
        blacklist_candidates = []

        for symbol_data in analysis['symbol_performance']:
            symbol = symbol_data['symbol']

            # ⚠️ 白名单保护 - 四大天王永不拉黑
            if symbol in self.BLACKLIST_WHITELIST:
                logger.info(f"🛡️ {symbol} 在白名单中,跳过黑名单检查")
                continue

            total_pnl = symbol_data['total_pnl']
            order_count = symbol_data['order_count']
            wins = symbol_data['wins']

            win_rate = wins / order_count if order_count > 0 else 0

            # 判断是否应该加入黑名单
            if (total_pnl < self.thresholds['blacklist_loss_threshold'] or
                win_rate < self.thresholds['blacklist_win_rate_threshold']):

                blacklist_candidates.append({
                    'symbol': symbol,
                    'total_pnl': total_pnl,
                    'win_rate': win_rate,
                    'order_count': order_count,
                    'reason': f"亏损${total_pnl:.2f}, 胜率{win_rate*100:.1f}%"
                })

        return blacklist_candidates

    def identify_problematic_signals(self, analysis: Dict) -> List[Dict]:
        """
        识别有问题的信号类型

        Args:
            analysis: 分析结果

        Returns:
            有问题的信号列表
        """
        problematic_signals = []

        for signal_data in analysis['signal_performance']:
            signal_type = signal_data['entry_signal_type']
            direction = signal_data['position_side']
            total_pnl = signal_data['total_pnl']
            order_count = signal_data['order_count']
            wins = signal_data['wins']
            avg_hold_minutes = signal_data['avg_hold_minutes']

            win_rate = wins / order_count if order_count > 0 else 0

            # 识别问题信号
            if total_pnl < self.thresholds['signal_direction_loss_threshold']:
                problematic_signals.append({
                    'signal_type': signal_type,
                    'direction': direction,
                    'total_pnl': total_pnl,
                    'win_rate': win_rate,
                    'order_count': order_count,
                    'avg_hold_minutes': avg_hold_minutes,
                    'severity': 'high' if total_pnl < -500 else 'medium',
                    'recommendation': self._generate_recommendation(
                        signal_type, direction, total_pnl, win_rate, avg_hold_minutes
                    )
                })

        return problematic_signals

    def _generate_recommendation(self, signal_type: str, direction: str,
                                total_pnl: float, win_rate: float,
                                avg_hold_minutes: float) -> str:
        """生成优化建议"""
        recommendations = []

        # 做多特殊优化
        if direction == 'LONG':
            if avg_hold_minutes < 90:
                recommendations.append(f"增加最小持仓时间到{self.thresholds['min_holding_time_long']}分钟")

            if win_rate < 0.15:
                recommendations.append("放宽止损到4%")

            if total_pnl < -500:
                recommendations.append("降低仓位到50%或暂时禁用")

        # 做空优化
        elif direction == 'SHORT':
            if total_pnl < -100:
                recommendations.append("检查信号逻辑,可能需要调整阈值")

        # 信号分数相关
        try:
            score = int(signal_type.split('_')[-1])
            if score >= 40 and total_pnl < -200:
                recommendations.append(f"降低{score}分信号的权重")
        except:
            pass

        return "; ".join(recommendations) if recommendations else "持续监控"

    def generate_optimization_report(self, hours: int = 24) -> Dict:
        """
        生成优化报告

        Args:
            hours: 分析时间范围

        Returns:
            优化报告
        """
        logger.info(f"🔍 生成最近{hours}小时的优化报告...")

        # 分析表现
        analysis = self.analyze_recent_performance(hours)

        # 识别黑名单候选
        blacklist_candidates = self.identify_blacklist_symbols(analysis)

        # 识别问题信号
        problematic_signals = self.identify_problematic_signals(analysis)

        # 生成报告
        report = {
            'timestamp': datetime.utcnow(),
            'analysis_hours': hours,
            'blacklist_candidates': blacklist_candidates,
            'problematic_signals': problematic_signals,
            'summary': {
                'total_symbols_analyzed': len(analysis['symbol_performance']),
                'blacklist_candidates_count': len(blacklist_candidates),
                'problematic_signals_count': len(problematic_signals),
                'high_severity_issues': len([s for s in problematic_signals if s['severity'] == 'high'])
            }
        }

        return report

    def apply_optimizations(self, report: Dict, auto_apply: bool = False, apply_params: bool = True, apply_weights: bool = True) -> Dict:
        """
        应用优化建议 - 更新数据库而不是config.yaml

        Args:
            report: 优化报告
            auto_apply: 是否自动应用优化
            apply_params: 是否自动应用参数调整 (止损、持仓时间等)
            apply_weights: 是否自动应用评分权重调整

        Returns:
            应用结果
        """
        results = {
            'blacklist_added': [],
            'params_updated': [],
            'weights_adjusted': [],
            'warnings': []
        }

        if not auto_apply:
            logger.warning("⚠️ 自动应用已禁用，仅生成建议")
            return results

        # 1. 更新黑名单到数据库
        if report['blacklist_candidates']:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()

                for candidate in report['blacklist_candidates']:
                    symbol = candidate['symbol']
                    reason = candidate['reason']
                    total_pnl = candidate['total_pnl']
                    win_rate = candidate['win_rate']
                    order_count = candidate['order_count']

                    # 检查是否已存在评级记录
                    cursor.execute("""
                        SELECT id, rating_level FROM trading_symbol_rating
                        WHERE symbol = %s
                    """, (symbol,))

                    existing = cursor.fetchone()

                    if existing:
                        # 如果已存在,升级黑名单等级
                        current_level = existing['rating_level']
                        new_level = min(current_level + 1, 3)  # 最高Level 3
                        new_multiplier = {0: 1.0, 1: 0.25, 2: 0.125, 3: 0}[new_level]

                        cursor.execute("""
                            UPDATE trading_symbol_rating
                            SET rating_level = %s,
                                margin_multiplier = %s,
                                level_change_reason = CONCAT(IFNULL(level_change_reason, ''), ' | ', %s),
                                previous_level = %s,
                                level_changed_at = NOW(),
                                updated_at = NOW()
                            WHERE symbol = %s
                        """, (new_level, new_multiplier, reason, current_level, symbol))

                        results['blacklist_added'].append({
                            'symbol': symbol,
                            'reason': f'升级到Level{new_level}: {reason}'
                        })
                        logger.info(f"⬆️ 升级黑名单等级: {symbol} Level{current_level}→{new_level} - {reason}")
                    else:
                        # 插入新记录,默认Level 1黑名单
                        cursor.execute("""
                            INSERT INTO trading_symbol_rating
                            (symbol, rating_level, margin_multiplier,
                             score_bonus, level_change_reason, stats_start_date, stats_end_date, created_at, updated_at)
                            VALUES (%s, 1, 0.25, 0, %s, CURDATE(), CURDATE(), NOW(), NOW())
                        """, (symbol, reason))

                        results['blacklist_added'].append({
                            'symbol': symbol,
                            'reason': f'Level1: {reason}'
                        })
                        logger.info(f"➕ 添加到数据库黑名单Level1: {symbol} - {reason}")

                conn.commit()
                cursor.close()

                logger.info(f"✅ 数据库黑名单已更新，新增{len(results['blacklist_added'])}个交易对")

            except Exception as e:
                logger.error(f"❌ 更新数据库黑名单失败: {e}")
                results['warnings'].append(f"更新黑名单失败: {e}")

        # 2. 自动调整参数到数据库 (LONG/SHORT止损、持仓时间等)
        if apply_params and report['problematic_signals']:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()

                # 分析问题信号并调整参数
                for signal in report['problematic_signals']:
                    direction = signal['direction']
                    total_pnl = signal['total_pnl']
                    win_rate = signal['win_rate']
                    avg_hold_minutes = signal['avg_hold_minutes']

                    # 只对高严重性问题自动调整
                    if signal['severity'] == 'high':
                        if direction == 'LONG':
                            # LONG信号亏损严重，自动调整参数

                            # 1. 增加最小持仓时间到120分钟
                            if avg_hold_minutes < 90:
                                cursor.execute("""
                                    SELECT param_value FROM adaptive_params
                                    WHERE param_key = 'long_min_holding_minutes'
                                """)
                                old_value = cursor.fetchone()
                                old_min_holding = old_value[0] if old_value else 60

                                cursor.execute("""
                                    UPDATE adaptive_params
                                    SET param_value = 120, updated_by = 'adaptive_optimizer'
                                    WHERE param_key = 'long_min_holding_minutes'
                                """)
                                results['params_updated'].append(f"LONG最小持仓时间: {old_min_holding:.0f}分钟 → 120分钟")

                            # 2. 放宽止损到4%
                            if win_rate < 0.15:
                                cursor.execute("""
                                    SELECT param_value FROM adaptive_params
                                    WHERE param_key = 'long_stop_loss_pct'
                                """)
                                old_value = cursor.fetchone()
                                old_stop_loss = old_value[0] if old_value else 0.03

                                cursor.execute("""
                                    UPDATE adaptive_params
                                    SET param_value = 0.04, updated_by = 'adaptive_optimizer'
                                    WHERE param_key = 'long_stop_loss_pct'
                                """)
                                results['params_updated'].append(f"LONG止损: {float(old_stop_loss)*100:.1f}% → 4.0%")

                            # 3. 降低仓位到50%
                            if total_pnl < -500:
                                cursor.execute("""
                                    SELECT param_value FROM adaptive_params
                                    WHERE param_key = 'long_position_size_multiplier'
                                """)
                                old_value = cursor.fetchone()
                                old_multiplier = old_value[0] if old_value else 1.0

                                cursor.execute("""
                                    UPDATE adaptive_params
                                    SET param_value = 0.5, updated_by = 'adaptive_optimizer'
                                    WHERE param_key = 'long_position_size_multiplier'
                                """)
                                results['params_updated'].append(f"LONG仓位倍数: {float(old_multiplier):.1f} → 0.5")

                        # 记录警告
                        results['warnings'].append(
                            f"⚠️ 高严重性: {signal['signal_type']} {signal['direction']} "
                            f"亏损${signal['total_pnl']:.2f} - {signal['recommendation']}"
                        )

                # 提交所有参数更新
                if results['params_updated']:
                    conn.commit()
                    logger.info(f"✅ 数据库参数已更新，共{len(results['params_updated'])}项")
                    for update in results['params_updated']:
                        logger.info(f"   📊 {update}")

                cursor.close()

            except Exception as e:
                logger.error(f"❌ 更新数据库参数失败: {e}")
                results['warnings'].append(f"更新自适应参数失败: {e}")

        # 3. 生成警告（未自动调整的问题）
        else:
            for signal in report['problematic_signals']:
                if signal['severity'] == 'high':
                    results['warnings'].append(
                        f"⚠️ 高严重性: {signal['signal_type']} {signal['direction']} "
                        f"亏损${signal['total_pnl']:.2f} - {signal['recommendation']}"
                    )

        # 4. 调整评分权重 (根据最近7天的表现)
        if apply_weights:
            try:
                logger.info("🔧 开始调整信号评分权重...")
                weight_results = self.weight_optimizer.adjust_weights(dry_run=False)

                if weight_results.get('adjusted'):
                    results['weights_adjusted'] = weight_results['adjusted']
                    logger.info(f"✅ 评分权重已调整，共 {len(weight_results['adjusted'])} 个")

                    # 打印调整详情
                    self.weight_optimizer.print_adjustment_report(weight_results)
                else:
                    logger.info("📊 评分权重无需调整")

            except Exception as e:
                logger.error(f"❌ 调整评分权重失败: {e}")
                results['warnings'].append(f"调整评分权重失败: {e}")

        return results

    def print_report(self, report: Dict):
        """打印优化报告"""
        print("\n" + "=" * 100)
        print("🧠 超级大脑自适应优化报告")
        print("=" * 100)
        print(f"\n📅 分析时间: {report['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"⏱️  时间范围: 最近 {report['analysis_hours']} 小时")
        print(f"📊 分析交易对数: {report['summary']['total_symbols_analyzed']}")

        # 黑名单候选
        if report['blacklist_candidates']:
            print("\n" + "=" * 100)
            print("🚫 建议加入黑名单的交易对")
            print("=" * 100)
            for candidate in report['blacklist_candidates']:
                print(f"  • {candidate['symbol']:<15} - {candidate['reason']}")
        else:
            print("\n✅ 没有需要加入黑名单的交易对")

        # 问题信号
        if report['problematic_signals']:
            print("\n" + "=" * 100)
            print("⚠️ 需要优化的信号")
            print("=" * 100)
            for signal in report['problematic_signals']:
                severity_icon = "🔴" if signal['severity'] == 'high' else "🟡"
                print(f"\n{severity_icon} {signal['signal_type']} - {signal['direction']}")
                print(f"  订单数: {signal['order_count']}")
                print(f"  胜率: {signal['win_rate']*100:.1f}%")
                print(f"  总盈亏: ${signal['total_pnl']:.2f}")
                print(f"  平均持仓: {signal['avg_hold_minutes']:.0f}分钟")
                print(f"  建议: {signal['recommendation']}")
        else:
            print("\n✅ 所有信号表现正常")

        print("\n" + "=" * 100)


def main():
    """主函数 - 用于测试和手动运行"""
    import sys

    # Set UTF-8 encoding for Windows console
    if sys.platform == 'win32':
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

    # 数据库配置
    db_config = {
        'host': '13.212.252.171',
        'port': 3306,
        'user': 'admin',
        'password': 'Tonny@1000',
        'database': 'binance-data'
    }

    # 创建优化器
    optimizer = AdaptiveOptimizer(db_config)

    # 生成报告
    report = optimizer.generate_optimization_report(hours=24)

    # 打印报告
    optimizer.print_report(report)

    # 询问是否应用
    if report['blacklist_candidates'] or report['problematic_signals']:
        print("\n是否自动应用优化? (y/n): ", end='')
        if sys.platform == 'win32':
            import msvcrt
            response = msvcrt.getch().decode('utf-8').lower()
            print(response)
        else:
            response = input().lower()

        if response == 'y':
            results = optimizer.apply_optimizations(report, auto_apply=True)
            print("\n✅ 优化已应用:")
            print(f"  新增黑名单: {len(results['blacklist_added'])}个")
            if results['warnings']:
                print("\n⚠️ 警告:")
                for warning in results['warnings']:
                    print(f"  {warning}")


if __name__ == '__main__':
    main()
