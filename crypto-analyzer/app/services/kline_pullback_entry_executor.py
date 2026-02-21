"""
K线回调建仓执行器 V2 (一次性开仓版本)
基于K线形态回调确认实现最优入场时机

核心策略：
- 做多：等待1根反向阴线作为回调确认
- 做空：等待1根反向阳线作为反弹确认
- 两级降级：15M（0-30分钟）→ 5M（30-60分钟）
- 纪律严明：宁愿错过，不追涨杀跌
- 确认后立即一次性开仓100%，不分批
"""
import asyncio
import json
import pymysql
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from decimal import Decimal
from loguru import logger

from app.services.optimization_config import OptimizationConfig


class KlinePullbackEntryExecutor:
    """K线回调建仓执行器（一次性开仓）"""

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

        # 如果仍没有opt_config，创建新实例
        if not self.opt_config:
            self.opt_config = OptimizationConfig(db_config)

        # 时间窗口配置
        self.total_window_minutes = 60  # 总时间窗口60分钟
        self.primary_window_minutes = 30  # 第一阶段30分钟（15M）
        self.check_interval_seconds = 60  # 每60秒检查一次（K线更新频率）

    def _get_margin_amount(self, symbol: str) -> float:
        """
        根据交易对评级等级获取保证金金额

        Args:
            symbol: 交易对符号

        Returns:
            保证金金额(USDT)，如果是黑名单3级则返回0
        """
        rating_level = self.opt_config.get_symbol_rating_level(symbol)

        # 根据评级等级设置保证金
        if rating_level == 0:
            # 白名单/默认：200U
            return 200.0
        elif rating_level == 1:
            # 黑名单1级：50U
            return 50.0
        elif rating_level == 2:
            # 黑名单2级：30U
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
            (止损价格, 止盈价格, 止损百分比, 止盈百分比)
        """
        if not self.brain or not self.opt_config:
            return None, None, None, None

        # 获取自适应参数
        if direction == 'LONG':
            adaptive_params = self.brain.adaptive_long
        else:
            adaptive_params = self.brain.adaptive_short

        # 计算止损
        base_stop_loss_pct = adaptive_params.get('stop_loss_pct', 0.03)
        # 波动率自适应止损（复用smart_trader_service的逻辑）
        stop_loss_pct = self._calculate_volatility_adjusted_stop_loss(signal_components, base_stop_loss_pct)

        # 计算止盈
        volatility_profile = self.opt_config.get_symbol_volatility_profile(symbol)
        if volatility_profile:
            if direction == 'LONG' and volatility_profile.get('long_fixed_tp_pct'):
                take_profit_pct = float(volatility_profile['long_fixed_tp_pct'])
            elif direction == 'SHORT' and volatility_profile.get('short_fixed_tp_pct'):
                take_profit_pct = float(volatility_profile['short_fixed_tp_pct'])
            else:
                take_profit_pct = adaptive_params.get('take_profit_pct', 0.02)
        else:
            take_profit_pct = adaptive_params.get('take_profit_pct', 0.02)

        # 计算具体价格
        if direction == 'LONG':
            stop_loss_price = current_price * (1 - stop_loss_pct)
            take_profit_price = current_price * (1 + take_profit_pct)
        else:  # SHORT
            stop_loss_price = current_price * (1 + stop_loss_pct)
            take_profit_price = current_price * (1 - take_profit_pct)

        return stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct

    def _calculate_volatility_adjusted_stop_loss(self, signal_components: dict, base_stop_loss_pct: float) -> float:
        """波动率自适应止损"""
        if not signal_components:
            return base_stop_loss_pct

        # 如果包含破位信号，扩大止损
        if any(key.startswith('breakdown_') for key in signal_components.keys()):
            adjusted_pct = base_stop_loss_pct * 1.5
            logger.debug(f"[VOLATILITY_SL] 破位信号，止损扩大1.5倍: {adjusted_pct*100:.2f}%")
            return adjusted_pct

        return base_stop_loss_pct

    async def _get_current_price(self, symbol: str) -> Optional[float]:
        """获取当前价格（优先WebSocket，回退到数据库）"""
        # 优先从WebSocket获取
        if self.price_service:
            price = self.price_service.get_price(symbol)
            if price and price > 0:
                return float(price)

        # 回退到数据库
        try:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT close FROM futures_klines
                WHERE symbol = %s AND interval = '5m' AND exchange = 'binance_futures'
                ORDER BY open_time DESC
                LIMIT 1
            """, (symbol,))
            result = cursor.fetchone()
            conn.close()
            if result:
                return float(result[0])
        except Exception as e:
            logger.error(f"❌ 从数据库获取价格失败: {e}")

        return None

    async def execute_entry(self, signal: Dict) -> Dict:
        """
        执行K线回调建仓

        流程：
        1. 阶段1（0-30分钟）：监控15M K线，等待1根反向K线
        2. 阶段2（30-60分钟）：切换到5M K线，等待1根反向K线
        3. 检测到回调确认后，立即一次性开仓100%
        4. 60分钟截止，如果未触发则放弃

        Args:
            signal: 开仓信号 {
                'symbol': str,
                'direction': 'LONG'/'SHORT',
                'leverage': int,
                'signal_time': datetime,
                'trade_params': {...}
            }

        Returns:
            建仓结果 {'success': bool, 'position_id': int, 'price': float}
        """
        symbol = signal['symbol']
        direction = signal['direction']

        # 使用真实的信号触发时间
        signal_time = signal.get('signal_time', datetime.now())
        if isinstance(signal_time, str):
            signal_time = datetime.fromisoformat(signal_time)

        # 获取保证金金额
        margin = self._get_margin_amount(symbol)

        if margin == 0:
            rating_level = self.opt_config.get_symbol_rating_level(symbol)
            logger.warning(f"❌ {symbol} 为黑名单{rating_level}级，禁止交易")
            return {'success': False, 'reason': f'黑名单{rating_level}级禁止交易'}

        logger.info(f"🚀 {symbol} 开始K线回调建仓 V2（一次性开仓） | 方向: {direction}")
        logger.info(f"   信号时间: {signal_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"   策略: 等待1根反向K线确认 | 15M(0-30min) → 5M(30-60min)")
        logger.info(f"💰 保证金: {margin}U (评级等级: {self.opt_config.get_symbol_rating_level(symbol)})")

        # 确保symbol已订阅到WebSocket价格服务
        if self.price_service and hasattr(self.price_service, 'subscribe'):
            try:
                await self.price_service.subscribe([symbol])
                logger.debug(f"✅ {symbol} 已订阅到WebSocket价格服务")
            except Exception as e:
                logger.warning(f"⚠️ {symbol} WebSocket订阅失败: {e}，将使用数据库价格")

        try:
            # 检查信号是否已过期
            elapsed_seconds = (datetime.now() - signal_time).total_seconds()
            if elapsed_seconds >= self.total_window_minutes * 60:
                logger.warning(f"⚠️ {symbol} 信号已过期 | 已过: {elapsed_seconds/60:.1f}分钟")
                return {'success': False, 'error': f'信号已过期({elapsed_seconds/60:.0f}分钟)'}

            # 主循环：等待回调确认
            logger.info(f"🔄 {symbol} 进入监控循环，窗口时长: {self.total_window_minutes}分钟")
            phase = 'primary'
            fallback_logged = False

            while (datetime.now() - signal_time).total_seconds() < self.total_window_minutes * 60:
                elapsed_minutes = (datetime.now() - signal_time).total_seconds() / 60

                # 判断当前阶段
                if elapsed_minutes < self.primary_window_minutes:
                    timeframe = '15m'
                    phase = 'primary'
                else:
                    timeframe = '5m'
                    phase = 'fallback'
                    if not fallback_logged:
                        logger.info(f"⏰ {symbol} 30分钟后切换到5M精准监控")
                        fallback_logged = True

                # 检测回调确认
                pullback_confirmed, reason = await self._check_pullback_confirmation(
                    symbol, direction, timeframe, signal_time, phase
                )

                if pullback_confirmed:
                    # 检测到回调确认，立即开仓
                    logger.info(f"✅ {symbol} 回调确认触发: {reason}")
                    return await self._execute_single_entry(
                        symbol, direction, margin, signal, signal_time
                    )

                # 等待下一次检查
                await asyncio.sleep(self.check_interval_seconds)

            # 超时未触发
            logger.warning(f"⏱️ {symbol} 60分钟窗口结束，未检测到回调确认，放弃建仓")
            return {'success': False, 'error': '超时未触发回调确认'}

        except Exception as e:
            logger.error(f"❌ {symbol} 回调建仓执行出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {'success': False, 'error': str(e)}

    async def _check_pullback_confirmation(
        self, symbol: str, direction: str, timeframe: str,
        signal_time: datetime, phase: str
    ) -> Tuple[bool, str]:
        """
        检查是否出现回调确认（1根反向K线）

        Args:
            symbol: 交易对
            direction: 方向 LONG/SHORT
            timeframe: 时间框架 15m/5m
            signal_time: 信号时间
            phase: 当前阶段 primary/fallback

        Returns:
            (是否确认, 原因描述)
        """
        try:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor(dictionary=True)

            # 根据阶段确定检测基准时间
            if phase == 'primary':
                base_time = signal_time
            else:
                base_time = signal_time + timedelta(minutes=self.primary_window_minutes)

            # 获取基准时间后的最近2根K线
            cursor.execute("""
                SELECT open_time, open, close
                FROM futures_klines
                WHERE symbol = %s AND interval = %s AND exchange = 'binance_futures'
                AND open_time >= %s
                ORDER BY open_time DESC
                LIMIT 2
            """, (symbol, timeframe, base_time))

            klines = cursor.fetchall()
            conn.close()

            if len(klines) < 1:
                return False, "数据不足"

            latest_kline = klines[0]
            is_green = latest_kline['close'] > latest_kline['open']  # 阳线
            is_red = latest_kline['close'] < latest_kline['open']    # 阴线

            # 做多：等待阴线回调
            if direction == 'LONG' and is_red:
                return True, f"{timeframe.upper()}阴线回调确认"

            # 做空：等待阳线反弹
            if direction == 'SHORT' and is_green:
                return True, f"{timeframe.upper()}阳线反弹确认"

            return False, "等待反向K线"

        except Exception as e:
            logger.error(f"❌ 检查回调确认失败: {e}")
            return False, f"检查失败: {e}"

    async def _execute_single_entry(
        self, symbol: str, direction: str, margin: float,
        signal: Dict, signal_time: datetime
    ) -> Dict:
        """
        执行一次性开仓

        Args:
            symbol: 交易对
            direction: 方向
            margin: 保证金金额
            signal: 原始信号
            signal_time: 信号时间

        Returns:
            开仓结果
        """
        try:
            # 获取当前价格
            current_price = await self._get_current_price(symbol)
            if not current_price:
                logger.error(f"❌ {symbol} 无法获取当前价格")
                return {'success': False, 'error': '无法获取价格'}

            # 计算止盈止损
            signal_components = signal.get('trade_params', {}).get('signal_components', {})
            stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct = \
                self._calculate_stop_take_prices(symbol, direction, current_price, signal_components)

            # 计算仓位
            leverage = signal.get('leverage', 5)
            quantity = margin * leverage / current_price
            notional_value = quantity * current_price

            # 生成信号组合键
            if signal_components:
                sorted_signals = sorted(signal_components.keys())
                signal_combination_key = "TREND_" + " + ".join(sorted_signals)
            else:
                signal_combination_key = "TREND_unknown"

            # 计算超时和计划平仓时间
            max_hold_minutes = 180  # 3小时
            timeout_at = datetime.utcnow() + timedelta(minutes=max_hold_minutes)
            planned_close_time = datetime.now() + timedelta(minutes=max_hold_minutes)

            # 准备数据
            entry_score = signal.get('trade_params', {}).get('entry_score', 0)
            entry_reason = f"V2回调确认 | 评分:{entry_score}"

            # 插入持仓记录
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO futures_positions
                (account_id, symbol, position_side, quantity, entry_price,
                 leverage, notional_value, margin, open_time, stop_loss_price, take_profit_price,
                 entry_signal_type, entry_reason, entry_score, signal_components, max_hold_minutes, timeout_at,
                 planned_close_time, source, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        'smart_trader', 'open', NOW(), NOW())
            """, (
                self.account_id, symbol, direction, quantity, current_price, leverage,
                notional_value, margin, stop_loss_price, take_profit_price,
                signal_combination_key, entry_reason, entry_score,
                json.dumps(signal_components) if signal_components else None,
                max_hold_minutes, timeout_at, planned_close_time
            ))

            position_id = cursor.lastrowid

            # 冻结资金
            cursor.execute("""
                UPDATE futures_trading_accounts
                SET available_balance = available_balance - %s,
                    frozen_balance = frozen_balance + %s
                WHERE id = %s
            """, (margin, margin, self.account_id))

            conn.commit()
            conn.close()

            logger.info(f"✅ {symbol} 一次性开仓完成 | 持仓ID:{position_id} | 价格:${current_price:.4f} | 保证金:{margin}U")

            # 启动智能平仓监控
            if self.live_engine.smart_exit_optimizer:
                try:
                    asyncio.create_task(
                        self.live_engine.smart_exit_optimizer.start_monitoring_position(position_id)
                    )
                    logger.info(f"✅ 持仓{position_id}已加入智能平仓监控")
                except Exception as e:
                    logger.error(f"❌ 持仓{position_id}启动监控失败: {e}")

            return {
                'success': True,
                'position_id': position_id,
                'price': current_price,
                'margin': margin,
                'quantity': quantity
            }

        except Exception as e:
            logger.error(f"❌ {symbol} 一次性开仓失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {'success': False, 'error': str(e)}
