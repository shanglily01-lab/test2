#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用K线评分计算器
适用于所有代币（包括Big4和其他代币）
"""
import pymysql
from typing import Dict, Optional
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class KlineScoreCalculator:
    """K线评分计算器（通用版）"""

    def __init__(self, db_config: Dict):
        self.db_config = db_config

    def calculate_score(self, symbol: str, direction: str = None) -> Dict:
        """
        计算代币的K线评分

        Args:
            symbol: 交易对（如 'RAY/USDT'）
            direction: 可选，指定方向 'LONG' 或 'SHORT'，用于计算5M反向加分

        Returns:
            {
                '1h_analysis': {'bullish_count': 7, 'bearish_count': 3, 'score': 20, 'level': '强多'},
                '15m_analysis': {'bullish_count': 6, 'bearish_count': 4, 'score': 10, 'level': '中多'},
                '5m_signal': {'bullish_count': 1, 'bearish_count': 2},
                'total_score': 25,  # 1H + 15M + 5M反向加分
                'main_score': 30,   # 1H + 15M（不含5M加分）
                'direction': 'LONG',  # LONG/SHORT/NEUTRAL
                'strength_level': 'strong',  # strong/medium/weak
                'reason': '1H强多(+20) + 15M中多(+10) + 5M部分阴回调(+5) = 总分+35'
            }
        """
        try:
            # 1. 分析1H K线
            h1_analysis = self._analyze_klines(symbol, '1h', count=10)

            # 2. 分析15M K线
            m15_analysis = self._analyze_klines(symbol, '15m', count=10)

            # 3. 分析5M K线（用于反向加分）
            m5_signal = self._analyze_klines(symbol, '5m', count=3, simple=True)

            # 4. 计算主趋势分数（1H + 15M）
            main_score = h1_analysis['score'] + m15_analysis['score']

            # 5. 计算5M反向加分
            five_m_bonus = 0
            bonus_desc = ""

            # 如果指定了方向，只计算该方向的加分
            if direction:
                if direction == 'LONG' and main_score > 0:
                    five_m_bonus, bonus_desc = self._calculate_reverse_bonus(
                        m5_signal, 'LONG'
                    )
                elif direction == 'SHORT' and main_score < 0:
                    five_m_bonus, bonus_desc = self._calculate_reverse_bonus(
                        m5_signal, 'SHORT'
                    )
            else:
                # 自动判断方向
                if main_score > 0:  # 主趋势多头
                    five_m_bonus, bonus_desc = self._calculate_reverse_bonus(
                        m5_signal, 'LONG'
                    )
                elif main_score < 0:  # 主趋势空头
                    five_m_bonus, bonus_desc = self._calculate_reverse_bonus(
                        m5_signal, 'SHORT'
                    )

            # 6. 计算总分
            total_score = main_score + five_m_bonus

            # 7. 判断方向和强度
            if total_score > 15:
                trend_direction = 'LONG'
                strength_level = 'strong'
            elif total_score > 5:
                trend_direction = 'LONG'
                strength_level = 'medium'
            elif total_score < -15:
                trend_direction = 'SHORT'
                strength_level = 'strong'
            elif total_score < -5:
                trend_direction = 'SHORT'
                strength_level = 'medium'
            else:
                trend_direction = 'NEUTRAL'
                strength_level = 'weak'

            # 8. 生成原因说明
            reason = self._generate_reason(
                h1_analysis, m15_analysis, five_m_bonus, bonus_desc, total_score
            )

            return {
                '1h_analysis': h1_analysis,
                '15m_analysis': m15_analysis,
                '5m_signal': m5_signal,
                'total_score': total_score,
                'main_score': main_score,
                'five_m_bonus': five_m_bonus,
                'direction': trend_direction,
                'strength_level': strength_level,
                'reason': reason
            }

        except Exception as e:
            logger.error(f"❌ {symbol} 计算K线评分失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _analyze_klines(
        self,
        symbol: str,
        timeframe: str,
        count: int = 10,
        simple: bool = False
    ) -> Dict:
        """
        分析K线数据

        Args:
            symbol: 交易对
            timeframe: 时间周期
            count: K线数量
            simple: 是否简化输出（只返回阳/阴计数）
        """
        try:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # 查询最近N根K线
            cursor.execute("""
                SELECT open_price, close_price, high_price, low_price, volume, open_time
                FROM kline_data
                WHERE symbol = %s
                  AND timeframe = %s
                  AND exchange = 'binance_futures'
                ORDER BY open_time DESC
                LIMIT %s
            """, (symbol, timeframe, count))

            klines = cursor.fetchall()
            cursor.close()
            conn.close()

            if not klines:
                logger.warning(f"⚠️ {symbol} {timeframe} 没有K线数据")
                return self._empty_analysis()

            # 统计阳线/阴线
            bullish_count = 0
            bearish_count = 0

            for kline in klines:
                open_price = float(kline['open_price'])
                close_price = float(kline['close_price'])

                if close_price > open_price:
                    bullish_count += 1
                elif close_price < open_price:
                    bearish_count += 1

            # 简化输出（5M用）
            if simple:
                return {
                    'bullish_count': bullish_count,
                    'bearish_count': bearish_count
                }

            # 完整分析（1H/15M用）
            # 计算得分：每根阳线+5分，每根阴线-5分
            score = (bullish_count - bearish_count) * 5

            # 判断级别
            if score >= 20:
                level = '强多'
            elif score >= 10:
                level = '中多'
            elif score > 0:
                level = '弱多'
            elif score <= -20:
                level = '强空'
            elif score <= -10:
                level = '中空'
            elif score < 0:
                level = '弱空'
            else:
                level = '中性'

            return {
                'bullish_count': bullish_count,
                'bearish_count': bearish_count,
                'score': score,
                'level': level
            }

        except Exception as e:
            logger.error(f"❌ {symbol} {timeframe} K线分析失败: {e}")
            return self._empty_analysis()

    def _calculate_reverse_bonus(self, m5_signal: Dict, direction: str) -> tuple:
        """
        计算5M反向加分

        Args:
            m5_signal: 5M K线信号
            direction: 主趋势方向 'LONG' 或 'SHORT'

        Returns:
            (加分, 描述)
        """
        if direction == 'LONG':
            # 做多：需要阴线回调
            bearish = m5_signal['bearish_count']
            if bearish == 3:
                return (10, "5M全阴回调(+10)")
            elif bearish == 2:
                return (5, "5M部分阴回调(+5)")
            else:
                return (0, "")

        elif direction == 'SHORT':
            # 做空：需要阳线反弹
            bullish = m5_signal['bullish_count']
            if bullish == 3:
                return (10, "5M全阳反弹(+10)")
            elif bullish == 2:
                return (5, "5M部分阳反弹(+5)")
            else:
                return (0, "")

        return (0, "")

    def _generate_reason(
        self,
        h1_analysis: Dict,
        m15_analysis: Dict,
        five_m_bonus: int,
        bonus_desc: str,
        total_score: int
    ) -> str:
        """生成评分原因说明"""
        parts = []

        # 1H
        h1_score = h1_analysis['score']
        h1_level = h1_analysis['level']
        parts.append(f"1H{h1_level}({h1_score:+d})")

        # 15M
        m15_score = m15_analysis['score']
        m15_level = m15_analysis['level']
        parts.append(f"15M{m15_level}({m15_score:+d})")

        # 5M加分
        if five_m_bonus > 0:
            parts.append(bonus_desc)

        # 总分
        parts.append(f"总分({total_score:+d})")

        return " + ".join(parts)

    def _empty_analysis(self) -> Dict:
        """返回空分析结果"""
        return {
            'bullish_count': 0,
            'bearish_count': 0,
            'score': 0,
            'level': '无数据'
        }
