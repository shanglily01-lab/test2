#!/usr/bin/env python3
"""
信号反转监控器
Signal Reversal Monitor

监控持仓是否出现金叉/死叉反转信号，自动平仓
"""

import pymysql
from loguru import logger
from typing import Dict, List, Optional
import asyncio
from datetime import datetime, timedelta


class SignalReversalMonitor:
    """信号反转监控器"""

    def __init__(self, db_config: dict, binance_config: dict = None, trade_notifier=None):
        """
        初始化监控器

        Args:
            db_config: 数据库配置
            binance_config: Binance配置
            trade_notifier: 交易通知服务
        """
        self.db_config = db_config
        self.binance_config = binance_config
        self.trade_notifier = trade_notifier

        # 初始化策略执行器（用于调用平仓逻辑）
        from app.services.strategy_executor_v2 import StrategyExecutorV2
        self.executor = StrategyExecutorV2(db_config)

        # 反转检测冷却：防止重复日志，格式 {(symbol, position_side, reason): timestamp}
        self._detected_reversals = {}
        self.REVERSAL_LOG_COOLDOWN_MINUTES = 5  # 同一反转信号5分钟内只记录一次

        logger.info("SignalReversalMonitor initialized")

    def get_db_connection(self):
        """获取数据库连接"""
        return pymysql.connect(
            host=self.db_config['host'],
            port=self.db_config['port'],
            user=self.db_config['user'],
            password=self.db_config['password'],
            database=self.db_config['database'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

    def monitor_all_positions(self) -> Dict:
        """
        监控所有持仓的信号反转

        Returns:
            监控结果统计
        """
        results = {
            'total_positions': 0,
            'reversal_closed': 0,
            'errors': 0
        }

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            # 查询所有开仓持仓
            cursor.execute("""
                SELECT
                    id, symbol, position_side, entry_signal_type,
                    entry_price, quantity, margin, leverage,
                    open_time, account_id, strategy_id
                FROM futures_positions
                WHERE status = 'open'
                ORDER BY open_time ASC
            """)

            positions = cursor.fetchall()
            results['total_positions'] = len(positions)

            cursor.close()
            conn.close()

            if not positions:
                return results

            logger.debug(f"[信号反转监控] 开始检查 {len(positions)} 个持仓")

            # 检查每个持仓
            for position in positions:
                try:
                    # 使用asyncio运行异步方法
                    closed = asyncio.run(self._check_position_reversal(position))
                    if closed:
                        results['reversal_closed'] += 1

                except Exception as e:
                    logger.error(f"[信号反转监控] 检查持仓失败 {position['symbol']}: {e}")
                    results['errors'] += 1

            if results['reversal_closed'] > 0:
                logger.info(f"[信号反转监控] 完成: {results['reversal_closed']} 个持仓因反转被平仓")

            return results

        except Exception as e:
            logger.error(f"[信号反转监控] 监控失败: {e}", exc_info=True)
            results['errors'] += 1
            return results

    async def _check_position_reversal(self, position: Dict) -> bool:
        """
        检查单个持仓是否应该因反转平仓

        Args:
            position: 持仓信息

        Returns:
            是否已平仓
        """
        symbol = position['symbol']
        position_id = position['id']
        position_side = position['position_side']

        try:
            # 获取策略配置
            conn = self.get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT config FROM trading_strategies
                WHERE id = %s
            """, (position['strategy_id'],))

            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if not result:
                return False

            import json
            strategy = json.loads(result['config'])

            # 获取策略配置的信号周期（用于反转检测）
            timeframe = strategy.get('buySignal', '15m')

            # 获取EMA数据（get_ema_data是同步方法，不需要await）
            ema_data = self.executor.get_ema_data(symbol, timeframe)
            if not ema_data:
                return False

            # 检查反转信号
            should_close, close_reason = self.executor.check_cross_reversal(position, ema_data)

            if should_close:
                # 执行平仓
                await self.executor.execute_close_position(position, close_reason, strategy)

                # 发送通知
                if self.trade_notifier:
                    try:
                        self.trade_notifier.send_close_signal(
                            symbol=symbol,
                            side=position_side,
                            reason=close_reason,
                            position_id=position_id
                        )
                    except Exception as e:
                        logger.warning(f"发送通知失败: {e}")

                # 清除冷却记录（已成功平仓）
                reversal_key = (symbol, position_side, close_reason)
                if reversal_key in self._detected_reversals:
                    del self._detected_reversals[reversal_key]

                logger.info(f"🔄 [信号反转] {symbol} {position_side} 触发反转并平仓: {close_reason}")
                return True

            # 检查是否检测到反转但无法平仓（盈利不足）
            # 从check_cross_reversal的实现可知，返回False但内部已检测到反转
            # 需要检查是否有反转但被盈利要求拦截
            reversal_detected = self._check_reversal_without_profit_requirement(position, ema_data)

            if reversal_detected:
                reversal_key = (symbol, position_side, reversal_detected)
                now = datetime.now()

                # 检查冷却
                if reversal_key in self._detected_reversals:
                    last_log_time = self._detected_reversals[reversal_key]
                    if (now - last_log_time).total_seconds() < self.REVERSAL_LOG_COOLDOWN_MINUTES * 60:
                        # 冷却中，跳过日志
                        return False

                # 记录新的反转检测
                self._detected_reversals[reversal_key] = now
                logger.debug(
                    f"[信号反转] {symbol} {position_side} 检测到反转信号 {reversal_detected}，"
                    f"但盈利不足无法平仓（需≥1.0%）"
                )

            return False

        except Exception as e:
            logger.error(f"[信号反转监控] 检查 {symbol} 失败: {e}", exc_info=True)
            return False

    def _check_reversal_without_profit_requirement(self, position: Dict, ema_data: Dict) -> Optional[str]:
        """
        检查是否存在反转信号（不考虑盈利要求）
        用于日志记录，避免重复打印

        Args:
            position: 持仓信息
            ema_data: EMA数据

        Returns:
            反转类型字符串，如果没有反转则返回None
        """
        try:
            symbol = position['symbol']
            position_side = position['position_side']

            ema9 = ema_data.get('ema9')
            ema26 = ema_data.get('ema26')
            prev_ema9 = ema_data.get('prev_ema9')
            prev_ema26 = ema_data.get('prev_ema26')

            if not all([ema9, ema26, prev_ema9, prev_ema26]):
                return None

            # 检查金叉/死叉反转（不检查盈利）
            if position_side == 'LONG':
                # 多头持仓，检查死叉（看跌反转）
                is_death_cross = prev_ema9 >= prev_ema26 and ema9 < ema26
                if is_death_cross:
                    return "trend_reversal_bearish"

            elif position_side == 'SHORT':
                # 空头持仓，检查金叉（看涨反转）
                is_golden_cross = prev_ema9 <= prev_ema26 and ema9 > ema26
                if is_golden_cross:
                    return "trend_reversal_bullish"

            return None

        except Exception as e:
            logger.debug(f"检查反转信号失败 {position.get('symbol')}: {e}")
            return None

    def close(self):
        """关闭监控器"""
        logger.info("SignalReversalMonitor stopped")
