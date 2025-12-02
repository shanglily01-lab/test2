"""
模拟合约交易引擎
支持多空双向交易、杠杆、止盈止损
"""

import uuid
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from loguru import logger
import pymysql

# 定义本地时区（UTC+8）
LOCAL_TIMEZONE = timezone(timedelta(hours=8))

def get_local_time() -> datetime:
    """获取本地时间（UTC+8）"""
    return datetime.now(LOCAL_TIMEZONE).replace(tzinfo=None)

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

    def __init__(self, db_config: dict):
        """初始化合约交易引擎"""
        self.db_config = db_config
        self.connection = None
        self._is_first_connection = True  # 标记是否是首次连接
        self._connection_created_at = None  # 连接创建时间（Unix时间戳）
        self._connection_max_age = 300  # 连接最大存活时间（秒），5分钟
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
        # 如果要求使用实时价格，尝试从交易所API获取
        if use_realtime:
            try:
                import requests
                from requests.adapters import HTTPAdapter
                from urllib3.util.retry import Retry
                
                # 标准化交易对格式
                symbol_clean = symbol.replace('/', '').upper()
                
                # 配置重试策略
                session = requests.Session()
                retry_strategy = Retry(
                    total=2,
                    backoff_factor=0.1,
                    status_forcelist=[429, 500, 502, 503, 504],
                )
                adapter = HTTPAdapter(max_retries=retry_strategy)
                session.mount("https://", adapter)
                
                # 优先从Binance合约API获取实时价格
                try:
                    response = session.get(
                        'https://fapi.binance.com/fapi/v1/ticker/price',
                        params={'symbol': symbol_clean},
                        timeout=2
                    )
                    if response.status_code == 200:
                        data = response.json()
                        if data and 'price' in data:
                            price = Decimal(str(data['price']))
                            logger.debug(f"从Binance合约API获取实时价格: {symbol} = {price}")
                            return price
                except Exception as e:
                    logger.debug(f"Binance合约API获取失败: {e}")
                
                # 如果Binance失败，尝试从Binance现货API获取
                try:
                    response = session.get(
                        'https://api.binance.com/api/v3/ticker/price',
                        params={'symbol': symbol_clean},
                        timeout=2
                    )
                    if response.status_code == 200:
                        data = response.json()
                        if data and 'price' in data:
                            price = Decimal(str(data['price']))
                            logger.debug(f"从Binance现货API获取实时价格: {symbol} = {price}")
                            return price
                except Exception as e:
                    logger.debug(f"Binance现货API获取失败: {e}")
                
                # 如果实时API都失败，回退到数据库缓存
                logger.warning(f"实时API获取失败，回退到数据库缓存: {symbol}")
            except Exception as e:
                logger.warning(f"获取实时价格异常，回退到数据库缓存: {symbol}, {e}")
        
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
            cursor = connection.cursor()
            # 尝试从1分钟K线获取最新价格
            cursor.execute(
                """SELECT close_price FROM kline_data
                WHERE symbol = %s AND timeframe = '1m'
                ORDER BY timestamp DESC LIMIT 1""",
                (symbol,)
            )
            result = cursor.fetchone()
            if result and result['close_price']:
                price = Decimal(str(result['close_price']))
                cursor.close()
                return price

            # 回退到价格表
            cursor.execute(
                """SELECT price FROM price_data
                WHERE symbol = %s
                ORDER BY timestamp DESC LIMIT 1""",
                (symbol,)
            )
            result = cursor.fetchone()
            cursor.close()
            if result and result['price']:
                return Decimal(str(result['price']))

            raise ValueError(f"无法获取{symbol}的价格")
        except Exception as e:
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
        strategy_id: Optional[int] = None
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

        Returns:
            开仓结果
        """
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
            # 如果设置了限价，检查是否需要创建未成交订单
            if limit_price and limit_price > 0:
                should_create_pending_order = False
                if position_side == 'LONG':
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
                        "SELECT current_balance, frozen_balance FROM paper_trading_accounts WHERE id = %s",
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
                    available_balance = current_balance - frozen_balance
                    
                    if available_balance < (limit_margin_required + limit_fee):
                        return {
                            'success': False,
                            'message': f"余额不足。需要: {limit_margin_required + limit_fee:.2f} USDT, 可用: {available_balance:.2f} USDT"
                        }
                    
                    # 创建未成交订单
                    order_id = f"FUT-{uuid.uuid4().hex[:16].upper()}"
                    side = f"OPEN_{position_side}"
                    
                    # 冻结保证金和手续费
                    total_frozen = limit_margin_required + limit_fee
                    new_balance = current_balance - total_frozen
                    cursor.execute(
                        """UPDATE paper_trading_accounts
                        SET current_balance = %s, frozen_balance = frozen_balance + %s
                        WHERE id = %s""",
                        (float(new_balance), float(total_frozen), account_id)
                    )
                    
                    # 创建订单记录（包含止盈止损和策略ID）
                    order_sql = """
                        INSERT INTO futures_orders (
                            account_id, order_id, symbol,
                            side, order_type, leverage,
                            price, quantity, executed_quantity,
                            margin, total_value, executed_value,
                            fee, fee_rate, status,
                            stop_loss_price, take_profit_price,
                            order_source, signal_id, strategy_id, created_at
                        ) VALUES (
                            %s, %s, %s,
                            %s, 'LIMIT', %s,
                            %s, %s, 0,
                            %s, %s, 0,
                            %s, %s, 'PENDING',
                            %s, %s,
                            %s, %s, %s, %s
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
                        source, signal_id, strategy_id, get_local_time()
                    ))
                    
                    # 更新总权益（限价单时还没有持仓，未实现盈亏为0）
                    cursor.execute(
                        """UPDATE paper_trading_accounts a
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

            # 2. 确定开仓价格
            # 限价单使用限价，市价单使用实时价格
            logger.info(f"🔍 {symbol} {position_side} 开仓价格确定: limit_price={limit_price}, current_price={current_price}")
            if limit_price and limit_price > 0:
                entry_price = limit_price
                logger.info(f"📌 {symbol} {position_side} 使用限价开仓: entry_price={entry_price}")
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
                    "SELECT current_balance, frozen_balance FROM paper_trading_accounts WHERE id = %s",
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
                available_balance = current_balance - frozen_balance
                
                # 保存变化前的余额信息（用于资金管理记录）
                balance_before = float(current_balance)
                frozen_before = float(frozen_balance)
                available_before = float(available_balance)
                
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
                    if position_side == 'LONG':
                        stop_loss_price = entry_price * (1 - stop_loss_pct / 100)
                    else:
                        stop_loss_price = entry_price * (1 + stop_loss_pct / 100)
                else:
                    stop_loss_price = None
            # 如果直接指定了止损价格，使用指定的价格

            # 处理止盈价格：优先使用直接指定的价格，否则根据百分比计算
            if take_profit_price is None:
                if take_profit_pct:
                    if position_side == 'LONG':
                        take_profit_price = entry_price * (1 + take_profit_pct / 100)
                    else:
                        take_profit_price = entry_price * (1 - take_profit_pct / 100)
                else:
                    take_profit_price = None
            # 如果直接指定了止盈价格，使用指定的价格

            # 6. 创建持仓记录
            position_sql = """
                INSERT INTO futures_positions (
                    account_id, symbol, position_side, leverage,
                    quantity, notional_value, margin,
                    entry_price, mark_price, liquidation_price,
                    stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct,
                    open_time, source, signal_id, status
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, 'open'
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
                get_local_time(), source, signal_id
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
                    order_source, signal_id
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, 'FILLED',
                    %s, %s,
                    %s, %s
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
                float(entry_price), get_local_time(),
                source, signal_id
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
                float(entry_price), get_local_time()
            ))

            # 9. 更新账户余额
            # 减少当前余额，增加冻结余额（保证金和手续费）
            total_frozen = margin_required + fee
            new_balance = current_balance - total_frozen
            cursor.execute(
                """UPDATE paper_trading_accounts
                SET current_balance = %s, frozen_balance = frozen_balance + %s
                WHERE id = %s""",
                (float(new_balance), float(total_frozen), account_id)
            )
            
            # 获取变化后的余额信息（用于资金管理记录）
            balance_after = float(new_balance)
            frozen_after = float(frozen_balance + total_frozen)
            available_after = balance_after - frozen_after

            # 10. 更新总权益（余额 + 冻结余额 + 持仓未实现盈亏）
            cursor.execute(
                """UPDATE paper_trading_accounts a
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
            current_time_str = get_local_time().strftime('%Y-%m-%d %H:%M:%S')
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

    def close_position(
        self,
        position_id: int,
        close_quantity: Optional[Decimal] = None,
        reason: str = 'manual',
        close_price: Optional[Decimal] = None
    ) -> Dict:
        """
        平仓

        Args:
            position_id: 持仓ID
            close_quantity: 平仓数量（None表示全部平仓）
            reason: 平仓原因

        Returns:
            平仓结果
        """
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
                "SELECT current_balance, frozen_balance FROM paper_trading_accounts WHERE id = %s",
                (account_id,)
            )
            account_before = cursor.fetchone()
            if account_before:
                balance_before = float(account_before['current_balance'])
                frozen_before = float(account_before.get('frozen_balance', 0) or 0)
                available_before = balance_before - frozen_before
            else:
                balance_before = frozen_before = available_before = None

            # 如果没指定平仓数量，则全部平仓
            if close_quantity is None:
                close_quantity = quantity
            else:
                # 根据交易对精度对平仓数量进行四舍五入
                close_quantity = round_quantity(close_quantity, symbol)

            if close_quantity <= 0:
                raise ValueError(f"平仓数量必须大于0")
            
            if close_quantity > quantity:
                raise ValueError(f"平仓数量{close_quantity}大于持仓数量{quantity}")

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
                    order_source
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, 'MARKET', %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, 'FILLED',
                    %s, %s,
                    %s, %s,
                    %s
                )
            """

            cursor.execute(order_sql, (
                account_id, order_id, position_id, symbol,
                side, leverage,
                float(current_price), float(close_quantity), float(close_quantity),
                float(close_value), float(close_value),
                float(fee), float(fee_rate),
                float(current_price), get_local_time(),
                float(realized_pnl), float(pnl_pct),
                reason
            ))

            # 6. 创建交易记录
            trade_id = f"T-{uuid.uuid4().hex[:16].upper()}"

            trade_sql = """
                INSERT INTO futures_trades (
                    account_id, order_id, position_id, trade_id,
                    symbol, side, price, quantity, notional_value,
                    leverage, margin, fee, fee_rate,
                    realized_pnl, pnl_pct, roi,
                    entry_price, trade_time
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s
                )
            """

            cursor.execute(trade_sql, (
                account_id, order_id, position_id, trade_id,
                symbol, side, float(current_price), float(close_quantity), float(close_value),
                leverage, float(position_margin), float(fee), float(fee_rate),
                float(realized_pnl), float(pnl_pct), float(roi),
                float(entry_price), get_local_time()
            ))

            # 7. 更新持仓状态
            if close_quantity == quantity:
                # 全部平仓
                cursor.execute(
                    """UPDATE futures_positions
                    SET status = 'closed', close_time = %s,
                        realized_pnl = %s
                    WHERE id = %s""",
                    (get_local_time(), float(realized_pnl), position_id)
                )

                # 释放全部保证金
                released_margin = margin
            else:
                # 部分平仓
                remaining_quantity = quantity - close_quantity
                remaining_margin = margin * (remaining_quantity / quantity)

                cursor.execute(
                    """UPDATE futures_positions
                    SET quantity = %s, margin = %s,
                        realized_pnl = realized_pnl + %s
                    WHERE id = %s""",
                    (float(remaining_quantity), float(remaining_margin),
                     float(realized_pnl), position_id)
                )

                released_margin = margin - remaining_margin

            # 8. 更新账户余额和交易统计
            # 判断是盈利还是亏损
            is_winning_trade = realized_pnl > 0
            
            cursor.execute(
                """UPDATE paper_trading_accounts
                SET current_balance = current_balance + %s + %s,
                    frozen_balance = frozen_balance - %s,
                    realized_pnl = realized_pnl + %s,
                    total_trades = total_trades + 1,
                    winning_trades = winning_trades + IF(%s > 0, 1, 0),
                    losing_trades = losing_trades + IF(%s < 0, 1, 0)
                WHERE id = %s""",
                (float(released_margin), float(realized_pnl), float(released_margin),
                 float(realized_pnl), float(realized_pnl), float(realized_pnl), account_id)
            )
            
            # 更新胜率
            cursor.execute(
                """UPDATE paper_trading_accounts
                SET win_rate = (winning_trades / GREATEST(total_trades, 1)) * 100
                WHERE id = %s""",
                (account_id,)
            )

            # 9. 更新总权益（余额 + 冻结余额 + 持仓未实现盈亏）
            cursor.execute(
                """UPDATE paper_trading_accounts a
                SET a.total_equity = a.current_balance + a.frozen_balance + COALESCE((
                    SELECT SUM(p.unrealized_pnl) 
                    FROM futures_positions p 
                    WHERE p.account_id = a.id AND p.status = 'open'
                ), 0)
                WHERE a.id = %s""",
                (account_id,)
            )
            
            # 获取变化后的账户余额信息（用于资金管理记录）
            cursor.execute(
                "SELECT current_balance, frozen_balance FROM paper_trading_accounts WHERE id = %s",
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
            
            for pos in positions:
                # 将 id 映射为 position_id，保持与API一致
                if 'id' in pos and 'position_id' not in pos:
                    pos['position_id'] = pos['id']
                
                try:
                    # 使用实时价格更新持仓
                    current_price = self.get_current_price(pos['symbol'], use_realtime=True)
                    entry_price = Decimal(str(pos['entry_price']))
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
                    cursor_update.execute(
                        """UPDATE futures_positions
                        SET mark_price = %s,
                            unrealized_pnl = %s,
                            unrealized_pnl_pct = %s,
                            last_update_time = NOW()
                        WHERE id = %s""",
                        (float(current_price), float(unrealized_pnl), float(unrealized_pnl_pct), pos['id'])
                    )

                    pos['current_price'] = float(current_price)
                    pos['unrealized_pnl'] = float(unrealized_pnl)
                    pos['unrealized_pnl_pct'] = float(unrealized_pnl_pct)
                    
                except Exception as e:
                    logger.warning(f"更新持仓 {pos.get('symbol', 'unknown')} 价格和盈亏失败: {e}")
                    # 如果更新失败，至少设置默认值
                    pos['current_price'] = float(pos.get('mark_price', 0))
                    pos['unrealized_pnl'] = float(pos.get('unrealized_pnl', 0))
                    pos['unrealized_pnl_pct'] = float(pos.get('unrealized_pnl_pct', 0))
                
                # 转换 Decimal 类型为 float，确保所有数值字段都能正确序列化
                for key, value in pos.items():
                    if isinstance(value, Decimal):
                        pos[key] = float(value)
        
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
            cursor.execute("SELECT id FROM paper_trading_accounts")
            all_account_ids = [row['id'] for row in cursor.fetchall()]
            
            updated_count = 0
            for account_id in all_account_ids:
                try:
                    # 更新该账户的总权益
                    cursor.execute(
                        """UPDATE paper_trading_accounts a
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
