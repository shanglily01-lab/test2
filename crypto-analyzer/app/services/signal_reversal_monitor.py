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

            # 获取EMA数据
            ema_data = await self.executor.get_ema_data(symbol)
            if not ema_data:
                return False

            # 检查反转信号
            should_close, close_reason = self.executor.check_cross_reversal(position, ema_data)

            if should_close:
                logger.info(f"🔄 [信号反转] {symbol} {position_side} 触发反转: {close_reason}")

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

                return True

            return False

        except Exception as e:
            logger.error(f"[信号反转监控] 检查 {symbol} 失败: {e}", exc_info=True)
            return False

    def close(self):
        """关闭监控器"""
        logger.info("SignalReversalMonitor stopped")
