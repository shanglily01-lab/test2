"""
智能平仓优化器
基于实时价格监控的智能平仓策略（独立持仓，全部平仓）
"""
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple
from decimal import Decimal
from loguru import logger
import mysql.connector
from mysql.connector import pooling
import aiohttp

from app.services.price_sampler import PriceSampler
from app.services.signal_analysis_service import SignalAnalysisService
from app.analyzers.kline_strength_scorer import KlineStrengthScorer


class SmartExitOptimizer:
    """智能平仓优化器（基于实时价格监控 + K线强度衰减检测 + 全部平仓）"""

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

        # 智能平仓计划
        self.exit_plans: Dict[int, Dict] = {}  # position_id -> exit_plan

        # === K线强度监控 ===
        self.signal_analyzer = SignalAnalysisService(db_config)
        self.kline_scorer = KlineStrengthScorer()
        self.enable_kline_monitoring = True  # 启用K线强度监控

        # K线强度检查间隔（15分钟）
        self.kline_check_interval = 900  # 秒
        self.last_kline_check: Dict[int, datetime] = {}  # position_id -> last_check_time

        # === 智能监控策略 K线缓冲区 (新增) ===
        self.kline_5m_buffer: Dict[int, List] = {}  # position_id -> 最近N根5M K线
        self.kline_15m_buffer: Dict[int, List] = {}  # position_id -> 最近N根15M K线
        self.last_5m_check: Dict[int, datetime] = {}  # position_id -> 上次检查5M的时间
        self.last_15m_check: Dict[int, datetime] = {}  # position_id -> 上次检查15M的时间

        # 价格采样器（用于150分钟后的最优价格评估）
        self.price_samples: Dict[int, List[float]] = {}  # position_id -> 价格采样列表

        # === HTTP Session 复用（性能优化）===
        self._http_session: Optional[aiohttp.ClientSession] = None

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

            # 清理K线检查时间记录
            if position_id in self.last_kline_check:
                del self.last_kline_check[position_id]

            # 清理K线缓冲区
            if position_id in self.kline_5m_buffer:
                del self.kline_5m_buffer[position_id]
            if position_id in self.kline_15m_buffer:
                del self.kline_15m_buffer[position_id]
            if position_id in self.last_5m_check:
                del self.last_5m_check[position_id]
            if position_id in self.last_15m_check:
                del self.last_15m_check[position_id]

            # 清理价格采样
            if position_id in self.price_samples:
                del self.price_samples[position_id]

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

                # 计算当前盈亏（如果avg_entry_price为空，使用entry_price作为备用）
                try:
                    profit_info = self._calculate_profit(position, current_price)
                except ValueError as ve:
                    # avg_entry_price 或 quantity 为空，可能是持仓刚创建或正在建仓中
                    logger.debug(f"持仓{position_id} 计算盈亏失败（可能正在建仓）: {ve}")
                    await asyncio.sleep(2)
                    continue

                # 更新最高盈利记录
                await self._update_max_profit(position_id, profit_info)

                # === 更新K线缓冲区和价格采样（用于智能监控）===
                await self._update_kline_buffers(position_id, position['symbol'])
                await self._update_price_samples(position_id, float(current_price))

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
                        # 统一全部平仓，不再分批
                        await self._execute_close(position_id, current_price, reason)
                        break

                # 检查智能平仓
                exit_completed = await self._smart_batch_exit(
                    position_id, position, current_price, profit_info
                )

                if exit_completed:
                    logger.info(f"✅ 智能平仓完成: 持仓{position_id}")
                    break

                await asyncio.sleep(1)  # 每秒检查一次（实时监控）

        except asyncio.CancelledError:
            logger.info(f"监控任务被取消: 持仓 {position_id}")
        except Exception as e:
            logger.error(f"监控持仓 {position_id} 异常: {type(e).__name__}: {e}", exc_info=True)

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

        # 第2级: REST API实时价格（异步，复用session）
        try:
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

            session = await self._get_http_session()
            async with session.get(
                api_url,
                params={'symbol': symbol_for_api},
                timeout=aiohttp.ClientTimeout(total=3)
            ) as response:
                if response.status == 200:
                    data = await response.json()
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
        # 验证必要字段
        avg_entry_price_val = position.get('avg_entry_price')
        position_size_val = position.get('position_size')

        # 如果 avg_entry_price 为空，尝试使用 entry_price 作为备用
        if avg_entry_price_val is None or avg_entry_price_val == '':
            entry_price_val = position.get('entry_price')
            if entry_price_val is not None and entry_price_val != '':
                avg_entry_price_val = entry_price_val
                logger.debug(f"持仓 {position.get('id')} avg_entry_price为空，使用entry_price={entry_price_val}作为备用")
            else:
                raise ValueError(f"持仓 {position.get('id')} avg_entry_price 和 entry_price 都为空")

        if position_size_val is None or position_size_val == '' or float(position_size_val) == 0:
            raise ValueError(f"持仓 {position.get('id')} position_size 为空或为0")

        avg_entry_price = Decimal(str(avg_entry_price_val))
        position_size = Decimal(str(position_size_val))
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

        # ========== 智能监控逻辑（开仓30分钟后启动，每秒实时检查）==========
        position_id = position['id']
        position_side = position.get('position_side', position['direction'])
        entry_time = position.get('entry_signal_time') or position.get('open_time') or datetime.now()
        hold_minutes = (datetime.now() - entry_time).total_seconds() / 60

        MIN_HOLD_MINUTES = 30  # 30分钟最小持仓时间

        # === 优先级1: 极端亏损兜底止损（无需等待30分钟）===
        if profit_pct <= -3.0:
            return True, f"极端亏损止损(亏损{profit_pct:.2f}%≥3.0%)"

        # === 开仓30分钟后启动智能监控 ===
        if hold_minutes >= MIN_HOLD_MINUTES:

            # === 优先级2: 智能亏损监控 ===
            # 策略A: 亏损≥2% + 2根5M K线无好转
            if profit_pct <= -2.0:
                no_improvement = await self._check_5m_no_improvement(position_id, position_side)
                if no_improvement:
                    return True, f"亏损2%+5M无好转(亏损{profit_pct:.2f}%)"

            # 策略B: 亏损≥1% + 2根15M K线无持续好转
            elif profit_pct <= -1.0:
                no_sustained = await self._check_15m_no_sustained_improvement(position_id, position_side)
                if no_sustained:
                    return True, f"亏损1%+15M无持续好转(亏损{profit_pct:.2f}%)"

            # === 优先级3: 移动止盈（盈利≥2%时追踪回撤0.5%）===
            TRAILING_STOP_PROFIT_THRESHOLD = 2.0
            TRAILING_STOP_DRAWDOWN_PCT = 0.5

            if profit_pct >= TRAILING_STOP_PROFIT_THRESHOLD:
                max_profit_price = position.get('max_profit_price')

                if max_profit_price and float(max_profit_price) > 0:
                    max_price = float(max_profit_price)
                    curr_price = float(current_price)

                    if position_side == 'LONG':
                        # 做多：从最高价回撤
                        drawdown_pct = ((max_price - curr_price) / max_price) * 100
                    else:  # SHORT
                        # 做空：从最低价反弹
                        drawdown_pct = ((curr_price - max_price) / max_price) * 100

                    # 触发移动止盈
                    if drawdown_pct >= TRAILING_STOP_DRAWDOWN_PCT:
                        return True, f"移动止盈(盈利{profit_pct:.2f}%,回撤{drawdown_pct:.2f}%)"

        # ========== 智能平仓逻辑（计划平仓前30分钟）==========
        planned_close_time = position['planned_close_time']

        # 如果没有设置计划平仓时间（恢复的分批建仓持仓），只检查止损止盈，不执行智能平仓
        if planned_close_time is None:
            return False, ""

        now = datetime.now()
        monitoring_start_time = planned_close_time - timedelta(minutes=30)

        # 如果还未到监控时间，继续其他检查（不再直接返回）
        if now < monitoring_start_time:
            return False, ""

        # ========== 到达监控窗口，使用智能平仓 ==========
        # 注意：这里不再直接返回平仓决策
        # 而是在 _monitor_position 中调用 _smart_batch_exit 处理平仓
        # 这个方法现在主要用于兜底逻辑

        # 兜底逻辑1: 超高盈利立即全部平仓
        if profit_pct >= 5.0:
            return True, f"超高盈利全部平仓(价格变化{profit_pct:.2f}%, ROI {roi_pct:.2f}%)"

        # 兜底逻辑2: 巨额亏损立即全部平仓
        if profit_pct <= -3.0:
            return True, f"巨额亏损全部平仓(价格变化{profit_pct:.2f}%, ROI {roi_pct:.2f}%)"

        # 默认：不平仓（由智能平仓处理）
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

        # 如果没有设置计划平仓时间（恢复的分批建仓持仓），不执行智能平仓
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
            if evaluation['profit_pct'] < -1.0:
                # 亏损超过1%，启用止损优化

                # 条件1: 极佳卖点（评分 >= 95分）- 减少亏损
                if evaluation['score'] >= 95:
                    return True, f"止损优化-极佳卖点(评分{evaluation['score']}, 亏损{evaluation['profit_pct']:.2f}%): {evaluation['reason']}"

                # 条件2: 优秀卖点（评分 >= 85分）- 减少亏损
                if evaluation['score'] >= 85:
                    return True, f"止损优化-优秀卖点(评分{evaluation['score']}, 亏损{evaluation['profit_pct']:.2f}%)"

                # 条件3: 突破基线最高价（亏损时的反弹机会，减少损失）
                if float(current_price) >= baseline['max_price'] * 1.001:
                    return True, f"止损优化-突破基线最高价(亏损{evaluation['profit_pct']:.2f}%)"

                # 条件4: 强下跌趋势预警（亏损时趋势恶化，提前止损）
                if baseline['trend']['direction'] == 'down' and baseline['trend']['strength'] > 0.6:
                    return True, f"止损优化-强下跌趋势预警(亏损{evaluation['profit_pct']:.2f}%)"

            # 条件5: 时间压力（T-10分钟，无论盈亏都必须平仓）
            if elapsed_minutes >= 20 and evaluation['score'] >= 60:
                return True, f"接近截止(已{elapsed_minutes:.0f}分钟)，评分{evaluation['score']}"

        else:  # SHORT
            exit_plan = self.exit_plans[position['id']]
            sampler = exit_plan['sampler']
            evaluation = sampler.is_good_short_exit_price(current_price, entry_price)

            # ===== 智能优化器仅在亏损时介入（止损优化） =====
            # 盈利订单由正常止盈逻辑处理，不需要优化器提前平仓
            if evaluation['profit_pct'] < -1.0:
                # 亏损超过1%，启用止损优化

                # 条件1: 极佳买点（评分 >= 95分）- 减少亏损
                if evaluation['score'] >= 95:
                    return True, f"止损优化-极佳买点(评分{evaluation['score']}, 亏损{evaluation['profit_pct']:.2f}%): {evaluation['reason']}"

                # 条件2: 优秀买点（评分 >= 85分）- 减少亏损
                if evaluation['score'] >= 85:
                    return True, f"止损优化-优秀买点(评分{evaluation['score']}, 亏损{evaluation['profit_pct']:.2f}%)"

                # 条件3: 跌破基线最低价（亏损时的下探机会，减少损失）
                if float(current_price) <= baseline['min_price'] * 0.999:
                    return True, f"止损优化-跌破基线最低价(亏损{evaluation['profit_pct']:.2f}%)"

                # 条件4: 强上涨趋势预警（空单亏损时趋势恶化，提前止损）
                if baseline['trend']['direction'] == 'up' and baseline['trend']['strength'] > 0.6:
                    return True, f"止损优化-强上涨趋势预警(亏损{evaluation['profit_pct']:.2f}%)"

            # 条件5: 时间压力（T-10分钟，无论盈亏都必须平仓）
            if elapsed_minutes >= 20 and evaluation['score'] >= 60:
                return True, f"接近截止(已{elapsed_minutes:.0f}分钟)，评分{evaluation['score']}"

        return False, ""

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
                    notes = CONCAT(IFNULL(notes, ''), '|close_reason:', %s)
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

    async def _update_kline_buffers(self, position_id: int, symbol: str):
        """
        更新K线缓冲区（5M和15M）

        Args:
            position_id: 持仓ID
            symbol: 交易对
        """
        try:
            now = datetime.now()

            # === 更新5M K线缓冲区 ===
            if position_id not in self.kline_5m_buffer:
                # 首次初始化：获取最近3根5M K线
                klines = await self._fetch_latest_kline(symbol, '5m', limit=3)
                if klines:
                    self.kline_5m_buffer[position_id] = klines
                    self.last_5m_check[position_id] = now
                    logger.debug(f"初始化5M K线缓冲区: 持仓{position_id}，获取{len(klines)}根K线")
            elif (now - self.last_5m_check.get(position_id, now)).total_seconds() >= 300:
                # 定期更新：每5分钟检查一次
                klines = await self._fetch_latest_kline(symbol, '5m', limit=1)
                if klines and len(klines) > 0:
                    latest_kline = klines[0]

                    # 检查是否是新K线（避免重复）
                    if len(self.kline_5m_buffer[position_id]) == 0 or \
                       latest_kline['close_time'] > self.kline_5m_buffer[position_id][-1]['close_time']:
                        self.kline_5m_buffer[position_id].append(latest_kline)
                        # 只保留最近3根
                        if len(self.kline_5m_buffer[position_id]) > 3:
                            self.kline_5m_buffer[position_id] = self.kline_5m_buffer[position_id][-3:]
                        logger.debug(f"更新5M K线: 持仓{position_id}，收盘时间{latest_kline['close_time']}")

                    self.last_5m_check[position_id] = now

            # === 更新15M K线缓冲区 ===
            if position_id not in self.kline_15m_buffer:
                # 首次初始化：获取最近3根15M K线
                klines = await self._fetch_latest_kline(symbol, '15m', limit=3)
                if klines:
                    self.kline_15m_buffer[position_id] = klines
                    self.last_15m_check[position_id] = now
                    logger.debug(f"初始化15M K线缓冲区: 持仓{position_id}，获取{len(klines)}根K线")
            elif (now - self.last_15m_check.get(position_id, now)).total_seconds() >= 900:
                # 定期更新：每15分钟检查一次
                klines = await self._fetch_latest_kline(symbol, '15m', limit=1)
                if klines and len(klines) > 0:
                    latest_kline = klines[0]

                    # 检查是否是新K线（避免重复）
                    if len(self.kline_15m_buffer[position_id]) == 0 or \
                       latest_kline['close_time'] > self.kline_15m_buffer[position_id][-1]['close_time']:
                        self.kline_15m_buffer[position_id].append(latest_kline)
                        # 只保留最近3根
                        if len(self.kline_15m_buffer[position_id]) > 3:
                            self.kline_15m_buffer[position_id] = self.kline_15m_buffer[position_id][-3:]
                        logger.debug(f"更新15M K线: 持仓{position_id}，收盘时间{latest_kline['close_time']}")

                    self.last_15m_check[position_id] = now

        except Exception as e:
            logger.error(f"更新K线缓冲区失败: {e}")

    async def _get_http_session(self):
        """获取或创建HTTP session（复用以提升性能）"""
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    async def _fetch_latest_kline(self, symbol: str, interval: str, limit: int = 1):
        """
        获取最新K线数据（异步，复用session）

        Args:
            symbol: 交易对
            interval: 时间间隔（5m/15m）
            limit: 获取K线数量（默认1根，初始化时可获取多根）

        Returns:
            K线字典列表 [{open, high, low, close, close_time, open_time}]
        """
        try:
            symbol_clean = symbol.replace('/', '').upper()

            # 根据交易对类型选择API
            if symbol.endswith('/USD'):
                api_url = 'https://dapi.binance.com/dapi/v1/klines'
                symbol_for_api = symbol_clean + '_PERP'
            else:
                api_url = 'https://fapi.binance.com/fapi/v1/klines'
                symbol_for_api = symbol_clean

            session = await self._get_http_session()
            async with session.get(
                api_url,
                params={'symbol': symbol_for_api, 'interval': interval, 'limit': limit},
                timeout=aiohttp.ClientTimeout(total=3)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if data and len(data) > 0:
                        # 返回K线列表
                        klines = []
                        for kline in data:
                            klines.append({
                                'open': float(kline[1]),
                                'high': float(kline[2]),
                                'low': float(kline[3]),
                                'close': float(kline[4]),
                                'open_time': datetime.fromtimestamp(kline[0] / 1000),
                                'close_time': datetime.fromtimestamp(kline[6] / 1000)
                            })
                        return klines
        except Exception as e:
            logger.warning(f"获取{symbol} {interval} K线失败: {e}")
            return None

    async def _check_5m_no_improvement(self, position_id: int, position_side: str) -> bool:
        """
        检查2根5M K线是否无好转

        Args:
            position_id: 持仓ID
            position_side: 持仓方向（LONG/SHORT）

        Returns:
            是否无好转
        """
        if position_id not in self.kline_5m_buffer:
            return False

        buffer = self.kline_5m_buffer[position_id]
        if len(buffer) < 2:
            return False

        candle_1, candle_2 = buffer[-2:]

        # 判断是否持续恶化或无明显好转
        if position_side == 'LONG':
            # 多仓: 期待价格上涨
            if candle_2['close'] <= candle_1['close']:
                logger.debug(f"持仓{position_id} LONG 5M无好转: {candle_1['close']:.6f} -> {candle_2['close']:.6f}")
                return True  # 继续下跌或横盘
        else:  # SHORT
            # 空仓: 期待价格下跌
            if candle_2['close'] >= candle_1['close']:
                logger.debug(f"持仓{position_id} SHORT 5M无好转: {candle_1['close']:.6f} -> {candle_2['close']:.6f}")
                return True  # 继续上涨或横盘

        return False

    async def _check_15m_no_sustained_improvement(self, position_id: int, position_side: str) -> bool:
        """
        检查2根15M K线是否无持续好转

        Args:
            position_id: 持仓ID
            position_side: 持仓方向（LONG/SHORT）

        Returns:
            是否无持续好转
        """
        if position_id not in self.kline_15m_buffer:
            return False

        buffer = self.kline_15m_buffer[position_id]
        if len(buffer) < 2:
            return False

        candle_1, candle_2 = buffer[-2:]

        # 判断是否持续好转
        if position_side == 'LONG':
            # 第1根好转但第2根反转
            if candle_1['close'] > candle_1['open'] and candle_2['close'] < candle_1['close']:
                logger.debug(
                    f"持仓{position_id} LONG 15M无持续好转: "
                    f"K1 {candle_1['open']:.6f}->{candle_1['close']:.6f}, "
                    f"K2 {candle_2['open']:.6f}->{candle_2['close']:.6f}"
                )
                return True
        else:  # SHORT
            # 第1根好转但第2根反转
            if candle_1['close'] < candle_1['open'] and candle_2['close'] > candle_1['close']:
                logger.debug(
                    f"持仓{position_id} SHORT 15M无持续好转: "
                    f"K1 {candle_1['open']:.6f}->{candle_1['close']:.6f}, "
                    f"K2 {candle_2['open']:.6f}->{candle_2['close']:.6f}"
                )
                return True

        return False

    async def _update_price_samples(self, position_id: int, current_price: float):
        """
        更新价格采样（用于150分钟后的最优价格评估）

        Args:
            position_id: 持仓ID
            current_price: 当前价格
        """
        if position_id not in self.price_samples:
            self.price_samples[position_id] = []

        self.price_samples[position_id].append(current_price)

        # 只保留最近30分钟的数据（每秒1个，保留1800个）
        if len(self.price_samples[position_id]) > 1800:
            self.price_samples[position_id] = self.price_samples[position_id][-1800:]

    async def _find_optimal_exit_price(self, position_id: int, position_side: str, current_price: float, profit_pct: float) -> bool:
        """
        寻找最优平仓价格（150分钟后启动）

        Args:
            position_id: 持仓ID
            position_side: 持仓方向
            current_price: 当前价格
            profit_pct: 当前盈亏百分比

        Returns:
            是否找到最优价格
        """
        if position_id not in self.price_samples or len(self.price_samples[position_id]) < 600:
            # 数据不足（少于10分钟）
            return False

        recent_prices = self.price_samples[position_id][-1800:]  # 最近30分钟

        if profit_pct > 0:
            # 盈利场景: 寻找局部高点
            if position_side == 'LONG':
                # 做多: 当前价格是最近10分钟的最高点
                recent_10min = recent_prices[-600:]
                if current_price >= max(recent_10min):
                    logger.info(f"持仓{position_id} LONG 找到局部高点 ${current_price:.6f}，盈利{profit_pct:.2f}%")
                    return True
            else:  # SHORT
                # 做空: 当前价格是最近10分钟的最低点
                recent_10min = recent_prices[-600:]
                if current_price <= min(recent_10min):
                    logger.info(f"持仓{position_id} SHORT 找到局部低点 ${current_price:.6f}，盈利{profit_pct:.2f}%")
                    return True
        else:
            # 亏损场景: 寻找相对回升点
            if position_side == 'LONG':
                # 做多亏损: 价格反弹（相对回升）
                recent_10min = recent_prices[-600:]
                if current_price >= max(recent_10min[-120:]):  # 最近2分钟的高点
                    logger.info(f"持仓{position_id} LONG 找到相对回升点 ${current_price:.6f}，亏损{profit_pct:.2f}%")
                    return True
            else:  # SHORT
                # 做空亏损: 价格回落（相对回升）
                recent_10min = recent_prices[-600:]
                if current_price <= min(recent_10min[-120:]):  # 最近2分钟的低点
                    logger.info(f"持仓{position_id} SHORT 找到相对回落点 ${current_price:.6f}，亏损{profit_pct:.2f}%")
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

            # 计算当前盈亏比例
            if position_side == 'LONG':
                profit_pct = ((current_price - entry_price) / entry_price) * 100
            else:  # SHORT
                profit_pct = ((entry_price - current_price) / entry_price) * 100

            # 获取1h和4h K线强度
            strength_1h = self.signal_analyzer.analyze_kline_strength(symbol, '1h', 24)
            strength_4h = self.signal_analyzer.analyze_kline_strength(symbol, '4h', 24)

            if not strength_1h or not strength_4h:
                return False, ""

            # 顶部识别（针对LONG持仓）
            if position_side == 'LONG':
                # 条件1: 有盈利（至少2%）
                has_profit = profit_pct >= 2.0

                # 条件2: 1h和4h都转为强烈看空
                strong_bearish_1h = strength_1h.get('net_power', 0) <= -5
                strong_bearish_4h = strength_4h.get('net_power', 0) <= -3

                if has_profit and strong_bearish_1h and strong_bearish_4h:
                    return True, f"顶部识别(盈利{profit_pct:.1f}%+强烈看空)"

            # 底部识别（针对SHORT持仓）
            elif position_side == 'SHORT':
                # 条件1: 有盈利（至少2%）
                has_profit = profit_pct >= 2.0

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
        2. 固定止盈检查（兜底）
        3. 移动止盈（30分钟后启动，盈利≥2%时追踪回撤0.5%）
        4. 智能顶底识别
        5. 动态超时检查
        6. 分阶段超时检查
        7. 3小时绝对时间强制平仓
        8. K线强度衰减检查

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

            # ============================================================
            # === 优先级0: 最小持仓时间限制 (30分钟) ===
            # ============================================================
            # 开仓30分钟内只允许止损和止盈,不允许其他原因平仓
            MIN_HOLD_MINUTES = 30  # 30分钟最小持仓时间

            # ============================================================
            # === 优先级1: 极端亏损兜底止损（风控底线，无需等待最小持仓时间） ===
            # ============================================================
            # 只在极端情况下立即止损，正常亏损由智能监控策略处理

            pnl_pct = profit_info.get('profit_pct', 0)

            # 极端亏损立即止损（兜底保护）
            if pnl_pct <= -3.0:
                # 亏损>=3%，立即止损（防止继续扩大）
                logger.warning(
                    f"🛑 持仓{position_id} {symbol} {position_side} 触发极端亏损止损 | "
                    f"亏损{pnl_pct:.2f}% >= 3.0%，立即止损"
                )
                return ('极端亏损止损(≥3%)', 1.0)

            # ============================================================
            # === 优先级1.5: 智能亏损监控（30分钟后启动）===
            # ============================================================
            # 策略A: 亏损≥2% + 2根5M K线无好转 → 立即平仓
            # 策略B: 亏损≥1% + 2根15M K线无持续好转 → 平仓

            if hold_minutes >= MIN_HOLD_MINUTES:
                pnl_pct = profit_info.get('profit_pct', 0)

                # 策略A: 亏损≥2% + 2根5M无好转
                if pnl_pct <= -2.0:
                    no_improvement = await self._check_5m_no_improvement(position_id, position_side)
                    if no_improvement:
                        logger.warning(
                            f"🚨 持仓{position_id} {symbol} {position_side} 触发智能亏损监控-策略A | "
                            f"亏损{pnl_pct:.2f}% >= 2% + 2根5M K线无好转，立即平仓"
                        )
                        return ('亏损2%+5M无好转', 1.0)

                # 策略B: 亏损≥1% + 2根15M无持续好转
                elif pnl_pct <= -1.0:
                    no_sustained = await self._check_15m_no_sustained_improvement(position_id, position_side)
                    if no_sustained:
                        logger.warning(
                            f"⚠️ 持仓{position_id} {symbol} {position_side} 触发智能亏损监控-策略B | "
                            f"亏损{pnl_pct:.2f}% >= 1% + 2根15M K线无持续好转，平仓"
                        )
                        return ('亏损1%+15M无持续好转', 1.0)

            # ============================================================
            # === 优先级1.6: 提前止损优化 (ROI亏损-10%时重点监控) ===
            # ============================================================
            # 当真实ROI亏损达到-10%时,检查是否有好转迹象,如无好转则提前止损
            # ROI = 价格变化% × 杠杆 (例: -1%价格 × 10倍杠杆 = -10% ROI)
            pnl_pct = profit_info.get('profit_pct', 0)  # 价格变化百分比
            leverage = float(position.get('leverage', 1))
            roi_pct = pnl_pct * leverage  # 真实ROI

            if roi_pct <= -10.0:  # 真实ROI亏损达到-10%
                # 获取5M K线判断短期趋势
                try:
                    strength_5m = self.signal_analyzer.analyze_kline_strength(symbol, '5m', 1)  # 最近1小时(12根5M)

                    # 判断是否有好转迹象
                    has_recovery_signal = False

                    if strength_5m and strength_5m.get('total', 0) >= 6:  # 至少6根K线
                        bull_pct = strength_5m.get('bull_pct', 50)
                        net_power = strength_5m.get('net_power', 0)
                        strong_bull = strength_5m.get('strong_bull', 0)
                        strong_bear = strength_5m.get('strong_bear', 0)

                        if position_side == 'LONG':
                            # 多单需要看涨信号: 阳线>60% 或 强力多头量能>3
                            if bull_pct >= 60 or (strong_bull >= 3 and net_power > 0):
                                has_recovery_signal = True
                                logger.info(
                                    f"⚡ 持仓{position_id} {symbol} LONG 亏损{pnl_pct:.2f}% 有反弹信号"
                                    f"(阳线{bull_pct:.0f}% 强多{strong_bull} 净{net_power:+d}), 继续持有"
                                )

                        elif position_side == 'SHORT':
                            # 空单需要看跌信号: 阴线>60% 或 强力空头量能>3
                            bear_pct = 100 - bull_pct
                            if bear_pct >= 60 or (strong_bear >= 3 and net_power < 0):
                                has_recovery_signal = True
                                logger.info(
                                    f"⚡ 持仓{position_id} {symbol} SHORT 亏损{pnl_pct:.2f}% 有下跌信号"
                                    f"(阴线{bear_pct:.0f}% 强空{strong_bear} 净{net_power:+d}), 继续持有"
                                )

                    # 如果无好转迹象,提前止损
                    if not has_recovery_signal:
                        if roi_pct <= -20.0:
                            # ROI亏损超过-20% (价格-2%),立即提前止损
                            logger.warning(
                                f"🚨 持仓{position_id} {symbol} {position_side} 亏损{pnl_pct:.2f}% 无好转迹象,提前止损"
                            )
                            return ('提前止损优化-无好转迹象', 1.0)
                        elif roi_pct <= -15.0:
                            # ROI亏损-15%到-20% (价格-1.5%到-2%),警告监控
                            logger.warning(
                                f"⚠️  持仓{position_id} {symbol} {position_side} 亏损{pnl_pct:.2f}% 无好转迹象,重点监控"
                            )

                except Exception as e:
                    logger.error(f"提前止损检查失败: {e}")

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
            # === 优先级3: 移动止盈（30分钟后启动，盈利≥2%时追踪）===
            # ============================================================
            TRAILING_STOP_START_MINUTES = 30  # 30分钟后开始监控
            TRAILING_STOP_PROFIT_THRESHOLD = 2.0  # 盈利≥2%时启动移动止盈
            TRAILING_STOP_DRAWDOWN_PCT = 0.5  # 回撤0.5%时平仓

            if hold_minutes >= TRAILING_STOP_START_MINUTES:
                current_profit_pct = profit_info.get('profit_pct', 0)

                # 启动条件：盈利≥2%
                if current_profit_pct >= TRAILING_STOP_PROFIT_THRESHOLD:
                    max_profit_price = position.get('max_profit_price')

                    if max_profit_price and float(max_profit_price) > 0:
                        # 计算回撤百分比（确保所有变量都是float类型）
                        max_price = float(max_profit_price)
                        curr_price = float(current_price)

                        if position_side == 'LONG':
                            # 做多：从最高价回撤
                            drawdown_pct = ((max_price - curr_price) / max_price) * 100
                        else:  # SHORT
                            # 做空：从最低价反弹
                            drawdown_pct = ((curr_price - max_price) / max_price) * 100

                        # 触发移动止盈
                        if drawdown_pct >= TRAILING_STOP_DRAWDOWN_PCT:
                            logger.info(
                                f"📈 [移动止盈] 持仓{position_id} {symbol} {position_side} 盈利{current_profit_pct:.2f}% | "
                                f"价格从最高点回撤{drawdown_pct:.2f}%≥{TRAILING_STOP_DRAWDOWN_PCT}%，触发平仓 | "
                                f"最高价${max_profit_price:.6f} → 当前价${current_price:.6f}"
                            )
                            return (f'移动止盈(回撤{drawdown_pct:.1f}%)', 1.0)

            # ============================================================
            # === 在此之后的所有平仓检查都需要满足最小持仓时间(30分钟) ===
            # ============================================================
            # 开仓30分钟内不平仓(除了止损和止盈)
            if hold_minutes < MIN_HOLD_MINUTES:
                # 30分钟内只允许止损和止盈,不进行其他平仓检查
                return None

            # ============================================================
            # === 优先级4: 智能顶底识别 ===
            # ============================================================
            # 注: 已满足30分钟最小持仓时间,现在可以检查顶底
            is_top_bottom, tb_reason = await self._check_top_bottom(symbol, position_side, entry_price)
            if is_top_bottom:
                logger.info(
                    f"🔝 持仓{position_id} {symbol}触发顶底识别: {tb_reason} | "
                    f"持仓{hold_hours:.1f}小时"
                )
                return (tb_reason, 1.0)

            # ============================================================
            # === 优先级4.5: 最优价格评估（150分钟后启动）===
            # ============================================================
            # 接近3小时持仓时间（150分钟后），启动价格评估系统寻找最优平仓点
            if hold_minutes >= 150:
                pnl_pct = profit_info.get('profit_pct', 0)
                optimal_found = await self._find_optimal_exit_price(
                    position_id, position_side, float(current_price), pnl_pct
                )
                if optimal_found:
                    logger.info(
                        f"💎 持仓{position_id} {symbol} {position_side} 找到最优平仓价格 | "
                        f"持仓{hold_minutes:.0f}分钟 | 盈亏{pnl_pct:+.2f}%"
                    )
                    return ('最优价格评估', 1.0)

            # ============================================================
            # === 优先级5: 动态超时检查（基于timeout_at字段） ===
            # ============================================================
            timeout_at = position.get('timeout_at')
            if timeout_at:
                now_utc = datetime.utcnow()
                if now_utc >= timeout_at:
                    max_hold_minutes = position.get('max_hold_minutes') or 180  # 3小时强制平仓
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
            # === 优先级7: 3小时绝对时间强制平仓 ===
            # ============================================================
            max_hold_minutes = position.get('max_hold_minutes') or 180  # 默认3小时强制平仓
            if hold_minutes >= max_hold_minutes:
                logger.warning(f"⏰ 持仓{position_id} {symbol}已持有{hold_hours:.1f}小时，触发3小时强制平仓")
                return ('持仓时长到期(3小时强制平仓)', 1.0)

            # ============================================================
            # === 优先级8: K线强度衰减检查（智能平仓） ===
            # ============================================================
            # 注意: 15M强力反转和亏损+反转已在优先级1处理(止损风控),这里不再重复检查

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
            # 注意: 这个检查在1小时限制之后,所以不会过早触发
            if profit_info['profit_pct'] < -1.0:
                # 亏损>1%，检查K线方向是否反转
                if current_kline['direction'] != 'NEUTRAL' and current_kline['direction'] != direction:
                    logger.warning(
                        f"⚠️ 持仓{position_id} {symbol}亏损>1%且K线方向反转 | "
                        f"当前方向{current_kline['direction']} vs 持仓{direction}"
                    )
                    return ('亏损>1%+方向反转', 1.0)

            # === 禁用盈利平仓，让利润奔跑 ===
            # 注: 盈利单不再平仓，由固定止盈8%或顶底识别触发全部平仓
            # 只有亏损单才平仓

            # 【已禁用】盈利平仓逻辑
            # 原因: 分批止盈导致平均盈利只有5.46U，应该让利润奔跑
            #
            # if current_stage == 0:
            #     if profit_info['profit_pct'] >= 2.0 and current_kline['total_score'] < 15:
            #         return ('盈利>=2%+强度大幅减弱', 0.5)
            #
            # 新策略: 盈利单不分批，等待固定止盈8%或移动止盈

            return None

        except Exception as e:
            logger.error(f"检查K线强度衰减失败: {e}")
            return None

