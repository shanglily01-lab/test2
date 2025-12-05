# -*- coding: utf-8 -*-
"""
实盘订单监控服务
监控限价单成交后自动设置止损止盈
支持趋势转向时自动取消未成交限价单
"""

import asyncio
from decimal import Decimal
from typing import Dict, Optional, List
import pymysql
import json
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

    def _calculate_ema(self, prices: List[float], period: int) -> List[float]:
        """
        计算EMA（指数移动平均）

        Args:
            prices: 价格列表
            period: EMA周期

        Returns:
            EMA值列表
        """
        if len(prices) < period:
            return []

        ema_values = []
        multiplier = 2 / (period + 1)

        # 初始EMA使用SMA
        sma = sum(prices[:period]) / period
        ema_values.append(sma)

        # 计算后续EMA
        for i in range(period, len(prices)):
            ema = prices[i] * multiplier + ema_values[-1] * (1 - multiplier)
            ema_values.append(ema)

        return ema_values

    def _check_trend_reversal(self, position: Dict) -> Optional[str]:
        """
        检查趋势是否已转向（出现反向EMA交叉信号）

        Args:
            position: 仓位信息

        Returns:
            取消原因（如果需要取消），否则返回 None
        """
        try:
            symbol = position['symbol']
            position_side = position['position_side']  # LONG 或 SHORT

            # 默认使用15分钟时间周期
            timeframe = '15m'

            # 查询最近的K线数据
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(
                """SELECT close_price
                FROM kline_data
                WHERE symbol = %s AND timeframe = %s
                ORDER BY timestamp DESC
                LIMIT 50""",
                (symbol, timeframe)
            )
            klines = cursor.fetchall()

            if not klines or len(klines) < 30:
                return None  # K线数据不足，跳过检查

            # 将K线反转为正序（从旧到新）
            prices = [float(k['close_price']) for k in reversed(klines)]

            # 计算EMA9和EMA26
            ema9_values = self._calculate_ema(prices, 9)
            ema26_values = self._calculate_ema(prices, 26)

            if len(ema9_values) < 2 or len(ema26_values) < 2:
                return None

            # 取最后两个EMA值来判断交叉
            curr_ema9 = ema9_values[-1]
            prev_ema9 = ema9_values[-2]
            curr_ema26 = ema26_values[-1]
            prev_ema26 = ema26_values[-2]

            # 检测死叉（EMA9下穿EMA26）
            is_death_cross = (prev_ema9 >= prev_ema26 and curr_ema9 < curr_ema26) or \
                            (prev_ema9 > prev_ema26 and curr_ema9 <= curr_ema26)

            # 检测金叉（EMA9上穿EMA26）
            is_golden_cross = (prev_ema9 <= prev_ema26 and curr_ema9 > curr_ema26) or \
                             (prev_ema9 < prev_ema26 and curr_ema9 >= curr_ema26)

            # 做多限价单，出现死叉则取消
            if position_side == 'LONG' and is_death_cross:
                ema_diff_pct = abs((curr_ema9 - curr_ema26) / curr_ema26 * 100)
                return f"趋势转向(死叉): EMA9={curr_ema9:.4f} < EMA26={curr_ema26:.4f}, 差值={ema_diff_pct:.2f}%"

            # 做空限价单，出现金叉则取消
            if position_side == 'SHORT' and is_golden_cross:
                ema_diff_pct = abs((curr_ema9 - curr_ema26) / curr_ema26 * 100)
                return f"趋势转向(金叉): EMA9={curr_ema9:.4f} > EMA26={curr_ema26:.4f}, 差值={ema_diff_pct:.2f}%"

            return None

        except Exception as e:
            logger.error(f"[实盘监控] 检查趋势转向时出错: {e}")
            return None

    async def _cancel_binance_order(self, position: Dict, reason: str):
        """
        取消币安订单

        Args:
            position: 仓位信息
            reason: 取消原因
        """
        try:
            symbol = position['symbol']
            order_id = position['binance_order_id']

            # 调用交易引擎取消订单
            result = self.live_engine.cancel_order(symbol, order_id)

            if result.get('success'):
                logger.info(f"[实盘监控] ✓ 币安订单已取消: {symbol} #{order_id} - {reason}")

                # 更新数据库状态
                await self._update_position_canceled(position, f'TREND_REVERSAL: {reason}')
            else:
                logger.error(f"[实盘监控] ✗ 取消币安订单失败: {result.get('error', '未知错误')}")

        except Exception as e:
            logger.error(f"[实盘监控] 取消币安订单异常: {e}")

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

            elif status == 'NEW':
                # 订单尚未成交，检查趋势是否转向
                trend_reversal_reason = self._check_trend_reversal(position)
                if trend_reversal_reason:
                    logger.info(f"[实盘监控] 📉 检测到趋势转向，准备取消限价单: {symbol} #{order_id}")
                    await self._cancel_binance_order(position, trend_reversal_reason)

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

        # 获取当前价格用于验证
        try:
            current_price = self.live_engine.get_current_price(symbol)
            if current_price == 0:
                logger.warning(f"[实盘监控] 无法获取 {symbol} 当前价格，跳过止损止盈设置")
                return
        except Exception as e:
            logger.error(f"[实盘监控] 获取价格失败: {e}")
            return

        # 设置止损
        if stop_loss_price:
            stop_loss_price = Decimal(str(stop_loss_price))
            # 验证止损价格是否合理
            # 做多：止损价必须低于当前价
            # 做空：止损价必须高于当前价
            is_valid = False
            if position_side == 'LONG' and stop_loss_price < current_price:
                is_valid = True
            elif position_side == 'SHORT' and stop_loss_price > current_price:
                is_valid = True

            if is_valid:
                try:
                    sl_result = self.live_engine._place_stop_loss(
                        symbol=symbol,
                        position_side=position_side,
                        quantity=executed_qty,
                        stop_price=stop_loss_price
                    )
                    if sl_result.get('success'):
                        logger.info(f"[实盘监控] ✓ 止损单已设置: {symbol} @ {stop_loss_price}")
                    else:
                        logger.error(f"[实盘监控] ✗ 止损单设置失败: {sl_result.get('error')}")
                except Exception as e:
                    logger.error(f"[实盘监控] 设置止损单异常: {e}")
            else:
                logger.warning(f"[实盘监控] 止损价 {stop_loss_price} 无效 ({position_side} 当前价 {current_price})，跳过止损设置")

        # 设置止盈
        if take_profit_price:
            take_profit_price = Decimal(str(take_profit_price))
            # 验证止盈价格是否合理
            # 做多：止盈价必须高于当前价
            # 做空：止盈价必须低于当前价
            is_valid = False
            if position_side == 'LONG' and take_profit_price > current_price:
                is_valid = True
            elif position_side == 'SHORT' and take_profit_price < current_price:
                is_valid = True

            if is_valid:
                try:
                    tp_result = self.live_engine._place_take_profit(
                        symbol=symbol,
                        position_side=position_side,
                        quantity=executed_qty,
                        take_profit_price=take_profit_price
                    )
                    if tp_result.get('success'):
                        logger.info(f"[实盘监控] ✓ 止盈单已设置: {symbol} @ {take_profit_price}")
                    else:
                        logger.error(f"[实盘监控] ✗ 止盈单设置失败: {tp_result.get('error')}")
                except Exception as e:
                    logger.error(f"[实盘监控] 设置止盈单异常: {e}")
            else:
                logger.warning(f"[实盘监控] 止盈价 {take_profit_price} 无效 ({position_side} 当前价 {current_price})，跳过止盈设置")


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
