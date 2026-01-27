#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K线强度评分器
基于1H/15M/5M三周期K线强度数据生成交易评分

Author: Claude
Date: 2026-01-27
"""

from typing import Dict, List
from loguru import logger


class KlineStrengthScorer:
    """K线强度评分器"""

    def __init__(self, config: dict = None):
        """
        初始化评分器

        Args:
            config: 配置字典
        """
        self.config = config or {}

        # 1H K线评分参数
        self.strong_net_power = self.config.get('strong_net_power', 8)
        self.medium_net_power = self.config.get('medium_net_power', 5)
        self.weak_net_power = self.config.get('weak_net_power', 3)

        self.strong_bull_pct = self.config.get('strong_bull_pct', 65)
        self.medium_bull_pct = self.config.get('medium_bull_pct', 60)
        self.weak_bull_pct = self.config.get('weak_bull_pct', 55)

        self.strong_bear_pct = self.config.get('strong_bear_pct', 35)
        self.medium_bear_pct = self.config.get('medium_bear_pct', 40)
        self.weak_bear_pct = self.config.get('weak_bear_pct', 45)

        # 15M一致性参数
        self.consistent_strong = self.config.get('consistent_strong', 5)
        self.consistent_medium = self.config.get('consistent_medium', 3)

        logger.info(f"✅ K线强度评分器已初始化")

    def calculate_strength_score(
        self,
        strength_1h: Dict,
        strength_15m: Dict,
        strength_5m: Dict
    ) -> Dict:
        """
        计算三周期K线强度综合评分

        Args:
            strength_1h: 1H K线强度数据
            strength_15m: 15M K线强度数据
            strength_5m: 5M K线强度数据

        Returns:
            {
                'total_score': int,      # 总分 (0-40)
                'score_1h': int,         # 1H评分 (0-20)
                'score_15m': int,        # 15M评分 (-10~15)
                'score_5m': int,         # 5M评分 (-5~5)
                'direction': str,        # LONG/SHORT/NEUTRAL
                'strength': str,         # STRONG/MEDIUM/WEAK
                'consistency': bool,     # 三周期是否一致
                'reasons': List[str]     # 评分原因
            }
        """
        reasons = []

        # 1. 计算1H评分 (0-20分)
        score_1h, direction, strength_level = self._score_1h_kline(strength_1h, reasons)

        # 2. 计算15M评分 (-10~15分)
        score_15m = self._score_15m_kline(strength_15m, direction, reasons)

        # 3. 计算5M评分 (-5~5分)
        score_5m = self._score_5m_kline(strength_5m, direction, reasons)

        # 4. 综合评分
        total_score = max(0, score_1h + score_15m + score_5m)

        # 5. 判断一致性
        consistency = self._check_consistency(strength_1h, strength_15m, strength_5m, direction)

        return {
            'total_score': total_score,
            'score_1h': score_1h,
            'score_15m': score_15m,
            'score_5m': score_5m,
            'direction': direction,
            'strength': strength_level,
            'consistency': consistency,
            'reasons': reasons
        }

    def _score_1h_kline(self, strength: Dict, reasons: List[str]) -> tuple:
        """
        评分1H K线强度 (20分)

        Returns:
            (score, direction, strength_level)
        """
        net_power = strength.get('net_power', 0)
        bull_pct = strength.get('bull_pct', 50)

        score = 0
        direction = 'NEUTRAL'
        strength_level = 'WEAK'

        # 基于净力量评分
        if net_power >= self.strong_net_power:
            score = 20
            direction = 'LONG'
            strength_level = 'STRONG'
            reasons.append(f"1H强多 (净力量+{net_power})")
        elif net_power >= self.medium_net_power:
            score = 15
            direction = 'LONG'
            strength_level = 'MEDIUM'
            reasons.append(f"1H偏多 (净力量+{net_power})")
        elif net_power >= self.weak_net_power:
            score = 10
            direction = 'LONG'
            strength_level = 'WEAK'
            reasons.append(f"1H多头 (净力量+{net_power})")
        elif net_power <= -self.strong_net_power:
            score = 20
            direction = 'SHORT'
            strength_level = 'STRONG'
            reasons.append(f"1H强空 (净力量{net_power})")
        elif net_power <= -self.medium_net_power:
            score = 15
            direction = 'SHORT'
            strength_level = 'MEDIUM'
            reasons.append(f"1H偏空 (净力量{net_power})")
        elif net_power <= -self.weak_net_power:
            score = 10
            direction = 'SHORT'
            strength_level = 'WEAK'
            reasons.append(f"1H空头 (净力量{net_power})")

        # 如果净力量不足，尝试用阳线比例
        if score == 0:
            if bull_pct >= self.strong_bull_pct:
                score = 20
                direction = 'LONG'
                strength_level = 'STRONG'
                reasons.append(f"1H强多 (阳线{bull_pct:.0f}%)")
            elif bull_pct >= self.medium_bull_pct:
                score = 15
                direction = 'LONG'
                strength_level = 'MEDIUM'
                reasons.append(f"1H偏多 (阳线{bull_pct:.0f}%)")
            elif bull_pct >= self.weak_bull_pct:
                score = 10
                direction = 'LONG'
                strength_level = 'WEAK'
                reasons.append(f"1H多头 (阳线{bull_pct:.0f}%)")
            elif bull_pct <= self.strong_bear_pct:
                score = 20
                direction = 'SHORT'
                strength_level = 'STRONG'
                reasons.append(f"1H强空 (阳线{bull_pct:.0f}%)")
            elif bull_pct <= self.medium_bear_pct:
                score = 15
                direction = 'SHORT'
                strength_level = 'MEDIUM'
                reasons.append(f"1H偏空 (阳线{bull_pct:.0f}%)")
            elif bull_pct <= self.weak_bear_pct:
                score = 10
                direction = 'SHORT'
                strength_level = 'WEAK'
                reasons.append(f"1H空头 (阳线{bull_pct:.0f}%)")
            else:
                reasons.append(f"1H震荡 (净力量{net_power}, 阳线{bull_pct:.0f}%)")

        return score, direction, strength_level

    def _score_15m_kline(self, strength: Dict, direction_1h: str, reasons: List[str]) -> int:
        """
        评分15M K线强度 (-10~15分)
        主要看与1H方向的一致性

        Args:
            strength: 15M K线强度
            direction_1h: 1H确定的方向

        Returns:
            15M评分
        """
        if direction_1h == 'NEUTRAL':
            return 0

        net_power = strength.get('net_power', 0)

        # 判断15M方向
        if net_power >= self.weak_net_power:
            direction_15m = 'LONG'
        elif net_power <= -self.weak_net_power:
            direction_15m = 'SHORT'
        else:
            direction_15m = 'NEUTRAL'

        # 同向一致性加分
        if direction_15m == direction_1h:
            if abs(net_power) >= self.consistent_strong:
                reasons.append(f"15M强化趋势 (净力量{net_power:+d})")
                return 15
            elif abs(net_power) >= self.consistent_medium:
                reasons.append(f"15M确认趋势 (净力量{net_power:+d})")
                return 10
            else:
                reasons.append(f"15M趋势微弱 (净力量{net_power:+d})")
                return 5

        # 反向冲突扣分
        elif direction_15m != 'NEUTRAL' and direction_15m != direction_1h:
            reasons.append(f"15M信号冲突 (净力量{net_power:+d})")
            return -10

        # 15M震荡
        else:
            reasons.append(f"15M震荡 (净力量{net_power:+d})")
            return 0

    def _score_5m_kline(self, strength: Dict, direction_1h: str, reasons: List[str]) -> int:
        """
        评分5M K线强度 (-5~5分)
        主要作为入场时机优化

        Args:
            strength: 5M K线强度
            direction_1h: 1H确定的方向

        Returns:
            5M评分
        """
        if direction_1h == 'NEUTRAL':
            return 0

        net_power = strength.get('net_power', 0)

        # 判断5M方向
        if net_power >= self.weak_net_power:
            direction_5m = 'LONG'
        elif net_power <= -self.weak_net_power:
            direction_5m = 'SHORT'
        else:
            direction_5m = 'NEUTRAL'

        # 同向加分 (最佳入场点)
        if direction_5m == direction_1h:
            reasons.append(f"5M同向 (净力量{net_power:+d}) - 最佳入场点")
            return 5

        # 反向但力量弱 (可接受的回调)
        elif direction_5m != direction_1h and abs(net_power) < 10:
            reasons.append(f"5M小幅回调 (净力量{net_power:+d})")
            return 0

        # 反向且力量强 (等待更好价格)
        elif direction_5m != direction_1h:
            reasons.append(f"5M强力回调 (净力量{net_power:+d}) - 等待")
            return -5

        # 震荡
        else:
            return 0

    def _check_consistency(
        self,
        strength_1h: Dict,
        strength_15m: Dict,
        strength_5m: Dict,
        direction: str
    ) -> bool:
        """
        检查三周期一致性

        Args:
            strength_1h: 1H强度
            strength_15m: 15M强度
            strength_5m: 5M强度
            direction: 主方向

        Returns:
            是否三周期一致
        """
        if direction == 'NEUTRAL':
            return False

        # 判断各周期方向
        net_1h = strength_1h.get('net_power', 0)
        net_15m = strength_15m.get('net_power', 0)
        net_5m = strength_5m.get('net_power', 0)

        if direction == 'LONG':
            # 三周期都是多头 (净力量>0)
            return net_1h > 0 and net_15m > 0 and net_5m > 0
        else:  # SHORT
            # 三周期都是空头 (净力量<0)
            return net_1h < 0 and net_15m < 0 and net_5m < 0

    def get_entry_strategy(self, total_score: int) -> Dict:
        """
        根据K线强度评分确定入场策略

        Args:
            total_score: K线强度总分

        Returns:
            {
                'mode': str,              # aggressive/standard/conservative
                'window_minutes': int,    # 建仓窗口(分钟)
                'batch_ratio': List,      # 分批比例
                'max_hold_minutes': int   # 最长持仓(分钟)
            }
        """
        if total_score >= 30:
            return {
                'mode': 'aggressive',
                'window_minutes': 15,
                'batch_ratio': [0.4, 0.3, 0.3],
                'max_hold_minutes': 360  # 6小时
            }
        elif total_score >= 20:
            return {
                'mode': 'standard',
                'window_minutes': 30,
                'batch_ratio': [0.3, 0.3, 0.4],
                'max_hold_minutes': 240  # 4小时
            }
        else:
            return {
                'mode': 'conservative',
                'window_minutes': 45,
                'batch_ratio': [0.2, 0.3, 0.5],
                'max_hold_minutes': 180  # 3小时
            }

    def format_score_report(self, score_result: Dict) -> str:
        """
        格式化评分报告

        Args:
            score_result: calculate_strength_score的返回结果

        Returns:
            格式化的文本报告
        """
        report = []
        report.append(f"【K线强度评分】")
        report.append(f"  总分: {score_result['total_score']}/40")
        report.append(f"  方向: {score_result['direction']}")
        report.append(f"  强度: {score_result['strength']}")
        report.append(f"  一致性: {'✓' if score_result['consistency'] else '✗'}")
        report.append(f"\n  评分明细:")
        report.append(f"    1H: {score_result['score_1h']}/20")
        report.append(f"   15M: {score_result['score_15m']}/15")
        report.append(f"    5M: {score_result['score_5m']}/5")
        report.append(f"\n  原因:")
        for reason in score_result['reasons']:
            report.append(f"    • {reason}")

        return '\n'.join(report)


# 单元测试
if __name__ == '__main__':
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    scorer = KlineStrengthScorer()

    # 测试案例1: VET/USDT (强多信号)
    print("=" * 60)
    print("测试案例1: VET/USDT")
    print("=" * 60)
    result1 = scorer.calculate_strength_score(
        strength_1h={'net_power': 10, 'bull_pct': 53},
        strength_15m={'net_power': 5, 'bull_pct': 44},
        strength_5m={'net_power': -18, 'bull_pct': 43}
    )
    print(scorer.format_score_report(result1))
    print(f"\n入场策略: {scorer.get_entry_strategy(result1['total_score'])}")

    # 测试案例2: PUMP/USDT (强多信号+三周期一致)
    print("\n" + "=" * 60)
    print("测试案例2: PUMP/USDT")
    print("=" * 60)
    result2 = scorer.calculate_strength_score(
        strength_1h={'net_power': 8, 'bull_pct': 69},
        strength_15m={'net_power': 10, 'bull_pct': 50},
        strength_5m={'net_power': 14, 'bull_pct': 50}
    )
    print(scorer.format_score_report(result2))
    print(f"\n入场策略: {scorer.get_entry_strategy(result2['total_score'])}")

    # 测试案例3: XMR/USDT (强空信号)
    print("\n" + "=" * 60)
    print("测试案例3: XMR/USDT")
    print("=" * 60)
    result3 = scorer.calculate_strength_score(
        strength_1h={'net_power': -6, 'bull_pct': 44},
        strength_15m={'net_power': -2, 'bull_pct': 41},
        strength_5m={'net_power': -21, 'bull_pct': 48}
    )
    print(scorer.format_score_report(result3))
    print(f"\n入场策略: {scorer.get_entry_strategy(result3['total_score'])}")

    # 测试案例4: 震荡行情
    print("\n" + "=" * 60)
    print("测试案例4: 震荡行情")
    print("=" * 60)
    result4 = scorer.calculate_strength_score(
        strength_1h={'net_power': 1, 'bull_pct': 52},
        strength_15m={'net_power': -1, 'bull_pct': 48},
        strength_5m={'net_power': 2, 'bull_pct': 51}
    )
    print(scorer.format_score_report(result4))
    print(f"\n入场策略: {scorer.get_entry_strategy(result4['total_score'])}")
