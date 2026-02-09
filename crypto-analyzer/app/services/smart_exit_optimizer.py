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

    def __init__(self, db_config: dict, live_engine, price_service, account_id=None):
        """
        初始化平仓优化器

        Args:
            db_config: 数据库配置
            live_engine: 交易引擎（用于执行平仓）
            price_service: 价格服务（WebSocket实时价格）
            account_id: 账户ID（可选，如果不提供则从live_engine获取或默认为2）
        """
        self.db_config = db_config
        self.live_engine = live_engine
        self.price_service = price_service
        # 优先使用传入的account_id，其次从live_engine获取，最后默认为2
        if account_id is not None:
            self.account_id = account_id
        else:
            self.account_id = getattr(live_engine, 'account_id', 2)

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

        # 🔥🔥🔥 重构: 移动止盈配置（优化：让利润奔跑）
        self.trailing_stop_enabled = True  # 启用移动止盈
        self.trailing_threshold_pct = 0.01  # 1%开启移动止盈
        self.trailing_step_pct = 0.015  # 优化: 0.5% → 1.5%，让利润有更多奔跑空间
        self.max_profit_tracker: Dict[int, float] = {}  # position_id -> max_profit_pct
        logger.info("🚀 移动止盈已启用: 门槛1%, 回撤阈值1.5%")

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

                # 如果无法获取价格，跳过本次检查
                if current_price is None:
                    logger.warning(f"持仓{position_id} {position['symbol']} 无法获取价格，跳过本次平仓检查")
                    await asyncio.sleep(2)  # 等待2秒后重试
                    continue

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
                    entry_signal_time, open_time, planned_close_time,
                    close_extended, extended_close_time,
                    max_profit_pct, max_profit_price, max_profit_time,
                    stop_loss_price, take_profit_price, leverage,
                    margin, entry_price, max_hold_minutes, timeout_at, created_at
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

            # 根据交易对类型选择API
            if symbol.endswith('/USD'):
                # 币本位合约使用dapi
                api_url = 'https://dapi.binance.com/dapi/v1/ticker/price'
                symbol_for_api = symbol_clean + '_PERP'
            else:
                # U本位合约使用fapi
                api_url = 'https://fapi.binance.com/fapi/v1/ticker/price'
                symbol_for_api = symbol_clean

            response = requests.get(
                api_url,
                params={'symbol': symbol_for_api},
                timeout=3
            )

            if response.status_code == 200:
                data = response.json()
                # 币本位API返回数组，U本位返回对象
                if isinstance(data, list) and len(data) > 0:
                    rest_price = float(data[0]['price'])
                else:
                    rest_price = float(data['price'])

                if rest_price > 0:
                    logger.info(f"{symbol} 降级到REST API价格: {rest_price}")
                    return Decimal(str(rest_price))
        except Exception as e:
            logger.warning(f"{symbol} REST API获取失败: {e}")

        # 第3级: 使用持仓的最后已知价格（entry_price或mark_price）作为最后保底
        # 绝对不能返回0，否则会误触发止盈止损
        logger.error(f"{symbol} WebSocket和REST API都失败，这不应该发生！请检查网络连接")
        return None  # 返回None表示无法获取价格，让调用方决定如何处理

    def _calculate_profit(self, position: Dict, current_price: Decimal) -> Dict:
        """
        计算当前盈亏信息

        Args:
            position: 持仓信息
            current_price: 当前价格

        Returns:
            {'profit_pct': float, 'profit_usdt': float, 'current_price': float}
        """
        # avg_entry_price可能为None，使用entry_price作为fallback
        entry_price_value = position['avg_entry_price'] or position['entry_price']
        if not entry_price_value:
            logger.error(f"持仓{position['id']}无有效的entry_price")
            return {'profit_pct': 0, 'profit_usdt': 0, 'current_price': float(current_price)}

        avg_entry_price = Decimal(str(entry_price_value))
        position_size = Decimal(str(position['position_size']))
        direction = position['direction']

        # 计算盈亏百分比（返回小数形式，如0.01表示1%）
        if direction == 'LONG':
            profit_pct = float((current_price - avg_entry_price) / avg_entry_price)
        else:  # SHORT
            profit_pct = float((avg_entry_price - current_price) / avg_entry_price)

        # 计算盈亏金额（USDT）
        profit_usdt = float(position_size * avg_entry_price * Decimal(str(profit_pct)))

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

        # 🔥🔥🔥 重构: 移动止盈逻辑 (优先级最高)
        if self.trailing_stop_enabled and profit_pct > 0:
            position_id = position['id']

            # 记录最高盈利 (BUG修复: 从数据库读取历史最高值)
            if position_id not in self.max_profit_tracker:
                # 从数据库读取之前记录的max_profit_pct
                db_max_pct = position.get('max_profit_pct', 0)
                if db_max_pct:
                    db_max_pct = float(db_max_pct)
                else:
                    db_max_pct = 0.0
                # 使用数据库记录和当前盈利中的较大值
                self.max_profit_tracker[position_id] = max(db_max_pct, profit_pct)
            elif profit_pct > self.max_profit_tracker[position_id]:
                self.max_profit_tracker[position_id] = profit_pct

            tracked_max = self.max_profit_tracker[position_id]

            # 如果盈利超过门槛 (1%)
            if tracked_max >= self.trailing_threshold_pct:
                # 🔥 动态回撤阈值：盈利越大，给予更多空间
                # 小盈利(1-3%): 1.5%回撤止盈
                # 中盈利(3-5%): 2%回撤止盈
                # 大盈利(>5%): 2.5%回撤止盈
                dynamic_step = self.trailing_step_pct
                if tracked_max >= 0.05:  # >5%
                    dynamic_step = 0.025  # 2.5%回撤
                elif tracked_max >= 0.03:  # 3-5%
                    dynamic_step = 0.02   # 2%回撤
                # else: 使用默认1.5%

                # 计算回撤幅度
                trailing_drawback = tracked_max - profit_pct

                if trailing_drawback >= dynamic_step:
                    protected_profit = profit_pct
                    return True, f"移动止盈(最高{tracked_max*100:.2f}% → 当前{profit_pct*100:.2f}%, 保护{protected_profit*100:.2f}%利润)"

        # 🔥🔥🔥 重构: 快速止损逻辑 (优化: 取消缓冲期，立即保护)
        if profit_pct < 0:
            # 计算持仓时长
            open_time = position.get('open_time') or position.get('created_at')
            if open_time:
                holding_minutes = (datetime.now() - open_time).total_seconds() / 60

                # 🔥 优化: 取消10分钟缓冲期，立即启用止损保护
                # 0-15分钟内亏损超过1% → 立即止损
                if holding_minutes <= 15 and profit_pct <= -0.01:
                    return True, f"快速止损-15分钟(亏损{profit_pct*100:.2f}%, 持仓{holding_minutes:.0f}分钟)"

                # 15-30分钟内亏损超过1.5% → 立即止损
                if holding_minutes <= 30 and profit_pct <= -0.015:
                    return True, f"快速止损-30分钟(亏损{profit_pct*100:.2f}%, 持仓{holding_minutes:.0f}分钟)"

                # 30-60分钟内亏损超过2% → 立即止损
                if holding_minutes <= 60 and profit_pct <= -0.02:
                    return True, f"快速止损-60分钟(亏损{profit_pct*100:.2f}%, 持仓{holding_minutes:.0f}分钟)"

                # 60分钟以上，由固定止损(1.5%)兜底

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

        # 如果没有设置计划平仓时间（恢复的分批建仓持仓），只检查止损止盈，不执行智能分批平仓
        if planned_close_time is None:
            return False, ""

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
        if profit_pct >= 0.05:  # 5%
            return True, f"超高盈利全部平仓(价格变化{profit_pct*100:.2f}%, ROI {roi_pct:.2f}%)"

        # 兜底逻辑2: 巨额亏损立即全部平仓
        if profit_pct <= -0.03:  # -3%
            return True, f"巨额亏损全部平仓(价格变化{profit_pct*100:.2f}%, ROI {roi_pct:.2f}%)"

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

        优化后策略：
        1. T-30启动监控，T-20完成价格基线（10分钟采样）
        2. T-20到T+0寻找最佳价格，一次性平仓100%
        3. T+0（planned_close_time）必须强制执行

        时间窗口示例（planned_close_time = 11:46）:
        - 11:16 (T-30): 启动监控
        - 11:26 (T-20): 完成10分钟价格基线
        - 11:26-11:46: 20分钟寻找最佳平仓价格
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

        # 如果没有设置计划平仓时间（恢复的分批建仓持仓），不执行智能分批平仓
        if planned_close_time is None:
            return False

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

            # 启动价格基线采样器 (优化后: 10分钟采样窗口)
            sampler = PriceSampler(position['symbol'], self.price_service, window_seconds=600)
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

            # 优化后: 等待10分钟建立基线
            logger.info(f"📊 {position['symbol']} 等待10分钟建立平仓价格基线...")

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

            # ===== 智能优化器仅在亏损时介入（止损优化） =====
            # 盈利订单由正常止盈逻辑处理，不需要优化器提前平仓
            if evaluation['profit_pct'] < -0.01:  # -1%
                # 亏损超过1%，启用止损优化

                # 条件1: 极佳卖点（评分 >= 95分）- 减少亏损
                if evaluation['score'] >= 95:
                    return True, f"止损优化-极佳卖点(评分{evaluation['score']}, 亏损{evaluation['profit_pct']*100:.2f}%): {evaluation['reason']}"

                # 条件2: 优秀卖点（评分 >= 85分）- 减少亏损
                if evaluation['score'] >= 85:
                    return True, f"止损优化-优秀卖点(评分{evaluation['score']}, 亏损{evaluation['profit_pct']*100:.2f}%)"

                # 条件3: 突破基线最高价（亏损时的反弹机会，减少损失）
                if float(current_price) >= baseline['max_price'] * 1.001:
                    return True, f"止损优化-突破基线最高价(亏损{evaluation['profit_pct']*100:.2f}%)"

                # 条件4: 强下跌趋势预警（亏损时趋势恶化，提前止损）
                if baseline['trend']['direction'] == 'down' and baseline['trend']['strength'] > 0.6:
                    return True, f"止损优化-强下跌趋势预警(亏损{evaluation['profit_pct']*100:.2f}%)"

            # 条件5: 时间压力（T-10分钟，无论盈亏都必须平仓）
            if elapsed_minutes >= 20 and evaluation['score'] >= 60:
                return True, f"接近截止(已{elapsed_minutes:.0f}分钟)，评分{evaluation['score']}"

        else:  # SHORT
            exit_plan = self.exit_plans[position['id']]
            sampler = exit_plan['sampler']
            evaluation = sampler.is_good_short_exit_price(current_price, entry_price)

            # ===== 智能优化器仅在亏损时介入（止损优化） =====
            # 盈利订单由正常止盈逻辑处理，不需要优化器提前平仓
            if evaluation['profit_pct'] < -0.01:  # -1%
                # 亏损超过1%，启用止损优化

                # 条件1: 极佳买点（评分 >= 95分）- 减少亏损
                if evaluation['score'] >= 95:
                    return True, f"止损优化-极佳买点(评分{evaluation['score']}, 亏损{evaluation['profit_pct']*100:.2f}%): {evaluation['reason']}"

                # 条件2: 优秀买点（评分 >= 85分）- 减少亏损
                if evaluation['score'] >= 85:
                    return True, f"止损优化-优秀买点(评分{evaluation['score']}, 亏损{evaluation['profit_pct']*100:.2f}%)"

                # 条件3: 跌破基线最低价（亏损时的下探机会，减少损失）
                if float(current_price) <= baseline['min_price'] * 0.999:
                    return True, f"止损优化-跌破基线最低价(亏损{evaluation['profit_pct']*100:.2f}%)"

                # 条件4: 强上涨趋势预警（空单亏损时趋势恶化，提前止损）
                if baseline['trend']['direction'] == 'up' and baseline['trend']['strength'] > 0.6:
                    return True, f"止损优化-强上涨趋势预警(亏损{evaluation['profit_pct']*100:.2f}%)"

            # 条件5: 时间压力（T-10分钟，无论盈亏都必须平仓）
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

    async def _check_top_bottom(self, symbol: str, position_side: str, entry_price: float) -> tuple:
        """
        检查是否触发顶底识别

        Args:
            symbol: 交易对
            position_side: 持仓方向（LONG/SHORT）
            entry_price: 开仓价格

        Returns:
            (is_top_bottom: bool, reason: str)
        """
        try:
            # 从live_engine获取当前价格
            current_price = self.live_engine.get_current_price(symbol)
            if not current_price:
                return False, ""

            # 计算当前盈亏比例（小数形式，如0.02表示2%）
            if position_side == 'LONG':
                profit_pct = (current_price - entry_price) / entry_price
            else:  # SHORT
                profit_pct = (entry_price - current_price) / entry_price

            # 获取1h和4h K线强度
            strength_1h = self.signal_analyzer.analyze_kline_strength(symbol, '1h', 24)
            strength_4h = self.signal_analyzer.analyze_kline_strength(symbol, '4h', 24)

            if not strength_1h or not strength_4h:
                return False, ""

            # 顶部识别（针对LONG持仓）
            if position_side == 'LONG':
                # 条件1: 有盈利（至少2%）
                has_profit = profit_pct >= 0.02

                # 条件2: 1h和4h都转为强烈看空
                strong_bearish_1h = strength_1h.get('net_power', 0) <= -5
                strong_bearish_4h = strength_4h.get('net_power', 0) <= -3

                if has_profit and strong_bearish_1h and strong_bearish_4h:
                    return True, f"顶部识别(盈利{profit_pct*100:.1f}%+强烈看空)"

            # 底部识别（针对SHORT持仓）
            elif position_side == 'SHORT':
                # 条件1: 有盈利（至少2%）
                has_profit = profit_pct >= 0.02

                # 条件2: 1h和4h都转为强烈看多
                strong_bullish_1h = strength_1h.get('net_power', 0) >= 5
                strong_bullish_4h = strength_4h.get('net_power', 0) >= 3

                if has_profit and strong_bullish_1h and strong_bullish_4h:
                    return True, f"底部识别(盈利{profit_pct:.1f}%+强烈看多)"

            return False, ""

        except Exception as e:
            logger.error(f"检查顶底识别失败: {e}")
            return False, ""

    async def _check_kline_strength_decay(
        self,
        position: Dict,
        current_price: float,
        profit_info: Dict
    ) -> Optional[Tuple[str, float]]:
        """
        统一平仓检查（止盈止损 + 超时 + K线强度衰减）

        优先级（从高到低）：
        1. 固定止损检查（风控底线）
        2. 智能顶底识别（替代固定止盈）
        3. 固定止盈检查（兜底）
        4. 动态超时检查
        5. 分阶段超时检查
        6. 6小时绝对时间托底
        7. K线强度衰减检查

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
            position_side = position.get('position_side', direction)  # LONG/SHORT
            entry_price = float(position.get('entry_price', 0))
            entry_time = position.get('entry_signal_time') or position.get('open_time') or datetime.now()
            quantity = float(position.get('quantity', 0))
            margin = float(position.get('margin', 0))
            leverage = float(position.get('leverage', 1))

            # 获取持仓时长（分钟）
            hold_minutes = (datetime.now() - entry_time).total_seconds() / 60
            hold_hours = hold_minutes / 60

            # 获取当前部分平仓阶段
            current_stage = self.partial_close_stage.get(position_id, 0)

            # ============================================================
            # === 优先级0: 最小持仓时间限制 (30分钟) ===
            # ============================================================
            # 🔥 紧急修复: 从2小时缩短到30分钟,避免反转行情巨亏
            MIN_HOLD_MINUTES = 30  # 30分钟最小持仓时间

            # ============================================================
            # === 优先级1: 固定止损检查（风控底线，无需等待最小持仓时间） ===
            # ============================================================
            stop_loss_price = position.get('stop_loss_price')

            # 止损立即生效，无需等待最小持仓时间
            if stop_loss_price and float(stop_loss_price) > 0:
                if position_side == 'LONG':
                    if current_price <= float(stop_loss_price):
                        pnl_pct = profit_info.get('profit_pct', 0)
                        logger.warning(
                            f"🛑 持仓{position_id} {symbol} LONG触发固定止损 | "
                            f"当前价${current_price:.6f} <= 止损价${stop_loss_price:.6f} | "
                            f"盈亏{pnl_pct:+.2f}%"
                        )
                        return ('固定止损', 1.0)
                elif position_side == 'SHORT':
                    if current_price >= float(stop_loss_price):
                        pnl_pct = profit_info.get('profit_pct', 0)
                        logger.warning(
                            f"🛑 持仓{position_id} {symbol} SHORT触发固定止损 | "
                            f"当前价${current_price:.6f} >= 止损价${stop_loss_price:.6f} | "
                            f"盈亏{pnl_pct:+.2f}%"
                        )
                        return ('固定止损', 1.0)

            # ============================================================
            # === 优先级2: 固定止盈检查（兜底） ===
            # ============================================================
            take_profit_price = position.get('take_profit_price')
            if take_profit_price and float(take_profit_price) > 0:
                if position_side == 'LONG':
                    if current_price >= float(take_profit_price):
                        pnl_pct = profit_info.get('profit_pct', 0)
                        logger.info(
                            f"✅ 持仓{position_id} {symbol} LONG触发固定止盈 | "
                            f"当前价${current_price:.6f} >= 止盈价${take_profit_price:.6f} | "
                            f"盈亏{pnl_pct:+.2f}%"
                        )
                        return ('固定止盈', 1.0)
                elif position_side == 'SHORT':
                    if current_price <= float(take_profit_price):
                        pnl_pct = profit_info.get('profit_pct', 0)
                        logger.info(
                            f"✅ 持仓{position_id} {symbol} SHORT触发固定止盈 | "
                            f"当前价${current_price:.6f} <= 止盈价${take_profit_price:.6f} | "
                            f"盈亏{pnl_pct:+.2f}%"
                        )
                        return ('固定止盈', 1.0)

            # ============================================================
            # === 优先级3: 紧急反转检测 (30分钟后生效) ===
            # ============================================================
            # 🔥 紧急修复: 在30分钟后,如果亏损>1.5%且K线强烈反转,立即止损
            if hold_minutes >= 30:
                if profit_info['profit_pct'] < -0.015:  # -1.5%
                    try:
                        strength_15m = self.signal_analyzer.analyze_kline_strength(symbol, '15m', 24)
                        strength_5m = self.signal_analyzer.analyze_kline_strength(symbol, '5m', 24)

                        if strength_15m and strength_5m:
                            net_power_15m = strength_15m.get('net_power', 0)
                            net_power_5m = strength_5m.get('net_power', 0)

                            # LONG持仓检查是否强烈反转为看空
                            if position_side == 'LONG':
                                if net_power_15m <= -6 and net_power_5m <= -6:
                                    logger.warning(
                                        f"🚨 紧急反转保护: 持仓{position_id} {symbol} LONG | "
                                        f"亏损{profit_info['profit_pct']*100:.1f}% | "
                                        f"15m净能量{net_power_15m}, 5m净能量{net_power_5m} (强烈看空) | "
                                        f"持仓{hold_minutes:.0f}分钟"
                                    )
                                    return ('紧急反转止损(亏损+强烈反转)', 1.0)

                            # SHORT持仓检查是否强烈反转为看多
                            elif position_side == 'SHORT':
                                if net_power_15m >= 6 and net_power_5m >= 6:
                                    logger.warning(
                                        f"🚨 紧急反转保护: 持仓{position_id} {symbol} SHORT | "
                                        f"亏损{profit_info['profit_pct']*100:.1f}% | "
                                        f"15m净能量{net_power_15m}, 5m净能量{net_power_5m} (强烈看多) | "
                                        f"持仓{hold_minutes:.0f}分钟"
                                    )
                                    return ('紧急反转止损(亏损+强烈反转)', 1.0)
                    except Exception as e:
                        logger.debug(f"紧急反转检查失败: {e}")

            # ============================================================
            # === 优先级4: 智能顶底识别 (立即生效,无需等待) ===
            # ============================================================
            # 🔥 紧急修复: 移除最小持仓时间限制,趋势策略30分钟后就能检查顶底
            is_top_bottom, tb_reason = await self._check_top_bottom(symbol, position_side, entry_price)
            if is_top_bottom:
                logger.info(
                    f"🔝 持仓{position_id} {symbol}触发顶底识别: {tb_reason} | "
                    f"持仓{hold_hours:.1f}小时"
                )
                return (tb_reason, 1.0)

            # ============================================================
            # === 优先级5: 动态超时检查（基于timeout_at字段） ===
            # ============================================================
            timeout_at = position.get('timeout_at')
            if timeout_at:
                now_utc = datetime.utcnow()
                if now_utc >= timeout_at:
                    max_hold_minutes = position.get('max_hold_minutes') or 240  # 4小时强制平仓
                    logger.warning(
                        f"⏰ 持仓{position_id} {symbol}触发动态超时 | "
                        f"超时阈值{max_hold_minutes}分钟"
                    )
                    return (f'动态超时({max_hold_minutes}min)', 1.0)

            # ============================================================
            # === 优先级6: 分阶段超时检查（1h/2h/3h/4h不同亏损阈值） ===
            # ============================================================
            # 获取分阶段超时阈值配置
            # 针对上涨趋势优化: 放宽阈值,给持仓更多时间
            staged_thresholds = {
                1: -0.025,  # 1小时: -2.5% (放宽0.5%)
                2: -0.02,   # 2小时: -2.0% (放宽0.5%)
                3: -0.015,  # 3小时: -1.5% (放宽0.5%)
                4: -0.01    # 4小时: -1.0% (放宽0.5%)
            }

            # 尝试从配置中获取
            if hasattr(self.live_engine, 'opt_config'):
                config_thresholds = self.live_engine.opt_config.get_staged_timeout_thresholds()
                if config_thresholds:
                    staged_thresholds = config_thresholds

            pnl_pct = profit_info.get('profit_pct', 0) / 100.0  # 转换为小数

            for hour_checkpoint, loss_threshold in sorted(staged_thresholds.items()):
                if hold_hours >= hour_checkpoint:
                    if pnl_pct < loss_threshold:
                        logger.warning(
                            f"⏱️ 持仓{position_id} {symbol}触发分阶段超时 | "
                            f"持仓{hold_hours:.1f}h >= {hour_checkpoint}h | "
                            f"亏损{pnl_pct*100:.2f}% < {loss_threshold*100:.2f}%"
                        )
                        return (f'分阶段超时{hour_checkpoint}H(亏损{pnl_pct*100:.1f}%)', 1.0)

            # ============================================================
            # === 优先级7: 4小时绝对时间强制平仓 ===
            # ============================================================
            max_hold_minutes = position.get('max_hold_minutes') or 240  # 默认4小时强制平仓
            if hold_minutes >= max_hold_minutes:
                logger.warning(f"⏰ 持仓{position_id} {symbol}已持有{hold_hours:.1f}小时，触发4小时强制平仓")
                return ('持仓时长到期(4小时强制平仓)', 1.0)

            # ============================================================
            # === 优先级8: K线强度衰减检查（智能分批平仓） ===
            # ============================================================
            # 注意: 15M强力反转和亏损+反转已在优先级3处理(紧急风控),这里不再重复检查

            # 获取K线强度
            strength_1h = self.signal_analyzer.analyze_kline_strength(symbol, '1h', 24)
            strength_15m = self.signal_analyzer.analyze_kline_strength(symbol, '15m', 24)
            strength_5m = self.signal_analyzer.analyze_kline_strength(symbol, '5m', 24)

            if not all([strength_1h, strength_15m, strength_5m]):
                return None

            # 计算当前K线强度评分
            current_kline = self.kline_scorer.calculate_strength_score(
                strength_1h, strength_15m, strength_5m
            )

            # === 亏损 + 强度反转（止损，全平） ===
            # 注意: 这个检查在2小时限制之后,所以不会过早触发
            if profit_info['profit_pct'] < -0.01:  # -1%
                # 亏损>1%，检查K线方向是否反转
                if current_kline['direction'] != 'NEUTRAL' and current_kline['direction'] != direction:
                    logger.warning(
                        f"⚠️ 持仓{position_id} {symbol}亏损>1%且K线方向反转 | "
                        f"当前方向{current_kline['direction']} vs 持仓{direction}"
                    )
                    return ('亏损>1%+方向反转', 1.0)

            # === 分阶段平仓逻辑（避免重复触发） ===
            # 注: 删除1H K线反转检查,避免打脸开仓信号
            # 只保留盈利+强度减弱的止盈逻辑

            # 阶段0 → 阶段1: 首次触发部分平仓50%
            if current_stage == 0:
                # 检测盈利+强度大幅减弱(止盈)
                if profit_info['profit_pct'] >= 0.02 and current_kline['total_score'] < 15:  # 2%
                    return ('盈利>=2%+强度大幅减弱', 0.5)  # 首次平仓50%

            # 阶段1 → 阶段2: 条件恶化，再平70%（总共平85%）
            elif current_stage == 1:
                # 盈利>=4%且强度减弱(止盈加码)
                if profit_info['profit_pct'] >= 0.04 and current_kline['total_score'] < 20:  # 4%
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
