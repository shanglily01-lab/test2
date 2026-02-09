#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多周期趋势分析器
用于确定高确定性交易机会

核心逻辑：
1. 30H K线 → 大趋势方向
2. 5H K线  → 中期趋势确认
3. 15M K线 → 小趋势入场时机
4. 5M K线  → 精准入场价格

只有三个周期（30H + 5H + 15M）完全共振，才认为是高确定性机会
"""

from typing import Dict, List, Optional
from datetime import datetime, timedelta
import pymysql
from loguru import logger


class MultiTimeframeAnalyzer:
    """多周期趋势分析器"""

    def __init__(self, db_config: dict):
        self.db_config = db_config

        # 确定性阈值
        self.CERTAINTY_THRESHOLD = 0.70  # 70%确定性才开仓

        # 趋势判断参数
        self.CONSECUTIVE_THRESHOLD = 3   # 连续3根同向K线
        self.SLOPE_THRESHOLD = 0.015     # 趋势斜率阈值1.5%

    def analyze_trend_certainty(self, symbol: str, side: str) -> Dict:
        """
        分析趋势确定性（三周期共振）

        Args:
            symbol: 交易对
            side: 方向 LONG/SHORT

        Returns:
            {
                'certainty': 0.75,  # 确定性评分 0-1
                'can_trade': True,  # 是否可以交易
                'resonance': True,  # 是否三周期共振
                'reason': '30H+5H+15M三周期共振看多',
                'details': {
                    '30h': {'direction': 'BULLISH', 'strength': 0.8, ...},
                    '5h': {'direction': 'BULLISH', 'strength': 0.75, ...},
                    '15m': {'direction': 'BULLISH', 'strength': 0.7, ...}
                }
            }
        """
        try:
            # 1. 分析 30H K线（大趋势）
            trend_30h = self._analyze_kline_trend(symbol, '30h', periods=48)

            # 2. 分析 5H K线（中期趋势）
            trend_5h = self._analyze_kline_trend(symbol, '5h', periods=48)

            # 3. 分析 15M K线（小趋势）
            trend_15m = self._analyze_kline_trend(symbol, '15m', periods=96)

            # 4. 检查三周期是否共振
            if side == 'LONG':
                resonance = (
                    trend_30h['direction'] == 'BULLISH' and
                    trend_5h['direction'] == 'BULLISH' and
                    trend_15m['direction'] == 'BULLISH'
                )
                direction_name = '看多'

            elif side == 'SHORT':
                resonance = (
                    trend_30h['direction'] == 'BEARISH' and
                    trend_5h['direction'] == 'BEARISH' and
                    trend_15m['direction'] == 'BEARISH'
                )
                direction_name = '看空'
            else:
                resonance = False
                direction_name = '未知'

            # 5. 计算确定性（趋势强度的加权平均）
            if resonance:
                certainty = (
                    trend_30h['strength'] * 0.5 +   # 30H权重50%（最重要）
                    trend_5h['strength'] * 0.3 +     # 5H权重30%
                    trend_15m['strength'] * 0.2      # 15M权重20%
                )
                can_trade = certainty >= self.CERTAINTY_THRESHOLD

                if can_trade:
                    reason = f"30H+5H+15M三周期共振{direction_name}（确定性{certainty:.0%}）"
                else:
                    reason = f"三周期同向但强度不足（确定性{certainty:.0%} < 70%）"
            else:
                certainty = 0
                can_trade = False
                reason = self._get_no_resonance_reason(trend_30h, trend_5h, trend_15m)

            return {
                'certainty': certainty,
                'can_trade': can_trade,
                'resonance': resonance,
                'reason': reason,
                'details': {
                    '30h': trend_30h,
                    '5h': trend_5h,
                    '15m': trend_15m
                }
            }

        except Exception as e:
            logger.error(f"多周期分析失败 {symbol}: {e}")
            return {
                'certainty': 0,
                'can_trade': False,
                'resonance': False,
                'reason': f'分析失败: {e}',
                'details': {}
            }

    def _analyze_kline_trend(self, symbol: str, interval: str, periods: int) -> Dict:
        """
        分析单个周期的K线趋势

        Args:
            symbol: 交易对
            interval: 时间周期 (30h, 5h, 15m)
            periods: 分析K线数量

        Returns:
            {
                'direction': 'BULLISH' | 'BEARISH' | 'NEUTRAL',
                'strength': 0.75,  # 趋势强度 0-1
                'details': {
                    'consecutive_up': 5,    # 连续上涨K线数
                    'consecutive_down': 0,
                    'bullish_count': 8,     # 总阳线数
                    'bearish_count': 2,     # 总阴线数
                    'trend_slope': 0.025,   # 趋势斜率
                    'volume_confirm': True,  # 成交量确认
                    'price_change_pct': 2.5  # 价格变化百分比
                }
            }
        """
        try:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # 转换时间周期到数据库字段
            interval_map = {
                '30h': '1h',   # 30H = 30根1H K线
                '5h': '1h',    # 5H = 5根1H K线
                '15m': '15m'
            }
            db_interval = interval_map.get(interval, '1h')

            # 调整查询数量
            if interval == '30h':
                limit = 30
            elif interval == '5h':
                limit = 5
            else:
                limit = periods

            # 查询K线数据
            cursor.execute("""
                SELECT open_time, open, high, low, close, volume
                FROM klines
                WHERE symbol = %s AND interval = %s
                ORDER BY open_time DESC
                LIMIT %s
            """, (symbol, db_interval, limit))

            klines = cursor.fetchall()
            cursor.close()
            conn.close()

            if not klines or len(klines) < 3:
                return self._get_neutral_result('数据不足')

            # 反转为时间升序
            klines = list(reversed(klines))

            # 计算趋势特征
            consecutive_up = self._count_consecutive_bullish(klines)
            consecutive_down = self._count_consecutive_bearish(klines)
            bullish_count = sum(1 for k in klines if k['close'] > k['open'])
            bearish_count = sum(1 for k in klines if k['close'] < k['open'])
            trend_slope = self._calculate_trend_slope(klines)
            volume_confirm = self._check_volume_confirmation(klines)

            # 价格变化百分比
            start_price = klines[0]['open']
            end_price = klines[-1]['close']
            price_change_pct = ((end_price - start_price) / start_price) * 100

            # 判断方向和强度
            direction, strength = self._determine_direction_and_strength(
                consecutive_up, consecutive_down,
                bullish_count, bearish_count,
                trend_slope, volume_confirm,
                len(klines)
            )

            return {
                'direction': direction,
                'strength': strength,
                'details': {
                    'consecutive_up': consecutive_up,
                    'consecutive_down': consecutive_down,
                    'bullish_count': bullish_count,
                    'bearish_count': bearish_count,
                    'trend_slope': trend_slope,
                    'volume_confirm': volume_confirm,
                    'price_change_pct': round(price_change_pct, 2),
                    'total_klines': len(klines)
                }
            }

        except Exception as e:
            logger.error(f"K线趋势分析失败 {symbol} {interval}: {e}")
            return self._get_neutral_result(f'分析失败: {e}')

    def _count_consecutive_bullish(self, klines: List[Dict]) -> int:
        """计算末尾连续上涨K线数"""
        count = 0
        for k in reversed(klines):
            if k['close'] > k['open']:
                count += 1
            else:
                break
        return count

    def _count_consecutive_bearish(self, klines: List[Dict]) -> int:
        """计算末尾连续下跌K线数"""
        count = 0
        for k in reversed(klines):
            if k['close'] < k['open']:
                count += 1
            else:
                break
        return count

    def _calculate_trend_slope(self, klines: List[Dict]) -> float:
        """
        计算趋势斜率（简化版线性回归）
        正数 = 上涨趋势，负数 = 下跌趋势
        """
        if len(klines) < 2:
            return 0

        # 使用首尾价格计算斜率
        start_price = klines[0]['close']
        end_price = klines[-1]['close']

        slope = (end_price - start_price) / start_price
        return slope

    def _check_volume_confirmation(self, klines: List[Dict]) -> bool:
        """
        检查成交量确认
        上涨趋势：最近成交量 > 平均成交量
        """
        if len(klines) < 5:
            return False

        avg_volume = sum(k['volume'] for k in klines) / len(klines)
        recent_volume = sum(k['volume'] for k in klines[-3:]) / 3

        return recent_volume > avg_volume * 0.8  # 最近成交量不低于平均的80%

    def _determine_direction_and_strength(
        self,
        consecutive_up: int,
        consecutive_down: int,
        bullish_count: int,
        bearish_count: int,
        trend_slope: float,
        volume_confirm: bool,
        total_klines: int
    ) -> tuple:
        """
        综合判断趋势方向和强度

        Returns:
            (direction, strength)
        """
        # 看多条件
        if (consecutive_up >= self.CONSECUTIVE_THRESHOLD and
            trend_slope > self.SLOPE_THRESHOLD):

            direction = 'BULLISH'

            # 计算强度 (0-1)
            # 因素1: 连续上涨数 (最多5根)
            factor1 = min(1.0, consecutive_up / 5)
            # 因素2: 阳线比例
            factor2 = bullish_count / total_klines if total_klines > 0 else 0
            # 因素3: 斜率强度 (2%斜率=0.5强度, 4%=1.0强度)
            factor3 = min(1.0, abs(trend_slope) / 0.04)
            # 因素4: 成交量确认
            factor4 = 0.2 if volume_confirm else 0

            strength = (factor1 * 0.3 + factor2 * 0.3 + factor3 * 0.3 + factor4)
            strength = min(1.0, max(0.0, strength))

        # 看空条件
        elif (consecutive_down >= self.CONSECUTIVE_THRESHOLD and
              trend_slope < -self.SLOPE_THRESHOLD):

            direction = 'BEARISH'

            # 计算强度
            factor1 = min(1.0, consecutive_down / 5)
            factor2 = bearish_count / total_klines if total_klines > 0 else 0
            factor3 = min(1.0, abs(trend_slope) / 0.04)
            factor4 = 0.2 if volume_confirm else 0

            strength = (factor1 * 0.3 + factor2 * 0.3 + factor3 * 0.3 + factor4)
            strength = min(1.0, max(0.0, strength))

        else:
            direction = 'NEUTRAL'
            strength = 0

        return direction, strength

    def _get_neutral_result(self, reason: str = '') -> Dict:
        """返回中性结果"""
        return {
            'direction': 'NEUTRAL',
            'strength': 0,
            'details': {
                'reason': reason
            }
        }

    def _get_no_resonance_reason(self, trend_30h: Dict, trend_5h: Dict, trend_15m: Dict) -> str:
        """生成未形成共振的原因说明"""
        reasons = []

        if trend_30h['direction'] == 'NEUTRAL':
            reasons.append('30H无明确趋势')
        if trend_5h['direction'] == 'NEUTRAL':
            reasons.append('5H无明确趋势')
        if trend_15m['direction'] == 'NEUTRAL':
            reasons.append('15M无明确趋势')

        if (trend_30h['direction'] != 'NEUTRAL' and
            trend_5h['direction'] != 'NEUTRAL' and
            trend_30h['direction'] != trend_5h['direction']):
            reasons.append(f'30H({trend_30h["direction"]})与5H({trend_5h["direction"]})方向冲突')

        if (trend_5h['direction'] != 'NEUTRAL' and
            trend_15m['direction'] != 'NEUTRAL' and
            trend_5h['direction'] != trend_15m['direction']):
            reasons.append(f'5H({trend_5h["direction"]})与15M({trend_15m["direction"]})方向冲突')

        if not reasons:
            reasons.append('未形成三周期共振')

        return ', '.join(reasons)

    def analyze_5m_entry_price(self, symbol: str, side: str) -> Dict:
        """
        5M价格分析（只看价格位置，不看方向）

        目标：在已确定方向的前提下，找最优入场价

        Returns:
            {
                'is_good_entry': True,
                'reason': '价格在低位30%，可入场',
                'current_price': 50000.0,
                'price_position': 0.3,  # 在近期区间的位置 0-1
                'recommended_price': 50000.0
            }
        """
        try:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # 获取最近20根5M K线
            cursor.execute("""
                SELECT open_time, open, high, low, close
                FROM klines
                WHERE symbol = %s AND interval = '5m'
                ORDER BY open_time DESC
                LIMIT 20
            """, (symbol,))

            klines = cursor.fetchall()
            cursor.close()
            conn.close()

            if not klines or len(klines) < 5:
                return {
                    'is_good_entry': False,
                    'reason': '5M数据不足',
                    'current_price': 0,
                    'price_position': 0
                }

            # 当前价格
            current_price = float(klines[0]['close'])

            # 最近5根K线的高低点
            recent_5 = klines[:5]
            recent_high = max(float(k['high']) for k in recent_5)
            recent_low = min(float(k['low']) for k in recent_5)

            # 价格在区间中的位置 (0=最低, 1=最高)
            if recent_high == recent_low:
                price_position = 0.5
            else:
                price_position = (current_price - recent_low) / (recent_high - recent_low)

            # 根据方向判断入场时机
            if side == 'LONG':
                # 做多：希望价格在回调位，不在高点
                if price_position > 0.8:
                    return {
                        'is_good_entry': False,
                        'reason': f'价格在高位{price_position:.0%}，等回调',
                        'current_price': current_price,
                        'price_position': price_position
                    }
                elif price_position < 0.3:
                    return {
                        'is_good_entry': True,
                        'reason': f'价格在低位{price_position:.0%}，入场机会好',
                        'current_price': current_price,
                        'price_position': price_position,
                        'recommended_price': current_price
                    }
                else:
                    return {
                        'is_good_entry': True,
                        'reason': f'价格在中位{price_position:.0%}，可入场',
                        'current_price': current_price,
                        'price_position': price_position,
                        'recommended_price': current_price
                    }

            else:  # SHORT
                # 做空：希望价格在反弹位，不在低点
                if price_position < 0.2:
                    return {
                        'is_good_entry': False,
                        'reason': f'价格在低位{price_position:.0%}，等反弹',
                        'current_price': current_price,
                        'price_position': price_position
                    }
                elif price_position > 0.7:
                    return {
                        'is_good_entry': True,
                        'reason': f'价格在高位{price_position:.0%}，入场机会好',
                        'current_price': current_price,
                        'price_position': price_position,
                        'recommended_price': current_price
                    }
                else:
                    return {
                        'is_good_entry': True,
                        'reason': f'价格在中位{price_position:.0%}，可入场',
                        'current_price': current_price,
                        'price_position': price_position,
                        'recommended_price': current_price
                    }

        except Exception as e:
            logger.error(f"5M价格分析失败 {symbol}: {e}")
            return {
                'is_good_entry': False,
                'reason': f'分析失败: {e}',
                'current_price': 0,
                'price_position': 0
            }
