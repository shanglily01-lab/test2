# -*- coding: utf-8 -*-
"""
实盘订单监控服务
监控限价单成交后自动设置止损止盈
"""

import asyncio
from decimal import Decimal
from typing import Dict, Optional, List
import pymysql
from loguru import logger
from datetime import datetime


class LiveOrderMonitor:
    """实盘订单监控器 - 监控限价单成交后设置止损止盈"""

    def __init__(self, db_config: Dict, live_engine):
        """
        初始化监控器

        Args:
            db_config: 数据库配置
            live_engine: 实盘交易引擎实例 (BinanceFuturesEngine)
        """
        self.db_config = db_config
        self.live_engine = live_engine
        self.running = False
        self.task = None
        self.connection = None
        self.check_interval = 10  # 检查间隔（秒）

    def _get_connection(self):
        """获取数据库连接"""
        if self.connection is None or not self.connection.open:
            try:
                self.connection = pymysql.connect(
                    host=self.db_config.get('host', 'localhost'),
                    port=self.db_config.get('port', 3306),
                    user=self.db_config.get('user', 'root'),
                    password=self.db_config.get('password', ''),
                    database=self.db_config.get('database', 'binance-data'),
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor,
                    autocommit=True
                )
            except Exception as e:
                logger.error(f"[实盘监控] 创建数据库连接失败: {e}")
                raise
        else:
            try:
                self.connection.ping(reconnect=True)
            except Exception:
                self.connection = pymysql.connect(
                    host=self.db_config.get('host', 'localhost'),
                    port=self.db_config.get('port', 3306),
                    user=self.db_config.get('user', 'root'),
                    password=self.db_config.get('password', ''),
                    database=self.db_config.get('database', 'binance-data'),
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor,
                    autocommit=True
                )
        return self.connection

    def start(self):
        """启动监控"""
        if self.running:
            logger.warning("[实盘监控] 监控已在运行中")
            return

        self.running = True
        self.task = asyncio.create_task(self._monitor_loop())
        logger.info("[实盘监控] 订单监控服务已启动")

    def stop(self):
        """停止监控"""
        self.running = False
        if self.task:
            self.task.cancel()
        logger.info("[实盘监控] 订单监控服务已停止")

    async def _monitor_loop(self):
        """监控循环"""
        while self.running:
            try:
                await self._check_pending_orders()
            except Exception as e:
                logger.error(f"[实盘监控] 检查待处理订单时出错: {e}")

            await asyncio.sleep(self.check_interval)

    async def _check_pending_orders(self):
        """检查待处理的限价单"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 查询状态为 PENDING 且有止损止盈设置的仓位
            cursor.execute("""
                SELECT id, binance_order_id, symbol, position_side, quantity,
                       stop_loss_price, take_profit_price, leverage
                FROM live_futures_positions
                WHERE status = 'PENDING'
                  AND (stop_loss_price IS NOT NULL OR take_profit_price IS NOT NULL)
                  AND binance_order_id IS NOT NULL
            """)

            pending_positions = cursor.fetchall()

            if not pending_positions:
                return

            logger.debug(f"[实盘监控] 发现 {len(pending_positions)} 个待监控的限价单")

            for position in pending_positions:
                await self._check_order_status(position)

        except Exception as e:
            logger.error(f"[实盘监控] 检查待处理订单失败: {e}")

    async def _check_order_status(self, position: Dict):
        """检查单个订单的状态"""
        try:
            order_id = position['binance_order_id']
            symbol = position['symbol']
            binance_symbol = symbol.replace('/', '').upper()

            # 查询币安订单状态
            result = self.live_engine._request('GET', '/fapi/v1/order', {
                'symbol': binance_symbol,
                'orderId': order_id
            })

            if isinstance(result, dict) and result.get('success') == False:
                logger.warning(f"[实盘监控] 查询订单 {order_id} 失败: {result.get('error')}")
                return

            status = result.get('status', '')
            executed_qty = Decimal(str(result.get('executedQty', '0')))
            avg_price = Decimal(str(result.get('avgPrice', '0')))

            if status == 'FILLED' and executed_qty > 0:
                logger.info(f"[实盘监控] 限价单 {order_id} 已成交: {executed_qty} @ {avg_price}")

                # 更新数据库状态
                await self._update_position_filled(position, executed_qty, avg_price)

                # 设置止损止盈
                await self._place_sl_tp_orders(position, executed_qty)

            elif status in ['CANCELED', 'EXPIRED', 'REJECTED']:
                # 订单已取消/过期/拒绝，更新数据库
                logger.info(f"[实盘监控] 限价单 {order_id} 状态: {status}")
                await self._update_position_canceled(position, status)

        except Exception as e:
            logger.error(f"[实盘监控] 检查订单状态失败: {e}")

    async def _update_position_filled(self, position: Dict, executed_qty: Decimal, avg_price: Decimal):
        """更新已成交的仓位"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE live_futures_positions
                SET status = 'OPEN',
                    quantity = %s,
                    entry_price = %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (float(executed_qty), float(avg_price), position['id']))

            logger.info(f"[实盘监控] 仓位 {position['id']} 已更新为 OPEN")

        except Exception as e:
            logger.error(f"[实盘监控] 更新仓位状态失败: {e}")

    async def _update_position_canceled(self, position: Dict, status: str):
        """更新已取消的仓位"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE live_futures_positions
                SET status = %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (status, position['id']))

            logger.info(f"[实盘监控] 仓位 {position['id']} 已更新为 {status}")

        except Exception as e:
            logger.error(f"[实盘监控] 更新仓位状态失败: {e}")

    async def _place_sl_tp_orders(self, position: Dict, executed_qty: Decimal):
        """设置止损止盈订单"""
        symbol = position['symbol']
        position_side = position['position_side']
        stop_loss_price = position.get('stop_loss_price')
        take_profit_price = position.get('take_profit_price')

        # 设置止损
        if stop_loss_price:
            try:
                sl_result = self.live_engine._place_stop_loss(
                    symbol=symbol,
                    position_side=position_side,
                    quantity=executed_qty,
                    stop_price=Decimal(str(stop_loss_price))
                )
                if sl_result.get('success'):
                    logger.info(f"[实盘监控] ✓ 止损单已设置: {symbol} @ {stop_loss_price}")
                else:
                    logger.error(f"[实盘监控] ✗ 止损单设置失败: {sl_result.get('error')}")
            except Exception as e:
                logger.error(f"[实盘监控] 设置止损单异常: {e}")

        # 设置止盈
        if take_profit_price:
            try:
                tp_result = self.live_engine._place_take_profit(
                    symbol=symbol,
                    position_side=position_side,
                    quantity=executed_qty,
                    take_profit_price=Decimal(str(take_profit_price))
                )
                if tp_result.get('success'):
                    logger.info(f"[实盘监控] ✓ 止盈单已设置: {symbol} @ {take_profit_price}")
                else:
                    logger.error(f"[实盘监控] ✗ 止盈单设置失败: {tp_result.get('error')}")
            except Exception as e:
                logger.error(f"[实盘监控] 设置止盈单异常: {e}")


# 全局监控实例
_live_order_monitor: Optional[LiveOrderMonitor] = None


def get_live_order_monitor() -> Optional[LiveOrderMonitor]:
    """获取全局监控实例"""
    return _live_order_monitor


def init_live_order_monitor(db_config: Dict, live_engine) -> LiveOrderMonitor:
    """初始化全局监控实例"""
    global _live_order_monitor
    _live_order_monitor = LiveOrderMonitor(db_config, live_engine)
    return _live_order_monitor
