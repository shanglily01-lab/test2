#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
破位持仓管理器

当Big4破位时:
1. 自动平掉反向持仓
2. 调整同向持仓的止损

Author: AI Assistant
Date: 2026-02-09
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class BreakoutPositionManager:
    """破位持仓管理器"""

    def __init__(self, exchange):
        """
        初始化

        Args:
            exchange: 交易所接口
        """
        self.exchange = exchange
        self.positions = {}  # 当前持仓 {symbol: position_info}

    def handle_big4_breakout(
        self,
        direction: str,
        strength: float,
        current_positions: Dict = None
    ) -> Dict:
        """
        处理Big4破位

        Args:
            direction: 破位方向 ('LONG' | 'SHORT')
            strength: 破位强度 (0-100)
            current_positions: 当前持仓（如果为None，使用self.positions）

        Returns:
            dict: {
                'closed': List[dict],  # 已平仓位
                'adjusted': List[dict]  # 已调整仓位
            }
        """
        logger.info(f"=" * 60)
        logger.info(f"[破位处理] Big4破位 {direction}, 强度 {strength:.1f}")
        logger.info(f"=" * 60)

        if current_positions is None:
            current_positions = self.positions

        # 1. 平掉反向持仓
        closed_positions = self.close_opposite_positions(
            direction,
            strength,
            current_positions
        )

        # 2. 调整同向持仓止损
        adjusted_positions = self.adjust_same_direction_stops(
            direction,
            strength,
            current_positions
        )

        logger.info(f"\n[处理结果]")
        logger.info(f"  平仓: {len(closed_positions)} 个")
        logger.info(f"  调整止损: {len(adjusted_positions)} 个")

        return {
            'closed': closed_positions,
            'adjusted': adjusted_positions
        }

    def close_opposite_positions(
        self,
        breakout_direction: str,
        strength: float,
        current_positions: Dict
    ) -> List[Dict]:
        """
        平掉反向持仓

        Args:
            breakout_direction: 破位方向
            strength: 破位强度
            current_positions: 当前持仓

        Returns:
            list: 已平仓位列表
        """
        closed = []

        for symbol, position in list(current_positions.items()):
            # 判断是否反向
            is_opposite = (
                (breakout_direction == 'SHORT' and position['side'] == 'LONG') or
                (breakout_direction == 'LONG' and position['side'] == 'SHORT')
            )

            if not is_opposite:
                continue

            # 根据破位强度决定是否平仓
            should_close = False
            close_reason = ""

            if strength >= 90:
                # 极强破位，无条件平仓
                should_close = True
                close_reason = "Big4极强破位，强制平仓"

            elif strength >= 80:
                # 强破位，亏损仓位平仓
                if position.get('unrealized_pnl', 0) < 0:
                    should_close = True
                    close_reason = "Big4强破位，亏损仓位止损"

            elif strength >= 70:
                # 中等破位，大幅亏损仓位平仓
                margin = position.get('margin', position.get('initial_margin', 0))
                if position.get('unrealized_pnl', 0) < -margin * 0.05:
                    should_close = True
                    close_reason = "Big4破位，大幅亏损止损"

            if should_close:
                try:
                    # 执行平仓
                    order = self.close_position(symbol, position, close_reason)

                    closed.append({
                        'symbol': symbol,
                        'side': position['side'],
                        'reason': close_reason,
                        'pnl': position.get('unrealized_pnl', 0),
                        'order': order
                    })

                    logger.warning(f"[强制平仓] {symbol} {position['side']}: {close_reason}")

                except Exception as e:
                    logger.error(f"[平仓失败] {symbol}: {e}")

        return closed

    def adjust_same_direction_stops(
        self,
        breakout_direction: str,
        strength: float,
        current_positions: Dict
    ) -> List[Dict]:
        """
        调整同向持仓的止损

        Args:
            breakout_direction: 破位方向
            strength: 破位强度
            current_positions: 当前持仓

        Returns:
            list: 调整后的持仓
        """
        adjusted = []

        for symbol, position in current_positions.items():
            # 判断是否同向
            is_same = position['side'] == breakout_direction

            if not is_same:
                continue

            # 根据破位强度调整止损
            new_stop_pct = None
            reason = ""

            if strength >= 90:
                # 极强破位，放宽止损，让利润奔跑
                new_stop_pct = 0.015  # 1.5%
                reason = "Big4极强破位，放宽止损"

            elif strength >= 80:
                # 强破位，适度放宽
                new_stop_pct = 0.012  # 1.2%
                reason = "Big4强破位，适度放宽止损"

            if new_stop_pct:
                try:
                    # 计算新止损价
                    old_stop = position.get('stop_loss', 0)
                    new_stop = self.calculate_new_stop_loss(
                        position['entry_price'],
                        position['side'],
                        new_stop_pct
                    )

                    # 只在新止损更宽松时才调整
                    if self.is_stop_more_loose(position['side'], old_stop, new_stop):
                        position['stop_loss'] = new_stop
                        position['stop_loss_reason'] = reason

                        adjusted.append({
                            'symbol': symbol,
                            'side': position['side'],
                            'old_stop': old_stop,
                            'new_stop': new_stop,
                            'reason': reason
                        })

                        logger.info(f"[调整止损] {symbol} {position['side']}: "
                                  f"{old_stop:.6f} → {new_stop:.6f}")

                except Exception as e:
                    logger.error(f"[调整止损失败] {symbol}: {e}")

        return adjusted

    def close_position(
        self,
        symbol: str,
        position: Dict,
        reason: str
    ) -> Optional[Dict]:
        """
        平仓

        Args:
            symbol: 交易对
            position: 持仓信息
            reason: 平仓原因

        Returns:
            dict: 订单信息
        """
        try:
            # 创建平仓订单
            side = 'SELL' if position['side'] == 'LONG' else 'BUY'

            order = self.exchange.create_order(
                symbol=symbol,
                side=side,
                type='MARKET',
                quantity=position['quantity'],
                params={'reduceOnly': True}  # 只减仓
            )

            # 记录平仓原因到数据库
            # TODO: 保存到 futures_orders 表的 notes 字段

            # 从持仓列表移除
            if symbol in self.positions:
                del self.positions[symbol]

            return order

        except Exception as e:
            logger.error(f"平仓 {symbol} 失败: {e}")
            raise

    def calculate_new_stop_loss(
        self,
        entry_price: float,
        side: str,
        stop_pct: float
    ) -> float:
        """
        计算新的止损价

        Args:
            entry_price: 入场价格
            side: 方向
            stop_pct: 止损百分比

        Returns:
            float: 止损价
        """
        if side == 'LONG':
            return entry_price * (1 - stop_pct)
        else:  # SHORT
            return entry_price * (1 + stop_pct)

    def is_stop_more_loose(
        self,
        side: str,
        old_stop: float,
        new_stop: float
    ) -> bool:
        """
        判断新止损是否更宽松

        Args:
            side: 方向
            old_stop: 旧止损
            new_stop: 新止损

        Returns:
            bool: 新止损是否更宽松
        """
        if old_stop == 0:
            return True  # 没有旧止损，直接设置

        if side == 'LONG':
            # 多单：新止损更低 = 更宽松
            return new_stop < old_stop
        else:  # SHORT
            # 空单：新止损更高 = 更宽松
            return new_stop > old_stop

    def update_positions(self, positions: Dict):
        """
        更新持仓信息

        Args:
            positions: 新的持仓字典
        """
        self.positions = positions


if __name__ == '__main__':
    # 测试代码
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    # 模拟持仓
    test_positions = {
        'DOGE/USDT': {
            'side': 'LONG',
            'entry_price': 0.08,
            'quantity': 10000,
            'margin': 800,
            'unrealized_pnl': -50,
            'stop_loss': 0.079
        },
        'SHIB/USDT': {
            'side': 'LONG',
            'entry_price': 0.000011,
            'quantity': 100000000,
            'margin': 1100,
            'unrealized_pnl': 30,
            'stop_loss': 0.0000109
        },
        'PEPE/USDT': {
            'side': 'SHORT',
            'entry_price': 0.0000012,
            'quantity': 500000000,
            'margin': 600,
            'unrealized_pnl': 100,
            'stop_loss': 0.00000122
        }
    }

    # 创建管理器（使用None作为exchange，测试时不实际交易）
    manager = BreakoutPositionManager(exchange=None)

    # 测试Big4破位向下
    print("\n=== 测试: Big4破位向下，强度85 ===")
    result = manager.handle_big4_breakout('SHORT', 85, test_positions)

    print(f"\n平仓列表: {len(result['closed'])}")
    for pos in result['closed']:
        print(f"  {pos['symbol']} {pos['side']}: {pos['reason']}")

    print(f"\n调整列表: {len(result['adjusted'])}")
    for pos in result['adjusted']:
        print(f"  {pos['symbol']} {pos['side']}: {pos['reason']}")

    print("\n破位持仓管理器已实现")
