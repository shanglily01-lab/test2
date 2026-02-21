"""
K线回调分批建仓执行器 V2
基于K线形态回调确认实现最优入场时机

核心策略：
- 做多：等待1根反向阴线作为回调确认
- 做空：等待1根反向阳线作为反弹确认
- 两级降级：15M（0-30分钟）→ 5M（30-60分钟）
- 纪律严明：宁愿错过，不追涨杀跌
"""
import asyncio
import json
import pymysql
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from decimal import Decimal
from loguru import logger
import pymysql

from app.services.batch_position_manager import BatchPositionManager
from app.services.optimization_config import OptimizationConfig


class KlinePullbackEntryExecutor:
    """K线回调分批建仓执行器"""

    def __init__(self, db_config: dict, live_engine, price_service, account_id=None, brain=None, opt_config=None):
        """
        初始化执行器

        Args:
            db_config: 数据库配置
            live_engine: 交易引擎
            price_service: 价格服务（WebSocket）
            account_id: 账户ID
            brain: 智能大脑（用于获取自适应参数）
            opt_config: 优化配置（用于获取波动率配置）
        """
        self.db_config = db_config
        self.live_engine = live_engine
        self.price_service = price_service
        if account_id is not None:
            self.account_id = account_id
        else:
            self.account_id = getattr(live_engine, 'account_id', 2)

        # 获取brain和opt_config（用于止盈止损计算）
        self.brain = brain if brain else getattr(live_engine, 'brain', None)
        self.opt_config = opt_config if opt_config else getattr(live_engine, 'opt_config', None)

        # 如果still没有opt_config，创建新实例
        if not self.opt_config:
            self.opt_config = OptimizationConfig(db_config)

        # 🔥 初始化持仓管理器（封装公共逻辑）
        self.position_manager = BatchPositionManager(db_config, self.account_id)

        # 分批配置
        self.batch_ratio = [0.3, 0.3, 0.4]  # 30%/30%/40% (已废弃，保留用于兼容)
        self.total_window_minutes = 60  # 总时间窗口60分钟
        self.primary_window_minutes = 30  # 第一阶段30分钟（15M）
        self.check_interval_seconds = 60  # 每60秒检查一次（K线更新频率）

    def _get_margin_per_batch(self, symbol: str) -> float:
        """
        根据交易对评级等级获取每批保证金金额

        Args:
            symbol: 交易对符号

        Returns:
            每批保证金金额(USDT)，如果是黑名单3级则返回0
        """
        rating_level = self.opt_config.get_symbol_rating_level(symbol)

        # 根据评级等级设置每批保证金
        if rating_level == 0:
            # 白名单/默认：200U每批
            return 200.0
        elif rating_level == 1:
            # 黑名单1级：50U每批
            return 50.0
        elif rating_level == 2:
            # 黑名单2级：30U每批
            return 30.0
        else:
            # 黑名单3级：不交易
            return 0.0

    def _calculate_stop_take_prices(self, symbol: str, direction: str, current_price: float, signal_components: dict) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        """
        计算止盈止损价格和百分比

        Args:
            symbol: 交易对
            direction: 方向 LONG/SHORT
            current_price: 当前价格
            signal_components: 信号组成

        Returns:
            (stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct)
        """
        try:
            # 如果没有brain或opt_config，返回None（使用SmartExitOptimizer默认逻辑）
            if not self.brain or not self.opt_config:
                logger.warning(f"⚠️ {symbol} brain或opt_config未初始化，止盈止损将由平仓优化器管理")
                return None, None, None, None

            # 获取自适应参数
            if direction == 'LONG':
                adaptive_params = self.brain.adaptive_long
            else:  # SHORT
                adaptive_params = self.brain.adaptive_short

            # 使用自适应参数计算止损
            base_stop_loss_pct = adaptive_params.get('stop_loss_pct', 0.03)

            # 波动率自适应止损（复用live_engine的方法）
            if hasattr(self.live_engine, 'calculate_volatility_adjusted_stop_loss'):
                stop_loss_pct = self.live_engine.calculate_volatility_adjusted_stop_loss(signal_components, base_stop_loss_pct)
            else:
                stop_loss_pct = base_stop_loss_pct

            # 使用波动率配置计算动态止盈
            volatility_profile = self.opt_config.get_symbol_volatility_profile(symbol)
            if volatility_profile:
                # 根据方向使用对应的止盈配置
                if direction == 'LONG' and volatility_profile.get('long_fixed_tp_pct'):
                    take_profit_pct = float(volatility_profile['long_fixed_tp_pct'])
                    logger.debug(f"[TP_DYNAMIC] {symbol} LONG 使用15M阳线动态止盈: {take_profit_pct*100:.3f}%")
                elif direction == 'SHORT' and volatility_profile.get('short_fixed_tp_pct'):
                    take_profit_pct = float(volatility_profile['short_fixed_tp_pct'])
                    logger.debug(f"[TP_DYNAMIC] {symbol} SHORT 使用15M阴线动态止盈: {take_profit_pct*100:.3f}%")
                else:
                    # 回退到自适应参数
                    take_profit_pct = adaptive_params.get('take_profit_pct', 0.02)
                    logger.debug(f"[TP_FALLBACK] {symbol} {direction} 波动率配置不全,使用自适应参数: {take_profit_pct*100:.2f}%")
            else:
                # 回退到自适应参数
                take_profit_pct = adaptive_params.get('take_profit_pct', 0.02)
                logger.debug(f"[TP_FALLBACK] {symbol} 无波动率配置,使用自适应参数: {take_profit_pct*100:.2f}%")

            # 计算止盈止损价格 (确保current_price是float类型)
            price = float(current_price)
            if direction == 'LONG':
                stop_loss_price = price * (1 - stop_loss_pct)
                take_profit_price = price * (1 + take_profit_pct)
            else:  # SHORT
                stop_loss_price = price * (1 + stop_loss_pct)
                take_profit_price = price * (1 - take_profit_pct)

            logger.info(f"[V2_SL_TP] {symbol} {direction} | 价格:${current_price:.4f} | 止损:${stop_loss_price:.4f}({stop_loss_pct*100:.2f}%) | 止盈:${take_profit_price:.4f}({take_profit_pct*100:.2f}%)")

            return stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct

        except Exception as e:
            logger.error(f"❌ {symbol} 计算止盈止损失败: {e}")
            return None, None, None, None

    async def execute_entry(self, signal: Dict) -> Dict:
        """
        执行K线回调分批建仓

        流程：
        1. 阶段1（0-30分钟）：监控15M K线，等待1根反向K线
        2. 阶段2（30-60分钟）：切换到5M K线，等待1根反向K线
        3. 60分钟截止，能完成几批算几批

        Args:
            signal: 开仓信号 {
                'symbol': str,
                'direction': 'LONG'/'SHORT',
                'amount': float,
                'total_margin': float,
                'leverage': int
            }

        Returns:
            建仓结果 {'success': bool, 'plan': dict, 'avg_price': float}
        """
        symbol = signal['symbol']
        direction = signal['direction']

        # 🔥 关键修复：使用真实的信号触发时间，而不是重启时间
        # 如果signal中有signal_time，使用它；否则使用当前时间（新信号）
        signal_time = signal.get('signal_time', datetime.now())

        # 如果signal_time是字符串，转换为datetime
        if isinstance(signal_time, str):
            signal_time = datetime.fromisoformat(signal_time)

        # === 根据黑名单等级获取每批保证金 (新增) ===
        margin_per_batch = self._get_margin_per_batch(symbol)

        if margin_per_batch == 0:
            # 黑名单3级，不交易
            rating_level = self.opt_config.get_symbol_rating_level(symbol)
            logger.warning(f"❌ {symbol} 为黑名单{rating_level}级，禁止交易")
            return {
                'success': False,
                'reason': f'黑名单{rating_level}级禁止交易',
                'plan': None,
                'avg_price': 0
            }

        logger.info(f"🚀 {symbol} 开始K线回调分批建仓 V2 | 方向: {direction}")
        logger.info(f"   信号时间: {signal_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"   策略: 1根反向K线确认 | 15M(0-30min) → 5M(30-60min)")
        logger.info(f"💰 每批保证金: {margin_per_batch}U (评级等级: {self.opt_config.get_symbol_rating_level(symbol)})")

        # 🔥 确保symbol已订阅到WebSocket价格服务
        if self.price_service and hasattr(self.price_service, 'subscribe'):
            try:
                await self.price_service.subscribe([symbol])
                logger.debug(f"✅ {symbol} 已订阅到WebSocket价格服务")
            except Exception as e:
                logger.warning(f"⚠️ {symbol} WebSocket订阅失败: {e}，将使用数据库价格")

        # 初始化建仓计划
        plan = {
            'symbol': symbol,
            'direction': direction,
            'signal_time': signal_time,
            'margin_per_batch': margin_per_batch,  # 每批固定保证金
            'leverage': signal.get('leverage', 5),
            'batches': [
                {'filled': False, 'price': None, 'time': None, 'reason': None, 'margin': None, 'quantity': None},
                {'filled': False, 'price': None, 'time': None, 'reason': None, 'margin': None, 'quantity': None},
                {'filled': False, 'price': None, 'time': None, 'reason': None, 'margin': None, 'quantity': None},
            ],
            'signal': signal,
            'phase': 'primary',  # primary=15M阶段, fallback=5M阶段
            'consecutive_reverse_count': 0  # 连续反向K线计数
        }

        # 🔥 计算止盈止损（基于当前价格的初始预估）
        current_price = await self._get_current_price(symbol)
        signal_components = signal.get('trade_params', {}).get('signal_components', {})

        if current_price:
            stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct = self._calculate_stop_take_prices(
                symbol, direction, current_price, signal_components
            )
        else:
            logger.warning(f"⚠️ {symbol} 无法获取当前价格，止盈止损将由平仓优化器管理")
            stop_loss_price = None
            take_profit_price = None
            stop_loss_pct = None
            take_profit_pct = None

        # 🔥 计算计划平仓时间（完全按V1方式: 统一3小时强制平仓）
        max_hold_minutes = 180  # 3小时强制平仓
        planned_close_time = datetime.now() + timedelta(minutes=max_hold_minutes)

        # 🔥 保存止盈止损参数到plan中，等第1批成交时创建持仓记录
        plan['stop_loss_price'] = stop_loss_price
        plan['take_profit_price'] = take_profit_price
        plan['stop_loss_pct'] = stop_loss_pct
        plan['take_profit_pct'] = take_profit_pct
        plan['planned_close_time'] = planned_close_time

        logger.info(f"💡 {symbol} V2策略：等待反向K线后才创建持仓（类似V1，避免被has_position跳过）")

        try:
            # 检查信号是否已过期
            elapsed_seconds = (datetime.now() - signal_time).total_seconds()
            if elapsed_seconds >= self.total_window_minutes * 60:
                logger.warning(f"⚠️ {symbol} 信号已过期 | 信号时间: {signal_time.strftime('%H:%M:%S')} | 已过: {elapsed_seconds/60:.1f}分钟 > {self.total_window_minutes}分钟窗口")
                return {
                    'success': False,
                    'error': f'信号已过期({elapsed_seconds/60:.0f}分钟)',
                    'position_id': None
                }

            # 执行分批建仓主循环
            logger.info(f"🔄 {symbol} 进入主循环，窗口时长: {self.total_window_minutes}分钟")
            while (datetime.now() - signal_time).total_seconds() < self.total_window_minutes * 60:
                elapsed_minutes = (datetime.now() - signal_time).total_seconds() / 60
                current_price = await self._get_current_price(symbol)

                if not current_price:
                    logger.warning(f"⚠️ {symbol} 无法获取当前价格，等待{self.check_interval_seconds}秒后重试...")
                    await asyncio.sleep(self.check_interval_seconds)
                    continue

                # 判断当前阶段（15M或5M）
                if elapsed_minutes < self.primary_window_minutes:
                    # 阶段1: 15M K线回调
                    timeframe = '15m'
                    plan['phase'] = 'primary'
                else:
                    # 阶段2: 30分钟后统一切换到5M（无论第1批是否完成）
                    timeframe = '5m'
                    plan['phase'] = 'fallback'
                    if plan.get('fallback_logged') != True:
                        completed = sum(1 for b in plan['batches'] if b['filled'])
                        logger.info(f"⏰ {symbol} 30分钟后切换到5M精准监控 | 已完成{completed}/3批")
                        plan['fallback_logged'] = True

                # 定期输出检查状态(每5分钟)
                if int(elapsed_minutes) % 5 == 0 and elapsed_minutes > 0:
                    completed = sum(1 for b in plan['batches'] if b['filled'])
                    logger.info(f"🔄 {symbol} 执行中 | {timeframe.upper()}阶段 | 已用时:{elapsed_minutes:.0f}分钟 | 已完成:{completed}/3批 | 价格:${current_price:.4f}")

                # 获取最近2根K线，判断是否连续反向
                # 根据阶段确定检测基准时间
                if plan['phase'] == 'primary':
                    # 15M阶段：从信号时间开始检测
                    detection_base_time = signal_time
                else:
                    # 5M阶段：从30分钟时刻开始检测
                    detection_base_time = signal_time + timedelta(minutes=self.primary_window_minutes)

                reverse_confirmed = await self._check_consecutive_reverse_klines(
                    symbol, direction, timeframe, count=1, signal_time=detection_base_time
                )

                if reverse_confirmed:
                    logger.info(f"✅ {symbol} 检测到{timeframe.upper()}反向K线 | 准备执行批次")
                    logger.info(f"🔍 {symbol} 当前阶段: {plan['phase']} | 批次状态: {[b['filled'] for b in plan['batches']]}")
                    # 找到第一个未完成的批次，但要遵守阶段和时间规则
                    for batch_idx, batch in enumerate(plan['batches']):
                        if not batch['filled']:
                            # 第1批（batch 0）：只在15M阶段执行
                            if batch_idx == 0 and plan['phase'] != 'primary':
                                logger.debug(f"⏭️ {symbol} 跳过第1批：当前阶段{plan['phase']}，需要primary")
                                continue

                            # 第2批（batch 1）：只在5M阶段执行
                            if batch_idx == 1 and plan['phase'] != 'fallback':
                                logger.debug(f"⏭️ {symbol} 跳过第2批：当前阶段{plan['phase']}，需要fallback")
                                continue

                            # 第3批（batch 2）：需要第2批完成，且至少间隔5分钟
                            if batch_idx == 2:
                                if plan['phase'] != 'fallback':
                                    continue
                                if not plan['batches'][1]['filled']:
                                    continue
                                # 检查第2批完成时间
                                batch2_time = plan['batches'][1].get('time')
                                if batch2_time:
                                    if isinstance(batch2_time, str):
                                        batch2_time = datetime.fromisoformat(batch2_time)
                                    elapsed = (datetime.now() - batch2_time).total_seconds() / 60
                                    if elapsed < 5:
                                        logger.debug(f"🕐 {symbol} 第3批需等待第2批完成后5分钟 | 已过{elapsed:.1f}分钟")
                                        continue

                            reason = f"{timeframe.upper()}反向K线回调确认"
                            await self._execute_batch(plan, batch_idx, current_price, reason)
                            break
                    else:
                        # for循环正常结束（没有break），说明没有找到可执行的批次
                        logger.debug(f"⏭️ {symbol} 所有批次都被跳过或已完成")

                # 检查是否全部完成
                if all(b['filled'] for b in plan['batches']):
                    logger.info(f"🎉 {symbol} 全部3批建仓完成！")
                    break

                await asyncio.sleep(self.check_interval_seconds)

        except Exception as e:
            logger.error(f"❌ {symbol} 分批建仓执行出错: {e}")

        # 建仓结束，统计结果
        filled_batches = [b for b in plan['batches'] if b['filled']]
        filled_count = len(filled_batches)

        if filled_count == 0:
            logger.warning(f"⚠️ {symbol} 建仓窗口结束，未完成任何批次（无回调机会，遵守纪律）")

            # 清理未完成的building记录，避免产生空的持仓记录
            position_id = plan.get('position_id')
            if position_id:
                try:
                    conn = pymysql.connect(**self.db_config)
                    cursor = conn.cursor()

                    cursor.execute("""
                        DELETE FROM futures_positions
                        WHERE id = %s AND status = 'building' AND quantity = 0
                    """, (position_id,))

                    conn.commit()
                    cursor.close()
                    conn.close()

                    logger.info(f"🗑️ {symbol} 已删除未完成的building记录（ID:{position_id}）")
                except Exception as e:
                    logger.error(f"❌ {symbol} 删除building记录失败: {e}")

            return {
                'success': False,
                'error': '无回调机会，未完成任何批次',
                'position_id': None
            }

        # 计算平均成本和总数量
        avg_price = self._calculate_avg_price(plan)
        total_quantity = sum(b.get('quantity', 0) for b in filled_batches)

        # 标记持仓为完全开仓状态
        await self._finalize_position(plan)

        position_id = plan.get('position_id')
        logger.info(
            f"✅ [KLINE_PULLBACK_COMPLETE] {symbol} {direction} | "
            f"持仓ID: {position_id} | "
            f"完成批次: {filled_count}/3 | "
            f"平均价格: ${avg_price:.4f} | "
            f"总数量: {total_quantity:.2f}"
        )

        return {
            'success': True,
            'position_id': position_id,
            'avg_price': avg_price,
            'total_quantity': total_quantity,
            'filled_batches': filled_count,
            'plan': plan
        }

    async def _check_consecutive_reverse_klines(
        self,
        symbol: str,
        direction: str,
        timeframe: str,
        count: int = 1,
        signal_time: datetime = None
    ) -> bool:
        """
        检查信号时间之后是否有反向K线

        Args:
            symbol: 交易对
            direction: 方向（LONG/SHORT）
            timeframe: 时间周期（15m/5m）
            count: 需要的K线数量（默认1根）
            signal_time: 信号时间（只检查此时间之后的K线）

        Returns:
            是否确认反向回调
        """
        try:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # 🔥 数据库中symbol格式为 'RAY/USDT'（带斜杠），不需要转换

            # 🔥 关键逻辑：查询信号时间之后的**固定前N根**K线（非滑动窗口）
            # 例如：信号14:42触发，等待的是14:45和15:00这固定的2根15M K线
            # 而不是每次都取最近的2根（那样永远等不到）
            if signal_time:
                # 🔥 将Python datetime转换为Unix毫秒时间戳（数据库存储格式）
                signal_timestamp = int(signal_time.timestamp() * 1000)

                # 🔥 关键修复：使用close_time而不是open_time来包含当前正在进行的K线
                # 例如：信号16:36触发，可以检测到16:30-16:45这根K线（close_time=16:45 > 16:36）
                # 而不是等到16:45开盘才能检测（open_time=16:45）
                logger.info(f"🔍 [{symbol}] 查询K线 | timeframe={timeframe} | signal_timestamp={signal_timestamp} | count={count}")
                cursor.execute("""
                    SELECT open_price, close_price, open_time, close_time
                    FROM kline_data
                    WHERE symbol = %s
                      AND timeframe = %s
                      AND exchange = 'binance_futures'
                      AND close_time > %s
                    ORDER BY close_time ASC
                    LIMIT %s
                """, (symbol, timeframe, signal_timestamp, count))

                klines = cursor.fetchall()
                logger.info(f"🔍 [{symbol}] 查询结果: {len(klines)}根K线")
            else:
                # 兼容旧逻辑（无signal_time时）
                cursor.execute("""
                    SELECT open_price, close_price, open_time
                    FROM kline_data
                    WHERE symbol = %s
                      AND timeframe = %s
                      AND exchange = 'binance_futures'
                    ORDER BY open_time DESC
                    LIMIT %s
                """, (symbol, timeframe, count))
                klines = cursor.fetchall()
            cursor.close()
            conn.close()

            if len(klines) < count:
                return False

            # 判断K线方向
            reverse_count = 0
            kline_times = []
            for kline in klines:
                open_price = float(kline['open_price'])
                close_price = float(kline['close_price'])
                kline_times.append(kline['close_time'])  # 使用收盘时间更直观

                if direction == 'LONG':
                    # 做多：需要阴线回调（close < open）
                    if close_price < open_price:
                        reverse_count += 1
                else:  # SHORT
                    # 做空：需要阳线反弹（close > open）
                    if close_price > open_price:
                        reverse_count += 1

            # 必须全部是反向K线
            is_confirmed = reverse_count == count

            logger.info(f"🔍 [{symbol}] 开始打印检测日志 | signal_time={signal_time} | reverse_count={reverse_count} | is_confirmed={is_confirmed}")

            # 调试日志
            if signal_time:
                kline_times_str = ', '.join([
                    datetime.fromtimestamp(kt / 1000).strftime('%H:%M') for kt in kline_times
                ]) if kline_times else '无'

                logger.info(
                    f"🔍 [{symbol}] {direction} {timeframe} K线检测 | "
                    f"信号时间: {signal_time.strftime('%H:%M:%S')} | "
                    f"检测到 {len(klines)}/{count} 根K线 [{kline_times_str}] | "
                    f"反向数: {reverse_count} | "
                    f"结果: {'✅确认' if is_confirmed else '❌未确认'}"
                )

            return is_confirmed

        except Exception as e:
            logger.error(f"❌ {symbol} 检查K线形态失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def _execute_batch(self, plan: Dict, batch_num: int, price: Decimal, reason: str):
        """
        执行单批建仓

        Args:
            plan: 建仓计划
            batch_num: 批次编号（0,1,2）
            price: 入场价格
            reason: 入场原因
        """
        batch = plan['batches'][batch_num]
        symbol = plan['symbol']
        direction = plan['direction']

        # 使用固定保证金计算数量（不再使用比例）
        batch_margin = plan['margin_per_batch']
        batch_quantity = (batch_margin * plan['leverage']) / float(price)

        # 记录批次信息
        batch['filled'] = True
        batch['price'] = float(price)
        batch['time'] = datetime.now()
        batch['reason'] = reason
        batch['margin'] = batch_margin
        batch['quantity'] = batch_quantity

        logger.success(
            f"📈 [{batch_num + 1}/3批] {symbol} {direction} | "
            f"价格: ${price:.4f} | "
            f"数量: {batch_quantity:.2f} | "
            f"原因: {reason}"
        )

        # 🔥 每批都创建独立的持仓记录，不再更新同一个持仓
        # 这样每个持仓都是独立的，盈亏独立计算，逻辑更清晰
        await self._create_position_record(plan, price, batch_num)

    async def _create_position_record(self, plan: Dict, entry_price: Decimal, batch_num: int = 0):
        """创建持仓记录 - 使用 BatchPositionManager 简化逻辑"""
        try:
            current_batch = plan['batches'][batch_num]

            # 🔥 使用公共的 position_manager 创建持仓
            position_id = self.position_manager.create_position(
                symbol=plan['symbol'],
                direction=plan['direction'],
                quantity=Decimal(str(current_batch['quantity'])),
                entry_price=entry_price,
                margin=Decimal(str(current_batch['margin'])),
                leverage=plan['leverage'],
                batch_num=batch_num,
                batch_ratio=1.0,  # 不再使用比例，传固定值
                signal=plan['signal'],
                signal_time=plan['signal_time'],
                planned_close_time=plan.get('planned_close_time'),
                source='smart_trader_batch_v2'
            )

            return position_id

        except Exception as e:
            logger.error(f"❌ {plan['symbol']} 创建持仓记录失败（第{batch_num+1}批）: {e}")
            raise

    # 🔥 已废弃：不再更新同一个持仓，每批都创建独立持仓
    # async def _update_position_record(self, plan: Dict):
    #     """更新持仓记录（第2、3批）- 已废弃"""
    #     pass

    async def _finalize_position(self, plan: Dict):
        """🔥 已废弃：每批都直接创建为'open'状态，不需要从'building'转换"""
        # 保留空方法以保持向后兼容，避免调用处报错
        pass

    async def _get_current_price(self, symbol: str) -> Optional[Decimal]:
        """获取当前价格"""
        try:
            if self.price_service:
                price = self.price_service.get_price(symbol)
                if price and price > 0:
                    return Decimal(str(price))

            # 回退到数据库
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # 数据库中symbol格式为 'ENSO/USDT'（带斜杠），直接使用
            cursor.execute("""
                SELECT close_price
                FROM kline_data
                WHERE symbol = %s AND timeframe = '5m' AND exchange = 'binance_futures'
                ORDER BY open_time DESC LIMIT 1
            """, (symbol,))

            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if result:
                return Decimal(str(result['close_price']))

        except Exception as e:
            logger.error(f"❌ 获取价格失败: {e}")

        return None

    def _calculate_avg_price(self, plan: Dict) -> float:
        """计算平均成本"""
        filled_batches = [b for b in plan['batches'] if b['filled']]
        if not filled_batches:
            return 0

        total_cost = sum(b['price'] * b['quantity'] for b in filled_batches)
        total_quantity = sum(b['quantity'] for b in filled_batches)

        return total_cost / total_quantity if total_quantity > 0 else 0

    async def recover_building_positions(self):
        """
        恢复未完成的分批建仓任务

        系统重启后，继续完成未完成的批次（如果还在60分钟窗口内）
        """
        import json

        try:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # 查询所有building状态的持仓（分批建仓中）
            cursor.execute("""
                SELECT id, symbol, position_side, batch_plan, batch_filled, entry_signal_time
                FROM futures_positions
                WHERE account_id = %s
                AND status = 'building'
                AND entry_signal_type = 'kline_pullback_v2'
                ORDER BY entry_signal_time ASC
            """, (self.account_id,))

            partial_positions = cursor.fetchall()
            cursor.close()
            conn.close()

            if not partial_positions:
                logger.info("✅ [V2-RECOVERY] 没有需要恢复的building状态持仓")
                return

            logger.info(f"🔄 [V2-RECOVERY] 发现 {len(partial_positions)} 个building状态持仓，开始恢复...")

            for pos in partial_positions:
                try:
                    await self._recover_single_position(pos)
                except Exception as e:
                    logger.error(f"❌ [V2-RECOVERY] 恢复持仓 {pos['id']} 失败: {e}")

            logger.info(f"✅ [V2-RECOVERY] 恢复任务完成")

        except Exception as e:
            logger.error(f"❌ [V2-RECOVERY] 恢复任务失败: {e}")

    async def _recover_single_position(self, pos: Dict):
        """恢复单个未完成的持仓"""
        import json

        position_id = pos['id']
        symbol = pos['symbol']
        direction = pos['position_side']  # 使用正确的字段名

        # 解析batch_plan和batch_filled
        try:
            batch_plan = json.loads(pos['batch_plan']) if pos['batch_plan'] else None
            batch_filled = json.loads(pos['batch_filled']) if pos['batch_filled'] else None
        except:
            logger.warning(f"⚠️ [V2-RECOVERY] 持仓 {position_id} batch数据解析失败，标记为completed")
            await self._mark_position_completed(position_id)
            return

        if batch_plan is None or batch_filled is None:
            logger.warning(f"⚠️ [V2-RECOVERY] 持仓 {position_id} 缺少batch数据，标记为completed")
            await self._mark_position_completed(position_id)
            return

        # 解析信号时间
        signal_time = pos['entry_signal_time']
        if isinstance(signal_time, str):
            signal_time = datetime.fromisoformat(signal_time)

        # 计算已过去时间和剩余时间
        elapsed_minutes = (datetime.now() - signal_time).total_seconds() / 60
        remaining_minutes = self.total_window_minutes - elapsed_minutes

        if remaining_minutes <= 0:
            # 超时，检查是否有实际仓位
            # 如果quantity=0（未完成任何批次），删除building记录
            # 如果quantity>0（有部分批次完成），标记为open
            try:
                conn = pymysql.connect(**self.db_config)
                cursor = conn.cursor(pymysql.cursors.DictCursor)

                cursor.execute("""
                    SELECT quantity FROM futures_positions WHERE id = %s
                """, (position_id,))

                result = cursor.fetchone()
                cursor.close()
                conn.close()

                if result and result['quantity'] and result['quantity'] > 0:
                    # 有实际仓位，标记为open
                    logger.info(
                        f"⏰ [V2-RECOVERY] 持仓 {position_id} ({symbol} {direction}) "
                        f"已超过60分钟窗口 (已过{elapsed_minutes:.1f}分钟)，标记为open"
                    )
                    await self._mark_position_completed(position_id)
                else:
                    # 没有仓位，删除building记录
                    logger.info(
                        f"⏰ [V2-RECOVERY] 持仓 {position_id} ({symbol} {direction}) "
                        f"已超过60分钟窗口 (已过{elapsed_minutes:.1f}分钟)，未完成任何批次，删除记录"
                    )
                    conn = pymysql.connect(**self.db_config)
                    cursor = conn.cursor()
                    cursor.execute("""
                        DELETE FROM futures_positions
                        WHERE id = %s AND status = 'building' AND (quantity = 0 OR quantity IS NULL)
                    """, (position_id,))
                    conn.commit()
                    cursor.close()
                    conn.close()

            except Exception as e:
                logger.error(f"❌ [V2-RECOVERY] 处理超时持仓 {position_id} 失败: {e}")

            return

        # 重建plan对象
        filled_count = len(batch_filled['batches'])
        total_batches = len(batch_plan['batches'])

        logger.info(
            f"🔄 [V2-RECOVERY] 恢复持仓 {position_id} ({symbol} {direction}) | "
            f"已完成 {filled_count}/{total_batches} 批次 | "
            f"剩余时间 {remaining_minutes:.1f}分钟"
        )

        # 重建plan
        plan = {
            'position_id': position_id,
            'symbol': symbol,
            'direction': direction,
            'signal_time': signal_time,
            'margin_per_batch': self._get_margin_per_batch(symbol),  # 使用新的固定保证金逻辑
            'leverage': batch_plan['leverage'],
            'batches': [],
            'phase': 'primary' if elapsed_minutes < self.primary_window_minutes else 'fallback'
        }

        # 重建batches数组
        for i, batch_spec in enumerate(batch_plan['batches']):
            # 检查这一批是否已完成
            filled_batch = next((b for b in batch_filled['batches'] if b['batch_num'] == i), None)

            if filled_batch:
                # 已完成的批次
                plan['batches'].append({
                    'ratio': batch_spec['ratio'],
                    'filled': True,
                    'price': filled_batch['price'],
                    'time': datetime.fromisoformat(filled_batch['time']),
                    'reason': filled_batch['reason'],
                    'margin': filled_batch['margin'],
                    'quantity': filled_batch['quantity']
                })
            else:
                # 未完成的批次
                plan['batches'].append({
                    'ratio': batch_spec['ratio'],
                    'filled': False,
                    'price': None,
                    'time': None,
                    'reason': None,
                    'margin': None,
                    'quantity': None
                })

        # 继续执行建仓流程（从当前时间点继续）
        try:
            while (datetime.now() - signal_time).total_seconds() < self.total_window_minutes * 60:
                elapsed_minutes = (datetime.now() - signal_time).total_seconds() / 60
                current_price = await self._get_current_price(symbol)

                if not current_price:
                    await asyncio.sleep(self.check_interval_seconds)
                    continue

                # 判断当前阶段（15M或5M）
                if elapsed_minutes < self.primary_window_minutes:
                    timeframe = '15m'
                    plan['phase'] = 'primary'
                else:
                    # 阶段2: 如果第1批未完成，切换到5M
                    if not plan['batches'][0]['filled']:
                        timeframe = '5m'
                        plan['phase'] = 'fallback'
                        logger.info(f"⏰ [V2-RECOVERY] {symbol} 切换到5M监控")
                    else:
                        timeframe = '15m'

                # 判断是否有反向K线
                reverse_confirmed = await self._check_consecutive_reverse_klines(
                    symbol, direction, timeframe, count=1, signal_time=signal_time
                )

                if reverse_confirmed:
                    # 找到第一个未完成的批次
                    for batch_idx, batch in enumerate(plan['batches']):
                        if not batch['filled']:
                            reason = f"{timeframe.upper()}反向K线回调确认(恢复)"
                            await self._execute_batch(plan, batch_idx, current_price, reason)
                            break

                # 检查是否全部完成
                if all(b['filled'] for b in plan['batches']):
                    logger.info(f"🎉 [V2-RECOVERY] {symbol} 全部批次建仓完成！")
                    await self._finalize_position(plan)
                    return

                await asyncio.sleep(self.check_interval_seconds)

            # 时间窗口结束
            logger.info(f"⏰ [V2-RECOVERY] {symbol} 建仓窗口结束，标记为completed")
            await self._finalize_position(plan)

        except Exception as e:
            logger.error(f"❌ [V2-RECOVERY] {symbol} 恢复执行失败: {e}")
            await self._mark_position_completed(position_id)

    async def _mark_position_completed(self, position_id: int):
        """标记持仓为open（完成分批建仓）"""
        try:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor()

            # 只有quantity > 0才改为open，避免产生空的持仓记录
            cursor.execute("""
                UPDATE futures_positions
                SET status = 'open', updated_at = NOW()
                WHERE id = %s AND quantity > 0
            """, (position_id,))

            rows_affected = cursor.rowcount
            if rows_affected == 0:
                logger.warning(f"⚠️ 持仓{position_id}未更新为open状态（可能quantity=0）")

            conn.commit()
            cursor.close()
            conn.close()

        except Exception as e:
            logger.error(f"❌ 标记持仓 {position_id} 为open失败: {e}")
