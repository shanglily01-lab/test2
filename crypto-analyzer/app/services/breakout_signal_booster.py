#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
破位信号加权器

当Big4破位时，对同向信号加权，对反向信号降权

Author: AI Assistant
Date: 2026-02-09
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class BreakoutSignalBooster:
    """破位信号加权器"""

    def __init__(self, expiry_hours: int = 4):
        """
        初始化

        Args:
            expiry_hours: 信号有效期（小时）
        """
        self.big4_direction = None  # 'LONG' | 'SHORT' | 'NEUTRAL'
        self.big4_strength = 0      # 0-100
        self.breakout_time = None   # 破位时间
        self.expiry_duration = expiry_hours * 3600  # 转换为秒

    def update_big4_breakout(self, direction: str, strength: float):
        """
        更新Big4破位状态

        Args:
            direction: 破位方向 ('LONG' | 'SHORT')
            strength: 破位强度 (0-100)
        """
        self.big4_direction = direction
        self.big4_strength = strength
        self.breakout_time = datetime.now()

        logger.info(f"[破位加权] Big4破位方向: {direction}, 强度: {strength:.1f}")
        logger.info(f"[破位加权] 有效期: {self.expiry_duration/3600:.1f}小时")

    def is_breakout_active(self) -> bool:
        """
        检查破位信号是否仍然有效

        Returns:
            bool: 是否有效
        """
        if not self.breakout_time:
            return False

        elapsed = (datetime.now() - self.breakout_time).total_seconds()

        if elapsed > self.expiry_duration:
            logger.debug(f"[破位失效] 信号已过期({elapsed/3600:.1f}小时)")
            return False

        return True

    def get_remaining_time(self) -> float:
        """
        获取剩余有效时间（秒）

        Returns:
            float: 剩余时间（秒）
        """
        if not self.breakout_time:
            return 0

        elapsed = (datetime.now() - self.breakout_time).total_seconds()
        remaining = max(0, self.expiry_duration - elapsed)

        return remaining

    def calculate_boost_score(self, signal_direction: str) -> int:
        """
        计算信号加权分数

        Args:
            signal_direction: 信号方向 ('LONG' | 'SHORT')

        Returns:
            int: 加权分数 (-50 到 +50)
        """
        if not self.is_breakout_active():
            return 0  # 破位信号已失效

        # 同向信号加分
        if signal_direction == self.big4_direction:
            # 根据Big4强度决定加分
            if self.big4_strength >= 90:
                boost = 50  # 极强破位，+50分
            elif self.big4_strength >= 80:
                boost = 40  # 强破位，+40分
            elif self.big4_strength >= 70:
                boost = 30  # 中等破位，+30分
            else:
                boost = 20  # 弱破位，+20分

            logger.debug(f"[破位加权] 同向信号 {signal_direction} 加权 +{boost}分")
            return boost

        # 反向信号扣分
        else:
            # 根据Big4强度决定扣分
            if self.big4_strength >= 90:
                penalty = -50  # 极强破位，-50分
            elif self.big4_strength >= 80:
                penalty = -40  # 强破位，-40分
            elif self.big4_strength >= 70:
                penalty = -30  # 中等破位，-30分
            else:
                penalty = -20  # 弱破位，-20分

            logger.debug(f"[破位加权] 反向信号 {signal_direction} 降权 {penalty}分")
            return penalty

    def should_skip_opposite_signal(self, signal_direction: str, signal_score: int) -> tuple:
        """
        判断是否应该跳过反向信号

        Args:
            signal_direction: 信号方向
            signal_score: 信号评分（已包含加权）

        Returns:
            tuple: (是否跳过, 原因)
        """
        if not self.is_breakout_active():
            return False, None  # 无破位，正常处理

        # 同向信号：放行
        if signal_direction == self.big4_direction:
            return False, None

        # 反向信号：根据强度判断
        if self.big4_strength >= 90:
            # 极强破位，直接跳过反向信号
            return True, f"Big4极强{self.big4_direction}破位，禁止{signal_direction}信号"

        elif self.big4_strength >= 80:
            # 强破位，评分不足直接跳过
            if signal_score < 90:
                return True, f"Big4强{self.big4_direction}破位，{signal_direction}信号评分不足"

        elif self.big4_strength >= 70:
            # 中等破位，评分不足跳过
            if signal_score < 80:
                return True, f"Big4{self.big4_direction}破位，{signal_direction}信号评分不足"

        return False, None

    def get_status(self) -> Dict:
        """
        获取当前状态

        Returns:
            dict: 状态信息
        """
        if not self.is_breakout_active():
            return {
                'active': False,
                'direction': None,
                'strength': 0,
                'remaining_time': 0
            }

        return {
            'active': True,
            'direction': self.big4_direction,
            'strength': self.big4_strength,
            'remaining_time': self.get_remaining_time(),
            'remaining_minutes': self.get_remaining_time() / 60,
            'breakout_time': self.breakout_time
        }


if __name__ == '__main__':
    # 测试代码
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    booster = BreakoutSignalBooster(expiry_hours=4)

    # 测试场景1: Big4破位向下
    print("\n=== 测试场景1: Big4破位向下 ===")
    booster.update_big4_breakout('SHORT', 85)

    # 同向信号（做空）
    boost = booster.calculate_boost_score('SHORT')
    print(f"做空信号加权: +{boost}分")  # 期望 +40

    # 反向信号（做多）
    boost = booster.calculate_boost_score('LONG')
    print(f"做多信号加权: {boost}分")  # 期望 -40

    # 测试场景2: 检查是否跳过反向信号
    print("\n=== 测试场景2: 检查反向信号 ===")
    should_skip, reason = booster.should_skip_opposite_signal('LONG', 75)
    print(f"做多信号(75分): 跳过={should_skip}, 原因={reason}")

    # 测试场景3: 状态查询
    print("\n=== 测试场景3: 状态查询 ===")
    status = booster.get_status()
    print(f"状态: {status}")

    print("\n破位信号加权器已实现")
