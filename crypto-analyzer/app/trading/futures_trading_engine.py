"""
模拟合约交易引擎
支持多空双向交易、杠杆、止盈止损
"""

import uuid
import time
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from loguru import logger
import pymysql

from app.utils.futures_symbol import futures_symbol_rating_canonical

def get_quantity_precision(symbol: str) -> int:
    """
    根据交易对获取数量精度（小数位数）
    
    Args:
        symbol: 交易对，如 'PUMP/USDT', 'DOGE/USDT'
    
    Returns:
        数量精度（小数位数）
    """
    symbol_upper = symbol.upper().replace('/', '')
    # PUMP/USDT 和 DOGE/USDT 保持8位小数
    if 'PUMP' in symbol_upper or 'DOGE' in symbol_upper:
        return 8
    # 其他交易对默认8位小数（数据库字段支持）
    return 8

def round_quantity(quantity: Decimal, symbol: str) -> Decimal:
    """
    根据交易对精度对数量进行四舍五入
    
    Args:
        quantity: 数量
        symbol: 交易对
    
    Returns:
        四舍五入后的数量
    """
    precision = get_quantity_precision(symbol)
    # 使用 quantize 进行精度控制
    from decimal import ROUND_HALF_UP
    quantize_str = '0.' + '0' * precision
    return quantity.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)


class FuturesTradingEngine:
    """模拟合约交易引擎"""

    @staticmethod
    def calculate_ema(prices: list, period: int) -> float:
        """
        计算EMA（指数移动平均线）

        Args:
            prices: 价格列表（从旧到新）
            period: EMA周期

        Returns:
            EMA值
        """
        if not prices or len(prices) < period:
            return 0.0

        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period  # 初始SMA

        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema

        return ema

    def __init__(self, db_config: dict, trade_notifier=None, live_engine=None):
        """初始化合约交易引擎

        Args:
            db_config: 数据库配置
            trade_notifier: TradeNotifier实例（可选）
            live_engine: 实盘交易引擎实例（可选，用于同步平仓）
        """
        self.db_config = db_config
        self.connection = None
        self._is_first_connection = True  # 标记是否是首次连接
        self._connection_created_at = None  # 连接创建时间（Unix时间戳）
        self._connection_max_age = 300  # 连接最大存活时间（秒），5分钟
        self.trade_notifier = trade_notifier  # TG通知器
        self.live_engine = live_engine  # 实盘引擎（用于同步平仓）
        self._connect_db()

    def _connect_db(self, is_reconnect=False):
        """连接数据库"""
        try:
            # 关闭旧连接
            if self.connection and self.connection.open:
                try:
                    self.connection.close()
                except:
                    pass
            
            self.connection = pymysql.connect(
                host=self.db_config.get('host', 'localhost'),
                port=self.db_config.get('port', 3306),
                user=self.db_config.get('user', 'root'),
                password=self.db_config.get('password', ''),
                database=self.db_config.get('database', 'binance-data'),
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True  # 启用自动提交，确保每次操作立即生效
            )
            self._connection_created_at = time.time()  # 记录连接创建时间
            
            if self._is_first_connection:
                logger.info("合约交易引擎数据库连接成功")
                self._is_first_connection = False
            elif is_reconnect:
                # 重连时使用DEBUG级别，避免频繁打印
                logger.debug("合约交易引擎数据库连接已重新建立")
        except Exception as e:
            logger.error(f"数据库连接失败: {e}")
            raise

    def _should_refresh_connection(self):
        """检查是否需要刷新连接（基于连接年龄）"""
        if self._connection_created_at is None:
            return True

        current_time = time.time()
        connection_age = current_time - self._connection_created_at

        # 如果连接年龄超过最大存活时间，需要刷新
        return connection_age > self._connection_max_age

    def get_ema_diff(self, symbol: str, timeframe: str = '15m') -> Optional[float]:
        """
        获取当前 EMA9 - EMA26 的差值

        Args:
            symbol: 交易对
            timeframe: K线周期，默认15分钟

        Returns:
            EMA9 - EMA26 的差值，无法计算时返回 None
        """
        try:
            cursor = self._get_cursor()
            # 获取最近30根K线（确保有足够数据计算EMA26）
            cursor.execute("""
                SELECT close_price
                FROM kline_data
                WHERE symbol = %s AND timeframe = %s
                ORDER BY timestamp DESC
                LIMIT 30
            """, (symbol, timeframe))

            rows = cursor.fetchall()
            if not rows or len(rows) < 26:
                logger.warning(f"[EMA差值] {symbol} K线数据不足，无法计算EMA")
                return None

            # 转换为从旧到新的价格列表
            prices = [float(row['close_price']) for row in reversed(rows)]

            ema9 = self.calculate_ema(prices, 9)
            ema26 = self.calculate_ema(prices, 26)

            if ema9 == 0 or ema26 == 0:
                return None

            ema_diff = ema9 - ema26
            logger.debug(f"[EMA差值] {symbol}: EMA9={ema9:.6f}, EMA26={ema26:.6f}, 差值={ema_diff:.6f}")
            return ema_diff

        except Exception as e:
            logger.error(f"[EMA差值] 计算失败: {e}")
            return None

    def _get_cursor(self):
        """获取数据库游标"""
        try:
            # 检查连接年龄，如果超过最大存活时间则主动刷新
            if self._should_refresh_connection():
                logger.debug("连接已过期，主动刷新数据库连接")
                self._connect_db(is_reconnect=True)
            
            if not self.connection or not self.connection.open:
                # 静默检查连接，如果断开则重连
                try:
                    if self.connection:
                        self.connection.ping(reconnect=True)
                except:
                    # 如果ping失败，重新连接
                    self._connect_db(is_reconnect=True)
            else:
                # 即使连接看起来正常，也尝试ping一下确保连接有效
                try:
                    self.connection.ping(reconnect=False)
                except:
                    # ping失败，重新连接
                    logger.debug("连接ping失败，重新建立连接")
                    self._connect_db(is_reconnect=True)
            
            return self.connection.cursor()
        except Exception as e:
            logger.error(f"获取数据库游标失败: {e}")
            # 如果获取游标失败，尝试重新连接
            try:
                self._connect_db(is_reconnect=True)
                return self.connection.cursor()
            except:
                raise

    def get_current_price(self, symbol: str, use_realtime: bool = False) -> Decimal:
        """
        获取当前市场价格

        Args:
            symbol: 交易对
            use_realtime: 是否使用实时API价格（市价单时使用）

        Returns:
            当前价格
        """
        # 如果要求使用实时价格, 走 BinanceDataHub (WS / 60s ticker 缓存 / DB / 限速 REST)
        if use_realtime:
            try:
                from app.services.binance_data_hub import get_global_data_hub
                hub = get_global_data_hub()
                if hub is not None:
                    p = hub.get_price_sync(symbol)
                    if p is not None and p > 0:
                        return p
                logger.warning(f"DataHub 未取到 {symbol} 实时价, 回退到数据库 K 线缓存")
            except Exception as e:
                logger.warning(f"DataHub 取价异常, 回退到数据库缓存: {symbol}, {e}")
        
        # 从数据库获取缓存价格（默认行为）
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
            from app.utils.futures_symbol import futures_symbol_kline_keys

            cursor = connection.cursor()
            kline_keys = futures_symbol_kline_keys(symbol)
            # 多周期 K 线回退（与币本位引擎一致；1m 可能已停采）
            for db_sym in kline_keys:
                for tf in ('5m', '15m', '1h', '1m'):
                    cursor.execute(
                        """SELECT close_price FROM kline_data
                        WHERE symbol = %s AND timeframe = %s
                        ORDER BY open_time DESC LIMIT 1""",
                        (db_sym, tf),
                    )
                    result = cursor.fetchone()
                    if result and result['close_price']:
                        price = Decimal(str(result['close_price']))
                        cursor.close()
                        return price

            # 回退到价格表
            for db_sym in kline_keys:
                cursor.execute(
                    """SELECT price FROM price_data
                    WHERE symbol = %s
                    ORDER BY timestamp DESC LIMIT 1""",
                    (db_sym,)
                )
                result = cursor.fetchone()
                if result and result['price']:
                    cursor.close()
                    return Decimal(str(result['price']))
            cursor.close()

            raise ValueError(f"无法获取{symbol}的价格")
        except Exception as e:
            # 无行情/K线时由调用方降级为 mark/entry，此处仅记 warning 避免与外层重复 ERROR
            if isinstance(e, ValueError) and "无法获取" in str(e):
                logger.warning(f"获取价格失败: {e}")
            else:
                logger.error(f"获取价格失败: {e}")
            raise
        finally:
            connection.close()

    def calculate_liquidation_price(
        self,
        entry_price: Decimal,
        position_side: str,
        leverage: int,
        maintenance_margin_rate: Decimal = Decimal('0.005')  # 0.5%维持保证金率
    ) -> Decimal:
        """
        计算强平价格

        Args:
            entry_price: 开仓价
            position_side: LONG 或 SHORT
            leverage: 杠杆倍数
            maintenance_margin_rate: 维持保证金率

        Returns:
            强平价格
        """
        if position_side == 'LONG':
            # 多头强平价 = 开仓价 * (1 - 1/杠杆 + 维持保证金率)
            liquidation_price = entry_price * (1 - Decimal('1')/Decimal(leverage) + maintenance_margin_rate)
        else:  # SHORT
            # 空头强平价 = 开仓价 * (1 + 1/杠杆 - 维持保证金率)
            liquidation_price = entry_price * (1 + Decimal('1')/Decimal(leverage) - maintenance_margin_rate)

        return liquidation_price

    def open_position(
        self,
        account_id: int,
        symbol: str,
        position_side: str,  # 'LONG' or 'SHORT'
        quantity: Decimal,
        leverage: int = 1,
        limit_price: Optional[Decimal] = None,
        stop_loss_pct: Optional[Decimal] = None,
        take_profit_pct: Optional[Decimal] = None,
        stop_loss_price: Optional[Decimal] = None,
        take_profit_price: Optional[Decimal] = None,
        source: str = 'manual',
        signal_id: Optional[int] = None,
        strategy_id: Optional[int] = None,
        entry_signal_type: Optional[str] = None,
        entry_reason: Optional[str] = None,
        entry_score: Optional[float] = None
    ) -> Dict:
        """
        开仓

        Args:
            account_id: 账户ID
            symbol: 交易对
            position_side: LONG(多头) 或 SHORT(空头)
            quantity: 开仓数量（币数）
            leverage: 杠杆倍数
            stop_loss_pct: 止损百分比（可选）
            take_profit_pct: 止盈百分比（可选）
            stop_loss_price: 止损价格（可选，优先于百分比）
            take_profit_price: 止盈价格（可选，优先于百分比）
            source: 来源
            signal_id: 信号ID
            strategy_id: 策略ID
            entry_signal_type: 开仓信号类型（如 golden_cross, death_cross, sustained_trend_FORWARD 等）
            entry_reason: 开仓原因详细说明

        Returns:
            开仓结果
        """
        symbol = futures_symbol_rating_canonical(symbol)
        try:
            cursor = self._get_cursor()
        except Exception as cursor_error:
            logger.error(f"获取数据库游标失败: {cursor_error}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                'success': False,
                'message': f"数据库连接失败: {str(cursor_error)}"
            }

        try:
            # 1. 获取当前价格
            # 限价单和市价单都使用实时价格（确保价格判断准确）
            use_realtime_for_entry = True
            try:
                current_price = self.get_current_price(symbol, use_realtime=use_realtime_for_entry)
                if not current_price or current_price <= 0:
                    raise ValueError(f"无法获取{symbol}的有效价格")
            except Exception as price_error:
                logger.error(f"获取价格失败: {price_error}")
                import traceback
                logger.error(traceback.format_exc())
                return {
                    'success': False,
                    'message': f"无法获取{symbol}的价格，请检查数据源或稍后重试。错误: {str(price_error)}"
                }

            # 1.5. 检查限价单逻辑
            from app.services.paper_limit_entry import (
                PAPER_ACCOUNT_ID,
                PAPER_LIMIT_TIMEOUT_MINUTES,
                calc_paper_limit_price,
                is_paper_limit_entry_enabled,
            )
            paper_limit_mode = account_id == PAPER_ACCOUNT_ID and is_paper_limit_entry_enabled()
            if paper_limit_mode and (not limit_price or limit_price <= 0):
                limit_price = Decimal(str(calc_paper_limit_price(position_side, float(current_price))))

            logger.info(f"[开仓] {symbol} {position_side} 收到 limit_price={limit_price}, current_price={current_price}")
            # 如果设置了限价，检查是否需要创建未成交订单
            if limit_price and limit_price > 0:
                should_create_pending_order = False
                if paper_limit_mode:
                    # 模拟盘：始终挂限价单，不直接成交
                    should_create_pending_order = True
                elif position_side == 'LONG':
                    # 做多：当前价格高于限价，则创建未成交订单
                    if current_price > limit_price:
                        should_create_pending_order = True
                else:  # SHORT
                    # 做空：当前价格低于限价，则创建未成交订单
                    if current_price < limit_price:
                        should_create_pending_order = True
                
                if should_create_pending_order:
                    # 使用限价计算保证金
                    limit_notional_value = limit_price * quantity
                    limit_margin_required = limit_notional_value / Decimal(leverage)
                    limit_fee = limit_notional_value * Decimal('0.0004')
                    
                    # 计算止盈止损价格（基于限价）
                    limit_stop_loss_price = None
                    limit_take_profit_price = None
                    
                    # 处理止损价格：优先使用直接指定的价格，否则根据百分比计算
                    if stop_loss_price is None:
                        if stop_loss_pct:
                            if position_side == 'LONG':
                                limit_stop_loss_price = limit_price * (1 - stop_loss_pct / 100)
                            else:
                                limit_stop_loss_price = limit_price * (1 + stop_loss_pct / 100)
                        else:
                            limit_stop_loss_price = None
                    else:
                        limit_stop_loss_price = stop_loss_price
                    
                    # 处理止盈价格：优先使用直接指定的价格，否则根据百分比计算
                    if take_profit_price is None:
                        if take_profit_pct:
                            if position_side == 'LONG':
                                limit_take_profit_price = limit_price * (1 + take_profit_pct / 100)
                            else:
                                limit_take_profit_price = limit_price * (1 - take_profit_pct / 100)
                        else:
                            limit_take_profit_price = None
                    else:
                        limit_take_profit_price = take_profit_price
                    
                    # 检查账户余额
                    cursor.execute(
                        "SELECT current_balance, frozen_balance, total_equity FROM futures_trading_accounts WHERE id = %s",
                        (account_id,)
                    )
                    account = cursor.fetchone()
                    if not account:
                        return {
                            'success': False,
                            'message': f"账户 {account_id} 不存在"
                        }

                    current_balance = Decimal(str(account['current_balance']))
                    frozen_balance = Decimal(str(account.get('frozen_balance', 0) or 0))
                    total_equity = Decimal(str(account.get('total_equity', 0) or current_balance))
                    available_balance = current_balance - frozen_balance

                    # 检查最大仓位限制（单笔保证金不超过总权益的10%）
                    max_margin_allowed = total_equity * Decimal('0.1')
                    if limit_margin_required > max_margin_allowed:
                        return {
                            'success': False,
                            'message': f"保证金超过限制。单笔保证金 {limit_margin_required:.2f} USDT 超过总权益的10% ({max_margin_allowed:.2f} USDT)。总权益: {total_equity:.2f} USDT"
                        }

                    if available_balance < (limit_margin_required + limit_fee):
                        return {
                            'success': False,
                            'message': f"余额不足。需要: {limit_margin_required + limit_fee:.2f} USDT, 可用: {available_balance:.2f} USDT"
                        }
                    
                    # 创建未成交订单
                    order_id = f"FUT-{uuid.uuid4().hex[:16].upper()}"
                    side = f"OPEN_{position_side}"

                    # 限价单不冻结保证金，只在成交时扣除
                    # 只记录订单，不修改账户余额
                    import json as _json
                    order_notes = None
                    if paper_limit_mode:
                        order_notes = _json.dumps({
                            "timeout_minutes": PAPER_LIMIT_TIMEOUT_MINUTES,
                            "ref_price": float(current_price),
                            "margin": float(limit_margin_required),
                            "entry_reason": entry_reason,
                        }, ensure_ascii=False)
                    
                    # 创建订单记录（包含止盈止损、策略ID和开仓原因）
                    order_sql = """
                        INSERT INTO futures_orders (
                            account_id, order_id, symbol,
                            side, order_type, leverage,
                            price, quantity, executed_quantity,
                            margin, total_value, executed_value,
                            fee, fee_rate, status,
                            stop_loss_price, take_profit_price,
                            order_source, entry_signal_type, signal_id, strategy_id,
                            notes, created_at
                        ) VALUES (
                            %s, %s, %s,
                            %s, 'LIMIT', %s,
                            %s, %s, 0,
                            %s, %s, 0,
                            %s, %s, 'PENDING',
                            %s, %s,
                            %s, %s, %s, %s,
                            %s, %s
                        )
                    """

                    cursor.execute(order_sql, (
                        account_id, order_id, symbol,
                        side, leverage,
                        float(limit_price), float(quantity),
                        float(limit_margin_required), float(limit_notional_value),
                        float(limit_fee), float(Decimal('0.0004')),
                        float(limit_stop_loss_price) if limit_stop_loss_price else None,
                        float(limit_take_profit_price) if limit_take_profit_price else None,
                        source, entry_signal_type, signal_id, strategy_id,
                        order_notes, datetime.now()
                    ))
                    
                    # 更新总权益（限价单时还没有持仓，未实现盈亏为0）
                    cursor.execute(
                        """UPDATE futures_trading_accounts a
                        SET a.total_equity = a.current_balance + a.frozen_balance + COALESCE((
                            SELECT SUM(p.unrealized_pnl) 
                            FROM futures_positions p 
                            WHERE p.account_id = a.id AND p.status = 'open'
                        ), 0)
                        WHERE a.id = %s""",
                        (account_id,)
                    )
                    
                    self.connection.commit()

                    logger.info(
                        f"创建限价单: {symbol} {position_side} {quantity} @ {limit_price} "
                        f"(当前价格: {current_price}), 杠杆{leverage}x, "
                        f"止损: {limit_stop_loss_price}, 止盈: {limit_take_profit_price}"
                    )

                    return {
                        'success': True,
                        'order_id': order_id,
                        'symbol': symbol,
                        'position_side': position_side,
                        'quantity': float(quantity),
                        'limit_price': float(limit_price),
                        'current_price': float(current_price),
                        'leverage': leverage,
                        'margin': float(limit_margin_required),
                        'stop_loss_price': float(limit_stop_loss_price) if limit_stop_loss_price else None,
                        'take_profit_price': float(limit_take_profit_price) if limit_take_profit_price else None,
                        'order_type': 'LIMIT',
                        'status': 'PENDING',
                        'message': f"限价单已创建，等待价格达到 {limit_price} 时成交"
                    }
                # 如果限价单可以立即成交，继续执行下面的市价单逻辑

            if paper_limit_mode:
                logger.error(
                    f"[开仓] 模拟盘 {symbol} {position_side} 不应走市价成交路径 "
                    f"(limit_price={limit_price})"
                )
                return {
                    'success': False,
                    'message': '模拟盘仅支持限价挂单，请使用 paper_limit_entry.create_paper_limit_order',
                }

            # 2. 确定开仓价格
            # 限价单立即成交时使用市价，因为实际是按市价成交的
            # 只有PENDING限价单成交时才用限价（由futures_limit_order_executor处理）
            logger.info(f"🔍 {symbol} {position_side} 开仓价格确定: limit_price={limit_price}, current_price={current_price}")
            if limit_price and limit_price > 0:
                # 限价单立即成交：使用市价作为入场价（实际成交价）
                entry_price = current_price
                logger.info(f"📌 {symbol} {position_side} 限价单立即成交，使用市价开仓: entry_price={entry_price} (限价:{limit_price})")
            else:
                # 市价单：再次获取实时价格，确保使用最新价格开仓
                try:
                    realtime_price = self.get_current_price(symbol, use_realtime=True)
                    if realtime_price and realtime_price > 0:
                        entry_price = realtime_price
                        logger.info(f"✅ {symbol} {position_side} 市价单使用实时价格开仓: entry_price={entry_price}")
                    else:
                        entry_price = current_price
                        logger.warning(f"⚠️ {symbol} {position_side} 实时价格获取失败，使用缓存价格: entry_price={entry_price}")
                except Exception as e:
                    logger.warning(f"⚠️ {symbol} {position_side} 获取实时价格失败，使用之前获取的价格: entry_price={current_price}, error={e}")
                    entry_price = current_price
            
            # 根据交易对精度对数量进行四舍五入
            quantity = round_quantity(quantity, symbol)
            
            # 计算名义价值和所需保证金
            notional_value = entry_price * quantity
            margin_required = notional_value / Decimal(leverage)

            # 3. 计算手续费 (0.04%)
            fee_rate = Decimal('0.0004')
            fee = notional_value * fee_rate

            # 4. 检查账户余额（并保存变化前的余额信息）
            try:
                cursor.execute(
                    "SELECT current_balance, frozen_balance, total_equity FROM futures_trading_accounts WHERE id = %s",
                    (account_id,)
                )
                account = cursor.fetchone()
                if not account:
                    return {
                        'success': False,
                        'message': f"账户 {account_id} 不存在"
                    }

                # 计算可用余额 = 当前余额 - 冻结余额
                current_balance = Decimal(str(account['current_balance']))
                frozen_balance = Decimal(str(account.get('frozen_balance', 0) or 0))
                total_equity = Decimal(str(account.get('total_equity', 0) or current_balance))
                available_balance = current_balance - frozen_balance

                # 保存变化前的余额信息（用于资金管理记录）
                balance_before = float(current_balance)
                frozen_before = float(frozen_balance)
                available_before = float(available_balance)

                # 检查最大仓位限制（单笔保证金不超过总权益的10%）
                max_margin_allowed = total_equity * Decimal('0.1')
                if margin_required > max_margin_allowed:
                    return {
                        'success': False,
                        'message': f"保证金超过限制。单笔保证金 {margin_required:.2f} USDT 超过总权益的10% ({max_margin_allowed:.2f} USDT)。总权益: {total_equity:.2f} USDT"
                    }

                if available_balance < (margin_required + fee):
                    return {
                        'success': False,
                        'message': f"余额不足。需要: {margin_required + fee:.2f} USDT, 可用: {available_balance:.2f} USDT (总余额: {current_balance:.2f}, 冻结: {frozen_balance:.2f})"
                    }
            except Exception as balance_error:
                logger.error(f"检查账户余额失败: {balance_error}")
                import traceback
                logger.error(traceback.format_exc())
                return {
                    'success': False,
                    'message': f"检查账户余额失败: {str(balance_error)}"
                }

            # 5. 计算强平价和止盈止损价（使用限价或当前价格）
            liquidation_price = self.calculate_liquidation_price(
                entry_price, position_side, leverage
            )

            # 处理止损价格：优先使用直接指定的价格，否则根据百分比计算
            if stop_loss_price is None:
                if stop_loss_pct:
                    # 确保所有值都是 Decimal，避免 Decimal * float 的类型错误
                    sl_pct = Decimal(str(stop_loss_pct))
                    if position_side == 'LONG':
                        stop_loss_price = entry_price * (1 - sl_pct / 100)
                    else:
                        stop_loss_price = entry_price * (1 + sl_pct / 100)
                else:
                    stop_loss_price = None
            # 如果直接指定了止损价格，使用指定的价格

            # 处理止盈价格：优先使用直接指定的价格，否则根据百分比计算
            if take_profit_price is None:
                if take_profit_pct:
                    # 确保所有值都是 Decimal，避免 Decimal * float 的类型错误
                    tp_pct = Decimal(str(take_profit_pct))
                    if position_side == 'LONG':
                        take_profit_price = entry_price * (1 + tp_pct / 100)
                    else:
                        take_profit_price = entry_price * (1 - tp_pct / 100)
                else:
                    take_profit_price = None
            # 如果直接指定了止盈价格，使用指定的价格

            # 5.5. 计算开仓时的 EMA 差值（用于趋势反转检测）
            entry_ema_diff = self.get_ema_diff(symbol, '15m')
            if entry_ema_diff is not None:
                logger.info(f"[EMA差值] {symbol} {position_side} 开仓EMA差值: {entry_ema_diff:.6f}")

            # 6. 创建持仓记录
            position_sql = """
                INSERT INTO futures_positions (
                    account_id, symbol, position_side, leverage,
                    quantity, notional_value, margin,
                    entry_price, mark_price, liquidation_price,
                    stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct,
                    entry_ema_diff, entry_signal_type, entry_score, entry_reason,
                    open_time, source, signal_id, strategy_id, status
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, 'open'
                )
            """

            cursor.execute(position_sql, (
                account_id, symbol, position_side, leverage,
                float(quantity), float(notional_value), float(margin_required),
                float(entry_price), float(entry_price), float(liquidation_price),
                float(stop_loss_price) if stop_loss_price else None,
                float(take_profit_price) if take_profit_price else None,
                float(stop_loss_pct) if stop_loss_pct else None,
                float(take_profit_pct) if take_profit_pct else None,
                entry_ema_diff, entry_signal_type, entry_score, entry_reason,
                datetime.now(), source, signal_id, strategy_id
            ))

            position_id = cursor.lastrowid

            # 7. 创建开仓订单记录
            order_id = f"FUT-{uuid.uuid4().hex[:16].upper()}"
            side = f"OPEN_{position_side}"

            order_sql = """
                INSERT INTO futures_orders (
                    account_id, order_id, position_id, symbol,
                    side, order_type, leverage,
                    price, quantity, executed_quantity,
                    margin, total_value, executed_value,
                    fee, fee_rate, status,
                    avg_fill_price, fill_time,
                    order_source, signal_id, strategy_id
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, 'FILLED',
                    %s, %s,
                    %s, %s, %s
                )
            """

            # 确定订单类型：如果有限价且不等于当前价格，则为限价单，否则为市价单
            order_type = 'LIMIT' if (limit_price and limit_price > 0 and limit_price != current_price) else 'MARKET'

            cursor.execute(order_sql, (
                account_id, order_id, position_id, symbol,
                side, order_type, leverage,
                float(entry_price), float(quantity), float(quantity),
                float(margin_required), float(notional_value), float(notional_value),
                float(fee), float(fee_rate),
                float(entry_price), datetime.now(),
                source, signal_id, strategy_id
            ))

            # 8. 创建交易记录
            trade_id = f"T-{uuid.uuid4().hex[:16].upper()}"

            trade_sql = """
                INSERT INTO futures_trades (
                    account_id, order_id, position_id, trade_id,
                    symbol, side, price, quantity, notional_value,
                    leverage, margin, fee, fee_rate,
                    entry_price, trade_time
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s
                )
            """

            cursor.execute(trade_sql, (
                account_id, order_id, position_id, trade_id,
                symbol, side, float(entry_price), float(quantity), float(notional_value),
                leverage, float(margin_required), float(fee), float(fee_rate),
                float(entry_price), datetime.now()
            ))

            # 9. 更新账户余额
            # 手续费直接扣除，只冻结保证金
            new_balance = current_balance - margin_required - fee  # 扣除保证金和手续费
            cursor.execute(
                """UPDATE futures_trading_accounts
                SET current_balance = %s, frozen_balance = frozen_balance + %s
                WHERE id = %s""",
                (float(new_balance), float(margin_required), account_id)  # 只冻结保证金
            )

            # 获取变化后的余额信息（用于资金管理记录）
            balance_after = float(new_balance)
            frozen_after = float(frozen_balance + margin_required)  # 只冻结保证金
            available_after = balance_after - frozen_after

            # 10. 更新总权益（余额 + 冻结余额 + 持仓未实现盈亏）
            cursor.execute(
                """UPDATE futures_trading_accounts a
                SET a.total_equity = a.current_balance + a.frozen_balance + COALESCE((
                    SELECT SUM(p.unrealized_pnl) 
                    FROM futures_positions p 
                    WHERE p.account_id = a.id AND p.status = 'open'
                ), 0)
                WHERE a.id = %s""",
                (account_id,)
            )

            self.connection.commit()

            # 记录当前时间（本地时间）
            current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            # 根据交易对确定数量显示精度
            qty_precision = get_quantity_precision(symbol)
            logger.info(
                f"{current_time_str}: 开仓成功: {symbol} {position_side} {float(quantity):.{qty_precision}f} @ {entry_price}, "
                f"杠杆{leverage}x, 保证金{margin_required:.2f} USDT"
            )

            return {
                'success': True,
                'position_id': position_id,
                'order_id': order_id,
                'trade_id': trade_id,
                'symbol': symbol,
                'position_side': position_side,
                'quantity': float(quantity),
                'entry_price': float(entry_price),
                'leverage': leverage,
                'margin': float(margin_required),
                'fee': float(fee),
                'liquidation_price': float(liquidation_price),
                'stop_loss_price': float(stop_loss_price) if stop_loss_price else None,
                'take_profit_price': float(take_profit_price) if take_profit_price else None,
                # 余额信息（用于资金管理记录）
                'balance_before': balance_before,
                'balance_after': balance_after,
                'frozen_before': frozen_before,
                'frozen_after': frozen_after,
                'available_before': available_before,
                'available_after': available_after,
                'message': f"开{position_side}仓成功"
            }

        except Exception as e:
            if self.connection:
                try:
                    self.connection.rollback()
                except:
                    pass
            logger.error(f"开仓失败: {e}")
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'error': str(e),
                'message': f"开仓失败: {str(e)}"
            }

    def _resolve_market_entry_price(self, symbol: str) -> Optional[Decimal]:
        """获取模拟盘市价成交价（转市价单 / 立即成交用）。"""
        sym = futures_symbol_rating_canonical(symbol)
        try:
            from app.utils.futures_price import get_futures_trade_price
            fresh = get_futures_trade_price(
                self.connection, sym, max_age_seconds=30,
                log_tag="limit_to_market", require_fresh=False,
            )
            if fresh and fresh > 0:
                return Decimal(str(fresh))
        except Exception as e:
            logger.debug(f"[市价转单] {sym} get_futures_trade_price 失败: {e}")
        try:
            px = self.get_current_price(sym, use_realtime=True)
            if px and px > 0:
                return Decimal(str(px))
        except Exception as e:
            logger.warning(f"[市价转单] {sym} 获取市价失败: {e}")
        return None

    def fill_paper_limit_order(self, order: Dict, at_market: bool = False) -> Dict:
        """将模拟盘 PENDING 限价单成交为持仓（默认入场价=限价；at_market=True 用当前市价）。"""
        from app.services.paper_limit_entry import parse_order_notes

        try:
            cursor = self._get_cursor()
            account_id = int(order['account_id'])
            symbol = futures_symbol_rating_canonical(order['symbol'])
            side_raw = order['side']
            position_side = 'LONG' if side_raw == 'OPEN_LONG' else 'SHORT'
            quantity = round_quantity(Decimal(str(order['quantity'])), symbol)
            leverage = int(order.get('leverage') or 1)
            limit_price = Decimal(str(order['price']))
            entry_price = limit_price
            if at_market:
                market_px = self._resolve_market_entry_price(symbol)
                if not market_px or market_px <= 0:
                    return {'success': False, 'message': '无法获取市价，转市价单失败'}
                entry_price = market_px
            stop_loss_price = Decimal(str(order['stop_loss_price'])) if order.get('stop_loss_price') else None
            take_profit_price = Decimal(str(order['take_profit_price'])) if order.get('take_profit_price') else None
            source = order.get('order_source') or 'manual'
            entry_signal_type = order.get('entry_signal_type')
            signal_id = order.get('signal_id')
            strategy_id = order.get('strategy_id')
            meta = parse_order_notes(order.get('notes'))
            if at_market:
                entry_reason = meta.get('entry_reason') or '手动转市价单'
            else:
                entry_reason = meta.get('entry_reason') or order.get('notes') or '限价单成交'
            entry_score = meta.get('entry_score')
            max_hold_minutes = meta.get('max_hold_minutes')
            planned_close_time = meta.get('planned_close_time')
            if planned_close_time and isinstance(planned_close_time, str):
                try:
                    planned_close_time = datetime.strptime(planned_close_time, '%Y-%m-%d %H:%M:%S')
                except Exception:
                    planned_close_time = None

            cursor.execute(
                "SELECT current_balance, frozen_balance FROM futures_trading_accounts WHERE id = %s",
                (account_id,),
            )
            account = cursor.fetchone()
            if not account:
                return {'success': False, 'message': f'账户 {account_id} 不存在'}

            current_balance = Decimal(str(account['current_balance']))
            frozen_balance = Decimal(str(account.get('frozen_balance', 0) or 0))
            notional_value = entry_price * quantity
            margin_required = notional_value / Decimal(leverage)
            fee_rate = Decimal('0.0004')
            fee = notional_value * fee_rate
            available_balance = current_balance - frozen_balance

            if available_balance < (margin_required + fee):
                return {'success': False, 'message': '余额不足，无法成交限价单'}

            liquidation_price = self.calculate_liquidation_price(
                entry_price, position_side, leverage,
            )
            entry_ema_diff = self.get_ema_diff(symbol, '15m')

            timeout_at = None
            if max_hold_minutes:
                timeout_at = datetime.now() + timedelta(minutes=int(max_hold_minutes))

            position_sql = """
                INSERT INTO futures_positions (
                    account_id, symbol, position_side, leverage,
                    quantity, notional_value, margin,
                    entry_price, mark_price, liquidation_price,
                    stop_loss_price, take_profit_price,
                    entry_ema_diff, entry_signal_type, entry_score, entry_reason,
                    max_hold_minutes, timeout_at, planned_close_time,
                    open_time, source, signal_id, strategy_id, status
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s, 'open'
                )
            """
            cursor.execute(position_sql, (
                account_id, symbol, position_side, leverage,
                float(quantity), float(notional_value), float(margin_required),
                float(entry_price), float(entry_price), float(liquidation_price),
                float(stop_loss_price) if stop_loss_price else None,
                float(take_profit_price) if take_profit_price else None,
                entry_ema_diff, entry_signal_type, entry_score, entry_reason,
                max_hold_minutes, timeout_at, planned_close_time,
                datetime.now(), source, signal_id, strategy_id,
            ))
            position_id = cursor.lastrowid

            pending_order_id = order['order_id']
            cursor.execute(
                """
                UPDATE futures_orders
                SET status='FILLED', position_id=%s,
                    executed_quantity=%s, executed_value=%s,
                    avg_fill_price=%s, fill_time=NOW(), updated_at=NOW()
                WHERE order_id=%s AND status='PENDING'
                """,
                (position_id, float(quantity), float(notional_value),
                 float(entry_price), pending_order_id),
            )
            if cursor.rowcount == 0:
                self.connection.rollback()
                return {'success': False, 'message': '订单已处理或不存在'}

            trade_id = f"T-{uuid.uuid4().hex[:16].upper()}"
            cursor.execute(
                """
                INSERT INTO futures_trades (
                    account_id, order_id, position_id, trade_id,
                    symbol, side, price, quantity, notional_value,
                    leverage, margin, fee, fee_rate,
                    entry_price, trade_time
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s
                )
                """,
                (
                    account_id, pending_order_id, position_id, trade_id,
                    symbol, side_raw, float(entry_price), float(quantity), float(notional_value),
                    leverage, float(margin_required), float(fee), float(fee_rate),
                    float(entry_price), datetime.now(),
                ),
            )

            new_balance = current_balance - margin_required - fee
            cursor.execute(
                """UPDATE futures_trading_accounts
                SET current_balance=%s, frozen_balance=frozen_balance+%s
                WHERE id=%s""",
                (float(new_balance), float(margin_required), account_id),
            )
            cursor.execute(
                """UPDATE futures_trading_accounts a
                SET a.total_equity = a.current_balance + a.frozen_balance + COALESCE((
                    SELECT SUM(p.unrealized_pnl)
                    FROM futures_positions p
                    WHERE p.account_id = a.id AND p.status = 'open'
                ), 0)
                WHERE a.id = %s""",
                (account_id,),
            )
            self.connection.commit()

            tag = '市价转单' if at_market else '限价成交'
            extra = f" 限价={limit_price}" if at_market else ''
            logger.info(
                f"[{tag}] {symbol} {position_side} {float(quantity)} @ {entry_price}"
                f"{extra} position_id={position_id} order={pending_order_id}"
            )
            return {
                'success': True,
                'position_id': position_id,
                'order_id': pending_order_id,
                'fill_price': float(entry_price),
                'at_market': at_market,
            }
        except Exception as e:
            if self.connection:
                try:
                    self.connection.rollback()
                except Exception:
                    pass
            tag = '市价转单' if at_market else '限价成交'
            logger.error(f"[{tag}] 失败 {order.get('symbol')}: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return {'success': False, 'message': str(e)}

    def close_position(
        self,
        position_id: int,
        close_quantity: Optional[Decimal] = None,  # 🔥 保留参数以兼容旧代码，但总是全部平仓
        reason: str = 'manual',
        close_price: Optional[Decimal] = None,
        _deadlock_retry: int = 0
    ) -> Dict:
        """
        平仓（全部平仓）

        🔥 新逻辑：每个持仓都是独立的，根据盈亏比例直接全部平仓
        不再支持分批平仓，close_quantity参数仅为兼容性保留

        Args:
            position_id: 持仓ID
            close_quantity: 已废弃，总是全部平仓
            reason: 平仓原因

        Returns:
            平仓结果
        """
        # 记录平仓开始和 live_engine 状态
        logger.info(f"📤 [模拟盘平仓] 开始: position_id={position_id}, reason={reason}, live_engine绑定状态={self.live_engine is not None}")

        # 每次操作都创建新连接，确保获取最新数据
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

        cursor = connection.cursor()

        try:
            # 1. 获取持仓信息（使用新连接确保获取最新数据）
            cursor.execute(
                """SELECT * FROM futures_positions WHERE id = %s AND status = 'open'""",
                (position_id,)
            )
            position = cursor.fetchone()

            if not position:
                # 持仓不存在或已平仓，这是正常情况（可能已经被其他操作平仓），返回成功结果
                logger.debug(f"持仓 {position_id} 不存在或已平仓，跳过平仓操作")
                return {
                    'success': True,
                    'message': f"持仓 {position_id} 不存在或已平仓",
                    'position_id': position_id,
                    'already_closed': True
                }

            symbol = position['symbol']
            position_side = position['position_side']
            account_id = position['account_id']
            entry_price = Decimal(str(position['entry_price']))
            quantity = Decimal(str(position['quantity']))
            leverage = position['leverage']
            margin = Decimal(str(position['margin']))
            
            # 获取变化前的账户余额信息（用于资金管理记录）
            cursor.execute(
                "SELECT current_balance, frozen_balance FROM futures_trading_accounts WHERE id = %s",
                (account_id,)
            )
            account_before = cursor.fetchone()
            if account_before:
                balance_before = float(account_before['current_balance'])
                frozen_before = float(account_before.get('frozen_balance', 0) or 0)
                available_before = balance_before - frozen_before
            else:
                balance_before = frozen_before = available_before = None

            # 🔥 总是全部平仓（忽略 close_quantity 参数）
            close_quantity = quantity
            logger.info(f"📤 全部平仓: {symbol} {position_side} 数量={float(quantity)}")

            # 2. 获取平仓价格
            # 如果指定了平仓价格（如止盈止损触发），使用指定价格；否则使用当前市场价格
            if close_price and close_price > 0:
                current_price = close_price
                logger.info(f"使用指定平仓价格: {close_price:.8f} (原因: {reason})")
            else:
                # 平仓时使用实时价格，确保以最新市价平仓
                current_price = self.get_current_price(symbol, use_realtime=True)
                if not current_price or current_price <= 0:
                    raise ValueError(f"无法获取{symbol}的有效价格")

            # 3. 计算盈亏
            close_value = current_price * close_quantity
            open_value = entry_price * close_quantity

            if position_side == 'LONG':
                # 多头盈亏 = (平仓价 - 开仓价) * 数量
                pnl = (current_price - entry_price) * close_quantity
            else:  # SHORT
                # 空头盈亏 = (开仓价 - 平仓价) * 数量
                pnl = (entry_price - current_price) * close_quantity

            # 止盈类平仓的亏损保护：如果是止盈原因触发的平仓，但实际执行时是亏损，则取消平仓
            # 止盈类型包括：EMA差值收窄止盈、EMA方向反转止盈、移动止盈、最大止盈
            take_profit_reasons = ['ema_diff_narrowing_tp', 'ema_direction_reversal_tp',
                                  'trailing_take_profit', 'max_take_profit',
                                  'trend_weakening']
            is_take_profit = any(reason.startswith(tp_reason) for tp_reason in take_profit_reasons)

            if is_take_profit and pnl < 0:
                # 计算盈亏百分比
                pnl_pct_check = (pnl / open_value) * 100 if open_value > 0 else 0
                logger.warning(
                    f"⚠️ {symbol} 止盈平仓取消: 触发时盈利但执行时亏损 {float(pnl_pct_check):.2f}%\n"
                    f"   原因: {reason}\n"
                    f"   入场价: {float(entry_price):.8f}, 当前价: {float(current_price):.8f}\n"
                    f"   价格波动导致止盈失效，保留持仓等待盈利"
                )
                return {
                    'success': False,
                    'message': f'止盈取消：执行时亏损 {float(pnl_pct_check):.2f}%',
                    'position_id': position_id,
                    'reason': 'take_profit_canceled_due_to_loss'
                }

            # 4. 计算手续费
            fee_rate = Decimal('0.0004')
            fee = close_value * fee_rate

            # 实际盈亏 = pnl - 手续费
            realized_pnl = pnl - fee

            # 收益率 = 盈亏 / 成本
            if open_value > 0:
                pnl_pct = (pnl / open_value) * 100
            else:
                pnl_pct = Decimal('0')

            # ROI = 盈亏 / 保证金 (杠杆收益率)
            if quantity > 0:
                position_margin = margin * (close_quantity / quantity)
            else:
                position_margin = margin
            
            if position_margin > 0:
                roi = (pnl / position_margin) * 100
            else:
                roi = Decimal('0')

            # 5. 创建平仓订单
            order_id = f"FUT-{uuid.uuid4().hex[:16].upper()}"
            side = f"CLOSE_{position_side}"

            order_sql = """
                INSERT INTO futures_orders (
                    account_id, order_id, position_id, symbol,
                    side, order_type, leverage,
                    price, quantity, executed_quantity,
                    total_value, executed_value,
                    fee, fee_rate, status,
                    avg_fill_price, fill_time,
                    realized_pnl, pnl_pct,
                    order_source, notes
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, 'MARKET', %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, 'FILLED',
                    %s, %s,
                    %s, %s,
                    %s, %s
                )
            """

            cursor.execute(order_sql, (
                account_id, order_id, position_id, symbol,
                side, leverage,
                float(current_price), float(close_quantity), float(close_quantity),
                float(close_value), float(close_value),
                float(fee), float(fee_rate),
                float(current_price), datetime.now(),
                float(realized_pnl), float(pnl_pct),
                'strategy', reason
            ))

            # 6. 创建交易记录
            trade_id = f"T-{uuid.uuid4().hex[:16].upper()}"

            trade_sql = """
                INSERT INTO futures_trades (
                    account_id, order_id, position_id, trade_id,
                    symbol, side, price, quantity, notional_value,
                    leverage, margin, fee, fee_rate,
                    realized_pnl, pnl_pct, roi,
                    entry_price, close_price, trade_time
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                )
            """

            cursor.execute(trade_sql, (
                account_id, order_id, position_id, trade_id,
                symbol, side, float(current_price), float(close_quantity), float(close_value),
                leverage, float(position_margin), float(fee), float(fee_rate),
                float(realized_pnl), float(pnl_pct), float(roi),
                float(entry_price), float(current_price), datetime.now()
            ))

            # 7. 更新持仓状态
            # 将原因转换为中文显示
            reason_map = {
                'stop_loss': '止损',
                'hard_stop_loss': 'hard_stop_loss',  # 硬止损保持原样
                'trailing_stop': '移动止损',
                'trail-tp': '移动止盈',
                'take_profit': '止盈',
                'manual': '手动平仓',
                'strategy': '策略平仓',
                'liquidation': '强制平仓',
                'MAX_HOLD_TIME': '超时平仓(计划持仓到期)',
                'SCORE_DROPPED': '评分下降平仓',
                'REVERSE_SIGNAL': '反向信号平仓'
            }
            notes_reason = reason_map.get(reason, reason)

            # 🔥 全部平仓（每个持仓都是独立的，直接全部平仓）
            profit_pct_at_close = 0.0
            if entry_price > 0:
                if position_side == 'LONG':
                    profit_pct_at_close = float((current_price - entry_price) / entry_price * 100)
                else:
                    profit_pct_at_close = float((entry_price - current_price) / entry_price * 100)

            cursor.execute(
                """UPDATE futures_positions
                SET status = 'closed', close_time = %s,
                    realized_pnl = %s, notes = %s,
                    mark_price = %s, unrealized_pnl_pct = %s
                WHERE id = %s""",
                (datetime.now(), float(realized_pnl), notes_reason,
                 float(current_price), profit_pct_at_close, position_id)
            )

            # 释放全部保证金
            released_margin = margin

            # 8. 更新账户余额和交易统计
            # 判断是盈利还是亏损
            is_winning_trade = realized_pnl > 0

            # 先释放保证金
            cursor.execute(
                """UPDATE futures_trading_accounts
                SET frozen_balance = frozen_balance - %s
                WHERE id = %s""",
                (float(released_margin), account_id)
            )

            # 再更新账户余额和已实现盈亏
            # 先用独立SELECT算出汇总值（避免UPDATE内嵌子查询跨表锁，防死锁）
            cursor.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0) as total_pnl FROM futures_positions WHERE account_id=%s AND status='closed'",
                (account_id,)
            )
            total_realized_pnl = float(cursor.fetchone()['total_pnl'])

            cursor.execute(
                """UPDATE futures_trading_accounts
                SET realized_pnl = %s,
                    current_balance = initial_balance + %s,
                    total_trades = total_trades + 1,
                    winning_trades = winning_trades + IF(%s > 0, 1, 0),
                    losing_trades = losing_trades + IF(%s < 0, 1, 0),
                    win_rate = (winning_trades + IF(%s > 0, 1, 0)) / GREATEST(total_trades + 1, 1) * 100
                WHERE id = %s""",
                (total_realized_pnl, total_realized_pnl,
                 float(realized_pnl), float(realized_pnl), float(realized_pnl),
                 account_id)
            )

            # 9. 更新总权益（余额 + 冻结余额 + 持仓未实现盈亏）
            # 先单独查未实现盈亏，再UPDATE（避免跨表子查询锁）
            cursor.execute(
                "SELECT COALESCE(SUM(unrealized_pnl), 0) as total_unrealized FROM futures_positions WHERE account_id=%s AND status='open'",
                (account_id,)
            )
            total_unrealized = float(cursor.fetchone()['total_unrealized'])

            cursor.execute(
                """UPDATE futures_trading_accounts
                SET total_equity = current_balance + frozen_balance + %s
                WHERE id = %s""",
                (total_unrealized, account_id)
            )
            
            # 获取变化后的账户余额信息（用于资金管理记录）
            cursor.execute(
                "SELECT current_balance, frozen_balance FROM futures_trading_accounts WHERE id = %s",
                (account_id,)
            )
            account_after = cursor.fetchone()
            if account_after:
                balance_after = float(account_after['current_balance'])
                frozen_after = float(account_after.get('frozen_balance', 0) or 0)
                available_after = balance_after - frozen_after
            else:
                balance_after = frozen_after = available_after = None

            connection.commit()
            cursor.close()

            # 根据交易对确定数量显示精度
            qty_precision = get_quantity_precision(symbol)
            logger.info(
                f"平仓成功: {symbol} {position_side} {float(close_quantity):.{qty_precision}f} @ {current_price}, "
                f"盈亏{realized_pnl:.2f} USDT ({pnl_pct:.2f}%), ROI {roi:.2f}%"
            )

            # ========== 发送 Telegram 通知 ==========
            try:
                if self.trade_notifier:
                    # 计算持仓时间
                    hold_time = None
                    if position.get('open_time'):
                        open_time = position['open_time']
                        if isinstance(open_time, str):
                            open_time = datetime.strptime(open_time, '%Y-%m-%d %H:%M:%S')
                        hold_duration = datetime.now() - open_time
                        hours, remainder = divmod(hold_duration.total_seconds(), 3600)
                        minutes = remainder // 60
                        if hours >= 24:
                            days = int(hours // 24)
                            hours = int(hours % 24)
                            hold_time = f"{days}天{int(hours)}小时{int(minutes)}分钟"
                        elif hours >= 1:
                            hold_time = f"{int(hours)}小时{int(minutes)}分钟"
                        else:
                            hold_time = f"{int(minutes)}分钟"

                    self.trade_notifier.notify_close_position(
                        symbol=symbol,
                        direction=position_side,
                        quantity=float(close_quantity),
                        entry_price=float(entry_price),
                        exit_price=float(current_price),
                        pnl=float(realized_pnl),
                        pnl_pct=float(roi),  # 使用 ROI（杠杆收益率）
                        reason=reason,
                        hold_time=hold_time,
                        is_paper=True  # 标记为模拟盘
                    )
            except Exception as notify_err:
                logger.warning(f"发送模拟盘平仓通知失败: {notify_err}")
            # ========== Telegram 通知结束 ==========

            # ========== 同步实盘平仓 ==========
            # 走 live_futures_positions.paper_position_id 关联,逐条用对应账号的
            # user_api_keys API 平仓 (与 smart_exit_optimizer._close_live_positions_on_exchange 同逻辑)。
            # 不再用 self.live_engine (config.yaml 单账号) 按 symbol+side 模糊匹配,
            # 多账号场景下那会用错账号的密钥去查持仓导致静默失败。
            try:
                # 先检查 live_close_enabled 开关 (控制模拟盘与实盘的平仓同步)
                live_close_enabled = False
                try:
                    chk_cur = connection.cursor()
                    chk_cur.execute(
                        "SELECT setting_value FROM system_settings WHERE setting_key='live_close_enabled'"
                    )
                    row = chk_cur.fetchone()
                    chk_cur.close()
                    if row:
                        live_close_enabled = str(row.get('setting_value', '0')).lower() in ('1', 'true', 'yes')
                except Exception as _ge:
                    logger.warning(f"[同步实盘] 读 live_close_enabled 失败,保守跳过: {_ge}")
                    live_close_enabled = False

                if not live_close_enabled:
                    logger.info(
                        f"[同步实盘] live_close_enabled=0,跳过 {symbol} {position_side} 实盘平仓 "
                        f"(paper_position_id={position_id}, reason={reason})"
                    )
                else:
                    paper_source = position.get("source") or ""
                    # 平仓不再按 source/TOP50/白名单过滤；只平 paper_position_id 明确绑定的实盘仓。
                    # 关键改动: 按 paper_position_id 查 live_futures_positions,
                    # 用每条记录对应账号 (user_api_keys) 的密钥平仓
                    from app.services.api_key_service import get_api_key_service
                    from app.trading.binance_futures_engine import BinanceFuturesEngine
                    from decimal import Decimal as _Dec

                    live_cur = connection.cursor()
                    live_cur.execute(
                        "SELECT id, account_id, quantity, entry_price "
                        "FROM live_futures_positions "
                        "WHERE paper_position_id=%s AND status='OPEN'",
                        (position_id,)
                    )
                    live_rows = live_cur.fetchall()
                    live_cur.close()

                    if not live_rows:
                        # 诊断日志: 看看为啥没匹配上
                        # (1) 同 symbol+side 的 OPEN 实盘单是否存在 (paper_position_id 是否为 NULL/不一致)
                        diag_cur = connection.cursor()
                        diag_cur.execute(
                            "SELECT id, account_id, paper_position_id, source, open_time "
                            "FROM live_futures_positions "
                            "WHERE symbol=%s AND position_side=%s AND status='OPEN' "
                            "ORDER BY open_time DESC LIMIT 5",
                            (symbol, position_side)
                        )
                        diag_rows = diag_cur.fetchall()
                        diag_cur.close()
                        logger.warning(
                            f"[同步实盘] {symbol} {position_side} 无 paper_position_id={position_id} 关联的 OPEN 实盘单,"
                            f"跳过实盘平仓 (reason={reason}). "
                            f"同 symbol+side 下其它 OPEN 实盘单 (最多 5 条): {diag_rows or '无'}"
                        )
                    else:
                        api_service = get_api_key_service()
                        if not api_service:
                            logger.warning("[同步实盘] api_key_service 未初始化,跳过")
                        else:
                            all_keys_list = api_service.get_all_active_api_keys()
                            all_keys = {ak['id']: ak for ak in all_keys_list}
                            logger.info(
                                f"[同步实盘] {symbol} {position_side} 找到 {len(live_rows)} 条 OPEN 实盘单,"
                                f"reason={reason}, paper_position_id={position_id}"
                            )
                            for row in live_rows:
                                acc_id = row['account_id']
                                key_info = all_keys.get(acc_id)
                                if not key_info:
                                    logger.warning(
                                        f"[同步实盘] account_id={acc_id} 无有效API密钥 "
                                        f"(user_api_keys.status='active'),跳过该单 id={row['id']}"
                                    )
                                    continue
                                try:
                                    live_engine = BinanceFuturesEngine(
                                        self.db_config,
                                        key_info['api_key'],
                                        key_info['api_secret']
                                    )
                                    live_result = live_engine.close_position_direct(
                                        symbol=symbol,
                                        position_side=position_side,
                                        quantity=_Dec(str(row['quantity'])),
                                        entry_price=_Dec(str(row['entry_price'])),
                                        reason=f'paper_sync_{reason}',
                                        strategy_name=paper_source or None,
                                        open_time=position.get('open_time'),
                                    )
                                    if live_result.get('success'):
                                        logger.info(
                                            f"[同步实盘] ✅ {key_info.get('account_name', acc_id)} "
                                            f"{symbol} {position_side} 平仓成功"
                                        )
                                        upd_cur = connection.cursor()
                                        upd_cur.execute(
                                            "UPDATE live_futures_positions "
                                            "SET status='CLOSED', close_time=%s, close_price=%s, "
                                            "realized_pnl=%s, close_reason=%s, updated_at=%s "
                                            "WHERE id=%s",
                                            (
                                                datetime.now(),
                                                live_result.get('close_price'),
                                                live_result.get('realized_pnl'),
                                                f'paper_sync_{reason}',
                                                datetime.now(),
                                                row['id'],
                                            ),
                                        )
                                        upd_cur.close()
                                    else:
                                        err = live_result.get('error', '未知错误')
                                        logger.error(
                                            f"[同步实盘] ❌ {key_info.get('account_name', acc_id)} "
                                            f"{symbol} {position_side} 平仓失败: {err}"
                                        )
                                except Exception as eng_ex:
                                    logger.error(
                                        f"[同步实盘] account_id={acc_id} 平仓异常: {eng_ex}"
                                    )
            except Exception as live_ex:
                logger.error(f"[同步实盘] ❌ {symbol} {position_side} 平仓异常: {live_ex}")
            # ========== 同步实盘平仓结束 ==========

            return {
                'success': True,
                'order_id': order_id,
                'trade_id': trade_id,
                'symbol': symbol,
                'position_side': position_side,
                'close_quantity': float(close_quantity),
                'exit_price': float(current_price),  # 添加 exit_price 别名，与开仓返回的 entry_price 对应
                'close_price': float(current_price),
                'entry_price': float(entry_price),
                'realized_pnl': float(realized_pnl),
                'pnl_pct': float(pnl_pct),
                'roi': float(roi),
                'fee': float(fee),
                'message': f"平仓成功，盈亏{realized_pnl:.2f} USDT ({pnl_pct:.2f}%)",
                # 余额信息（用于资金管理记录）
                'balance_before': balance_before,
                'balance_after': balance_after,
                'frozen_before': frozen_before,
                'frozen_after': frozen_after,
                'available_before': available_before,
                'available_after': available_after,
                'margin': float(position_margin),  # 释放的保证金
            }

        except ValueError as e:
            # ValueError 通常是业务逻辑错误（如持仓不存在），已经在上面处理了
            # 但如果是其他 ValueError，需要处理
            error_msg = str(e)
            if '不存在或已平仓' in error_msg:
                # 这种情况已经在上面处理了，不应该到这里
                logger.debug(f"持仓不存在（已在上面处理）: {e}")
                return {
                    'success': True,
                    'message': error_msg,
                    'already_closed': True
                }
            else:
                # 其他 ValueError
                if connection:
                    try:
                        connection.rollback()
                    except:
                        pass
                logger.error(f"平仓失败: {e}")
                return {
                    'success': False,
                    'error': error_msg,
                    'message': f"平仓失败: {error_msg}"
                }
        except Exception as e:
            if connection:
                try:
                    connection.rollback()
                except:
                    pass
            # 死锁自动重试（最多2次）
            if isinstance(e, pymysql.err.OperationalError) and e.args[0] == 1213 and _deadlock_retry < 2:
                import time, random
                wait = random.uniform(0.1, 0.4) * (_deadlock_retry + 1)
                logger.warning(f"[DEADLOCK] 平仓死锁，{wait:.2f}s后重试({_deadlock_retry + 1}/2): position_id={position_id}")
                time.sleep(wait)
                return self.close_position(position_id, close_quantity, reason, close_price, _deadlock_retry=_deadlock_retry + 1)
            logger.error(f"平仓失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                'success': False,
                'error': str(e),
                'message': f"平仓失败: {str(e)}"
            }
        finally:
            if connection:
                try:
                    connection.close()
                except:
                    pass

    def get_open_positions(self, account_id: int) -> List[Dict]:
        """获取账户的所有持仓"""
        # 每次查询都创建新连接，避免连接池缓存问题
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
            cursor = connection.cursor()
            cursor.execute(
                """SELECT * FROM futures_positions
                WHERE account_id = %s AND status = 'open'
                ORDER BY open_time DESC""",
                (account_id,)
            )

            positions = cursor.fetchall()
            cursor.close()
        finally:
            connection.close()

        # 更新每个持仓的当前盈亏，并统一字段名
        # 使用实时价格更新持仓价格和盈亏
        connection_update = pymysql.connect(
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
            cursor_update = connection_update.cursor()

            # 完全不打 Binance REST. 价格走 DB (kline_data 5m, 由 ws_kline_collector
            # 实时写入) → DB mark_price (上次值) → entry_price 三层兜底.
            # 历史 bug: 之前每次 /positions HTTP 请求都打 ticker/price REST, 是 -1003
            # 死循环主因 (ban 解封 30s 内立刻又被打到触发新 ban). 彻底移除.
            price_cache = {}

            for pos in positions:
                # 将 id 映射为 position_id，保持与API一致
                if 'id' in pos and 'position_id' not in pos:
                    pos['position_id'] = pos['id']

                try:
                    # 优先从缓存获取价格，否则从数据库获取
                    symbol = pos['symbol']
                    if symbol in price_cache:
                        current_price = price_cache[symbol]
                    else:
                        try:
                            current_price = self.get_current_price(symbol, use_realtime=False)
                        except Exception:
                            current_price = None

                    # 如果无法获取价格，使用数据库中的mark_price，再回退开仓价
                    if current_price is None:
                        current_price = pos.get('mark_price')
                    if current_price is None:
                        ep = pos.get('avg_entry_price') or pos.get('entry_price')
                        if ep is not None:
                            current_price = Decimal(str(ep))
                            logger.debug(f"持仓 {symbol} 无行情，暂用开仓价展示")
                    if current_price is None:
                        raise ValueError(f"无法获取{symbol}的价格")

                    # 对于分批建仓的持仓，使用avg_entry_price，否则使用entry_price
                    entry_price = Decimal(str(pos.get('avg_entry_price') or pos['entry_price']))
                    quantity = Decimal(str(pos['quantity']))
                    leverage = Decimal(str(pos.get('leverage', 1)))
                    margin = Decimal(str(pos.get('margin', 0)))

                    # 计算未实现盈亏（基于名义价值，不乘以杠杆）
                    # 杠杆只影响保证金，不影响盈亏本身
                    if pos['position_side'] == 'LONG':
                        unrealized_pnl = (current_price - entry_price) * quantity
                    else:
                        unrealized_pnl = (entry_price - current_price) * quantity

                    # 计算盈亏百分比（基于保证金）
                    unrealized_pnl_pct = (unrealized_pnl / margin * 100) if margin > 0 else Decimal('0')

                    # 更新数据库中的 mark_price 和未实现盈亏
                    # 🔥 只更新 open 持仓，防止竞态条件覆盖已平仓记录的 close_price
                    cursor_update.execute(
                        """UPDATE futures_positions
                        SET mark_price = %s,
                            unrealized_pnl = %s,
                            unrealized_pnl_pct = %s,
                            last_update_time = NOW()
                        WHERE id = %s
                          AND status = 'open'""",
                        (float(current_price), float(unrealized_pnl), float(unrealized_pnl_pct), pos['id'])
                    )

                    pos['current_price'] = float(current_price)
                    pos['unrealized_pnl'] = float(unrealized_pnl)
                    pos['unrealized_pnl_pct'] = float(unrealized_pnl_pct)

                except Exception as e:
                    logger.warning(f"更新持仓 {pos.get('symbol', 'unknown')} 价格和盈亏失败: {e}")
                    # 如果更新失败，至少设置默认值
                    # 使用 or 0 而不是 get(..., 0)，因为值可能是None而不是不存在
                    pos['current_price'] = float(pos.get('mark_price') or pos.get('entry_price') or 0)
                    pos['unrealized_pnl'] = float(pos.get('unrealized_pnl') or 0)
                    pos['unrealized_pnl_pct'] = float(pos.get('unrealized_pnl_pct') or 0)

                # 转换 Decimal 和 datetime 类型，确保所有字段都能正确序列化为 JSON
                for key, value in pos.items():
                    if isinstance(value, Decimal):
                        pos[key] = float(value)
                    elif isinstance(value, datetime):
                        pos[key] = value.isoformat()
                    elif isinstance(value, date):
                        pos[key] = value.isoformat()

        finally:
            cursor_update.close()
            connection_update.close()

        return positions

    def update_all_accounts_equity(self):
        """
        更新所有账户的总权益
        总权益 = 当前余额 + 冻结余额 + 所有持仓的未实现盈亏总和
        
        注意：此方法会先更新所有持仓的未实现盈亏（基于最新价格），然后再更新总权益
        """
        try:
            if not self.connection or not self.connection.open:
                self._connect_db()
            
            cursor = self.connection.cursor()
            
            # 第一步：更新所有持仓的未实现盈亏（基于最新价格）
            cursor.execute(
                """SELECT id, symbol, entry_price, quantity, position_side, margin, leverage
                FROM futures_positions 
                WHERE status = 'open'"""
            )
            positions = cursor.fetchall()
            
            for pos in positions:
                try:
                    # 获取当前价格
                    current_price = self.get_current_price(pos['symbol'], use_realtime=True)
                    if current_price == 0:
                        continue
                    
                    entry_price = Decimal(str(pos['entry_price']))
                    quantity = Decimal(str(pos['quantity']))
                    margin = Decimal(str(pos.get('margin', 0)))
                    
                    # 计算未实现盈亏
                    if pos['position_side'] == 'LONG':
                        unrealized_pnl = (current_price - entry_price) * quantity
                    else:  # SHORT
                        unrealized_pnl = (entry_price - current_price) * quantity
                    
                    # 计算盈亏百分比
                    unrealized_pnl_pct = (unrealized_pnl / margin * 100) if margin > 0 else Decimal('0')
                    
                    # 更新持仓的未实现盈亏
                    cursor.execute(
                        """UPDATE futures_positions
                        SET mark_price = %s,
                            unrealized_pnl = %s,
                            unrealized_pnl_pct = %s,
                            last_update_time = NOW()
                        WHERE id = %s""",
                        (float(current_price), float(unrealized_pnl), float(unrealized_pnl_pct), pos['id'])
                    )
                except Exception as e:
                    logger.warning(f"更新持仓 {pos.get('symbol', 'unknown')} 未实现盈亏失败: {e}")
                    continue
            
            # 第二步：更新所有账户的总权益
            # 获取所有有合约持仓的账户
            cursor.execute(
                """SELECT DISTINCT account_id 
                FROM futures_positions 
                WHERE status = 'open'"""
            )
            account_ids_with_positions = [row['account_id'] for row in cursor.fetchall()]
            
            # 获取所有账户（包括没有持仓的）
            cursor.execute("SELECT id FROM futures_trading_accounts")
            all_account_ids = [row['id'] for row in cursor.fetchall()]
            
            updated_count = 0
            for account_id in all_account_ids:
                try:
                    # 更新该账户的总权益
                    cursor.execute(
                        """UPDATE futures_trading_accounts a
                        SET a.total_equity = a.current_balance + a.frozen_balance + COALESCE((
                            SELECT SUM(p.unrealized_pnl) 
                            FROM futures_positions p 
                            WHERE p.account_id = a.id AND p.status = 'open'
                        ), 0),
                        updated_at = NOW()
                        WHERE a.id = %s""",
                        (account_id,)
                    )
                    updated_count += 1
                except Exception as e:
                    logger.warning(f"更新账户 {account_id} 总权益失败: {e}")
                    continue
            
            self.connection.commit()
            cursor.close()
            
            return updated_count
            
        except Exception as e:
            logger.error(f"更新所有账户总权益失败: {e}")
            import traceback
            traceback.print_exc()
            if self.connection:
                try:
                    self.connection.rollback()
                except:
                    pass
            return 0

    def __del__(self):
        """关闭数据库连接"""
        if self.connection and self.connection.open:
            self.connection.close()
