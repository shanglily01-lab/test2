#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Big4破位检测器

检测BTC/ETH/BNB/SOL的破位信号，基于三大特征：
1. 突破24H极值
2. K线无影线（暴力突破）
3. 成交量放大

Author: AI Assistant
Date: 2026-02-09
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)


class Big4BreakoutDetector:
    """Big4破位检测器"""

    # Big4币种和权重
    BIG4_SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']
    BIG4_WEIGHTS = {
        'BTC/USDT': 0.40,  # BTC权重40%
        'ETH/USDT': 0.30,  # ETH权重30%
        'BNB/USDT': 0.15,  # BNB权重15%
        'SOL/USDT': 0.15   # SOL权重15%
    }

    def __init__(self, exchange):
        """
        初始化

        Args:
            exchange: 交易所接口
        """
        self.exchange = exchange

    def detect_market_direction(self) -> Dict:
        """
        检测市场方向

        Returns:
            dict: {
                'direction': 'LONG' | 'SHORT' | 'NEUTRAL',
                'strength': 0-100,
                'confidence': 0-1.0,
                'long_score': 0-100,
                'short_score': 0-100,
                'details': {...}
            }
        """
        logger.info("=" * 60)
        logger.info("Big4破位检测开始")
        logger.info("=" * 60)

        results = {}
        long_score = 0
        short_score = 0

        for symbol in self.BIG4_SYMBOLS:
            try:
                # 检测该币种的破位
                breakout_result = self.detect_symbol_breakout(symbol)

                # 计算加权分数
                weight = self.BIG4_WEIGHTS[symbol]

                if breakout_result['is_valid']:
                    if breakout_result['direction'] == 'UP':
                        long_score += breakout_result['score'] * weight
                    elif breakout_result['direction'] == 'DOWN':
                        short_score += breakout_result['score'] * weight

                results[symbol] = breakout_result

                logger.info(f"{symbol}: {breakout_result['direction']} "
                           f"得分{breakout_result['score']}")

            except Exception as e:
                logger.error(f"检测{symbol}失败: {e}")
                results[symbol] = {
                    'is_valid': False,
                    'direction': 'NEUTRAL',
                    'score': 0,
                    'error': str(e)
                }

        # 综合判断方向
        if long_score > short_score + 20:
            direction = 'LONG'
            strength = long_score
        elif short_score > long_score + 20:
            direction = 'SHORT'
            strength = short_score
        else:
            direction = 'NEUTRAL'
            strength = max(long_score, short_score)

        # 计算置信度
        confidence = abs(long_score - short_score) / 100

        logger.info(f"\n市场方向: {direction}")
        logger.info(f"强度: {strength:.1f}")
        logger.info(f"置信度: {confidence:.2f}")
        logger.info(f"做多得分: {long_score:.1f}")
        logger.info(f"做空得分: {short_score:.1f}")

        return {
            'direction': direction,
            'strength': strength,
            'confidence': confidence,
            'long_score': long_score,
            'short_score': short_score,
            'details': results,
            'timestamp': datetime.now()
        }

    def detect_symbol_breakout(self, symbol: str) -> Dict:
        """
        检测单个币种的破位

        Args:
            symbol: 交易对

        Returns:
            dict: 破位检测结果
        """
        # 获取5M K线数据（24小时 = 288根）
        klines_5m = self.exchange.fetch_ohlcv(
            symbol,
            timeframe='5m',
            limit=288 + 3  # 多取3根用于检测
        )

        if len(klines_5m) < 288:
            return {
                'is_valid': False,
                'direction': 'NEUTRAL',
                'score': 0,
                'reason': 'K线数据不足'
            }

        # 特征1: 检查24H破位
        feature1 = self.check_24h_breakout(klines_5m)

        if not feature1['is_breakout']:
            return {
                'is_valid': False,
                'direction': 'NEUTRAL',
                'score': 0,
                'reason': '未破位24H极值'
            }

        # 特征2: 检查K线形态（无影线）
        feature2 = self.check_candle_pattern(klines_5m, feature1['direction'])

        # 特征3: 检查成交量
        feature3 = self.check_volume_surge(klines_5m)

        # 综合评分
        score = self.calculate_breakout_score(feature1, feature2, feature3)

        # 判断是否有效
        is_valid = score >= 70

        return {
            'is_valid': is_valid,
            'direction': feature1['direction'],
            'score': score,
            'feature1_24h': feature1,
            'feature2_candle': feature2,
            'feature3_volume': feature3,
            'timestamp': datetime.now()
        }

    def check_24h_breakout(self, klines: List) -> Dict:
        """
        检查是否突破24H极值

        Args:
            klines: K线数据

        Returns:
            dict: {
                'is_breakout': bool,
                'direction': 'UP' | 'DOWN' | 'NEUTRAL',
                'breakout_level': float,
                'current_price': float,
                'strength': float
            }
        """
        # 历史24H数据（排除最近3根）
        historical = klines[:-3]
        recent = klines[-3:]  # 最近3根K线

        # 计算24H最高最低
        high_24h = max([k[2] for k in historical])  # high
        low_24h = min([k[3] for k in historical])   # low

        # 检查最近3根是否破位
        for candle in recent:
            high = candle[2]
            low = candle[3]

            # 向上破位
            if high > high_24h * 1.001:  # 突破0.1%
                strength = (high - high_24h) / high_24h * 100
                return {
                    'is_breakout': True,
                    'direction': 'UP',
                    'breakout_level': high_24h,
                    'current_price': high,
                    'strength': strength,
                    'score': 40  # 满分40
                }

            # 向下破位
            if low < low_24h * 0.999:  # 跌破0.1%
                strength = (low_24h - low) / low_24h * 100
                return {
                    'is_breakout': True,
                    'direction': 'DOWN',
                    'breakout_level': low_24h,
                    'current_price': low,
                    'strength': strength,
                    'score': 40  # 满分40
                }

        return {
            'is_breakout': False,
            'direction': 'NEUTRAL',
            'breakout_level': 0,
            'current_price': 0,
            'strength': 0,
            'score': 0
        }

    def check_candle_pattern(self, klines: List, direction: str) -> Dict:
        """
        检查K线形态（无影线）

        Args:
            klines: K线数据
            direction: 破位方向

        Returns:
            dict: K线形态评分
        """
        # 取最近3根K线
        recent = klines[-3:]

        total_score = 0
        valid_count = 0

        for candle in recent:
            open_price = candle[1]
            high = candle[2]
            low = candle[3]
            close = candle[4]

            total_range = high - low
            if total_range == 0:
                continue

            # 计算实体和影线
            body = abs(close - open_price)
            body_ratio = body / total_range

            if direction == 'DOWN':
                # 向下破位：检查下影线
                body_low = min(open_price, close)
                lower_shadow = body_low - low
                shadow_ratio = lower_shadow / total_range

                # 评分
                candle_score = 0

                # 下影线评分（最高15分）
                if shadow_ratio < 0.10:
                    candle_score += 15  # 极短
                elif shadow_ratio < 0.20:
                    candle_score += 12  # 短
                elif shadow_ratio < 0.30:
                    candle_score += 8   # 中等
                else:
                    candle_score += 0   # 长

                # 实体评分（最高10分）
                if body_ratio > 0.70:
                    candle_score += 10  # 大实体
                elif body_ratio > 0.60:
                    candle_score += 8   # 中实体
                else:
                    candle_score += 5   # 小实体

                total_score += candle_score
                valid_count += 1

            elif direction == 'UP':
                # 向上破位：检查上影线
                body_high = max(open_price, close)
                upper_shadow = high - body_high
                shadow_ratio = upper_shadow / total_range

                # 评分
                candle_score = 0

                if shadow_ratio < 0.10:
                    candle_score += 15
                elif shadow_ratio < 0.20:
                    candle_score += 12
                elif shadow_ratio < 0.30:
                    candle_score += 8
                else:
                    candle_score += 0

                if body_ratio > 0.70:
                    candle_score += 10
                elif body_ratio > 0.60:
                    candle_score += 8
                else:
                    candle_score += 5

                total_score += candle_score
                valid_count += 1

        # 平均分数（最高30分）
        avg_score = (total_score / valid_count) if valid_count > 0 else 0
        normalized_score = min(avg_score * 30 / 25, 30)  # 归一化到30分

        return {
            'score': normalized_score,
            'avg_shadow_ratio': 0,  # 可以记录平均值
            'avg_body_ratio': 0,
            'valid_count': valid_count
        }

    def check_volume_surge(self, klines: List) -> Dict:
        """
        检查成交量是否放大

        Args:
            klines: K线数据

        Returns:
            dict: 成交量评分
        """
        # 历史平均成交量（排除最近3根）
        historical = klines[:-3]
        recent = klines[-3:]

        historical_volumes = [k[5] for k in historical]  # volume
        avg_volume = np.mean(historical_volumes)

        # 最近3根的最大成交量
        recent_volumes = [k[5] for k in recent]
        max_recent_volume = max(recent_volumes)

        # 计算放量倍数
        volume_ratio = max_recent_volume / avg_volume if avg_volume > 0 else 0

        # 评分（最高30分）
        if volume_ratio >= 3.0:
            score = 30  # 放量3倍以上
        elif volume_ratio >= 2.0:
            score = 25  # 放量2倍
        elif volume_ratio >= 1.5:
            score = 20  # 放量1.5倍
        elif volume_ratio >= 1.2:
            score = 15  # 放量1.2倍
        else:
            score = 10  # 无明显放量

        return {
            'score': score,
            'volume_ratio': volume_ratio,
            'avg_volume': avg_volume,
            'max_recent_volume': max_recent_volume
        }

    def calculate_breakout_score(
        self,
        feature1: Dict,
        feature2: Dict,
        feature3: Dict
    ) -> float:
        """
        计算破位综合得分

        Args:
            feature1: 24H破位特征
            feature2: K线形态特征
            feature3: 成交量特征

        Returns:
            float: 综合得分 (0-100)
        """
        score = (
            feature1['score'] +  # 40分
            feature2['score'] +  # 30分
            feature3['score']    # 30分
        )

        return min(score, 100)


if __name__ == '__main__':
    # 测试代码
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # 这里需要实际的交易所接口
    # from app.exchanges.binance_client import BinanceClient
    # exchange = BinanceClient()
    # detector = Big4BreakoutDetector(exchange)
    # result = detector.detect_market_direction()

    print("Big4破位检测器已实现")
