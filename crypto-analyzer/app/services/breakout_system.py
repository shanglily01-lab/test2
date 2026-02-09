#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
破位信号系统集成

整合所有破位相关组件:
- Big4破位检测
- 信号加权
- 持仓管理
- 收敛机制

Author: AI Assistant
Date: 2026-02-09
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from .big4_breakout_detector import Big4BreakoutDetector
from .breakout_signal_booster import BreakoutSignalBooster
from .breakout_position_manager import BreakoutPositionManager
from .breakout_convergence import BreakoutConvergence

logger = logging.getLogger(__name__)


class BreakoutSystem:
    """破位信号系统"""

    def __init__(self, exchange):
        """
        初始化

        Args:
            exchange: 交易所接口
        """
        self.exchange = exchange

        # 初始化各个组件
        self.detector = Big4BreakoutDetector(exchange)
        self.booster = BreakoutSignalBooster(expiry_hours=4)
        self.position_manager = BreakoutPositionManager(exchange)
        self.convergence = BreakoutConvergence()

        # 最后一次检测结果
        self.last_detection = None

    def check_and_handle_breakout(
        self,
        current_positions: Dict = None
    ) -> Dict:
        """
        检测并处理破位

        Args:
            current_positions: 当前持仓

        Returns:
            dict: 处理结果
        """
        logger.info("\n" + "=" * 60)
        logger.info("破位信号系统 - 开始检测")
        logger.info("=" * 60)

        # 1. 检测Big4破位
        market = self.detector.detect_market_direction()
        self.last_detection = market

        # 2. 判断是否有效破位
        if market['direction'] == 'NEUTRAL':
            logger.info("[检测结果] Big4方向不明确，无破位信号")
            return {
                'has_breakout': False,
                'market': market
            }

        if market['strength'] < 70:
            logger.info(f"[检测结果] Big4强度不足({market['strength']:.1f})")
            return {
                'has_breakout': False,
                'market': market
            }

        logger.info(f"\n[检测结果] Big4破位 {market['direction']}, "
                   f"强度 {market['strength']:.1f}")

        # 3. 获取破位价格（使用BTC作为代表）
        btc_detail = market['details'].get('BTC/USDT', {})
        breakout_price = btc_detail.get('feature1_24h', {}).get('current_price', 0)

        # 4. 更新各个组件
        self.booster.update_big4_breakout(
            market['direction'],
            market['strength']
        )

        session_id = self.convergence.start_breakout_session(
            market['direction'],
            market['strength'],
            breakout_price
        )

        # 5. 处理现有持仓
        position_result = self.position_manager.handle_big4_breakout(
            market['direction'],
            market['strength'],
            current_positions
        )

        logger.info(f"\n[持仓处理]")
        logger.info(f"  平仓: {len(position_result['closed'])} 个")
        logger.info(f"  调整止损: {len(position_result['adjusted'])} 个")

        return {
            'has_breakout': True,
            'market': market,
            'session_id': session_id,
            'position_result': position_result
        }

    def calculate_signal_score(
        self,
        symbol: str,
        base_score: float,
        signal_direction: str,
        current_price: float
    ) -> Dict:
        """
        计算信号评分（包含破位加权）

        Args:
            symbol: 币种
            base_score: 基础评分
            signal_direction: 信号方向
            current_price: 当前价格

        Returns:
            dict: {
                'base_score': float,
                'boost_score': int,
                'total_score': float,
                'should_skip': bool,
                'skip_reason': str,
                'should_generate': bool,
                'generate_reason': str
            }
        """
        # 1. 计算破位加权
        boost_score = self.booster.calculate_boost_score(signal_direction)

        # 2. 总分
        total_score = base_score + boost_score

        # 3. 检查是否应该跳过反向信号
        should_skip, skip_reason = self.booster.should_skip_opposite_signal(
            signal_direction,
            total_score
        )

        if should_skip:
            return {
                'base_score': base_score,
                'boost_score': boost_score,
                'total_score': total_score,
                'should_skip': True,
                'skip_reason': skip_reason,
                'should_generate': False,
                'generate_reason': skip_reason
            }

        # 4. 检查收敛机制
        should_generate, generate_reason = self.convergence.should_generate_signal(
            symbol,
            current_price
        )

        return {
            'base_score': base_score,
            'boost_score': boost_score,
            'total_score': total_score,
            'should_skip': False,
            'skip_reason': None,
            'should_generate': should_generate,
            'generate_reason': generate_reason
        }

    def record_opening(self, symbol: str):
        """
        记录开仓

        Args:
            symbol: 币种
        """
        self.convergence.record_opening(symbol)

    def get_system_status(self) -> Dict:
        """
        获取系统状态

        Returns:
            dict: 系统状态
        """
        return {
            'booster': self.booster.get_status(),
            'convergence': self.convergence.get_status(),
            'last_detection': self.last_detection
        }


if __name__ == '__main__':
    # 测试代码
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    print("=" * 60)
    print("破位信号系统集成完成")
    print("=" * 60)

    print("\n核心功能:")
    print("1. Big4破位检测 - 基于三大特征")
    print("2. 信号加权 - 同向加分，反向降分")
    print("3. 持仓管理 - 自动平仓，调整止损")
    print("4. 收敛机制 - 时间/数量/价格/趋势收敛")

    print("\n使用示例:")
    print("""
    from app.services.breakout_system import BreakoutSystem

    # 初始化系统
    system = BreakoutSystem(exchange)

    # 检测并处理破位
    result = system.check_and_handle_breakout(current_positions)

    # 计算信号评分
    score_result = system.calculate_signal_score(
        symbol='DOGE/USDT',
        base_score=75,
        signal_direction='SHORT',
        current_price=0.08
    )

    # 如果生成信号
    if score_result['should_generate']:
        # 开仓
        open_position('DOGE/USDT')
        system.record_opening('DOGE/USDT')
    """)
