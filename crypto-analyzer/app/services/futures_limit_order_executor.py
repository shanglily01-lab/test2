# -*- coding: utf-8 -*-
"""
合约限价单自动执行服务
后台定时检查合约限价单，当价格达到触发条件时自动执行
"""

import asyncio
from decimal import Decimal
from typing import Dict, Optional, List
import pymysql
import json
from loguru import logger


class FuturesLimitOrderExecutor:
    """合约限价单自动执行器"""

    def __init__(self, db_config: Dict, trading_engine, price_cache_service=None, live_engine=None):
        """
        初始化执行器

        Args:
            db_config: 数据库配置
            trading_engine: 合约交易引擎实例 (FuturesTradingEngine)
            price_cache_service: 价格缓存服务（可选）
            live_engine: 实盘交易引擎实例 (BinanceFuturesEngine, 可选)
        """
        self.db_config = db_config
        self.trading_engine = trading_engine
        self.price_cache_service = price_cache_service
        self.live_engine = live_engine
        self.running = False
        self.task = None
        self.connection = None  # 持久数据库连接
        
    def _get_connection(self):
        """获取数据库连接（复用持久连接）"""
        # 如果连接不存在或已断开，创建新连接
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
                    connect_timeout=5,
                    read_timeout=10,
                    write_timeout=10,
                    autocommit=True  # 自动提交，避免事务问题
                )
                # 只在首次创建连接时记录（DEBUG级别）
            except Exception as e:
                logger.error(f"❌ 创建数据库连接失败: {e}")
                raise
        else:
            # 静默检查连接是否还活着（不打印日志）
            try:
                self.connection.ping(reconnect=True)
            except Exception as e:
                # 只有在连接真正断开需要重连时才记录
                logger.warning(f"数据库连接已断开，尝试重连: {e}")
                try:
                    self.connection = pymysql.connect(
                        host=self.db_config.get('host', 'localhost'),
                        port=self.db_config.get('port', 3306),
                        user=self.db_config.get('user', 'root'),
                        password=self.db_config.get('password', ''),
                        database=self.db_config.get('database', 'binance-data'),
                        charset='utf8mb4',
                        cursorclass=pymysql.cursors.DictCursor,
                        connect_timeout=5,
                        read_timeout=10,
                        write_timeout=10,
                        autocommit=True
                    )
                    logger.debug("✅ 数据库连接已重新建立（合约限价单执行器）")
                except Exception as e2:
                    logger.error(f"❌ 重连数据库失败: {e2}")
                    raise
        
        return self.connection
    
    def get_current_price(self, symbol: str, use_realtime: bool = False) -> Decimal:
        """
        获取当前价格
        
        Args:
            symbol: 交易对
            use_realtime: 是否使用实时API价格（限价单扫描时使用）
            
        Returns:
            当前价格，如果获取失败返回0
        """
        # 如果要求使用实时价格，直接调用交易引擎的实时价格方法
        if use_realtime:
            try:
                price = self.trading_engine.get_current_price(symbol, use_realtime=True)
                return Decimal(str(price)) if price else Decimal('0')
            except Exception as e:
                logger.error(f"获取 {symbol} 实时价格失败: {e}")
                return Decimal('0')
        
        # 优先使用价格缓存服务（非实时模式）
        if self.price_cache_service:
            try:
                price = self.price_cache_service.get_price(symbol)
                if price and price > 0:
                    return Decimal(str(price))
            except Exception as e:
                logger.debug(f"从价格缓存获取 {symbol} 价格失败: {e}")
        
        # 回退到交易引擎的价格获取方法
        try:
            price = self.trading_engine.get_current_price(symbol)
            return Decimal(str(price)) if price else Decimal('0')
        except Exception as e:
            logger.error(f"获取 {symbol} 价格失败: {e}")
            return Decimal('0')

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

    def _check_trend_reversal(self, connection, order: Dict) -> Optional[str]:
        """
        检查趋势是否已转向（出现反向EMA交叉信号）

        Args:
            connection: 数据库连接
            order: 订单信息（包含 strategy_config, symbol, side）

        Returns:
            取消原因（如果需要取消），否则返回 None
        """
        try:
            strategy_config = order.get('strategy_config')
            if not strategy_config:
                return None

            # 解析策略配置（可能是双重JSON编码）
            config = strategy_config
            # 循环解析直到不再是字符串
            parse_attempts = 0
            while isinstance(config, str) and parse_attempts < 3:
                try:
                    config = json.loads(config)
                    parse_attempts += 1
                except json.JSONDecodeError:
                    break

            # 如果解析后仍然是字符串，则无法处理
            if isinstance(config, str):
                logger.warning(f"无法解析策略配置: {strategy_config[:100]}...")
                return None

            # 检查是否是有效的字典
            if not isinstance(config, dict):
                logger.warning(f"策略配置不是字典类型: {type(config)}")
                return None

            # 检查是否启用趋势转向取消功能
            cancel_on_trend_reversal = config.get('cancelOnTrendReversal', True)
            if not cancel_on_trend_reversal:
                return None

            symbol = order['symbol']
            side = order['side']  # OPEN_LONG 或 OPEN_SHORT

            # 获取买入时间周期（默认15m）
            buy_signals = config.get('buySignals', {})
            buy_timeframe = '15m'

            # buySignals 可能是字符串（如 "ema_15m"）或字典
            if isinstance(buy_signals, str):
                # 如果是字符串，根据字符串内容判断时间周期
                if '5m' in buy_signals:
                    buy_timeframe = '5m'
                elif '1h' in buy_signals:
                    buy_timeframe = '1h'
                # 默认 15m
            elif isinstance(buy_signals, dict):
                if buy_signals.get('ema_5m', {}).get('enabled'):
                    buy_timeframe = '5m'
                elif buy_signals.get('ema_1h', {}).get('enabled'):
                    buy_timeframe = '1h'

            # 查询最近的K线数据（至少需要30根来计算EMA26）
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT close_price
                    FROM kline_data
                    WHERE symbol = %s AND timeframe = %s
                    ORDER BY timestamp DESC
                    LIMIT 50""",
                    (symbol, buy_timeframe)
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
            # EMA26从第26根K线开始，所以需要对齐索引
            # ema9_values 从第9根开始，长度为 len(prices) - 8
            # ema26_values 从第26根开始，长度为 len(prices) - 25
            # 两者最后的共同索引：取最后两个

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
            if side == 'OPEN_LONG' and is_death_cross:
                ema_diff_pct = abs((curr_ema9 - curr_ema26) / curr_ema26 * 100)
                return f"趋势转向(死叉): EMA9={curr_ema9:.4f} < EMA26={curr_ema26:.4f}, 差值={ema_diff_pct:.2f}%"

            # 做空限价单，出现金叉则取消
            if side == 'OPEN_SHORT' and is_golden_cross:
                ema_diff_pct = abs((curr_ema9 - curr_ema26) / curr_ema26 * 100)
                return f"趋势转向(金叉): EMA9={curr_ema9:.4f} > EMA26={curr_ema26:.4f}, 差值={ema_diff_pct:.2f}%"

            return None

        except Exception as e:
            logger.error(f"检查趋势转向时出错: {e}")
            return None

    def _check_ema_unfavorable(self, connection, order) -> Optional[str]:
        """
        检查EMA状态是否不利于开仓（用于超时转市价前的检查）

        与 _check_trend_reversal 的区别：
        - _check_trend_reversal: 检测EMA交叉（金叉/死叉）
        - _check_ema_unfavorable: 检查当前EMA状态是否已不适合开仓方向

        判断逻辑：
        - 做多(OPEN_LONG): 如果 EMA9 < EMA26，说明短期趋势弱于长期趋势，不适合做多
        - 做空(OPEN_SHORT): 如果 EMA9 > EMA26，说明短期趋势强于长期趋势，不适合做空

        Returns:
            如果EMA状态不利于开仓，返回原因字符串；否则返回None
        """
        try:
            strategy_config = order.get('strategy_config')
            if not strategy_config:
                return None

            # 解析策略配置
            config = strategy_config
            parse_attempts = 0
            while isinstance(config, str) and parse_attempts < 3:
                try:
                    config = json.loads(config)
                    parse_attempts += 1
                except json.JSONDecodeError:
                    logger.warning(f"无法解析策略配置: {strategy_config[:100]}...")
                    return None

            if not isinstance(config, dict):
                return None

            symbol = order['symbol']
            side = order['side']  # OPEN_LONG 或 OPEN_SHORT

            # 获取卖出信号时间周期（默认5m）
            # 注意：这里用卖出信号的时间周期，因为我们要检查的是"是否快要触发卖出信号"
            sell_signals = config.get('sellSignals', 'ema_5m')
            sell_timeframe = '5m'  # 默认5分钟

            if isinstance(sell_signals, str):
                if '15m' in sell_signals:
                    sell_timeframe = '15m'
                elif '1h' in sell_signals:
                    sell_timeframe = '1h'
                # 默认 5m
            elif isinstance(sell_signals, dict):
                if sell_signals.get('ema_15m', {}).get('enabled'):
                    sell_timeframe = '15m'
                elif sell_signals.get('ema_1h', {}).get('enabled'):
                    sell_timeframe = '1h'

            # 查询最近的K线数据（使用卖出信号的时间周期）
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT close_price
                    FROM kline_data
                    WHERE symbol = %s AND timeframe = %s
                    ORDER BY timestamp DESC
                    LIMIT 50""",
                    (symbol, sell_timeframe)
                )
                klines = cursor.fetchall()

            if not klines or len(klines) < 30:
                return None  # K线数据不足，跳过检查

            # 将K线反转为正序（从旧到新）
            prices = [float(k['close_price']) for k in reversed(klines)]

            # 计算EMA9和EMA26
            ema9_values = self._calculate_ema(prices, 9)
            ema26_values = self._calculate_ema(prices, 26)

            if not ema9_values or not ema26_values:
                return None

            curr_ema9 = ema9_values[-1]
            curr_ema26 = ema26_values[-1]
            ema_diff_pct = (curr_ema9 - curr_ema26) / curr_ema26 * 100

            # 做多但EMA9 < EMA26，趋势不利（即将触发卖出信号）
            if side == 'OPEN_LONG' and curr_ema9 < curr_ema26:
                return f"[{sell_timeframe}] EMA状态不利于做多: EMA9={curr_ema9:.4f} < EMA26={curr_ema26:.4f}, 差值={ema_diff_pct:.2f}%"

            # 做空但EMA9 > EMA26，趋势不利（即将触发卖出信号）
            if side == 'OPEN_SHORT' and curr_ema9 > curr_ema26:
                return f"[{sell_timeframe}] EMA状态不利于做空: EMA9={curr_ema9:.4f} > EMA26={curr_ema26:.4f}, 差值={ema_diff_pct:.2f}%"

            return None

        except Exception as e:
            logger.error(f"检查EMA状态时出错: {e}")
            return None

    def _calculate_rsi(self, prices: List[float], period: int = 14) -> Optional[float]:
        """
        计算RSI (Relative Strength Index)

        Args:
            prices: 价格列表（从旧到新）
            period: RSI周期，默认14

        Returns:
            RSI值 (0-100)，如果数据不足返回None
        """
        if len(prices) < period + 1:
            return None

        # 计算价格变化
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]

        # 初始平均涨跌幅
        gains = [d if d > 0 else 0 for d in deltas[:period]]
        losses = [-d if d < 0 else 0 for d in deltas[:period]]

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        # 使用Wilder平滑法计算后续RSI
        for i in range(period, len(deltas)):
            delta = deltas[i]
            gain = delta if delta > 0 else 0
            loss = -delta if delta < 0 else 0

            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    def _check_rsi_filter(self, connection, order: Dict) -> Optional[str]:
        """
        检查RSI过滤条件（限价单触发前检查）

        判断逻辑：
        - 做多(OPEN_LONG): 如果 RSI > longMax（默认65），说明超买，不适合做多
        - 做空(OPEN_SHORT): 如果 RSI < shortMin（默认35），说明超卖，不适合做空

        Args:
            connection: 数据库连接
            order: 订单信息（包含 strategy_config, symbol, side）

        Returns:
            如果RSI条件不满足，返回原因字符串；否则返回None
        """
        try:
            strategy_config = order.get('strategy_config')
            if not strategy_config:
                return None

            # 解析策略配置
            config = strategy_config
            parse_attempts = 0
            while isinstance(config, str) and parse_attempts < 3:
                try:
                    config = json.loads(config)
                    parse_attempts += 1
                except json.JSONDecodeError:
                    return None

            if not isinstance(config, dict):
                return None

            # 检查RSI过滤器配置
            rsi_config = config.get('rsiFilter', {})
            if not isinstance(rsi_config, dict):
                return None

            # 如果RSI过滤器被禁用，跳过检查
            if rsi_config.get('enabled', True) == False:
                return None

            symbol = order['symbol']
            side = order['side']  # OPEN_LONG 或 OPEN_SHORT

            # 获取RSI阈值（默认值与策略执行器一致）
            long_max = rsi_config.get('longMax', 65)   # 做多时RSI上限
            short_min = rsi_config.get('shortMin', 35)  # 做空时RSI下限
            rsi_period = rsi_config.get('period', 14)   # RSI周期
            rsi_timeframe = rsi_config.get('timeframe', '15m')  # RSI使用的时间周期

            # 查询K线数据计算RSI（需要更多数据点来计算准确的RSI）
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT close_price
                    FROM kline_data
                    WHERE symbol = %s AND timeframe = %s
                    ORDER BY timestamp DESC
                    LIMIT 100""",
                    (symbol, rsi_timeframe)
                )
                klines = cursor.fetchall()

            if not klines or len(klines) < rsi_period + 1:
                logger.debug(f"K线数据不足，无法计算RSI: {symbol} ({len(klines) if klines else 0}条)")
                return None

            # 将K线反转为正序（从旧到新）
            prices = [float(k['close_price']) for k in reversed(klines)]

            # 计算RSI
            rsi = self._calculate_rsi(prices, rsi_period)
            if rsi is None:
                return None

            # 做多但RSI超过上限（超买）
            if side == 'OPEN_LONG' and rsi > long_max:
                return f"RSI超买: RSI={rsi:.1f} > {long_max} (上限), 不适合做多"

            # 做空但RSI低于下限（超卖）
            if side == 'OPEN_SHORT' and rsi < short_min:
                return f"RSI超卖: RSI={rsi:.1f} < {short_min} (下限), 不适合做空"

            return None

        except Exception as e:
            logger.error(f"检查RSI过滤条件时出错: {e}")
            return None

    def _get_ema_data_for_validation(self, connection, symbol: str, timeframe: str = '15m') -> Optional[Dict]:
        """
        获取 EMA 数据用于创建待开仓记录

        Args:
            connection: 数据库连接
            symbol: 交易对
            timeframe: 时间周期

        Returns:
            EMA 数据字典，包含 ema9, ema26, ema_diff_pct, current_price 等
        """
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT close_price
                    FROM kline_data
                    WHERE symbol = %s AND timeframe = %s
                    ORDER BY timestamp DESC
                    LIMIT 50
                """, (symbol, timeframe))
                klines = cursor.fetchall()

            if not klines or len(klines) < 30:
                return None

            # 将K线反转为正序（从旧到新）
            prices = [float(k['close_price']) for k in reversed(klines)]

            # 计算 EMA9 和 EMA26
            ema9_values = self._calculate_ema(prices, 9)
            ema26_values = self._calculate_ema(prices, 26)

            if not ema9_values or not ema26_values:
                return None

            ema9 = ema9_values[-1]
            ema26 = ema26_values[-1]
            current_price = prices[-1]
            ema_diff_pct = abs((ema9 - ema26) / ema26 * 100) if ema26 != 0 else 0

            return {
                'ema9': ema9,
                'ema26': ema26,
                'ema_diff_pct': ema_diff_pct,
                'current_price': current_price
            }

        except Exception as e:
            logger.error(f"获取EMA数据时出错: {e}")
            return None

    async def check_and_execute_limit_orders(self):
        """检查并执行限价单（每次查询都创建新连接，确保获取最新数据）"""
        if not self.running:
            return
            
        try:
            # 每次查询都创建新连接，确保获取最新数据
            connection = pymysql.connect(
                host=self.db_config.get('host', 'localhost'),
                port=self.db_config.get('port', 3306),
                user=self.db_config.get('user', 'root'),
                password=self.db_config.get('password', ''),
                database=self.db_config.get('database', 'binance-data'),
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True
            )
            
            try:
                with connection.cursor() as cursor:
                    # 设置会话时区为 UTC+8（与存储的时间一致）
                    cursor.execute("SET time_zone = '+08:00'")

                    # 获取所有待成交的限价单（只处理开仓订单）
                    # 同时获取策略的超时配置
                    # 注意：使用 strategy_timeout 避免与 futures_orders.timeout_minutes 字段冲突
                    cursor.execute(
                        """SELECT o.*,
                               COALESCE(
                                   CAST(JSON_EXTRACT(s.config, '$.limitOrderTimeoutMinutes') AS UNSIGNED),
                                   15
                               ) as strategy_timeout,
                               COALESCE(
                                   CAST(JSON_EXTRACT(s.config, '$.limitOrderMaxDeviation') AS DECIMAL(5,2)),
                                   1.5
                               ) as strategy_max_deviation,
                               s.config as strategy_config,
                               s.name as strategy_name,
                               NOW() as db_now,
                               TIMESTAMPDIFF(SECOND, o.created_at, NOW()) as elapsed_seconds
                        FROM futures_orders o
                        LEFT JOIN trading_strategies s ON CAST(o.strategy_id AS UNSIGNED) = CAST(s.id AS UNSIGNED)
                        WHERE o.status = 'PENDING'
                        AND o.order_type = 'LIMIT'
                        AND o.side IN ('OPEN_LONG', 'OPEN_SHORT')
                        ORDER BY o.created_at ASC"""
                    )
                    pending_orders = cursor.fetchall()
                
                if not pending_orders:
                    return
                
                for order in pending_orders:
                    try:
                        order_id = order['order_id']
                        account_id = order['account_id']
                        symbol = order['symbol']
                        side = order['side']  # OPEN_LONG 或 OPEN_SHORT
                        limit_price = Decimal(str(order['price']))
                        quantity = Decimal(str(order['quantity']))
                        leverage = order.get('leverage', 1)
                        stop_loss_price = Decimal(str(order['stop_loss_price'])) if order.get('stop_loss_price') else None
                        take_profit_price = Decimal(str(order['take_profit_price'])) if order.get('take_profit_price') else None
                        
                        # 获取当前价格（限价单扫描使用实时价格）
                        current_price = self.get_current_price(symbol, use_realtime=True)

                        if current_price == 0:
                            logger.warning(f"无法获取 {symbol} 的价格，跳过订单 {order_id}")
                            continue

                        # 检查是否达到触发条件
                        should_execute = False
                        position_side = 'LONG' if side == 'OPEN_LONG' else 'SHORT'

                        # ===== 趋势转向检测：在趋势转向时取消限价单 =====
                        trend_reversal_reason = self._check_trend_reversal(connection, order)
                        if trend_reversal_reason:
                            logger.info(f"📉 限价单趋势转向取消: {symbol} {position_side} - {trend_reversal_reason}")

                            # 解冻保证金
                            frozen_margin = Decimal(str(order.get('margin', 0)))
                            if frozen_margin > 0:
                                with connection.cursor() as update_cursor:
                                    update_cursor.execute(
                                        """UPDATE paper_trading_accounts
                                        SET current_balance = current_balance + %s,
                                            frozen_balance = GREATEST(0, frozen_balance - %s)
                                        WHERE id = %s""",
                                        (float(frozen_margin), float(frozen_margin), account_id)
                                    )

                            # 更新订单状态为已取消
                            with connection.cursor() as update_cursor:
                                update_cursor.execute(
                                    """UPDATE futures_orders
                                    SET status = 'CANCELLED',
                                        cancellation_reason = 'trend_reversal',
                                        canceled_at = NOW(),
                                        notes = CONCAT(COALESCE(notes, ''), ' TREND_REVERSAL: ', %s)
                                    WHERE order_id = %s""",
                                    (trend_reversal_reason, order_id)
                                )

                            connection.commit()

                            # ========== 同步取消实盘订单 (趋势转向) ==========
                            try:
                                strategy_config = order.get('strategy_config')
                                if strategy_config and self.live_engine:
                                    config = strategy_config
                                    if isinstance(config, str):
                                        try:
                                            config = json.loads(config)
                                        except:
                                            config = {}

                                    sync_live = config.get('syncLive', False)
                                    if sync_live:
                                        # 查找对应的实盘PENDING持仓
                                        with connection.cursor() as cursor:
                                            cursor.execute("""
                                                SELECT id, binance_order_id
                                                FROM live_futures_positions
                                                WHERE symbol = %s
                                                  AND position_side = %s
                                                  AND status = 'PENDING'
                                                ORDER BY created_at DESC
                                                LIMIT 1
                                            """, (symbol, position_side))
                                            live_pos = cursor.fetchone()

                                        if live_pos and live_pos.get('binance_order_id'):
                                            binance_order_id = live_pos['binance_order_id']
                                            logger.info(f"[同步实盘] 取消限价单(趋势转向): {symbol} {position_side}, 订单ID={binance_order_id}")

                                            # 调用实盘引擎取消订单
                                            cancel_result = self.live_engine.cancel_order(
                                                symbol=symbol,
                                                order_id=binance_order_id
                                            )

                                            if cancel_result.get('success'):
                                                # 更新实盘持仓状态
                                                with connection.cursor() as cursor:
                                                    cursor.execute("""
                                                        UPDATE live_futures_positions
                                                        SET status = 'CANCELED',
                                                            close_reason = 'trend_reversal',
                                                            close_time = NOW(),
                                                            notes = CONCAT(COALESCE(notes, ''), ' SYNCED_CANCEL_TREND_REVERSAL')
                                                        WHERE id = %s
                                                    """, (live_pos['id'],))
                                                connection.commit()
                                                logger.info(f"[同步实盘] ✅ 限价单已取消(趋势转向): {symbol} {position_side}")
                                            else:
                                                error_msg = cancel_result.get('error', cancel_result.get('message', '未知错误'))
                                                logger.error(f"[同步实盘] ❌ 取消限价单失败(趋势转向): {symbol} {position_side} - {error_msg}")
                            except Exception as sync_ex:
                                logger.error(f"[同步实盘] ❌ 取消限价单异常(趋势转向): {symbol} {position_side} - {sync_ex}")
                            # ========== 同步取消实盘订单结束 ==========

                            continue  # 跳过此订单

                        # 检查超时转市价（从策略配置中读取）
                        strategy_timeout_raw = order.get('strategy_timeout')
                        try:
                            timeout_minutes = int(strategy_timeout_raw) if strategy_timeout_raw else 0
                        except (ValueError, TypeError):
                            timeout_minutes = 0

                        if timeout_minutes > 0:
                            # 使用数据库计算的时间差，避免时区问题
                            elapsed_seconds = order.get('elapsed_seconds', 0) or 0
                            elapsed_minutes = elapsed_seconds / 60
                            timeout_seconds = timeout_minutes * 60

                            if elapsed_seconds >= timeout_seconds:
                                # ========== 限价单超时：直接取消，不转自检 ==========
                                logger.info(f"⏰ 限价单超时取消: {symbol} {position_side} 已等待 {elapsed_minutes:.1f} 分钟, 限价={limit_price}, 当前={current_price}")

                                # 解冻保证金
                                frozen_margin = Decimal(str(order.get('margin', 0)))
                                if frozen_margin > 0:
                                    with connection.cursor() as update_cursor:
                                        update_cursor.execute(
                                            """UPDATE paper_trading_accounts
                                            SET current_balance = current_balance + %s,
                                                frozen_balance = GREATEST(0, frozen_balance - %s)
                                            WHERE id = %s""",
                                            (float(frozen_margin), float(frozen_margin), account_id)
                                        )

                                # 更新限价单状态为 EXPIRED（超时取消）
                                with connection.cursor() as update_cursor:
                                    update_cursor.execute(
                                        """UPDATE futures_orders
                                        SET status = 'EXPIRED',
                                            cancellation_reason = 'timeout',
                                            canceled_at = NOW(),
                                            notes = CONCAT(COALESCE(notes, ''), ' TIMEOUT_CANCELLED')
                                        WHERE order_id = %s""",
                                        (order_id,)
                                    )

                                connection.commit()

                                # 同步取消实盘限价单
                                try:
                                    strategy_config = order.get('strategy_config')
                                    if strategy_config and self.live_engine:
                                        config = strategy_config
                                        if isinstance(config, str):
                                            try:
                                                config = json.loads(config)
                                            except:
                                                config = {}

                                        sync_live = config.get('syncLive', False) if isinstance(config, dict) else False
                                        if sync_live:
                                            # 查找对应的实盘PENDING持仓
                                            with connection.cursor() as cursor:
                                                cursor.execute("""
                                                    SELECT id, binance_order_id
                                                    FROM live_futures_positions
                                                    WHERE symbol = %s
                                                      AND position_side = %s
                                                      AND status = 'PENDING'
                                                    ORDER BY created_at DESC
                                                    LIMIT 1
                                                """, (symbol, position_side))
                                                live_pos = cursor.fetchone()

                                            if live_pos and live_pos.get('binance_order_id'):
                                                binance_order_id = live_pos['binance_order_id']
                                                logger.info(f"[同步实盘] 取消限价单(超时): {symbol} {position_side}, 订单ID={binance_order_id}")

                                                cancel_result = self.live_engine.cancel_order(
                                                    symbol=symbol,
                                                    order_id=binance_order_id
                                                )

                                                if cancel_result.get('success'):
                                                    with connection.cursor() as cursor:
                                                        cursor.execute("""
                                                            UPDATE live_futures_positions
                                                            SET status = 'CANCELED',
                                                                close_reason = 'timeout',
                                                                close_time = NOW(),
                                                                notes = CONCAT(COALESCE(notes, ''), ' SYNCED_CANCEL_TIMEOUT')
                                                            WHERE id = %s
                                                        """, (live_pos['id'],))
                                                    connection.commit()
                                                    logger.info(f"[同步实盘] ✅ 限价单已取消(超时): {symbol} {position_side}")
                                                else:
                                                    error_msg = cancel_result.get('error', cancel_result.get('message', '未知错误'))
                                                    logger.error(f"[同步实盘] ❌ 取消限价单失败(超时): {symbol} {position_side} - {error_msg}")
                                except Exception as sync_ex:
                                    logger.error(f"[同步实盘] ❌ 取消限价单异常(超时): {symbol} {position_side} - {sync_ex}")

                                continue  # 跳过此订单，已超时取消

                        # 如果没有超时，检查价格是否达到限价条件
                        if not should_execute:
                            if side == 'OPEN_LONG':
                                # 做多：当前价格 <= 限价时触发
                                if current_price <= limit_price:
                                    should_execute = True
                                    logger.info(f"✅ 做多限价单触发: {symbol} @ {current_price} <= {limit_price}")
                            elif side == 'OPEN_SHORT':
                                # 做空：当前价格 >= 限价时触发
                                if current_price >= limit_price:
                                    should_execute = True
                                    logger.info(f"✅ 做空限价单触发: {symbol} @ {current_price} >= {limit_price}")
                        
                        if should_execute:
                            # ===== RSI过滤检查：限价单触发前检查RSI是否超买/超卖 =====
                            rsi_rejection_reason = self._check_rsi_filter(connection, order)
                            if rsi_rejection_reason:
                                logger.info(f"📊 限价单RSI过滤取消: {symbol} {position_side} - {rsi_rejection_reason}")

                                # 解冻保证金
                                frozen_margin = Decimal(str(order.get('margin', 0)))
                                if frozen_margin > 0:
                                    with connection.cursor() as update_cursor:
                                        update_cursor.execute(
                                            """UPDATE paper_trading_accounts
                                            SET current_balance = current_balance + %s,
                                                frozen_balance = GREATEST(0, frozen_balance - %s)
                                            WHERE id = %s""",
                                            (float(frozen_margin), float(frozen_margin), account_id)
                                        )

                                # 更新订单状态为已取消
                                with connection.cursor() as update_cursor:
                                    update_cursor.execute(
                                        """UPDATE futures_orders
                                        SET status = 'CANCELLED',
                                            cancellation_reason = 'rsi_filter',
                                            canceled_at = NOW(),
                                            notes = CONCAT(COALESCE(notes, ''), ' RSI_FILTER: ', %s)
                                        WHERE order_id = %s""",
                                        (rsi_rejection_reason, order_id)
                                    )

                                connection.commit()

                                # ========== 同步取消实盘订单 (RSI过滤) ==========
                                try:
                                    strategy_config = order.get('strategy_config')
                                    if strategy_config and self.live_engine:
                                        config = strategy_config
                                        if isinstance(config, str):
                                            try:
                                                config = json.loads(config)
                                            except:
                                                config = {}

                                        sync_live = config.get('syncLive', False) if isinstance(config, dict) else False
                                        if sync_live:
                                            # 查找对应的实盘PENDING持仓
                                            with connection.cursor() as cursor:
                                                cursor.execute("""
                                                    SELECT id, binance_order_id
                                                    FROM live_futures_positions
                                                    WHERE symbol = %s
                                                      AND position_side = %s
                                                      AND status = 'PENDING'
                                                    ORDER BY created_at DESC
                                                    LIMIT 1
                                                """, (symbol, position_side))
                                                live_pos = cursor.fetchone()

                                            if live_pos and live_pos.get('binance_order_id'):
                                                binance_order_id = live_pos['binance_order_id']
                                                logger.info(f"[同步实盘] 取消限价单(RSI过滤): {symbol} {position_side}, 订单ID={binance_order_id}")

                                                # 调用实盘引擎取消订单
                                                cancel_result = self.live_engine.cancel_order(
                                                    symbol=symbol,
                                                    order_id=binance_order_id
                                                )

                                                if cancel_result.get('success'):
                                                    # 更新实盘持仓状态
                                                    with connection.cursor() as cursor:
                                                        cursor.execute("""
                                                            UPDATE live_futures_positions
                                                            SET status = 'CANCELED',
                                                                close_reason = 'rsi_filter',
                                                                close_time = NOW(),
                                                                notes = CONCAT(COALESCE(notes, ''), ' SYNCED_CANCEL_RSI_FILTER')
                                                            WHERE id = %s
                                                        """, (live_pos['id'],))
                                                    connection.commit()
                                                    logger.info(f"[同步实盘] ✅ 限价单已取消(RSI过滤): {symbol} {position_side}")
                                                else:
                                                    error_msg = cancel_result.get('error', cancel_result.get('message', '未知错误'))
                                                    logger.error(f"[同步实盘] ❌ 取消限价单失败(RSI过滤): {symbol} {position_side} - {error_msg}")
                                except Exception as sync_ex:
                                    logger.error(f"[同步实盘] ❌ 取消限价单异常(RSI过滤): {symbol} {position_side} - {sync_ex}")
                                # ========== 同步取消实盘订单结束 ==========

                                continue  # 跳过此订单

                            # 执行开仓（使用限价作为成交价）
                            try:
                                # 先解冻保证金（因为限价单创建时已经冻结了保证金）
                                # 开仓时会重新冻结，所以这里先解冻避免重复冻结
                                frozen_margin = Decimal(str(order.get('margin', 0)))
                                if frozen_margin > 0:
                                    with connection.cursor() as update_cursor:
                                        update_cursor.execute(
                                            """UPDATE paper_trading_accounts
                                            SET current_balance = current_balance + %s,
                                                frozen_balance = GREATEST(0, frozen_balance - %s)
                                            WHERE id = %s""",
                                            (float(frozen_margin), float(frozen_margin), account_id)
                                        )

                                # 提交解冻操作
                                connection.commit()
                                
                                # 执行开仓
                                # 保留原始订单的来源、信号ID和策略ID（如果是策略订单）
                                original_source = order.get('order_source', 'limit_order')
                                original_signal_id = order.get('signal_id')
                                original_strategy_id = order.get('strategy_id')

                                # 限价单触发：以市价执行
                                # 限价只是触发条件，实际成交价为市价
                                # 止损止盈已基于限价计算好，直接传入价格
                                execution_price = current_price  # 实际成交价为市价

                                result = self.trading_engine.open_position(
                                    account_id=account_id,
                                    symbol=symbol,
                                    position_side=position_side,
                                    quantity=quantity,
                                    leverage=leverage,
                                    limit_price=None,  # 不传限价，直接执行，避免再创建PENDING
                                    stop_loss_price=stop_loss_price,  # 已基于限价计算好
                                    take_profit_price=take_profit_price,  # 已基于限价计算好
                                    source=original_source,  # 保留原始来源（strategy 或 limit_order）
                                    signal_id=original_signal_id,  # 保留原始信号ID
                                    strategy_id=original_strategy_id  # 保留原始策略ID（用于实盘同步）
                                )

                                if result.get('success'):
                                    # 从结果中获取实际的 symbol 和 position_id
                                    actual_symbol = result.get('symbol', symbol)
                                    paper_position_id = result.get('position_id')

                                    # 验证 symbol 是否匹配
                                    if actual_symbol != symbol:
                                        logger.warning(f"⚠️  限价单 {order_id} symbol 不匹配: 订单中为 {symbol}, 开仓结果为 {actual_symbol}")

                                    # 计算已成交价值（基于限价）
                                    executed_value = float(execution_price * quantity)

                                    # 更新订单状态为已成交
                                    with connection.cursor() as update_cursor:
                                        update_cursor.execute(
                                            """UPDATE futures_orders
                                            SET status = 'FILLED',
                                                executed_quantity = %s,
                                                executed_value = %s,
                                                avg_fill_price = %s,
                                                fill_time = NOW()
                                            WHERE order_id = %s""",
                                            (float(quantity), executed_value, float(execution_price), order_id)
                                        )

                                    connection.commit()

                                    logger.info(f"✅ 限价单执行成功: {symbol} {position_side} {quantity} @ {execution_price} (触发价:{limit_price})")

                                    # ========== 同步实盘交易 ==========
                                    # 检查策略是否启用实盘同步
                                    # 重要：只有模拟盘真正创建了持仓才同步，避免重复同步
                                    if not paper_position_id:
                                        logger.warning(f"⚠️ 模拟盘未创建持仓（可能被防重复开仓拦截），跳过实盘同步: {symbol} {position_side}")
                                    else:
                                        try:
                                            strategy_config = order.get('strategy_config')
                                            if strategy_config and self.live_engine:
                                                # 解析策略配置
                                                config = strategy_config
                                                parse_attempts = 0
                                                while isinstance(config, str) and parse_attempts < 3:
                                                    try:
                                                        config = json.loads(config)
                                                        parse_attempts += 1
                                                    except json.JSONDecodeError:
                                                        break

                                                if isinstance(config, dict):
                                                    sync_live = config.get('syncLive', False)
                                                    live_quantity_pct = config.get('liveQuantityPct', 10)
                                                    live_max_position_usdt = config.get('liveMaxPositionUsdt', 100)

                                                    if sync_live:
                                                        # 获取实盘可用余额
                                                        live_balance = self.live_engine.get_account_balance()
                                                        live_available = float(live_balance.get('available', 0))

                                                        # 计算实盘保证金和数量（使用 float 避免类型错误）
                                                        live_margin_to_use = live_available * (float(live_quantity_pct) / 100)
                                                        if live_margin_to_use > float(live_max_position_usdt):
                                                            logger.info(f"[同步实盘] {symbol} 保证金限制: {live_margin_to_use:.2f} USDT 超过上限 {live_max_position_usdt:.2f} USDT")
                                                            live_margin_to_use = float(live_max_position_usdt)

                                                        # 计算实盘数量 (确保所有类型都是Decimal)
                                                        execution_price_decimal = Decimal(str(execution_price))
                                                        live_quantity = Decimal(str(live_margin_to_use)) * Decimal(str(leverage)) / execution_price_decimal

                                                        logger.info(f"[同步实盘] {symbol} {position_side} 开始同步: 实盘可用={live_available:.2f} USDT, 使用{live_quantity_pct}%={live_margin_to_use:.2f} USDT, 数量={live_quantity}")

                                                        # 计算止损止盈百分比 (确保所有类型都是Decimal)
                                                        stop_loss_pct_value = None
                                                        take_profit_pct_value = None

                                                        if stop_loss_price:
                                                            stop_loss_decimal = Decimal(str(stop_loss_price))
                                                            # 止损百分比应该是正数
                                                            # LONG: 止损价 < 入场价, 百分比 = (入场价 - 止损价) / 入场价
                                                            # SHORT: 止损价 > 入场价, 百分比 = (止损价 - 入场价) / 入场价
                                                            if position_side == 'LONG':
                                                                stop_loss_pct_value = (execution_price_decimal - stop_loss_decimal) / execution_price_decimal * Decimal('100')
                                                            else:
                                                                stop_loss_pct_value = (stop_loss_decimal - execution_price_decimal) / execution_price_decimal * Decimal('100')

                                                        if take_profit_price:
                                                            take_profit_decimal = Decimal(str(take_profit_price))
                                                            if position_side == 'LONG':
                                                                take_profit_pct_value = (take_profit_decimal - execution_price_decimal) / execution_price_decimal * Decimal('100')
                                                            else:
                                                                take_profit_pct_value = (execution_price_decimal - take_profit_decimal) / execution_price_decimal * Decimal('100')

                                                        # 调用实盘引擎开仓（市价执行，止损止盈基于限价计算）
                                                        live_result = self.live_engine.open_position(
                                                            account_id=2,
                                                            symbol=symbol,
                                                            position_side=position_side,
                                                            quantity=live_quantity,
                                                            leverage=leverage,
                                                            limit_price=None,  # 市价执行
                                                            stop_loss_pct=stop_loss_pct_value,
                                                            take_profit_pct=take_profit_pct_value,
                                                            source='limit_order_sync',
                                                            strategy_id=order.get('strategy_id')
                                                        )

                                                        if live_result.get('success'):
                                                            live_position_id = live_result.get('position_id')
                                                            logger.info(f"[同步实盘] ✅ {symbol} {position_side} 成功: 数量={live_quantity}, 价格={execution_price}, 实盘持仓ID={live_position_id}")

                                                            # 更新模拟盘持仓记录，关联实盘持仓ID
                                                            if paper_position_id and live_position_id:
                                                                try:
                                                                    with connection.cursor() as update_cursor:
                                                                        update_cursor.execute("""
                                                                            UPDATE futures_positions
                                                                            SET live_position_id = %s
                                                                            WHERE id = %s
                                                                        """, (live_position_id, paper_position_id))
                                                                    connection.commit()
                                                                    logger.info(f"[同步实盘] 已关联模拟盘持仓 {paper_position_id} -> 实盘 {live_position_id}")
                                                                except Exception as db_ex:
                                                                    logger.warning(f"[同步实盘] 更新关联ID失败: {db_ex}")
                                                        else:
                                                            live_error = live_result.get('error', live_result.get('message', '未知错误'))
                                                            logger.error(f"[同步实盘] ❌ {symbol} {position_side} 失败: {live_error}")
                                        except Exception as live_ex:
                                            logger.error(f"[同步实盘] ❌ {symbol} {position_side} 异常: {live_ex}")
                                    # ========== 同步实盘交易结束 ==========

                                else:
                                    # 如果开仓失败，恢复冻结的保证金并取消订单
                                    error_message = result.get('message', '未知错误')
                                    logger.error(f"❌ 限价单执行失败: {symbol} {position_side} - {error_message}")

                                    # 取消订单，避免无限重试
                                    with connection.cursor() as update_cursor:
                                        update_cursor.execute(
                                            """UPDATE futures_orders
                                            SET status = 'CANCELED',
                                                cancellation_reason = %s,
                                                canceled_at = NOW()
                                            WHERE order_id = %s""",
                                            (f"执行失败: {error_message[:100]}", order_id)
                                        )

                                        # 恢复冻结的保证金到可用余额
                                        if frozen_margin > 0:
                                            update_cursor.execute(
                                                """UPDATE paper_trading_accounts
                                                SET frozen_balance = GREATEST(0, frozen_balance - %s)
                                                WHERE id = %s""",
                                                (float(frozen_margin), account_id)
                                            )
                                    connection.commit()
                                    logger.info(f"📛 已取消限价单 {order_id}，原因: {error_message}")
                                    
                            except Exception as e:
                                logger.error(f"执行限价单 {order_id} 时出错: {e}")
                                import traceback
                                traceback.print_exc()
                                # 如果出错，尝试恢复冻结的保证金
                                try:
                                    frozen_margin = Decimal(str(order.get('margin', 0)))
                                    if frozen_margin > 0:
                                        with connection.cursor() as update_cursor:
                                            update_cursor.execute(
                                                """UPDATE paper_trading_accounts
                                                SET current_balance = current_balance - %s,
                                                    frozen_balance = frozen_balance + %s
                                                WHERE id = %s""",
                                                (float(frozen_margin), float(frozen_margin), account_id)
                                            )
                                        connection.commit()
                                except:
                                    pass
                                continue
                        else:
                            pass
                            
                    except Exception as e:
                        logger.error(f"处理限价单 {order.get('order_id', 'unknown')} 时出错: {e}")
                        import traceback
                        traceback.print_exc()
                        continue
            finally:
                connection.close()
                
        except Exception as e:
            logger.error(f"检查合约限价单时出错: {e}")
            import traceback
            traceback.print_exc()
    
    async def run_loop(self, interval: int = 5):
        """
        运行监控循环
        
        Args:
            interval: 检查间隔（秒），默认5秒
        """
        self.running = True
        logger.info(f"🔄 合约限价单自动执行服务已启动（间隔: {interval}秒）")
        
        try:
            while self.running:
                try:
                    await self.check_and_execute_limit_orders()
                except Exception as e:
                    logger.error(f"合约限价单执行循环出错: {e}")
                
                # 等待指定间隔
                try:
                    await asyncio.sleep(interval)
                except asyncio.CancelledError:
                    logger.info("合约限价单执行服务收到取消信号")
                    break
        except asyncio.CancelledError:
            logger.info("合约限价单执行服务已取消")
            raise
    
    def start(self, interval: int = 5):
        """
        启动后台任务
        
        Args:
            interval: 检查间隔（秒），默认5秒
        """
        if self.running:
            logger.warning("合约限价单执行器已在运行")
            return
        
        # 获取或创建事件循环
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        self.task = loop.create_task(self.run_loop(interval))
    
    def stop(self):
        """停止后台任务"""
        self.running = False
        if self.task and not self.task.done():
            self.task.cancel()
            logger.debug("⏹️  合约限价单自动执行服务已停止")
        
        # 关闭数据库连接
        if self.connection and self.connection.open:
            try:
                self.connection.close()
                # 静默关闭，不打印日志
            except Exception as e:
                logger.warning(f"关闭数据库连接时出错: {e}")
            finally:
                self.connection = None

