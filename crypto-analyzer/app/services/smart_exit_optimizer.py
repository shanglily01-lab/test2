"""
智能平仓优化器
基于实时价格监控的智能分批平仓策略
"""
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple
from decimal import Decimal
from loguru import logger
import mysql.connector
from mysql.connector import pooling

from app.services.price_sampler import PriceSampler
from app.services.signal_analysis_service import SignalAnalysisService
from app.analyzers.kline_strength_scorer import KlineStrengthScorer


class SmartExitOptimizer:
    """智能平仓优化器（基于实时价格监控 + K线强度衰减检测 + 智能分批平仓）"""

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

        # 智能平仓计划（分批平仓）
        self.exit_plans: Dict[int, Dict] = {}  # position_id -> exit_plan

        # === K线强度监控 (新增) ===
        self.signal_analyzer = SignalAnalysisService(db_config)
        self.kline_scorer = KlineStrengthScorer()
        self.enable_kline_monitoring = True  # 启用K线强度监控

        # K线强度检查间隔（15分钟）
        self.kline_check_interval = 900  # 秒
        self.last_kline_check: Dict[int, datetime] = {}  # position_id -> last_check_time

        # 部分平仓阶段跟踪（避免重复触发）
        self.partial_close_stage: Dict[int, int] = {}  # position_id -> stage (0=未平仓, 1=平50%, 2=平70%, 3=平100%)

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

            # 清理部分平仓阶段记录
            if position_id in self.partial_close_stage:
                del self.partial_close_stage[position_id]

            # 清理K线检查时间记录
            if position_id in self.last_kline_check:
                del self.last_kline_check[position_id]

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

                # 检查兜底平仓条件（超高盈利/巨额亏损）
                should_close, reason = await self._check_exit_conditions(
                    position, current_price, profit_info
                )

                if should_close:
                    logger.info(
                        f"🚨 触发兜底平仓: 持仓{position_id} {position['symbol']} "
                        f"{position['direction']} | {reason}"
                    )
                    await self._execute_close(position_id, current_price, reason)
                    break

                # === K线强度衰减检测 (新增 - 每15分钟检查一次) ===
                should_check_kline = await self._should_check_kline_strength(position_id)
                if should_check_kline and self.enable_kline_monitoring:
                    kline_exit_signal = await self._check_kline_strength_decay(
                        position, current_price, profit_info
                    )
                    if kline_exit_signal:
                        reason, ratio = kline_exit_signal
                        logger.info(
                            f"📊 K线强度衰减触发平仓: 持仓{position_id} {position['symbol']} | {reason}"
                        )
                        if ratio >= 1.0:
                            # 全部平仓
                            await self._execute_close(position_id, current_price, reason)
                            break
                        else:
                            # 部分平仓
                            await self._execute_partial_close(position_id, current_price, ratio, reason)

                # 检查智能分批平仓
                exit_completed = await self._smart_batch_exit(
                    position_id, position, current_price, profit_info
                )

                if exit_completed:
                    logger.info(f"✅ 智能分批平仓完成: 持仓{position_id}")
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
                    max_profit_pct, max_profit_price, max_profit_time,
                    stop_loss_price, take_profit_price, leverage
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
        获取实时价格（多级降级策略）

        Args:
            symbol: 交易对

        Returns:
            当前价格
        """
        # 第1级: WebSocket价格
        try:
            price = self.price_service.get_price(symbol)
            if price and price > 0:
                return Decimal(str(price))
        except Exception as e:
            logger.warning(f"{symbol} WebSocket获取失败: {e}")

        # 第2级: REST API实时价格
        try:
            import requests
            symbol_clean = symbol.replace('/', '').upper()

            response = requests.get(
                'https://fapi.binance.com/fapi/v1/ticker/price',
                params={'symbol': symbol_clean},
                timeout=3
            )

            if response.status_code == 200:
                rest_price = float(response.json()['price'])
                if rest_price > 0:
                    logger.info(f"{symbol} 降级到REST API价格: {rest_price}")
                    return Decimal(str(rest_price))
        except Exception as e:
            logger.warning(f"{symbol} REST API获取失败: {e}")

        # 所有方法都失败
        logger.error(f"{symbol} 所有价格获取方法均失败")
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

        # 计算ROI（相对保证金的收益率）
        leverage = float(position.get('leverage', 1))
        roi_pct = profit_pct * leverage

        # 计算当前回撤（从最高点）
        drawback = max_profit_pct - profit_pct

        # ========== 优先级最高：止损止盈检查（任何时候都检查） ==========

        # 检查止损价格
        stop_loss_price = position.get('stop_loss_price')
        if stop_loss_price and float(stop_loss_price) > 0:
            stop_loss_price = Decimal(str(stop_loss_price))
            direction = position['direction']

            if direction == 'LONG':
                # 多头：当前价格 <= 止损价
                if current_price <= stop_loss_price:
                    return True, f"止损(价格{current_price:.8f} <= 止损价{stop_loss_price:.8f}, 价格变化{profit_pct:.2f}%, ROI {roi_pct:.2f}%)"
            else:  # SHORT
                # 空头：当前价格 >= 止损价
                if current_price >= stop_loss_price:
                    return True, f"止损(价格{current_price:.8f} >= 止损价{stop_loss_price:.8f}, 价格变化{profit_pct:.2f}%, ROI {roi_pct:.2f}%)"

        # 检查止盈价格
        take_profit_price = position.get('take_profit_price')
        if take_profit_price and float(take_profit_price) > 0:
            take_profit_price = Decimal(str(take_profit_price))
            direction = position['direction']

            if direction == 'LONG':
                # 多头：当前价格 >= 止盈价
                if current_price >= take_profit_price:
                    return True, f"止盈(价格{current_price:.8f} >= 止盈价{take_profit_price:.8f}, 价格变化{profit_pct:.2f}%, ROI {roi_pct:.2f}%)"
            else:  # SHORT
                # 空头：当前价格 <= 止盈价
                if current_price <= take_profit_price:
                    return True, f"止盈(价格{current_price:.8f} <= 止盈价{take_profit_price:.8f}, 价格变化{profit_pct:.2f}%, ROI {roi_pct:.2f}%)"

        # ========== 智能分批平仓逻辑（计划平仓前30分钟）==========
        planned_close_time = position['planned_close_time']
        now = datetime.now()
        monitoring_start_time = planned_close_time - timedelta(minutes=30)

        # 如果还未到监控时间，只检查止损止盈
        if now < monitoring_start_time:
            return False, ""

        # ========== 到达监控窗口，使用智能分批平仓 ==========
        # 注意：这里不再直接返回平仓决策
        # 而是在 _monitor_position 中调用 _smart_batch_exit 处理分批平仓
        # 这个方法现在主要用于兜底逻辑

        # 兜底逻辑1: 超高盈利立即全部平仓
        if profit_pct >= 5.0:
            return True, f"超高盈利全部平仓(价格变化{profit_pct:.2f}%, ROI {roi_pct:.2f}%)"

        # 兜底逻辑2: 巨额亏损立即全部平仓
        if profit_pct <= -3.0:
            return True, f"巨额亏损全部平仓(价格变化{profit_pct:.2f}%, ROI {roi_pct:.2f}%)"

        # 默认：不平仓（由智能分批平仓处理）
        return False, ""

    async def _smart_batch_exit(
        self,
        position_id: int,
        position: Dict,
        current_price: Decimal,
        profit_info: Dict
    ) -> bool:
        """
        智能平仓逻辑（计划平仓前30分钟）

        策略：
        1. T-30启动监控，T-25完成价格基线（5分钟采样）
        2. T-25到T+0寻找最佳价格，一次性平仓100%
        3. T+0（planned_close_time）必须强制执行

        时间窗口示例（planned_close_time = 11:46）:
        - 11:16 (T-30): 启动监控
        - 11:21 (T-25): 完成5分钟价格基线
        - 11:21-11:46: 25分钟寻找最佳平仓价格
        - 11:46 (T+0): 计划平仓时间，必须强制执行

        Args:
            position_id: 持仓ID
            position: 持仓信息
            current_price: 当前价格
            profit_info: 盈亏信息

        Returns:
            是否完成平仓
        """
        planned_close_time = position['planned_close_time']
        now = datetime.now()
        monitoring_start_time = planned_close_time - timedelta(minutes=30)

        # ========== 最高优先级：超时强制平仓 ==========
        if now >= planned_close_time:
            logger.warning(
                f"⚡ {position['symbol']} 已超过计划平仓时间，立即强制平仓! | "
                f"计划: {planned_close_time.strftime('%H:%M:%S')}, "
                f"当前: {now.strftime('%H:%M:%S')}"
            )
            # 获取当前价格
            current_price = await self._get_realtime_price(position['symbol'])
            await self._execute_close(position_id, current_price, "超时强制平仓")
            return True

        # 如果还未到监控时间，直接返回
        if now < monitoring_start_time:
            return False

        # 初始化平仓计划（第一次进入监控窗口）
        if position_id not in self.exit_plans:
            logger.info(
                f"🎯 {position['symbol']} 进入智能平仓窗口（30分钟） | "
                f"当前盈亏: {profit_info['profit_pct']:.2f}% | "
                f"计划平仓: {planned_close_time.strftime('%H:%M:%S')}"
            )

            # 启动价格基线采样器
            sampler = PriceSampler(position['symbol'], self.price_service, window_seconds=300)
            sampling_task = asyncio.create_task(sampler.start_background_sampling())

            # 创建平仓计划
            exit_plan = {
                'symbol': position['symbol'],
                'direction': position['direction'],
                'entry_price': float(position['avg_entry_price']),
                'total_quantity': float(position['position_size']),
                'monitoring_start_time': monitoring_start_time,
                'planned_close_time': planned_close_time,
                'sampler': sampler,
                'sampling_task': sampling_task,
                'baseline_built': False,
                'closed': False
            }

            self.exit_plans[position_id] = exit_plan

            # 等待5分钟建立基线
            logger.info(f"📊 {position['symbol']} 等待5分钟建立平仓价格基线...")

        exit_plan = self.exit_plans[position_id]

        # 如果已经平仓，直接返回
        if exit_plan['closed']:
            return True

        sampler = exit_plan['sampler']

        # 等待基线建立
        if not exit_plan['baseline_built']:
            if sampler.initial_baseline_built:
                exit_plan['baseline_built'] = True
                baseline = sampler.get_current_baseline()
                logger.info(
                    f"✅ {position['symbol']} 平仓基线建立: "
                    f"范围 {baseline['min_price']:.6f} - {baseline['max_price']:.6f}"
                )
            else:
                # 基线还未建立，继续等待
                return False

        baseline = sampler.get_current_baseline()
        if not baseline:
            return False

        elapsed_minutes = (now - exit_plan['monitoring_start_time']).total_seconds() / 60

        # ========== 平仓判断（一次性100%）==========
        should_exit, reason = await self._should_exit_single(
            position, current_price, baseline, exit_plan['entry_price'],
            elapsed_minutes, planned_close_time
        )

        if should_exit:
            # 一次性平仓100%
            await self._execute_close(position_id, current_price, reason)
            exit_plan['closed'] = True

            logger.info(
                f"✅ 智能平仓完成: {position['symbol']} @ {current_price:.6f} | {reason}"
            )

            # 停止采样器
            sampler.stop_sampling()
            exit_plan['sampling_task'].cancel()

            # 清理平仓计划
            del self.exit_plans[position_id]

            return True  # 完成平仓

        return False  # 未完成平仓

    async def _should_exit_single(
        self,
        position: Dict,
        current_price: Decimal,
        baseline: Dict,
        entry_price: float,
        elapsed_minutes: float,
        planned_close_time: datetime
    ) -> tuple[bool, str]:
        """
        一次性平仓判断（100%）

        时间窗口: T-30 到 T+0 (30分钟)
        强制截止: T+0 (planned_close_time必须执行)

        策略：
        1. 寻找最佳价格立即平仓
        2. T+0（planned_close_time）必须强制执行

        Returns:
            (是否平仓, 原因)
        """
        direction = position['direction']
        now = datetime.now()

        # ========== 最高优先级：超时强制平仓（已到达planned_close_time）==========
        if now >= planned_close_time:
            return True, f"计划平仓时间已到，强制执行"

        if direction == 'LONG':
            # 使用 PriceSampler 的评分系统
            exit_plan = self.exit_plans[position['id']]
            sampler = exit_plan['sampler']
            evaluation = sampler.is_good_long_exit_price(current_price, entry_price)

            # 条件1: 极佳卖点（评分 >= 95分）
            if evaluation['score'] >= 95:
                return True, f"极佳卖点(评分{evaluation['score']}): {evaluation['reason']}"

            # 条件2: 优秀卖点 + 有盈利（评分 >= 85分，盈利 > 0）
            if evaluation['score'] >= 85 and evaluation['profit_pct'] > 0:
                return True, f"优秀卖点(评分{evaluation['score']}, 盈利{evaluation['profit_pct']:.2f}%)"

            # 条件3: 突破基线最高价（冲高机会）
            if float(current_price) >= baseline['max_price'] * 1.001:
                return True, f"突破基线最高价({baseline['max_price']:.6f})"

            # 条件4: 盈利 >= 2% + 价格在P50以上
            if evaluation['profit_pct'] >= 2.0 and float(current_price) >= baseline['p50']:
                return True, f"高盈利(+{evaluation['profit_pct']:.2f}%) + 价格在中位数以上"

            # 条件5: 强下跌趋势预警（趋势转向，快速止盈）
            if baseline['trend']['direction'] == 'down' and baseline['trend']['strength'] > 0.6:
                if evaluation['profit_pct'] >= 0.5:  # 有盈利就跑
                    return True, f"强下跌趋势预警，快速止盈(+{evaluation['profit_pct']:.2f}%)"

            # 条件6: 时间压力（T-10分钟，评分 >= 60分）
            if elapsed_minutes >= 20 and evaluation['score'] >= 60:
                return True, f"接近截止(已{elapsed_minutes:.0f}分钟)，评分{evaluation['score']}"

        else:  # SHORT
            exit_plan = self.exit_plans[position['id']]
            sampler = exit_plan['sampler']
            evaluation = sampler.is_good_short_exit_price(current_price, entry_price)

            # 条件1: 极佳买点（评分 >= 95分）
            if evaluation['score'] >= 95:
                return True, f"极佳买点(评分{evaluation['score']}): {evaluation['reason']}"

            # 条件2: 优秀买点 + 有盈利
            if evaluation['score'] >= 85 and evaluation['profit_pct'] > 0:
                return True, f"优秀买点(评分{evaluation['score']}, 盈利{evaluation['profit_pct']:.2f}%)"

            # 条件3: 跌破基线最低价
            if float(current_price) <= baseline['min_price'] * 0.999:
                return True, f"跌破基线最低价({baseline['min_price']:.6f})"

            # 条件4: 盈利 >= 2% + 价格在P50以下
            if evaluation['profit_pct'] >= 2.0 and float(current_price) <= baseline['p50']:
                return True, f"高盈利(+{evaluation['profit_pct']:.2f}%) + 价格在中位数以下"

            # 条件5: 强上涨趋势预警
            if baseline['trend']['direction'] == 'up' and baseline['trend']['strength'] > 0.6:
                if evaluation['profit_pct'] >= 0.5:
                    return True, f"强上涨趋势预警，快速止盈(+{evaluation['profit_pct']:.2f}%)"

            # 条件6: 时间压力
            if elapsed_minutes >= 20 and evaluation['score'] >= 60:
                return True, f"接近截止(已{elapsed_minutes:.0f}分钟)，评分{evaluation['score']}"

        return False, ""

    async def _execute_partial_close(
        self,
        position_id: int,
        position: Dict,
        current_price: Decimal,
        close_ratio: float,
        reason: str
    ):
        """
        执行部分平仓

        Args:
            position_id: 持仓ID
            position: 持仓信息
            current_price: 当前价格
            close_ratio: 平仓比例（0.5=平50%, 1.0=平剩余全部）
            reason: 平仓原因
        """
        try:
            # 计算平仓数量
            remaining_quantity = float(position['position_size'])

            # 如果是第2批，检查已平仓的数量
            if position_id in self.exit_plans:
                exit_plan = self.exit_plans[position_id]
                if exit_plan['batches'][0]['filled']:
                    # 第1批已平仓，计算剩余数量
                    remaining_quantity = exit_plan['total_quantity'] * 0.5

            close_quantity = remaining_quantity * close_ratio

            logger.info(
                f"🔴 执行部分平仓({close_ratio*100:.0f}%): 持仓{position_id} {position['symbol']} "
                f"{position['direction']} | 数量{close_quantity:.8f} | 价格{current_price} | {reason}"
            )

            # 调用实盘引擎执行部分平仓
            close_result = await self.live_engine.close_position(
                symbol=position['symbol'],
                direction=position['direction'],
                position_size=close_quantity,
                reason=reason
            )

            if close_result['success']:
                # 更新数据库（减少持仓数量）
                await self._update_position_partial_close(
                    position_id,
                    close_quantity,
                    float(current_price),
                    reason
                )

                logger.info(f"✅ 部分平仓成功: 持仓{position_id} 平仓{close_quantity:.8f}")
            else:
                logger.error(f"部分平仓失败: 持仓{position_id} | {close_result.get('error')}")

        except Exception as e:
            logger.error(f"执行部分平仓异常: {e}")

    async def _update_position_partial_close(
        self,
        position_id: int,
        close_quantity: float,
        close_price: float,
        close_reason: str
    ):
        """
        更新持仓记录（部分平仓）

        Args:
            position_id: 持仓ID
            close_quantity: 平仓数量
            close_price: 平仓价格
            close_reason: 平仓原因
        """
        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor(dictionary=True)

            # 获取当前持仓数量
            cursor.execute("""
                SELECT quantity, notes
                FROM futures_positions
                WHERE id = %s
            """, (position_id,))

            result = cursor.fetchone()
            if not result:
                return

            current_quantity = float(result['quantity'])
            current_notes = result['notes'] or ''

            # 计算剩余数量
            remaining_quantity = current_quantity - close_quantity

            # 更新持仓数量和备注
            new_notes = f"{current_notes}\n部分平仓: {close_quantity:.8f} @ {close_price:.6f} - {close_reason}" if current_notes else f"部分平仓: {close_quantity:.8f} @ {close_price:.6f} - {close_reason}"

            if remaining_quantity <= 0.0001:  # 全部平仓
                cursor.execute("""
                    UPDATE futures_positions
                    SET
                        quantity = 0,
                        status = 'closed',
                        close_time = %s,
                        notes = %s,
                        updated_at = NOW()
                    WHERE id = %s
                """, (datetime.now(), new_notes, position_id))
            else:  # 部分平仓
                cursor.execute("""
                    UPDATE futures_positions
                    SET
                        quantity = %s,
                        notional_value = quantity * avg_entry_price,
                        notes = %s,
                        updated_at = NOW()
                    WHERE id = %s
                """, (remaining_quantity, new_notes, position_id))

            conn.commit()

            cursor.close()
            conn.close()

        except Exception as e:
            logger.error(f"更新部分平仓状态失败: {e}")

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

    # ==================== K线强度监控方法 (新增) ====================

    async def _should_check_kline_strength(self, position_id: int) -> bool:
        """
        判断是否需要检查K线强度（每15分钟检查一次）

        Args:
            position_id: 持仓ID

        Returns:
            是否需要检查
        """
        now = datetime.now()

        if position_id not in self.last_kline_check:
            # 首次检查
            self.last_kline_check[position_id] = now
            return True

        last_check = self.last_kline_check[position_id]
        elapsed = (now - last_check).total_seconds()

        if elapsed >= self.kline_check_interval:
            self.last_kline_check[position_id] = now
            return True

        return False

    async def _check_kline_strength_decay(
        self,
        position: Dict,
        current_price: float,
        profit_info: Dict
    ) -> Optional[Tuple[str, float]]:
        """
        检查K线强度是否衰减，决定是否平仓

        Args:
            position: 持仓信息
            current_price: 当前价格
            profit_info: 盈亏信息

        Returns:
            (平仓原因, 平仓比例) 或 None
        """
        try:
            position_id = position['id']
            symbol = position['symbol']
            direction = position['direction']
            entry_time = position.get('entry_signal_time', datetime.now())

            # 获取持仓时长（分钟）
            hold_minutes = (datetime.now() - entry_time).total_seconds() / 60

            # 获取当前部分平仓阶段
            current_stage = self.partial_close_stage.get(position_id, 0)

            # === 检测0: 6小时绝对时间托底（最高优先级） ===
            max_hold_minutes = position.get('max_hold_minutes', 360)  # 默认6小时
            if hold_minutes >= max_hold_minutes:
                # 超过6小时，无论什么情况都必须平仓
                logger.warning(f"⏰ 持仓{position_id} {symbol}已持有{hold_minutes/60:.1f}小时，触发时间托底")
                return ('持仓时长到期(6小时托底)', 1.0)  # 强制全部平仓

            # 获取当前K线强度
            strength_1h = self.signal_analyzer.analyze_kline_strength(symbol, '1h', 24)
            strength_15m = self.signal_analyzer.analyze_kline_strength(symbol, '15m', 24)
            strength_5m = self.signal_analyzer.analyze_kline_strength(symbol, '5m', 24)

            if not all([strength_1h, strength_15m, strength_5m]):
                return None

            # 计算当前K线强度评分
            current_kline = self.kline_scorer.calculate_strength_score(
                strength_1h, strength_15m, strength_5m
            )

            # === 检测1: 15M连续强力反转（最危险，立即全平） ===
            if direction == 'LONG':
                # 检查15M是否连续3根强空K线
                is_strong_reversal = (
                    strength_15m['net_power'] <= -5 and
                    strength_5m['net_power'] <= -5
                )
                if is_strong_reversal:
                    return ('15M连续强力反转', 1.0)  # 全部平仓

            elif direction == 'SHORT':
                # 检查15M是否连续3根强多K线
                is_strong_reversal = (
                    strength_15m['net_power'] >= 5 and
                    strength_5m['net_power'] >= 5
                )
                if is_strong_reversal:
                    return ('15M连续强力反转', 1.0)  # 全部平仓

            # === 检测2: 亏损 + 强度反转（止损，全平） ===
            if profit_info['profit_pct'] < -1.0:
                # 亏损>1%，检查K线方向是否反转
                if current_kline['direction'] != 'NEUTRAL' and current_kline['direction'] != direction:
                    return ('亏损>1%+方向反转', 1.0)  # 止损

            # === 分阶段平仓逻辑（避免重复触发） ===

            # 阶段0 → 阶段1: 首次触发部分平仓50%
            if current_stage == 0:
                # 检测1H K线反转
                if direction == 'LONG' and strength_1h['net_power'] <= -3:
                    return ('1H K线反转', 0.5)  # 首次平仓50%
                elif direction == 'SHORT' and strength_1h['net_power'] >= 3:
                    return ('1H K线反转', 0.5)  # 首次平仓50%

                # 检测盈利+强度大幅减弱
                if profit_info['profit_pct'] >= 2.0 and current_kline['total_score'] < 15:
                    return ('盈利>=2%+强度大幅减弱', 0.5)  # 首次平仓50%

            # 阶段1 → 阶段2: 条件恶化，再平70%（总共平85%）
            elif current_stage == 1:
                # 如果K线反转加剧或盈利提高
                if direction == 'LONG' and strength_1h['net_power'] <= -5:
                    return ('1H K线反转加剧', 0.7)  # 再平70%
                elif direction == 'SHORT' and strength_1h['net_power'] >= 5:
                    return ('1H K线反转加剧', 0.7)  # 再平70%

                # 盈利>=4%且强度减弱
                if profit_info['profit_pct'] >= 4.0 and current_kline['total_score'] < 20:
                    return ('盈利>=4%+强度减弱', 0.7)  # 再平70%

                # 持仓接近4小时且强度不足
                if hold_minutes >= 240 and current_kline['total_score'] < 15:
                    return ('持仓4小时+强度衰减', 0.7)  # 再平70%

            # 阶段2 → 阶段3: 最终清仓
            elif current_stage == 2:
                # 持仓接近5小时，清空剩余15%
                if hold_minutes >= 300:
                    return ('持仓5小时+部分平仓后托底', 1.0)  # 全部平仓

                # K线强度持续减弱
                if current_kline['total_score'] < 10:
                    return ('强度持续减弱', 1.0)  # 全部平仓

            return None

        except Exception as e:
            logger.error(f"检查K线强度衰减失败: {e}")
            return None

    async def _execute_partial_close(
        self,
        position_id: int,
        current_price: float,
        close_ratio: float,
        reason: str
    ):
        """
        执行部分平仓

        Args:
            position_id: 持仓ID
            current_price: 当前价格
            close_ratio: 平仓比例 (0.0-1.0)
            reason: 平仓原因
        """
        try:
            # 获取持仓
            position = await self._get_position(position_id)
            if not position:
                return

            # 计算平仓数量
            total_size = Decimal(str(position['position_size']))
            close_size = total_size * Decimal(str(close_ratio))

            logger.info(
                f"📉 执行部分平仓: 持仓{position_id} {position['symbol']} | "
                f"比例{close_ratio*100:.0f}% | 数量{float(close_size):.4f}/{float(total_size):.4f}"
            )

            # 调用实盘引擎执行平仓
            if self.live_engine:
                await self.live_engine.close_position_partial(
                    position_id=position_id,
                    close_ratio=close_ratio,
                    reason=reason
                )

            # 更新数据库 (减少持仓数量)
            conn = self.db_pool.get_connection()
            cursor = conn.cursor()

            remaining_size = total_size - close_size

            cursor.execute("""
                UPDATE futures_positions
                SET quantity = %s,
                    notes = CONCAT(COALESCE(notes, ''), %s)
                WHERE id = %s
            """, (
                float(remaining_size),
                f"\n[部分平仓{close_ratio*100:.0f}%] {reason} @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                position_id
            ))

            conn.commit()
            cursor.close()
            conn.close()

            # 更新部分平仓阶段
            current_stage = self.partial_close_stage.get(position_id, 0)
            if close_ratio >= 1.0:
                # 全部平仓，设置为阶段3
                self.partial_close_stage[position_id] = 3
            elif close_ratio >= 0.7:
                # 平仓70%，进入阶段2
                self.partial_close_stage[position_id] = 2
            elif close_ratio >= 0.5:
                # 平仓50%，进入阶段1
                self.partial_close_stage[position_id] = 1

            logger.info(
                f"✅ 部分平仓完成: 持仓{position_id} | 剩余数量{float(remaining_size):.4f} | "
                f"阶段{current_stage}→{self.partial_close_stage[position_id]}"
            )

        except Exception as e:
            logger.error(f"执行部分平仓失败: {e}")
