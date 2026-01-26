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

        # 4. 生成优化建议
        optimization_suggestions = self._generate_optimization_suggestions(
            all_opportunities, signal_performances
        )

        # 5. 参数调整建议
        parameter_adjustments = self._suggest_parameter_adjustments(
            all_opportunities, signal_performances
        )

        # 6. 生成报告
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
            optimization_suggestions=optimization_suggestions,
            parameter_adjustments=parameter_adjustments
        )

        # 7. 保存报告到数据库
        await self._save_report(report)

        # 8. 生成可读报告
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
        avg_volume = sum(k['volume'] for k in klines) / len(klines)

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
                direction,
                entry_signal_type,
                entry_signal_time,
                avg_entry_price,
                exit_price,
                realized_pnl_pct,
                created_at
            FROM futures_positions
            WHERE symbol = %s
            AND entry_signal_time >= %s
            AND entry_signal_time <= %s
            AND status = 'closed'
            ORDER BY entry_signal_time ASC
            LIMIT 1
        """, (opportunity.symbol, search_start, search_end))

        position = cursor.fetchone()
        cursor.close()

        if position:
            # 检查方向是否匹配
            expected_direction = 'LONG' if opportunity.move_type == 'pump' else 'SHORT'

            if position['direction'] == expected_direction:
                # 计算捕捉延迟
                delay_seconds = (position['entry_signal_time'] - opportunity.start_time).total_seconds()
                delay_minutes = int(delay_seconds / 60)

                opportunity.captured = True
                opportunity.capture_delay_minutes = delay_minutes
                opportunity.signal_type = position['entry_signal_type']
                opportunity.position_pnl_pct = float(position['realized_pnl_pct']) if position['realized_pnl_pct'] else None

                logger.debug(
                    f"✅ 捕捉到: {opportunity.symbol} {opportunity.move_type} "
                    f"{opportunity.price_change_pct:.2f}% | 延迟{delay_minutes}分钟"
                )
            else:
                opportunity.captured = False
                opportunity.miss_reason = f"方向错误(期望{expected_direction},实际{position['direction']})"
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
                SUM(CASE WHEN realized_pnl_pct > 0 THEN 1 ELSE 0 END) as winning_trades,
                AVG(realized_pnl_pct) as avg_pnl_pct,
                MAX(realized_pnl_pct) as best_trade,
                MIN(realized_pnl_pct) as worst_trade
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

    async def _save_report(self, report: ReviewReport):
        """
        保存复盘报告到数据库

        Args:
            report: 复盘报告
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # 创建表（如果不存在）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_review_reports (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    date DATE NOT NULL,
                    report_json TEXT NOT NULL,
                    total_opportunities INT,
                    captured_count INT,
                    missed_count INT,
                    capture_rate FLOAT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_date (date)
                )
            """)

            # 插入或更新报告
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

            conn.commit()
            logger.info(f"💾 复盘报告已保存到数据库: {report.date}")

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
