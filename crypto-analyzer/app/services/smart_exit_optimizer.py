"""
智能平仓优化器
基于实时价格监控的分层平仓策略
"""
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from decimal import Decimal
from loguru import logger
import mysql.connector
from mysql.connector import pooling


class SmartExitOptimizer:
    """智能平仓优化器（基于实时价格监控）"""

    def __init__(self, db_config: dict, live_engine, price_service):
        """
        初始化平仓优化器

        Args:
            db_config: 数据库配置
            live_engine: 实盘引擎（用于执行平仓）
            price_service: 价格服务（WebSocket实时价格）
        """
        self.db_config = db_config
        self.live_engine = live_engine
        self.price_service = price_service

        # 数据库连接池（增加池大小以支持多个并发监控任务）
        # 每个监控任务每秒需要1个连接，预留20个连接支持20个并发持仓监控
        self.db_pool = pooling.MySQLConnectionPool(
            pool_name="exit_optimizer_pool",
            pool_size=20,
            **db_config
        )

        # 监控状态
        self.monitoring_tasks: Dict[str, asyncio.Task] = {}  # position_id -> task

    async def start_monitoring_position(self, position_id: int):
        """
        开始监控持仓（从开仓完成后立即开始）

        Args:
            position_id: 持仓ID
        """
        if position_id in self.monitoring_tasks:
            logger.warning(f"持仓 {position_id} 已在监控中")
            return

        # 创建独立监控任务
        task = asyncio.create_task(self._monitor_position(position_id))
        self.monitoring_tasks[position_id] = task

        logger.info(f"✅ 开始监控持仓 {position_id}")

    async def stop_monitoring_position(self, position_id: int):
        """
        停止监控持仓

        Args:
            position_id: 持仓ID
        """
        if position_id in self.monitoring_tasks:
            self.monitoring_tasks[position_id].cancel()
            del self.monitoring_tasks[position_id]
            logger.info(f"⏹️ 停止监控持仓 {position_id}")

    async def _monitor_position(self, position_id: int):
        """
        持仓监控主循环（实时价格监控）

        Args:
            position_id: 持仓ID
        """
        try:
            while True:
                # 获取持仓信息
                position = await self._get_position(position_id)

                if not position:
                    logger.info(f"持仓 {position_id} 不存在，停止监控")
                    break

                # 支持monitoring status='open'和'building'（分批建仓中）
                if position['status'] not in ('open', 'building'):
                    logger.info(f"持仓 {position_id} 已关闭 (status={position['status']})，停止监控")
                    break

                # 获取实时价格
                current_price = await self._get_realtime_price(position['symbol'])

                # 计算当前盈亏
                profit_info = self._calculate_profit(position, current_price)

                # 更新最高盈利记录
                await self._update_max_profit(position_id, profit_info)

                # 检查是否需要平仓
                should_close, reason = await self._check_exit_conditions(
                    position, current_price, profit_info
                )

                if should_close:
                    logger.info(
                        f"🚨 触发平仓条件: 持仓{position_id} {position['symbol']} "
                        f"{position['direction']} | {reason}"
                    )
                    await self._execute_close(position_id, current_price, reason)
                    break

                await asyncio.sleep(1)  # 每秒检查一次（实时监控）

        except asyncio.CancelledError:
            logger.info(f"监控任务被取消: 持仓 {position_id}")
        except Exception as e:
            logger.error(f"监控持仓 {position_id} 异常: {e}")

    async def _get_position(self, position_id: int) -> Optional[Dict]:
        """
        获取持仓信息

        Args:
            position_id: 持仓ID

        Returns:
            持仓字典
        """
        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor(dictionary=True)

            cursor.execute("""
                SELECT
                    id, symbol, position_side as direction, status,
                    avg_entry_price, quantity as position_size,
                    entry_signal_time, planned_close_time,
                    close_extended, extended_close_time,
                    max_profit_pct, max_profit_price, max_profit_time
                FROM futures_positions
                WHERE id = %s
            """, (position_id,))

            position = cursor.fetchone()

            cursor.close()
            conn.close()

            return position

        except Exception as e:
            logger.error(f"获取持仓信息失败: {e}")
            return None

    async def _get_realtime_price(self, symbol: str) -> Decimal:
        """
        获取实时价格（从WebSocket价格服务）

        Args:
            symbol: 交易对

        Returns:
            当前价格
        """
        try:
            # 从WebSocket价格服务获取
            price = self.price_service.get_price(symbol)
            if price:
                return Decimal(str(price))

            # 降级：从REST API获取
            logger.warning(f"{symbol} WebSocket价格不可用，降级到REST API")
            # TODO: 调用REST API获取价格
            return Decimal('0')

        except Exception as e:
            logger.error(f"获取实时价格失败: {e}")
            return Decimal('0')

    def _calculate_profit(self, position: Dict, current_price: Decimal) -> Dict:
        """
        计算当前盈亏信息

        Args:
            position: 持仓信息
            current_price: 当前价格

        Returns:
            {'profit_pct': float, 'profit_usdt': float, 'current_price': float}
        """
        avg_entry_price = Decimal(str(position['avg_entry_price']))
        position_size = Decimal(str(position['position_size']))
        direction = position['direction']

        # 计算盈亏百分比
        if direction == 'LONG':
            profit_pct = float((current_price - avg_entry_price) / avg_entry_price * 100)
        else:  # SHORT
            profit_pct = float((avg_entry_price - current_price) / avg_entry_price * 100)

        # 计算盈亏金额（USDT）
        profit_usdt = float(position_size * avg_entry_price * Decimal(str(profit_pct / 100)))

        return {
            'profit_pct': profit_pct,
            'profit_usdt': profit_usdt,
            'current_price': float(current_price)
        }

    async def _update_max_profit(self, position_id: int, profit_info: Dict):
        """
        更新最高盈利记录

        Args:
            position_id: 持仓ID
            profit_info: 盈亏信息
        """
        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor(dictionary=True)

            # 获取当前最高盈利
            cursor.execute("""
                SELECT max_profit_pct
                FROM futures_positions
                WHERE id = %s
            """, (position_id,))

            result = cursor.fetchone()
            current_max = float(result['max_profit_pct']) if result and result['max_profit_pct'] else 0.0

            # 如果当前盈利更高，更新记录
            if profit_info['profit_pct'] > current_max:
                cursor.execute("""
                    UPDATE futures_positions
                    SET
                        max_profit_pct = %s,
                        max_profit_price = %s,
                        max_profit_time = %s
                    WHERE id = %s
                """, (
                    profit_info['profit_pct'],
                    profit_info['current_price'],
                    datetime.now(),
                    position_id
                ))

                conn.commit()

                logger.debug(
                    f"📈 更新最高盈利: 持仓{position_id} "
                    f"{current_max:.2f}% -> {profit_info['profit_pct']:.2f}%"
                )

            cursor.close()
            conn.close()

        except Exception as e:
            logger.error(f"更新最高盈利失败: {e}")

    async def _check_exit_conditions(
        self,
        position: Dict,
        current_price: Decimal,
        profit_info: Dict
    ) -> tuple[bool, str]:
        """
        检查平仓条件（分层逻辑）

        Args:
            position: 持仓信息
            current_price: 当前价格
            profit_info: 盈亏信息

        Returns:
            (should_close: bool, reason: str)
        """
        profit_pct = profit_info['profit_pct']
        max_profit_pct = float(position['max_profit_pct']) if position['max_profit_pct'] else 0.0

        # 计算当前回撤（从最高点）
        drawback = max_profit_pct - profit_pct

        # ========== 首先检查时间：只在计划平仓前30分钟才开始检查平仓条件 ==========
        planned_close_time = position['planned_close_time']
        close_extended = position['close_extended']
        now = datetime.now()

        # 计划平仓前30分钟
        monitoring_start_time = planned_close_time - timedelta(minutes=30)

        # 如果还未到监控时间（距离计划平仓还有30分钟以上），不检查任何平仓条件
        if now < monitoring_start_time:
            return False, ""

        # ========== 到达监控时间后，开始检查分层平仓逻辑 ==========

        # 层级1: 盈利 ≥ 3%，回撤 ≥ 0.5% → 平仓
        if max_profit_pct >= 3.0 and drawback >= 0.5:
            return True, f"高盈利回撤止盈(盈利{profit_pct:.2f}%, 最高{max_profit_pct:.2f}%, 回撤{drawback:.2f}%)"

        # 层级2: 盈利 1-3%，回撤 ≥ 0.4% → 平仓
        if max_profit_pct >= 1.0 and max_profit_pct < 3.0 and drawback >= 0.4:
            return True, f"中盈利回撤止盈(盈利{profit_pct:.2f}%, 最高{max_profit_pct:.2f}%, 回撤{drawback:.2f}%)"

        # 层级3: 盈利 ≥ 1%，立即平仓（保住利润）
        if profit_pct >= 1.0:
            return True, f"盈利止盈(盈利{profit_pct:.2f}%)"

        # 层级4: 微亏损（-0.5% ~ 0%）或微盈利（0-1%），根据时间决策
        if -0.5 <= profit_pct < 1.0:
            # 到达监控时间但未到计划时间，检查是否需要延长
            if now >= monitoring_start_time and now < planned_close_time and not close_extended:
                # 继续持有，等待到达计划平仓时间
                return False, ""

            # 到达计划平仓时间，延长30分钟
            if now >= planned_close_time and not close_extended:
                await self._extend_close_time(position['id'], 30)
                return False, "延长平仓时间30分钟（微盈利/微亏损）"

            # 如果已经延长过，检查延长后的时间
            if close_extended:
                extended_close_time = position['extended_close_time']
                if now >= extended_close_time:
                    return True, f"延长时间已到，强制平仓(盈亏{profit_pct:+.2f}%)"

        # 层级5: 亏损 > 0.5%，到达计划时间直接平仓
        if profit_pct < -0.5:
            if now >= planned_close_time:
                return True, f"计划平仓时间已到(亏损{profit_pct:.2f}%)"

        # 默认：不平仓
        return False, ""

    async def _extend_close_time(self, position_id: int, extend_minutes: int):
        """
        延长平仓时间

        Args:
            position_id: 持仓ID
            extend_minutes: 延长分钟数
        """
        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor()

            # 获取当前计划平仓时间
            cursor.execute("""
                SELECT planned_close_time
                FROM futures_positions
                WHERE id = %s
            """, (position_id,))

            result = cursor.fetchone()
            if not result:
                return

            planned_close_time = result[0]
            extended_close_time = planned_close_time + timedelta(minutes=extend_minutes)

            # 更新延长时间
            cursor.execute("""
                UPDATE futures_positions
                SET
                    close_extended = TRUE,
                    extended_close_time = %s
                WHERE id = %s
            """, (extended_close_time, position_id))

            conn.commit()

            cursor.close()
            conn.close()

            logger.info(
                f"⏰ 延长平仓时间: 持仓{position_id} "
                f"{planned_close_time.strftime('%H:%M:%S')} -> {extended_close_time.strftime('%H:%M:%S')}"
            )

        except Exception as e:
            logger.error(f"延长平仓时间失败: {e}")

    async def _execute_close(self, position_id: int, current_price: Decimal, reason: str):
        """
        执行平仓操作

        Args:
            position_id: 持仓ID
            current_price: 当前价格
            reason: 平仓原因
        """
        try:
            # 获取持仓信息
            position = await self._get_position(position_id)

            if not position:
                logger.error(f"持仓 {position_id} 不存在，无法平仓")
                return

            logger.info(
                f"🔴 执行平仓: 持仓{position_id} {position['symbol']} "
                f"{position['direction']} | 价格{current_price} | {reason}"
            )

            # 调用实盘引擎执行平仓
            close_result = await self.live_engine.close_position(
                symbol=position['symbol'],
                direction=position['direction'],
                position_size=float(position['position_size']),
                reason=reason
            )

            if close_result['success']:
                # 更新数据库状态
                await self._update_position_closed(
                    position_id,
                    float(current_price),
                    reason
                )

                logger.info(f"✅ 平仓成功: 持仓{position_id}")

                # 停止监控
                await self.stop_monitoring_position(position_id)
            else:
                logger.error(f"平仓失败: 持仓{position_id} | {close_result.get('error')}")

        except Exception as e:
            logger.error(f"执行平仓异常: {e}")

    async def _update_position_closed(
        self,
        position_id: int,
        close_price: float,
        close_reason: str
    ):
        """
        更新持仓为已平仓状态

        Args:
            position_id: 持仓ID
            close_price: 平仓价格
            close_reason: 平仓原因
        """
        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE futures_positions
                SET
                    status = 'closed',
                    close_time = %s,
                    notes = %s
                WHERE id = %s
            """, (
                datetime.now(),
                close_reason,
                position_id
            ))

            conn.commit()

            cursor.close()
            conn.close()

        except Exception as e:
            logger.error(f"更新持仓状态失败: {e}")
