#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日复盘分析器 - Daily Review Analyzer

功能:
1. 每天复盘24H行情走势
2. 识别错过的大行情机会
3. 分析现有信号捕捉效果
4. 自动优化信号参数
5. 生成复盘报告

Author: Claude
Date: 2026-01-26
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from decimal import Decimal
from loguru import logger
import pymysql
import json
from dataclasses import dataclass, asdict


@dataclass
class BigMoveOpportunity:
    """大行情机会"""
    symbol: str
    start_time: datetime
    end_time: datetime
    timeframe: str  # '5m', '15m', '1h'
    move_type: str  # 'pump' (上涨), 'dump' (下跌)
    price_change_pct: float  # 价格变化百分比
    volume_ratio: float  # 成交量倍数
    max_price: float
    min_price: float
    start_price: float
    end_price: float

    # 信号捕捉情况
    captured: bool  # 是否捕捉到
    capture_delay_minutes: Optional[int]  # 捕捉延迟(分钟)
    signal_type: Optional[str]  # 捕捉的信号类型
    position_pnl_pct: Optional[float]  # 实际盈亏百分比

    # 错过原因分析
    miss_reason: Optional[str]  # 错过的原因


@dataclass
class SignalPerformance:
    """信号表现统计"""
    signal_type: str
    total_signals: int
    captured_opportunities: int  # 捕捉到的大行情数
    missed_opportunities: int  # 错过的大行情数
    avg_capture_delay: float  # 平均捕捉延迟(分钟)

    # 交易统计
    total_trades: int
    winning_trades: int
    win_rate: float
    avg_pnl_pct: float
    best_trade_pnl_pct: float
    worst_trade_pnl_pct: float


@dataclass
class ReviewReport:
    """复盘报告"""
    date: str
    review_period: str  # '24h'

    # 大行情统计
    total_opportunities: int
    captured_count: int
    missed_count: int
    capture_rate: float

    # 按时间周期统计
    opportunities_by_timeframe: Dict[str, int]  # {'5m': 10, '15m': 5, '1h': 2}

    # 错过的机会列表
    missed_opportunities: List[BigMoveOpportunity]

    # 信号表现
    signal_performances: List[SignalPerformance]

    # 信号分析（新增）
    signal_analysis: Dict[str, any]  # 各类信号的详细分析

    # 机会分析（新增）
    opportunity_analysis: Dict[str, any]  # 交易机会分析（信号评分、捕获情况、错过原因）

    # 优化建议
    optimization_suggestions: List[str]

    # 参数调整建议
    parameter_adjustments: Dict[str, any]


class DailyReviewAnalyzer:
    """每日复盘分析器"""

    def __init__(self, db_config: dict):
        """
        初始化复盘分析器

        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config
        self.connection = None

        # 大行情识别阈值
        self.thresholds = {
            '5m': {
                'price_change_min': 0.5,  # 最小价格变化 0.5%
                'volume_ratio_min': 2.0,   # 最小成交量倍数 2x
                'duration_candles': 3      # 持续K线数量
            },
            '15m': {
                'price_change_min': 1.0,   # 1.0%
                'volume_ratio_min': 2.0,
                'duration_candles': 2
            },
            '1h': {
                'price_change_min': 2.0,   # 2.0%
                'volume_ratio_min': 1.5,
                'duration_candles': 2
            }
        }

        logger.info("✅ 每日复盘分析器已初始化")

    def _get_connection(self):
        """获取数据库连接"""
        if self.connection is None or not self.connection.open:
            port = self.db_config.get('port', 3306)
            # 确保port是整数类型
            if not isinstance(port, int):
                try:
                    port = int(port)
                except (ValueError, TypeError):
                    port = 3306

            self.connection = pymysql.connect(
                host=self.db_config.get('host', 'localhost'),
                port=port,
                user=self.db_config.get('user', 'root'),
                password=self.db_config.get('password', ''),
                database=self.db_config.get('database', 'binance-data'),
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
        return self.connection

    async def run_daily_review(self, symbols: List[str]) -> ReviewReport:
        """
        执行每日复盘分析

        Args:
            symbols: 要分析的交易对列表

        Returns:
            复盘报告
        """
        logger.info(f"🔍 开始每日复盘分析 | 交易对数量: {len(symbols)}")

        # 分析时间范围: 过去24小时
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=24)

        # 1. 识别所有大行情机会
        all_opportunities = []

        for symbol in symbols:
            # 分析不同时间周期
            for timeframe in ['5m', '15m', '1h']:
                opportunities = await self._detect_big_moves(
                    symbol, timeframe, start_time, end_time
                )
                all_opportunities.extend(opportunities)

        logger.info(f"📊 识别到 {len(all_opportunities)} 个大行情机会")

        # 2. 检查哪些机会被捕捉到
        for opp in all_opportunities:
            await self._check_if_captured(opp)

        # 3. 统计信号表现
        signal_performances = await self._analyze_signal_performance(start_time, end_time)

        # 4. 详细信号分析（新增）
        signal_analysis = await self._analyze_signals_detailed(start_time, end_time, all_opportunities)

        # 5. 机会分析（新增）
        opportunity_analysis = await self._analyze_entry_opportunities(start_time, end_time, all_opportunities)

        # 6. 生成优化建议
        optimization_suggestions = self._generate_optimization_suggestions(
            all_opportunities, signal_performances
        )

        # 7. 参数调整建议
        parameter_adjustments = self._suggest_parameter_adjustments(
            all_opportunities, signal_performances
        )

        # 8. 生成报告
        captured = [o for o in all_opportunities if o.captured]
        missed = [o for o in all_opportunities if not o.captured]

        timeframe_stats = {}
        for tf in ['5m', '15m', '1h']:
            timeframe_stats[tf] = len([o for o in all_opportunities if o.timeframe == tf])

        report = ReviewReport(
            date=end_time.strftime('%Y-%m-%d'),
            review_period='24h',
            total_opportunities=len(all_opportunities),
            captured_count=len(captured),
            missed_count=len(missed),
            capture_rate=len(captured) / len(all_opportunities) * 100 if all_opportunities else 0,
            opportunities_by_timeframe=timeframe_stats,
            missed_opportunities=missed[:20],  # 只保留前20个
            signal_performances=signal_performances,
            signal_analysis=signal_analysis,  # 新增
            opportunity_analysis=opportunity_analysis,  # 新增
            optimization_suggestions=optimization_suggestions,
            parameter_adjustments=parameter_adjustments
        )

        # 9. 保存报告到数据库（包括详细数据）
        await self._save_report(report)

        # 10. 生成可读报告
        self._print_report(report)

        logger.info(f"✅ 复盘分析完成 | 捕获率: {report.capture_rate:.1f}%")

        return report

    async def _detect_big_moves(
        self,
        symbol: str,
        timeframe: str,
        start_time: datetime,
        end_time: datetime
    ) -> List[BigMoveOpportunity]:
        """
        检测大行情机会

        Args:
            symbol: 交易对
            timeframe: 时间周期
            start_time: 开始时间
            end_time: 结束时间

        Returns:
            大行情机会列表
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # 获取K线数据
        cursor.execute("""
            SELECT
                timestamp,
                open_price,
                high_price,
                low_price,
                close_price,
                volume
            FROM kline_data
            WHERE symbol = %s
            AND timeframe = %s
            AND timestamp >= %s
            AND timestamp <= %s
            AND exchange = 'binance_futures'
            ORDER BY timestamp ASC
        """, (symbol, timeframe, start_time, end_time))

        klines = cursor.fetchall()
        cursor.close()

        if len(klines) < 10:
            return []

        # 计算平均成交量
        avg_volume = float(sum(k['volume'] for k in klines) / len(klines))

        opportunities = []
        threshold = self.thresholds[timeframe]

        # 滑动窗口检测大行情
        window_size = threshold['duration_candles']

        for i in range(len(klines) - window_size + 1):
            window = klines[i:i + window_size]

            # 计算窗口内的价格变化和成交量
            start_price = float(window[0]['open_price'])
            prices = []
            volumes = []

            for k in window:
                prices.extend([
                    float(k['open_price']),
                    float(k['high_price']),
                    float(k['low_price']),
                    float(k['close_price'])
                ])
                volumes.append(float(k['volume']))

            max_price = max(prices)
            min_price = min(prices)
            end_price = float(window[-1]['close_price'])

            # 计算价格变化和成交量倍数
            price_change_pct = abs(end_price - start_price) / start_price * 100
            avg_window_volume = sum(volumes) / len(volumes)
            volume_ratio = avg_window_volume / avg_volume if avg_volume > 0 else 0

            # 判断是否为大行情
            if (price_change_pct >= threshold['price_change_min'] and
                volume_ratio >= threshold['volume_ratio_min']):

                move_type = 'pump' if end_price > start_price else 'dump'

                opportunity = BigMoveOpportunity(
                    symbol=symbol,
                    start_time=window[0]['timestamp'],
                    end_time=window[-1]['timestamp'],
                    timeframe=timeframe,
                    move_type=move_type,
                    price_change_pct=price_change_pct if move_type == 'pump' else -price_change_pct,
                    volume_ratio=volume_ratio,
                    max_price=max_price,
                    min_price=min_price,
                    start_price=start_price,
                    end_price=end_price,
                    captured=False,
                    capture_delay_minutes=None,
                    signal_type=None,
                    position_pnl_pct=None,
                    miss_reason=None
                )

                opportunities.append(opportunity)

        return opportunities

    async def _check_if_captured(self, opportunity: BigMoveOpportunity):
        """
        检查大行情是否被捕捉到

        Args:
            opportunity: 大行情机会
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # 查找在机会时间范围内创建的持仓
        # 允许提前5分钟到延迟30分钟的持仓
        search_start = opportunity.start_time - timedelta(minutes=5)
        search_end = opportunity.end_time + timedelta(minutes=30)

        cursor.execute("""
            SELECT
                id,
                symbol,
                position_side,
                entry_signal_type,
                open_time,
                entry_price,
                mark_price,
                unrealized_pnl_pct,
                created_at
            FROM futures_positions
            WHERE symbol = %s
            AND open_time >= %s
            AND open_time <= %s
            AND status = 'closed'
            ORDER BY open_time ASC
            LIMIT 1
        """, (opportunity.symbol, search_start, search_end))

        position = cursor.fetchone()
        cursor.close()

        if position:
            # 检查方向是否匹配
            expected_direction = 'LONG' if opportunity.move_type == 'pump' else 'SHORT'

            if position['position_side'] == expected_direction:
                # 计算捕捉延迟
                delay_seconds = (position['open_time'] - opportunity.start_time).total_seconds()
                delay_minutes = int(delay_seconds / 60)

                opportunity.captured = True
                opportunity.capture_delay_minutes = delay_minutes
                opportunity.signal_type = position['entry_signal_type']
                opportunity.position_pnl_pct = float(position['unrealized_pnl_pct']) if position['unrealized_pnl_pct'] else None

                logger.debug(
                    f"✅ 捕捉到: {opportunity.symbol} {opportunity.move_type} "
                    f"{opportunity.price_change_pct:.2f}% | 延迟{delay_minutes}分钟"
                )
            else:
                opportunity.captured = False
                opportunity.miss_reason = f"方向错误(期望{expected_direction},实际{position['position_side']})"
        else:
            opportunity.captured = False
            opportunity.miss_reason = "未产生信号或未开仓"

    async def _analyze_signal_performance(
        self,
        start_time: datetime,
        end_time: datetime
    ) -> List[SignalPerformance]:
        """
        分析信号表现

        Args:
            start_time: 开始时间
            end_time: 结束时间

        Returns:
            信号表现列表
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # 获取所有信号类型的统计
        cursor.execute("""
            SELECT
                entry_signal_type,
                COUNT(*) as total_trades,
                SUM(CASE WHEN unrealized_pnl_pct > 0 THEN 1 ELSE 0 END) as winning_trades,
                AVG(unrealized_pnl_pct) as avg_pnl_pct,
                MAX(unrealized_pnl_pct) as best_trade,
                MIN(unrealized_pnl_pct) as worst_trade
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= %s
            AND close_time <= %s
            AND entry_signal_type IS NOT NULL
            GROUP BY entry_signal_type
        """, (start_time, end_time))

        results = cursor.fetchall()
        cursor.close()

        performances = []

        for row in results:
            signal_type = row['entry_signal_type']
            total_trades = row['total_trades']
            winning_trades = row['winning_trades'] or 0

            performance = SignalPerformance(
                signal_type=signal_type,
                total_signals=total_trades,  # 简化，假设每个信号都开仓
                captured_opportunities=0,  # 需要从opportunities中统计
                missed_opportunities=0,
                avg_capture_delay=0.0,
                total_trades=total_trades,
                winning_trades=winning_trades,
                win_rate=winning_trades / total_trades * 100 if total_trades > 0 else 0,
                avg_pnl_pct=float(row['avg_pnl_pct']) if row['avg_pnl_pct'] else 0,
                best_trade_pnl_pct=float(row['best_trade']) if row['best_trade'] else 0,
                worst_trade_pnl_pct=float(row['worst_trade']) if row['worst_trade'] else 0
            )

            performances.append(performance)

        return performances

    def _generate_optimization_suggestions(
        self,
        opportunities: List[BigMoveOpportunity],
        performances: List[SignalPerformance]
    ) -> List[str]:
        """
        生成优化建议

        Args:
            opportunities: 大行情机会列表
            performances: 信号表现列表

        Returns:
            优化建议列表
        """
        suggestions = []

        # 1. 分析错过的机会
        missed = [o for o in opportunities if not o.captured]

        if len(missed) > len(opportunities) * 0.3:  # 错过超过30%
            suggestions.append(
                f"⚠️ 错过了{len(missed)}/{len(opportunities)}个大行情机会 ({len(missed)/len(opportunities)*100:.1f}%)，"
                "建议降低信号触发阈值"
            )

        # 2. 按错过原因分类
        miss_reasons = {}
        for opp in missed:
            reason = opp.miss_reason or "未知原因"
            miss_reasons[reason] = miss_reasons.get(reason, 0) + 1

        for reason, count in sorted(miss_reasons.items(), key=lambda x: x[1], reverse=True)[:3]:
            suggestions.append(f"🔍 主要错过原因: {reason} ({count}次)")

        # 3. 分析延迟
        captured_with_delay = [o for o in opportunities if o.captured and o.capture_delay_minutes is not None]

        if captured_with_delay:
            avg_delay = sum(o.capture_delay_minutes for o in captured_with_delay) / len(captured_with_delay)

            if avg_delay > 10:
                suggestions.append(
                    f"⏰ 平均捕捉延迟{avg_delay:.1f}分钟，建议优化信号检测速度或降低触发条件"
                )

        # 4. 分析信号胜率
        low_win_rate_signals = [p for p in performances if p.win_rate < 50 and p.total_trades >= 5]

        for perf in low_win_rate_signals:
            suggestions.append(
                f"📉 信号 '{perf.signal_type}' 胜率较低({perf.win_rate:.1f}%)，"
                f"建议调整参数或考虑禁用"
            )

        # 5. 分析不同时间周期的机会分布
        timeframe_counts = {}
        for opp in opportunities:
            timeframe_counts[opp.timeframe] = timeframe_counts.get(opp.timeframe, 0) + 1

        if '5m' in timeframe_counts and timeframe_counts['5m'] > sum(timeframe_counts.values()) * 0.5:
            suggestions.append(
                "📊 5分钟级别机会较多，建议增加5M高频信号检测"
            )

        return suggestions

    def _suggest_parameter_adjustments(
        self,
        opportunities: List[BigMoveOpportunity],
        performances: List[SignalPerformance]
    ) -> Dict[str, any]:
        """
        建议参数调整

        Args:
            opportunities: 大行情机会列表
            performances: 信号表现列表

        Returns:
            参数调整建议字典
        """
        adjustments = {}

        # 1. 如果错过太多pump机会，建议降低BOTTOM_REVERSAL_LONG阈值
        missed_pumps = [o for o in opportunities if not o.captured and o.move_type == 'pump']

        if len(missed_pumps) >= 5:
            adjustments['BOTTOM_REVERSAL_LONG'] = {
                'current_threshold': 50,
                'suggested_threshold': 40,
                'reason': f'错过了{len(missed_pumps)}个上涨机会'
            }

        # 2. 如果错过太多dump机会，建议降低WEAK_RALLY_SHORT阈值
        missed_dumps = [o for o in opportunities if not o.captured and o.move_type == 'dump']

        if len(missed_dumps) >= 5:
            adjustments['WEAK_RALLY_SHORT'] = {
                'current_threshold': 50,
                'suggested_threshold': 40,
                'reason': f'错过了{len(missed_dumps)}个下跌机会'
            }

        # 3. 如果平均延迟太高，建议缩短采样窗口
        captured_with_delay = [o for o in opportunities if o.captured and o.capture_delay_minutes is not None]

        if captured_with_delay:
            avg_delay = sum(o.capture_delay_minutes for o in captured_with_delay) / len(captured_with_delay)

            if avg_delay > 10:
                adjustments['price_sampling'] = {
                    'current_window': '5m',
                    'suggested_window': '3m',
                    'reason': f'平均延迟{avg_delay:.1f}分钟过高'
                }

        return adjustments

    async def _analyze_signals_detailed(
        self,
        start_time: datetime,
        end_time: datetime,
        opportunities: List[BigMoveOpportunity]
    ) -> Dict[str, any]:
        """
        详细分析信号表现

        Args:
            start_time: 开始时间
            end_time: 结束时间
            opportunities: 大行情机会列表

        Returns:
            信号分析字典
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # 1. 获取所有信号的详细数据
        cursor.execute("""
            SELECT
                entry_signal_type,
                position_side,
                entry_price,
                mark_price,
                unrealized_pnl_pct,
                open_time,
                close_time,
                symbol
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= %s
            AND close_time <= %s
            AND entry_signal_type IS NOT NULL
            ORDER BY close_time DESC
        """, (start_time, end_time))

        trades = cursor.fetchall()
        cursor.close()

        # 2. 按信号类型分组统计
        signal_stats = {}

        for trade in trades:
            signal_type = trade['entry_signal_type']

            if signal_type not in signal_stats:
                signal_stats[signal_type] = {
                    'total_trades': 0,
                    'win_trades': 0,
                    'loss_trades': 0,
                    'total_pnl': 0,
                    'best_trade': None,
                    'worst_trade': None,
                    'long_trades': 0,
                    'short_trades': 0,
                    'avg_holding_minutes': 0,
                    'captured_opportunities': 0
                }

            stats = signal_stats[signal_type]
            stats['total_trades'] += 1

            pnl = float(trade['unrealized_pnl_pct']) if trade['unrealized_pnl_pct'] else 0
            stats['total_pnl'] += pnl

            if pnl > 0:
                stats['win_trades'] += 1
            else:
                stats['loss_trades'] += 1

            if stats['best_trade'] is None or pnl > stats['best_trade']:
                stats['best_trade'] = pnl

            if stats['worst_trade'] is None or pnl < stats['worst_trade']:
                stats['worst_trade'] = pnl

            if trade['position_side'] == 'LONG':
                stats['long_trades'] += 1
            else:
                stats['short_trades'] += 1

            # 计算持仓时长
            if trade['close_time'] and trade['open_time']:
                holding_time = (trade['close_time'] - trade['open_time']).total_seconds() / 60
                stats['avg_holding_minutes'] += holding_time

        # 3. 计算平均值和比率
        for signal_type, stats in signal_stats.items():
            if stats['total_trades'] > 0:
                stats['win_rate'] = stats['win_trades'] / stats['total_trades'] * 100
                stats['avg_pnl'] = stats['total_pnl'] / stats['total_trades']
                stats['avg_holding_minutes'] = stats['avg_holding_minutes'] / stats['total_trades']

                # 统计捕获的大行情
                stats['captured_opportunities'] = len([
                    o for o in opportunities
                    if o.captured and o.signal_type == signal_type
                ])

        # 4. 生成信号评级
        for signal_type, stats in signal_stats.items():
            score = 0

            # 胜率权重: 50%
            if stats['win_rate'] >= 60:
                score += 50
            elif stats['win_rate'] >= 50:
                score += 30
            elif stats['win_rate'] >= 40:
                score += 10

            # 平均盈亏权重: 30%
            if stats['avg_pnl'] >= 1.5:
                score += 30
            elif stats['avg_pnl'] >= 0.5:
                score += 20
            elif stats['avg_pnl'] >= 0:
                score += 10

            # 捕获机会权重: 20%
            if stats['captured_opportunities'] >= 5:
                score += 20
            elif stats['captured_opportunities'] >= 3:
                score += 10
            elif stats['captured_opportunities'] >= 1:
                score += 5

            # 评级
            if score >= 80:
                stats['rating'] = '🌟优秀'
            elif score >= 60:
                stats['rating'] = '✅良好'
            elif score >= 40:
                stats['rating'] = '⚠️一般'
            else:
                stats['rating'] = '❌较差'

            stats['score'] = score

        return {
            'signal_stats': signal_stats,
            'total_signals': len(signal_stats),
            'summary': {
                'best_signal': max(signal_stats.items(), key=lambda x: x[1]['score'])[0] if signal_stats else None,
                'worst_signal': min(signal_stats.items(), key=lambda x: x[1]['score'])[0] if signal_stats else None
            }
        }

    async def _analyze_entry_opportunities(
        self,
        start_time: datetime,
        end_time: datetime,
        opportunities: List[BigMoveOpportunity]
    ) -> Dict[str, any]:
        """
        分析交易机会的捕获情况（包括信号评分对比、捕获/错过分析）

        Args:
            start_time: 开始时间
            end_time: 结束时间
            opportunities: 大行情机会列表

        Returns:
            机会分析字典（信号评分、捕获情况、错过原因等）
        """
        # 1. 按时间周期统计捕获情况
        timeframe_analysis = {}

        for tf in ['5m', '15m', '1h']:
            tf_opps = [o for o in opportunities if o.timeframe == tf]
            captured = [o for o in tf_opps if o.captured]
            missed = [o for o in tf_opps if not o.captured]

            # 统计pump和dump
            pumps = [o for o in tf_opps if o.move_type == 'pump']
            dumps = [o for o in tf_opps if o.move_type == 'dump']

            captured_pumps = [o for o in pumps if o.captured]
            captured_dumps = [o for o in dumps if o.captured]

            timeframe_analysis[tf] = {
                'total_opportunities': len(tf_opps),
                'captured': len(captured),
                'missed': len(missed),
                'capture_rate': len(captured) / len(tf_opps) * 100 if tf_opps else 0,
                'pumps': {
                    'total': len(pumps),
                    'captured': len(captured_pumps),
                    'rate': len(captured_pumps) / len(pumps) * 100 if pumps else 0
                },
                'dumps': {
                    'total': len(dumps),
                    'captured': len(captured_dumps),
                    'rate': len(captured_dumps) / len(dumps) * 100 if dumps else 0
                },
                'avg_price_change': sum(abs(o.price_change_pct) for o in tf_opps) / len(tf_opps) if tf_opps else 0,
                'avg_volume_ratio': sum(o.volume_ratio for o in tf_opps) / len(tf_opps) if tf_opps else 0
            }

        # 2. 分析错过的原因分布
        miss_reasons = {}
        for opp in opportunities:
            if not opp.captured and opp.miss_reason:
                reason = opp.miss_reason
                if reason not in miss_reasons:
                    miss_reasons[reason] = {
                        'count': 0,
                        'total_pct_change': 0,
                        'examples': []
                    }
                miss_reasons[reason]['count'] += 1
                miss_reasons[reason]['total_pct_change'] += abs(opp.price_change_pct)

                if len(miss_reasons[reason]['examples']) < 3:
                    miss_reasons[reason]['examples'].append({
                        'symbol': opp.symbol,
                        'time': opp.start_time.strftime('%H:%M'),
                        'change': abs(opp.price_change_pct),
                        'type': opp.move_type
                    })

        # 计算平均错失幅度
        for reason, data in miss_reasons.items():
            data['avg_missed_change'] = data['total_pct_change'] / data['count']

        # 3. 按交易对统计
        symbol_analysis = {}
        for opp in opportunities:
            symbol = opp.symbol
            if symbol not in symbol_analysis:
                symbol_analysis[symbol] = {
                    'total_opportunities': 0,
                    'captured': 0,
                    'missed': 0
                }

            symbol_analysis[symbol]['total_opportunities'] += 1
            if opp.captured:
                symbol_analysis[symbol]['captured'] += 1
            else:
                symbol_analysis[symbol]['missed'] += 1

        # 计算捕获率并排序
        for symbol, stats in symbol_analysis.items():
            stats['capture_rate'] = stats['captured'] / stats['total_opportunities'] * 100 if stats['total_opportunities'] > 0 else 0

        # 找出表现最好和最差的交易对
        sorted_symbols = sorted(symbol_analysis.items(), key=lambda x: x[1]['capture_rate'], reverse=True)

        # 4. 时间分布分析（按小时统计）
        hour_analysis = {}
        for opp in opportunities:
            hour = opp.start_time.hour
            if hour not in hour_analysis:
                hour_analysis[hour] = {'total': 0, 'captured': 0}

            hour_analysis[hour]['total'] += 1
            if opp.captured:
                hour_analysis[hour]['captured'] += 1

        # 计算每小时捕获率
        for hour, stats in hour_analysis.items():
            stats['rate'] = stats['captured'] / stats['total'] * 100 if stats['total'] > 0 else 0

        return {
            'timeframe_analysis': timeframe_analysis,
            'miss_reasons': miss_reasons,
            'symbol_analysis': {
                'all_symbols': symbol_analysis,
                'best_symbols': sorted_symbols[:5] if sorted_symbols else [],
                'worst_symbols': sorted_symbols[-5:] if len(sorted_symbols) >= 5 else []
            },
            'hour_analysis': hour_analysis,
            'summary': {
                'total_opportunities': len(opportunities),
                'best_timeframe': max(timeframe_analysis.items(), key=lambda x: x[1]['capture_rate'])[0] if timeframe_analysis else None,
                'worst_timeframe': min(timeframe_analysis.items(), key=lambda x: x[1]['capture_rate'])[0] if timeframe_analysis else None,
                'main_miss_reason': max(miss_reasons.items(), key=lambda x: x[1]['count'])[0] if miss_reasons else None
            }
        }

    async def _save_report(self, report: ReviewReport):
        """
        保存复盘报告到数据库

        Args:
            report: 复盘报告
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # 1. 创建主报告表（如果不存在）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_review_reports (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    date DATE NOT NULL,
                    report_json MEDIUMTEXT NOT NULL,
                    total_opportunities INT,
                    captured_count INT,
                    missed_count INT,
                    capture_rate FLOAT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_date (date),
                    INDEX idx_date (date),
                    INDEX idx_capture_rate (capture_rate)
                )
            """)

            # 2. 创建机会详情表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_review_opportunities (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    review_date DATE NOT NULL,
                    symbol VARCHAR(20) NOT NULL,
                    timeframe VARCHAR(10) NOT NULL,
                    move_type VARCHAR(10) NOT NULL,
                    start_time DATETIME NOT NULL,
                    end_time DATETIME NOT NULL,
                    price_change_pct FLOAT NOT NULL,
                    volume_ratio FLOAT NOT NULL,
                    captured BOOLEAN NOT NULL,
                    capture_delay_minutes INT,
                    signal_type VARCHAR(50),
                    position_pnl_pct FLOAT,
                    miss_reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_review_date (review_date),
                    INDEX idx_symbol (symbol),
                    INDEX idx_captured (captured),
                    INDEX idx_timeframe (timeframe)
                )
            """)

            # 3. 创建信号分析表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_review_signal_analysis (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    review_date DATE NOT NULL,
                    signal_type VARCHAR(50) NOT NULL,
                    total_trades INT NOT NULL,
                    win_trades INT NOT NULL,
                    loss_trades INT NOT NULL,
                    win_rate FLOAT NOT NULL,
                    avg_pnl FLOAT NOT NULL,
                    best_trade FLOAT,
                    worst_trade FLOAT,
                    long_trades INT NOT NULL,
                    short_trades INT NOT NULL,
                    avg_holding_minutes FLOAT,
                    captured_opportunities INT NOT NULL,
                    rating VARCHAR(20),
                    score INT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_review_signal (review_date, signal_type),
                    INDEX idx_review_date (review_date),
                    INDEX idx_score (score)
                )
            """)

            # 4. 插入或更新主报告
            cursor.execute("""
                INSERT INTO daily_review_reports
                (date, report_json, total_opportunities, captured_count, missed_count, capture_rate)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                report_json = VALUES(report_json),
                total_opportunities = VALUES(total_opportunities),
                captured_count = VALUES(captured_count),
                missed_count = VALUES(missed_count),
                capture_rate = VALUES(capture_rate)
            """, (
                report.date,
                json.dumps(asdict(report), ensure_ascii=False, default=str),
                report.total_opportunities,
                report.captured_count,
                report.missed_count,
                report.capture_rate
            ))

            # 5. 删除当天旧的机会记录
            cursor.execute("""
                DELETE FROM daily_review_opportunities WHERE review_date = %s
            """, (report.date,))

            # 6. 插入所有机会（捕获的和错过的）
            all_opps = report.missed_opportunities if report.missed_opportunities else []

            # 如果report里有captured的机会，也需要保存（这里简化处理，可以后续优化）
            for opp in all_opps:
                cursor.execute("""
                    INSERT INTO daily_review_opportunities
                    (review_date, symbol, timeframe, move_type, start_time, end_time,
                     price_change_pct, volume_ratio, captured, capture_delay_minutes,
                     signal_type, position_pnl_pct, miss_reason)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    report.date, opp.symbol, opp.timeframe, opp.move_type,
                    opp.start_time, opp.end_time, opp.price_change_pct, opp.volume_ratio,
                    opp.captured, opp.capture_delay_minutes, opp.signal_type,
                    opp.position_pnl_pct, opp.miss_reason
                ))

            # 7. 删除当天旧的信号分析
            cursor.execute("""
                DELETE FROM daily_review_signal_analysis WHERE review_date = %s
            """, (report.date,))

            # 8. 插入信号分析数据
            if report.signal_analysis and report.signal_analysis.get('signal_stats'):
                for signal_type, stats in report.signal_analysis['signal_stats'].items():
                    cursor.execute("""
                        INSERT INTO daily_review_signal_analysis
                        (review_date, signal_type, total_trades, win_trades, loss_trades,
                         win_rate, avg_pnl, best_trade, worst_trade, long_trades, short_trades,
                         avg_holding_minutes, captured_opportunities, rating, score)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        report.date, signal_type, stats['total_trades'],
                        stats['win_trades'], stats['loss_trades'], stats['win_rate'],
                        stats['avg_pnl'], stats['best_trade'], stats['worst_trade'],
                        stats['long_trades'], stats['short_trades'], stats['avg_holding_minutes'],
                        stats['captured_opportunities'], stats['rating'], stats['score']
                    ))

            conn.commit()
            logger.info(f"💾 复盘报告及详细数据已保存到数据库: {report.date}")

        except Exception as e:
            logger.error(f"保存复盘报告失败: {e}")
            conn.rollback()
        finally:
            cursor.close()

    def _print_report(self, report: ReviewReport):
        """
        打印复盘报告

        Args:
            report: 复盘报告
        """
        logger.info("\n" + "="*80)
        logger.info(f"📊 每日复盘报告 - {report.date}")
        logger.info("="*80)

        logger.info(f"\n【大行情统计】")
        logger.info(f"  总机会数: {report.total_opportunities}")
        logger.info(f"  已捕获: {report.captured_count} ({report.capture_rate:.1f}%)")
        logger.info(f"  已错过: {report.missed_count}")

        logger.info(f"\n【按时间周期】")
        for tf, count in report.opportunities_by_timeframe.items():
            logger.info(f"  {tf}: {count}个机会")

        logger.info(f"\n【错过的重要机会】(前5个)")
        for i, opp in enumerate(report.missed_opportunities[:5], 1):
            logger.info(
                f"  {i}. {opp.symbol} {opp.timeframe} {opp.move_type.upper()} "
                f"{abs(opp.price_change_pct):.2f}% | {opp.volume_ratio:.1f}x量能"
            )
            logger.info(f"     时间: {opp.start_time.strftime('%H:%M')} - {opp.end_time.strftime('%H:%M')}")
            logger.info(f"     原因: {opp.miss_reason}")

        logger.info(f"\n【信号表现】")
        for perf in report.signal_performances:
            logger.info(
                f"  {perf.signal_type}: "
                f"{perf.total_trades}笔 | "
                f"胜率{perf.win_rate:.1f}% | "
                f"平均{perf.avg_pnl_pct:.2f}%"
            )

        # 信号分析（新增）
        if report.signal_analysis and report.signal_analysis.get('signal_stats'):
            logger.info(f"\n【信号分析】")

            signal_stats = report.signal_analysis['signal_stats']

            # 按评分排序
            sorted_signals = sorted(signal_stats.items(), key=lambda x: x[1]['score'], reverse=True)

            for signal_type, stats in sorted_signals:
                logger.info(
                    f"  {stats['rating']} {signal_type} (评分: {stats['score']})"
                )
                logger.info(
                    f"     交易: {stats['total_trades']}笔 | "
                    f"胜率: {stats['win_rate']:.1f}% | "
                    f"平均盈亏: {stats['avg_pnl']:.2f}%"
                )
                logger.info(
                    f"     最佳: +{stats['best_trade']:.2f}% | "
                    f"最差: {stats['worst_trade']:.2f}% | "
                    f"捕获机会: {stats['captured_opportunities']}个"
                )
                logger.info(
                    f"     做多: {stats['long_trades']}笔 | "
                    f"做空: {stats['short_trades']}笔 | "
                    f"平均持仓: {stats['avg_holding_minutes']:.0f}分钟"
                )

            if report.signal_analysis.get('summary'):
                summary = report.signal_analysis['summary']
                logger.info(
                    f"\n  💡 最佳信号: {summary.get('best_signal', 'N/A')} | "
                    f"需改进信号: {summary.get('worst_signal', 'N/A')}"
                )

        # 机会分析（新增）
        if report.opportunity_analysis:
            logger.info(f"\n【机会分析】")

            tf_analysis = report.opportunity_analysis.get('timeframe_analysis', {})

            # 1. 按时间周期展示
            logger.info("  时间周期表现:")
            for tf in ['5m', '15m', '1h']:
                if tf in tf_analysis:
                    stats = tf_analysis[tf]
                    logger.info(
                        f"    {tf.upper()}: 机会{stats['total_opportunities']}个 | "
                        f"捕获{stats['captured']}个 ({stats['capture_rate']:.1f}%) | "
                        f"错过{stats['missed']}个"
                    )
                    logger.info(
                        f"          上涨: {stats['pumps']['captured']}/{stats['pumps']['total']} ({stats['pumps']['rate']:.1f}%) | "
                        f"下跌: {stats['dumps']['captured']}/{stats['dumps']['total']} ({stats['dumps']['rate']:.1f}%)"
                    )

            # 2. 错过原因分析
            miss_reasons = report.opportunity_analysis.get('miss_reasons', {})
            if miss_reasons:
                logger.info(f"\n  主要错过原因:")
                sorted_reasons = sorted(miss_reasons.items(), key=lambda x: x[1]['count'], reverse=True)[:3]

                for reason, data in sorted_reasons:
                    logger.info(
                        f"    • {reason}: {data['count']}次 | "
                        f"平均错失{data['avg_missed_change']:.2f}%涨跌幅"
                    )

                    # 显示示例
                    if data.get('examples'):
                        examples = data['examples'][:2]
                        for ex in examples:
                            logger.info(
                                f"      - {ex['symbol']} {ex['time']} {ex['type'].upper()} {ex['change']:.2f}%"
                            )

            # 3. 交易对表现
            symbol_analysis = report.opportunity_analysis.get('symbol_analysis', {})
            if symbol_analysis:
                best_symbols = symbol_analysis.get('best_symbols', [])[:3]
                worst_symbols = symbol_analysis.get('worst_symbols', [])[:3]

                if best_symbols:
                    logger.info(f"\n  捕获率最高交易对:")
                    for symbol, stats in best_symbols:
                        logger.info(
                            f"    ✅ {symbol}: {stats['capture_rate']:.1f}% "
                            f"({stats['captured']}/{stats['total_opportunities']})"
                        )

                if worst_symbols:
                    logger.info(f"\n  捕获率最低交易对:")
                    for symbol, stats in worst_symbols:
                        logger.info(
                            f"    ❌ {symbol}: {stats['capture_rate']:.1f}% "
                            f"({stats['captured']}/{stats['total_opportunities']})"
                        )

            # 4. 总结建议
            if report.opportunity_analysis.get('summary'):
                summary = report.opportunity_analysis['summary']
                logger.info(f"\n  📊 总结:")
                logger.info(f"     最佳时间周期: {summary.get('best_timeframe', 'N/A')}")
                logger.info(f"     最弱时间周期: {summary.get('worst_timeframe', 'N/A')}")
                logger.info(f"     主要错过原因: {summary.get('main_miss_reason', 'N/A')}")

        logger.info(f"\n【优化建议】")
        for suggestion in report.optimization_suggestions:
            logger.info(f"  {suggestion}")

        if report.parameter_adjustments:
            logger.info(f"\n【参数调整建议】")
            for param, adjustment in report.parameter_adjustments.items():
                logger.info(f"  {param}:")
                logger.info(f"    原因: {adjustment['reason']}")
                if 'current_threshold' in adjustment:
                    logger.info(
                        f"    建议: {adjustment['current_threshold']} → "
                        f"{adjustment['suggested_threshold']}"
                    )

        logger.info("\n" + "="*80 + "\n")


async def main():
    """测试主函数"""
    db_config = {
        'host': 'localhost',
        'port': 3306,
        'user': 'root',
        'password': '',
        'database': 'binance-data'
    }

    analyzer = DailyReviewAnalyzer(db_config)

    # 测试交易对
    test_symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT']

    report = await analyzer.run_daily_review(test_symbols)

    print(f"\n复盘完成！捕获率: {report.capture_rate:.1f}%")


if __name__ == '__main__':
    asyncio.run(main())
