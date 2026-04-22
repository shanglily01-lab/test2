#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Big4紧急干预监控器
核心功能: 检测Big4重大事件反转，触发紧急平仓
"""

from app.utils.config_loader import get_db_config
import asyncio
import pymysql
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from app.utils.logger import logger


class Big4EmergencyMonitor:
    """Big4紧急干预监控器"""

    def __init__(self, db_config: dict):
        self.db_config = db_config

        # 🚨 紧急干预阈值
        self.emergency_strength_threshold = 12  # Big4强度 >= 12触发紧急干预
        self.check_interval_seconds = 60  # 每60秒检查一次Big4状态

        # 记录上次Big4状态
        self.last_big4_status = {}  # {symbol: {'signal': 'BULL', 'strength': 8}}

    def get_db_connection(self):
        """获取数据库连接"""
        return pymysql.connect(
            host=self.db_config['host'],
            user=self.db_config['user'],
            password=self.db_config['password'],
            database=self.db_config['database'],
            cursorclass=pymysql.cursors.DictCursor
        )

    async def start_monitoring(self):
        """启动Big4紧急监控"""
        logger.info("[Big4紧急监控] 启动")

        while True:
            try:
                await self.check_big4_emergency()
                await asyncio.sleep(self.check_interval_seconds)
            except Exception as e:
                logger.error(f"[Big4紧急监控] 异常: {e}", exc_info=True)
                await asyncio.sleep(self.check_interval_seconds)

    async def check_big4_emergency(self):
        """检查Big4紧急干预条件"""
        try:
            # 获取所有开仓持仓
            open_positions = await self.get_open_positions()
            if not open_positions:
                return

            # 获取当前Big4信号
            symbols = list(set([p['symbol'] for p in open_positions]))
            current_big4_signals = await self.get_current_big4_signals(symbols)

            # 检查每个持仓
            for position in open_positions:
                symbol = position['symbol']
                position_side = position['position_side']
                position_id = position['id']

                if symbol not in current_big4_signals:
                    continue

                big4_data = current_big4_signals[symbol]
                current_signal = big4_data['signal']
                current_strength = big4_data['strength']

                # 🚨 检查是否需要紧急干预
                should_emergency_close, reason = self._check_emergency_condition(
                    position_side=position_side,
                    big4_signal=current_signal,
                    big4_strength=current_strength,
                    symbol=symbol
                )

                if should_emergency_close:
                    logger.critical(
                        f"🚨🚨🚨 [BIG4-EMERGENCY] {symbol} {position_side} "
                        f"触发紧急平仓! 原因: {reason}"
                    )

                    # 执行紧急平仓
                    await self.emergency_close_position(
                        position=position,
                        reason=reason,
                        big4_signal=current_signal,
                        big4_strength=current_strength
                    )

                # 更新Big4状态记录
                self.last_big4_status[symbol] = {
                    'signal': current_signal,
                    'strength': current_strength,
                    'timestamp': datetime.now()
                }

        except Exception as e:
            logger.error(f"[Big4紧急监控] 检查异常: {e}", exc_info=True)

    def _check_emergency_condition(
        self,
        position_side: str,
        big4_signal: str,
        big4_strength: int,
        symbol: str
    ) -> tuple[bool, Optional[str]]:
        """
        检查是否触发紧急干预条件

        紧急干预条件:
        1. Big4强度 >= 12 (强烈信号)
        2. Big4方向与持仓方向相反
        3. Big4发生了反转 (可选: 增加反转检测)

        Returns:
            (是否紧急平仓, 原因)
        """
        # 解析Big4方向
        if not big4_signal or big4_strength < self.emergency_strength_threshold:
            return False, None

        if 'BULL' in big4_signal.upper():
            big4_direction = 'LONG'
        elif 'BEAR' in big4_signal.upper():
            big4_direction = 'SHORT'
        else:
            return False, None  # NEUTRAL不干预

        # 检查方向是否相反
        if big4_direction == position_side:
            return False, None  # 同向不干预

        # 🚨 触发紧急干预
        reason = (
            f"Big4紧急干预: {big4_signal}(强度{big4_strength}) "
            f"与持仓{position_side}相反"
        )

        # 可选: 检测是否发生反转
        reversal_info = self._detect_big4_reversal(symbol, big4_signal, big4_strength)
        if reversal_info:
            reason += f" | {reversal_info}"

        return True, reason

    def _detect_big4_reversal(
        self,
        symbol: str,
        current_signal: str,
        current_strength: int
    ) -> Optional[str]:
        """
        检测Big4是否发生反转

        Returns:
            反转描述，如果没有反转返回None
        """
        if symbol not in self.last_big4_status:
            return None

        last_status = self.last_big4_status[symbol]
        last_signal = last_status['signal']
        last_strength = last_status['strength']

        # 检测反转
        if 'BULL' in last_signal.upper() and 'BEAR' in current_signal.upper():
            return f"反转: {last_signal}({last_strength}) → {current_signal}({current_strength})"
        elif 'BEAR' in last_signal.upper() and 'BULL' in current_signal.upper():
            return f"反转: {last_signal}({last_strength}) → {current_signal}({current_strength})"

        return None

    async def emergency_close_position(
        self,
        position: Dict,
        reason: str,
        big4_signal: str,
        big4_strength: int
    ):
        """
        执行紧急平仓

        Args:
            position: 持仓信息
            reason: 平仓原因
            big4_signal: 当前Big4信号
            big4_strength: Big4强度
        """
        position_id = position['id']
        symbol = position['symbol']
        position_side = position['position_side']
        quantity = position['quantity']
        entry_price = position['entry_price']

        try:
            # 获取当前价格
            current_price = await self.get_current_price(symbol)
            if not current_price:
                logger.error(f"[Big4紧急平仓] {symbol} 无法获取当前价格")
                return

            # 计算实现盈亏
            if position_side == 'LONG':
                realized_pnl = (current_price - entry_price) * quantity
            else:
                realized_pnl = (entry_price - current_price) * quantity

            logger.critical(
                f"🚨 [Big4紧急平仓] {symbol} {position_side}\n"
                f"  持仓ID: {position_id}\n"
                f"  入场价: ${entry_price:.2f}\n"
                f"  当前价: ${current_price:.2f}\n"
                f"  数量: {quantity:.6f}\n"
                f"  实现盈亏: ${realized_pnl:.2f}\n"
                f"  Big4状态: {big4_signal}(强度{big4_strength})\n"
                f"  原因: {reason}"
            )

            # TODO: 对接交易所API执行平仓
            # await self.place_close_order(symbol, position_side, quantity)

            # 更新数据库
            conn = self.get_db_connection()
            cursor = conn.cursor()

            try:
                # 更新持仓记录
                cursor.execute("""
                    UPDATE futures_positions
                    SET status = 'closed',
                        close_price = %s,
                        close_time = NOW(),
                        realized_pnl = %s,
                        close_reason = %s,
                        notes = CONCAT(COALESCE(notes, ''), %s),
                        updated_at = NOW()
                    WHERE id = %s
                """, (
                    current_price,
                    realized_pnl,
                    'Big4紧急干预',
                    f"\n[紧急干预] {reason} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    position_id
                ))

                # 解冻保证金
                margin = position['margin']
                account_id = position['account_id']

                cursor.execute("""
                    UPDATE futures_trading_accounts
                    SET current_balance = current_balance + %s + %s,
                        frozen_balance = frozen_balance - %s,
                        total_realized_pnl = total_realized_pnl + %s,
                        updated_at = NOW()
                    WHERE id = %s
                """, (margin, realized_pnl, margin, realized_pnl, account_id))

                conn.commit()

                logger.info(f"✅ [Big4紧急平仓] 数据库更新成功 | 持仓ID={position_id}")

            except Exception as e:
                conn.rollback()
                logger.error(f"❌ [Big4紧急平仓] 数据库更新失败: {e}", exc_info=True)
            finally:
                cursor.close()
                conn.close()

        except Exception as e:
            logger.error(f"❌ [Big4紧急平仓] 执行失败: {e}", exc_info=True)

    async def get_open_positions(self) -> List[Dict]:
        """获取所有开仓持仓"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    id, account_id, symbol, position_side, quantity,
                    entry_price, margin, leverage, open_time
                FROM futures_positions
                WHERE status = 'open'
                ORDER BY open_time DESC
            """)

            positions = cursor.fetchall()
            cursor.close()
            conn.close()

            return positions

        except Exception as e:
            logger.error(f"[Big4紧急监控] 获取持仓失败: {e}", exc_info=True)
            return []

    async def get_current_big4_signals(self, symbols: List[str]) -> Dict[str, Dict]:
        """
        获取当前Big4信号

        Returns:
            {symbol: {'signal': 'BULL', 'strength': 12}}
        """
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            # 获取最新Big4信号
            placeholders = ', '.join(['%s'] * len(symbols))
            cursor.execute(f"""
                SELECT symbol, signal, strength, created_at
                FROM big4_signals
                WHERE symbol IN ({placeholders})
                AND created_at >= NOW() - INTERVAL 10 MINUTE
                ORDER BY created_at DESC
            """, tuple(symbols))

            rows = cursor.fetchall()
            cursor.close()
            conn.close()

            # 每个币种取最新记录
            result = {}
            for row in rows:
                symbol = row['symbol']
                if symbol not in result:
                    result[symbol] = {
                        'signal': row['signal'],
                        'strength': row['strength'],
                        'timestamp': row['created_at']
                    }

            return result

        except Exception as e:
            logger.error(f"[Big4紧急监控] 获取Big4信号失败: {e}", exc_info=True)
            return {}

    async def get_current_price(self, symbol: str) -> Optional[float]:
        """获取当前价格"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT close_price
                FROM kline_data
                WHERE symbol = %s AND timeframe = '1m'
                ORDER BY open_time DESC
                LIMIT 1
            """, (symbol,))

            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if result:
                return float(result['close_price'])
            return None

        except Exception as e:
            logger.error(f"[Big4紧急监控] 获取价格失败: {e}", exc_info=True)
            return None


# 测试代码
async def test_big4_emergency_monitor():
    """测试Big4紧急监控器"""
    import os
    from dotenv import load_dotenv

    load_dotenv()

    db_config = {
        **get_db_config()
    }

    monitor = Big4EmergencyMonitor(db_config)

    print("\n" + "="*80)
    print("Big4紧急监控器测试")
    print("="*80)
    print(f"紧急强度阈值: {monitor.emergency_strength_threshold}")
    print(f"检查间隔: {monitor.check_interval_seconds}秒")
    print("="*80 + "\n")

    # 启动监控
    await monitor.start_monitoring()


if __name__ == '__main__':
    asyncio.run(test_big4_emergency_monitor())
