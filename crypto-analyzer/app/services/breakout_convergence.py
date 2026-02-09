#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
破位信号收敛机制

防止破位信号长时间持续，通过多维度收敛:
1. 时间收敛: 最大有效期4小时，开仓窗口30分钟
2. 数量收敛: 根据强度限制开仓次数
3. 价格收敛: 价格反转到破位点则失效
4. 趋势收敛: 趋势反向移动2%则停止

Author: AI Assistant
Date: 2026-02-09
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class BreakoutConvergence:
    """破位信号收敛管理器"""

    def __init__(self, config: Dict = None):
        """
        初始化

        Args:
            config: 配置参数
        """
        # 默认配置（平衡型）
        self.config = config or {
            'max_duration': 4 * 3600,      # 4小时失效
            'opening_window': 30 * 60,     # 30分钟窗口
            'max_openings': 5,             # 同方向最多5个仓位
            'reversal_threshold': 0.01,    # 1%反转
            'trend_end_threshold': 0.02    # 2%趋势结束
        }

        # 当前破位会话
        self.session_id = None
        self.breakout_time = None
        self.breakout_direction = None
        self.breakout_strength = 0
        self.breakout_price = 0

        # 开仓记录（改为字典，记录每个币种的开仓次数）
        self.max_openings_per_symbol = self.config['max_openings']  # 每个币种最多5次
        self.opened_symbols = {}  # {symbol: count} 记录每个币种已开仓次数

    def start_breakout_session(
        self,
        direction: str,
        strength: float,
        breakout_price: float
    ) -> str:
        """
        开始新的破位会话

        Args:
            direction: 破位方向
            strength: 破位强度
            breakout_price: 破位价格

        Returns:
            str: 会话ID
        """
        # 生成会话ID
        timestamp = int(datetime.now().timestamp())
        self.session_id = f"{direction}_{timestamp}"

        # 记录破位信息
        self.breakout_time = datetime.now()
        self.breakout_direction = direction
        self.breakout_strength = strength
        self.breakout_price = breakout_price

        # 重置开仓记录
        self.opened_symbols = {}  # 清空所有币种的开仓记录

        logger.info(f"=" * 60)
        logger.info(f"[破位会话] {self.session_id} 开始")
        logger.info(f"  方向: {direction}")
        logger.info(f"  强度: {strength:.1f}")
        logger.info(f"  破位价: {breakout_price}")
        logger.info(f"  每个币种最多: {self.max_openings_per_symbol} 次开仓")
        logger.info(f"  开仓窗口: {self.config['opening_window']/60:.0f} 分钟")
        logger.info(f"  有效期: {self.config['max_duration']/3600:.1f} 小时")
        logger.info(f"=" * 60)

        return self.session_id

    def should_generate_signal(
        self,
        symbol: str,
        current_price: float
    ) -> Tuple[bool, str]:
        """
        判断是否应该生成破位信号

        Args:
            symbol: 币种
            current_price: 当前价格

        Returns:
            tuple: (是否生成, 原因)
        """
        # 检查1: 是否有活动会话
        if not self.session_id:
            return False, "无活动破位会话"

        # 检查2: 时间失效
        elapsed = (datetime.now() - self.breakout_time).total_seconds()

        if elapsed > self.config['max_duration']:
            return False, f"破位信号已失效({elapsed/3600:.1f}小时)"

        # 检查3: 开仓窗口
        if elapsed > self.config['opening_window']:
            return False, f"超过开仓窗口({elapsed/60:.0f}分钟)"

        # 检查4: 该币种开仓次数
        symbol_count = self.opened_symbols.get(symbol, 0)
        if symbol_count >= self.max_openings_per_symbol:
            return False, f"{symbol}已开仓{symbol_count}次，达到上限({self.max_openings_per_symbol})"

        # 检查6: 价格反转
        if self.check_price_reversal(current_price):
            return False, "价格已反转，破位失效"

        # 检查7: 趋势结束
        if self.check_trend_end(current_price):
            return False, "趋势已结束"

        # 通过所有检查
        remaining_for_symbol = self.max_openings_per_symbol - symbol_count
        remaining_time = (self.config['opening_window'] - elapsed) / 60

        return True, f"破位信号有效 ({symbol}还可开{remaining_for_symbol}次, 窗口剩余{remaining_time:.0f}分钟)"

    def check_price_reversal(self, current_price: float) -> bool:
        """
        检查价格是否反转

        Args:
            current_price: 当前价格

        Returns:
            bool: 是否反转
        """
        threshold = self.config['reversal_threshold']

        if self.breakout_direction == 'DOWN':
            # 向下破位后，价格反弹回破位点上方
            if current_price > self.breakout_price * (1 + threshold):
                logger.warning(
                    f"[价格反转] 当前价{current_price:.6f} 回到破位点"
                    f"{self.breakout_price:.6f}上方{threshold*100:.1f}%"
                )
                return True

        elif self.breakout_direction == 'UP':
            # 向上破位后，价格回落到破位点下方
            if current_price < self.breakout_price * (1 - threshold):
                logger.warning(
                    f"[价格反转] 当前价{current_price:.6f} 回到破位点"
                    f"{self.breakout_price:.6f}下方{threshold*100:.1f}%"
                )
                return True

        return False

    def check_trend_end(self, current_price: float) -> bool:
        """
        检查趋势是否结束

        Args:
            current_price: 当前价格

        Returns:
            bool: 趋势是否结束
        """
        threshold = self.config['trend_end_threshold']

        if self.breakout_direction == 'DOWN':
            # 向下破位后，价格反弹超过阈值
            if current_price > self.breakout_price * (1 + threshold):
                logger.info(
                    f"[趋势结束] 向下破位后价格反弹超{threshold*100:.1f}%"
                )
                return True

        elif self.breakout_direction == 'UP':
            # 向上破位后，价格回落超过阈值
            if current_price < self.breakout_price * (1 - threshold):
                logger.info(
                    f"[趋势结束] 向上破位后价格回落超{threshold*100:.1f}%"
                )
                return True

        return False

    def record_opening(self, symbol: str):
        """
        记录开仓

        Args:
            symbol: 币种
        """
        # 增加该币种的开仓次数
        if symbol not in self.opened_symbols:
            self.opened_symbols[symbol] = 0
        self.opened_symbols[symbol] += 1

        remaining = self.max_openings_per_symbol - self.opened_symbols[symbol]
        total_opened = sum(self.opened_symbols.values())
        logger.info(f"[开仓记录] {symbol} 第{self.opened_symbols[symbol]}次开仓, 还可开{remaining}次 (总共已开{total_opened}单)")

    def get_status(self) -> Dict:
        """
        获取会话状态

        Returns:
            dict: 状态信息
        """
        if not self.session_id:
            return {
                'active': False
            }

        elapsed = (datetime.now() - self.breakout_time).total_seconds()
        remaining_time = max(0, self.config['max_duration'] - elapsed)
        remaining_window = max(0, self.config['opening_window'] - elapsed)

        # 统计总开仓数
        total_opened = sum(self.opened_symbols.values())

        return {
            'active': True,
            'session_id': self.session_id,
            'direction': self.breakout_direction,
            'strength': self.breakout_strength,
            'breakout_price': self.breakout_price,
            'breakout_time': self.breakout_time,
            'elapsed_seconds': elapsed,
            'elapsed_minutes': elapsed / 60,
            'remaining_time': remaining_time,
            'remaining_window': remaining_window,
            'opened_count': total_opened,  # 总开仓数
            'max_openings': self.max_openings_per_symbol,  # 每个币种最多开仓数
            'opened_symbols': self.opened_symbols.copy()  # {symbol: count}
        }

    def reset(self):
        """重置会话"""
        self.session_id = None
        self.breakout_time = None
        self.breakout_direction = None
        self.breakout_strength = 0
        self.breakout_price = 0
        self.opened_symbols = {}

        logger.info("[破位会话] 已重置")


if __name__ == '__main__':
    # 测试代码
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    convergence = BreakoutConvergence()

    # 测试场景1: 启动破位会话
    print("\n=== 测试场景1: 启动破位会话 ===")
    session_id = convergence.start_breakout_session('SHORT', 85, 59800)
    print(f"会话ID: {session_id}")

    # 测试场景2: 检查是否应该生成信号
    print("\n=== 测试场景2: 检查信号生成 ===")
    should_gen, reason = convergence.should_generate_signal('DOGE/USDT', 60000)
    print(f"DOGE/USDT: {should_gen}, {reason}")

    # 记录开仓
    if should_gen:
        convergence.record_opening('DOGE/USDT')

    # 测试场景3: 检查价格反转
    print("\n=== 测试场景3: 价格反转检测 ===")
    should_gen, reason = convergence.should_generate_signal('SHIB/USDT', 62000)
    print(f"SHIB/USDT (价格62000): {should_gen}, {reason}")

    # 测试场景4: 获取状态
    print("\n=== 测试场景4: 会话状态 ===")
    status = convergence.get_status()
    print(f"活动: {status['active']}")
    print(f"方向: {status['direction']}")
    print(f"已开仓: {status['opened_count']}/{status['max_openings']}")
    print(f"剩余时间: {status['remaining_window']/60:.0f}分钟")

    print("\n破位收敛机制已实现")
