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


class AdaptiveOptimizer:
    """自适应优化器 - 让超级大脑自我学习和改进"""

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

        logger.info("✅ 自适应优化器已初始化")

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

        cutoff_time = datetime.now() - timedelta(hours=hours)

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
            'timestamp': datetime.now(),
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

    def apply_optimizations(self, report: Dict, auto_apply: bool = False, apply_params: bool = True) -> Dict:
        """
        应用优化建议

        Args:
            report: 优化报告
            auto_apply: 是否自动应用优化
            apply_params: 是否自动应用参数调整 (止损、持仓时间等)

        Returns:
            应用结果
        """
        results = {
            'blacklist_added': [],
            'params_updated': [],
            'warnings': []
        }

        if not auto_apply:
            logger.warning("⚠️ 自动应用已禁用，仅生成建议")
            return results

        # 1. 更新黑名单
        if report['blacklist_candidates']:
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)

                current_blacklist = config.get('signals', {}).get('blacklist', [])

                for candidate in report['blacklist_candidates']:
                    symbol = candidate['symbol']
                    if symbol not in current_blacklist:
                        current_blacklist.append(symbol)
                        results['blacklist_added'].append({
                            'symbol': symbol,
                            'reason': candidate['reason']
                        })
                        logger.info(f"➕ 添加到黑名单: {symbol} - {candidate['reason']}")

                # 更新配置
                if 'signals' not in config:
                    config['signals'] = {}
                config['signals']['blacklist'] = current_blacklist

                # 写回配置文件
                with open(self.config_path, 'w', encoding='utf-8') as f:
                    yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

                logger.info(f"✅ 黑名单已更新，新增{len(results['blacklist_added'])}个交易对")

            except Exception as e:
                logger.error(f"❌ 更新黑名单失败: {e}")
                results['warnings'].append(f"更新黑名单失败: {e}")

        # 2. 自动调整参数 (LONG/SHORT止损、持仓时间等)
        if apply_params and report['problematic_signals']:
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)

                # 确保adaptive结构存在
                if 'signals' not in config:
                    config['signals'] = {}
                if 'adaptive' not in config['signals']:
                    config['signals']['adaptive'] = {
                        'long': {'stop_loss_pct': 0.03, 'take_profit_pct': 0.02, 'min_holding_minutes': 60, 'position_size_multiplier': 1.0},
                        'short': {'stop_loss_pct': 0.03, 'take_profit_pct': 0.02, 'min_holding_minutes': 60, 'position_size_multiplier': 1.0}
                    }

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
                            if avg_hold_minutes < 90:
                                # 增加最小持仓时间到120分钟
                                old_min_holding = config['signals']['adaptive']['long']['min_holding_minutes']
                                config['signals']['adaptive']['long']['min_holding_minutes'] = 120
                                results['params_updated'].append(f"LONG最小持仓时间: {old_min_holding}分钟 → 120分钟")

                            if win_rate < 0.15:
                                # 放宽止损到4%
                                old_stop_loss = config['signals']['adaptive']['long']['stop_loss_pct']
                                config['signals']['adaptive']['long']['stop_loss_pct'] = 0.04
                                results['params_updated'].append(f"LONG止损: {old_stop_loss*100:.1f}% → 4.0%")

                            if total_pnl < -500:
                                # 降低仓位到50%
                                old_multiplier = config['signals']['adaptive']['long']['position_size_multiplier']
                                config['signals']['adaptive']['long']['position_size_multiplier'] = 0.5
                                results['params_updated'].append(f"LONG仓位倍数: {old_multiplier:.1f} → 0.5")

                        # 记录警告
                        results['warnings'].append(
                            f"⚠️ 高严重性: {signal['signal_type']} {signal['direction']} "
                            f"亏损${signal['total_pnl']:.2f} - {signal['recommendation']}"
                        )

                # 如果有参数更新，写回配置文件
                if results['params_updated']:
                    with open(self.config_path, 'w', encoding='utf-8') as f:
                        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

                    logger.info(f"✅ 自适应参数已更新，共{len(results['params_updated'])}项")
                    for update in results['params_updated']:
                        logger.info(f"   📊 {update}")

            except Exception as e:
                logger.error(f"❌ 更新自适应参数失败: {e}")
                results['warnings'].append(f"更新自适应参数失败: {e}")

        # 3. 生成警告（未自动调整的问题）
        else:
            for signal in report['problematic_signals']:
                if signal['severity'] == 'high':
                    results['warnings'].append(
                        f"⚠️ 高严重性: {signal['signal_type']} {signal['direction']} "
                        f"亏损${signal['total_pnl']:.2f} - {signal['recommendation']}"
                    )

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
