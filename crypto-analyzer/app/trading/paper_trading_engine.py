"""
模拟现货交易引擎
实现买入、卖出、持仓管理、盈亏计算等核心功能
"""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Optional, Tuple
import pymysql
from loguru import logger


class PaperTradingEngine:
    """模拟交易引擎"""

    def __init__(self, db_config: Dict, price_cache_service=None):
        """
        初始化交易引擎

        Args:
            db_config: 数据库配置
            price_cache_service: 价格缓存服务（可选，用于优化性能）
        """
        self.db_config = db_config
        self.fee_rate = Decimal('0.001')  # 手续费率 0.1%
        self.price_cache_service = price_cache_service  # 价格缓存服务

    def _get_connection(self):
        """获取数据库连接"""
        return pymysql.connect(
            host=self.db_config.get('host', 'localhost'),
            port=self.db_config.get('port', 3306),
            user=self.db_config.get('user', 'root'),
            password=self.db_config.get('password', ''),
            database=self.db_config.get('database', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
            read_timeout=10,
            write_timeout=10
        )

    def get_account(self, account_id: int = None) -> Optional[Dict]:
        """
        获取账户信息

        Args:
            account_id: 账户ID，None 则获取默认账户

        Returns:
            账户信息字典
        """
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
                if account_id:
                    cursor.execute(
                        "SELECT * FROM paper_trading_accounts WHERE id = %s",
                        (account_id,)
                    )
                else:
                    cursor.execute(
                        "SELECT * FROM paper_trading_accounts WHERE is_default = TRUE LIMIT 1"
                    )
                account = cursor.fetchone()
                
                # 转换 Decimal 类型为 float，确保所有数值字段都能正确序列化
                if account:
                    for key, value in account.items():
                        if isinstance(value, Decimal):
                            account[key] = float(value)
                
                return account
        finally:
            connection.close()

    def create_account(self, account_name: str, initial_balance: Decimal = Decimal('10000')) -> int:
        """
        创建新账户

        Args:
            account_name: 账户名称
            initial_balance: 初始资金

        Returns:
            账户ID
        """
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO paper_trading_accounts
                    (account_name, initial_balance, current_balance, total_equity)
                    VALUES (%s, %s, %s, %s)""",
                    (account_name, initial_balance, initial_balance, initial_balance)
                )
                conn.commit()
                return cursor.lastrowid
        finally:
            conn.close()

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
                
                # 优先从Binance现货API获取实时价格
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
                
                # 如果Binance失败，尝试从Gate.io获取
                try:
                    gate_symbol = symbol.replace('/', '_').upper()
                    response = session.get(
                        'https://api.gateio.ws/api/v4/spot/tickers',
                        params={'currency_pair': gate_symbol},
                        timeout=2
                    )
                    if response.status_code == 200:
                        data = response.json()
                        if data and len(data) > 0 and 'last' in data[0]:
                            price = Decimal(str(data[0]['last']))
                            logger.debug(f"从Gate.io获取实时价格: {symbol} = {price}")
                            return price
                except Exception as e:
                    logger.debug(f"Gate.io API获取失败: {e}")
                
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
            with connection.cursor() as cursor:
                # 从 price_data 表获取最新价格
                cursor.execute(
                    """SELECT price FROM price_data
                    WHERE symbol = %s
                    ORDER BY timestamp DESC
                    LIMIT 1""",
                    (symbol,)
                )
                result = cursor.fetchone()

                if result and result['price']:
                    price = Decimal(str(result['price']))
                    return price

                # 如果 price_data 没有数据，尝试从 kline_data 获取
                cursor.execute(
                    """SELECT close_price FROM kline_data
                    WHERE symbol = %s
                    ORDER BY open_time DESC
                    LIMIT 1""",
                    (symbol,)
                )
                result = cursor.fetchone()

                if result and result['close_price']:
                    price = Decimal(str(result['close_price']))
                    return price

                logger.warning(f"找不到 {symbol} 的价格数据")
                return Decimal('0')
        finally:
            connection.close()

    def place_order(self,
                   account_id: int,
                   symbol: str,
                   side: str,
                   quantity: Decimal,
                   order_type: str = 'MARKET',
                   price: Decimal = None,
                   order_source: str = 'manual',
                   signal_id: int = None,
                   pending_order_id: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
        """
        下单

        Args:
            account_id: 账户ID
            symbol: 交易对
            side: 订单方向 BUY/SELL
            quantity: 数量
            order_type: 订单类型 MARKET/LIMIT
            price: 限价单价格
            order_source: 订单来源
            signal_id: 信号ID
            pending_order_id: 待成交订单ID（如果是从待成交订单触发的，用于精确匹配）

        Returns:
            (是否成功, 消息, 订单ID)
        """
        conn = self._get_connection()
        try:
            # 1. 获取账户信息
            account = self.get_account(account_id)
            if not account:
                return False, "账户不存在", None

            if account['status'] != 'active':
                return False, "账户未激活", None

            # 2. 获取当前价格
            # 限价单和市价单都使用实时价格（确保价格判断准确）
            use_realtime_for_check = True
            current_price = self.get_current_price(symbol, use_realtime=use_realtime_for_check)
            if current_price == 0:
                return False, f"无法获取 {symbol} 的市场价格", None

            # 3. 限价单价格检查
            if order_type == 'LIMIT':
                if not price or price <= 0:
                    return False, "限价单必须指定价格", None
                
                # 检查限价单价格条件
                if side == 'BUY':
                    # 买单：当前价格必须 <= 限价（价格下跌到限价或以下时成交）
                    if current_price > price:
                        # 价格未达到限价，创建 PENDING 订单
                        order_id = f"ORDER_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
                        with conn.cursor() as cursor:
                            # 计算所需金额（基于限价）
                            total_amount = price * quantity
                            fee = total_amount * self.fee_rate
                            
                            # 检查余额是否足够
                            required_balance = total_amount + fee
                            if account['current_balance'] < required_balance:
                                return False, f"余额不足，需要 {required_balance:.2f} USDT，当前余额 {account['current_balance']:.2f} USDT", None
                            
                            # 创建 PENDING 状态的限价单
                            cursor.execute(
                                """INSERT INTO paper_trading_orders
                                (account_id, order_id, symbol, side, order_type, price, quantity,
                                 executed_quantity, total_amount, executed_amount, fee, status,
                                 avg_fill_price, fill_time, order_source, signal_id)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                                (account_id, order_id, symbol, side, order_type, price, quantity,
                                 0, total_amount, 0, fee, 'PENDING',
                                 None, None, order_source, signal_id)
                            )
                            conn.commit()
                            return True, f"限价买单已创建，当前价格 {current_price:.2f}，限价 {price:.2f}，价格达到限价时将自动成交", order_id
                else:  # SELL
                    # 卖单：当前价格必须 >= 限价（价格上涨到限价或以上时成交）
                    if current_price < price:
                        # 价格未达到限价，创建 PENDING 订单
                        order_id = f"ORDER_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
                        with conn.cursor() as cursor:
                            # 检查持仓
                            position = self._get_position(account_id, symbol)
                            if not position or position['available_quantity'] < quantity:
                                available = position['available_quantity'] if position else 0
                                return False, f"持仓不足，需要 {quantity} 个，当前可用 {available} 个", None
                            
                            # 计算交易金额和手续费（基于限价）
                            total_amount = price * quantity
                            fee = total_amount * self.fee_rate
                            
                            # 创建 PENDING 状态的限价单
                            cursor.execute(
                                """INSERT INTO paper_trading_orders
                                (account_id, order_id, symbol, side, order_type, price, quantity,
                                 executed_quantity, total_amount, executed_amount, fee, status,
                                 avg_fill_price, fill_time, order_source, signal_id)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                                (account_id, order_id, symbol, side, order_type, price, quantity,
                                 0, total_amount, 0, fee, 'PENDING',
                                 None, None, order_source, signal_id)
                            )
                            conn.commit()
                            return True, f"限价卖单已创建，当前价格 {current_price:.2f}，限价 {price:.2f}，价格达到限价时将自动成交", order_id
                
                # 价格满足条件，继续执行（使用限价作为执行价格）
                exec_price = price
            else:
                # 市价单（买入或卖出）：再次获取实时价格，确保使用最新价格成交
                try:
                    realtime_price = self.get_current_price(symbol, use_realtime=True)
                    if realtime_price and realtime_price > 0:
                        exec_price = realtime_price
                        side_name = "买入" if side == 'BUY' else "卖出"
                        logger.info(f"市价{side_name}使用实时价格成交: {symbol} {side} = {exec_price}")
                    else:
                        exec_price = current_price
                        side_name = "买入" if side == 'BUY' else "卖出"
                        logger.warning(f"市价{side_name}实时价格获取失败，使用缓存价格: {symbol} = {exec_price}")
                except Exception as e:
                    exec_price = current_price
                    side_name = "买入" if side == 'BUY' else "卖出"
                    logger.warning(f"市价{side_name}获取实时价格失败，使用之前获取的价格: {symbol}, {e}")

            # 4. 计算交易金额和手续费
            total_amount = exec_price * quantity
            fee = total_amount * self.fee_rate

            # 5. 检查资金和持仓
            if side == 'BUY':
                # 买入：检查余额
                required_balance = total_amount + fee
                if account['current_balance'] < required_balance:
                    return False, f"余额不足，需要 {required_balance:.2f} USDT，当前余额 {account['current_balance']:.2f} USDT", None

            elif side == 'SELL':
                # 卖出：检查持仓
                position = self._get_position(account_id, symbol)
                if not position or position['available_quantity'] < quantity:
                    available = position['available_quantity'] if position else 0
                    return False, f"持仓不足，需要 {quantity} 个，当前可用 {available} 个", None

            # 6. 生成订单ID
            order_id = f"ORDER_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
            trade_id = f"TRADE_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"

            with conn.cursor() as cursor:
                # 7. 创建订单记录
                cursor.execute(
                    """INSERT INTO paper_trading_orders
                    (account_id, order_id, symbol, side, order_type, price, quantity,
                     executed_quantity, total_amount, executed_amount, fee, status,
                     avg_fill_price, fill_time, order_source, signal_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (account_id, order_id, symbol, side, order_type, exec_price, quantity,
                     quantity, total_amount, total_amount, fee, 'FILLED',
                     exec_price, datetime.now(), order_source, signal_id)
                )

                # 7. 执行买入或卖出
                if side == 'BUY':
                    success, message = self._execute_buy(
                        cursor, account_id, symbol, quantity, exec_price, fee, order_id, trade_id
                    )
                else:
                    success, message = self._execute_sell(
                        cursor, account_id, symbol, quantity, exec_price, fee, order_id, trade_id
                    )

                if not success:
                    conn.rollback()
                    return False, message, None

                # 8. 检查是否有对应的待成交订单，如果有则标记为已执行
                # 优先通过pending_order_id精确匹配，如果没有则查找同交易对同方向的待成交订单
                if pending_order_id:
                    # 精确匹配：通过pending_order_id查找
                    cursor.execute(
                        """SELECT order_id FROM paper_trading_pending_orders
                        WHERE account_id = %s AND order_id = %s 
                        AND executed = FALSE AND status = 'PENDING'""",
                        (account_id, pending_order_id)
                    )
                    pending_order = cursor.fetchone()
                    if pending_order:
                        cursor.execute(
                            """UPDATE paper_trading_pending_orders
                            SET executed = TRUE, status = 'EXECUTED', executed_at = NOW(),
                                executed_order_id = %s, updated_at = NOW()
                            WHERE account_id = %s AND order_id = %s""",
                            (order_id, account_id, pending_order_id)
                        )
                        logger.info(f"待成交订单 {pending_order_id} 已标记为已执行，执行订单ID: {order_id}")
                else:
                    # 兼容旧逻辑：查找同交易对同方向的待成交订单（最早创建的）
                    cursor.execute(
                        """SELECT order_id FROM paper_trading_pending_orders
                        WHERE account_id = %s AND symbol = %s AND side = %s 
                        AND executed = FALSE AND status = 'PENDING'
                        ORDER BY created_at ASC LIMIT 1""",
                        (account_id, symbol, side)
                    )
                    pending_order = cursor.fetchone()
                    if pending_order:
                        cursor.execute(
                            """UPDATE paper_trading_pending_orders
                            SET executed = TRUE, status = 'EXECUTED', executed_at = NOW(),
                                executed_order_id = %s, updated_at = NOW()
                            WHERE account_id = %s AND order_id = %s""",
                            (order_id, account_id, pending_order['order_id'])
                        )
                        logger.info(f"待成交订单 {pending_order['order_id']} 已标记为已执行，执行订单ID: {order_id}")

                # 9. 提交事务
                conn.commit()
                logger.info(f"订单 {order_id} 执行成功: {side} {quantity} {symbol} @ {exec_price}")
                return True, f"订单执行成功，{side} {quantity} {symbol} @ {exec_price:.2f} USDT", order_id

        except Exception as e:
            conn.rollback()
            logger.error(f"下单失败: {e}")
            return False, f"下单失败: {str(e)}", None
        finally:
            conn.close()

    def _execute_buy(self, cursor, account_id: int, symbol: str, quantity: Decimal,
                    price: Decimal, fee: Decimal, order_id: str, trade_id: str) -> Tuple[bool, str]:
        """
        执行买入操作

        Args:
            cursor: 数据库游标
            account_id: 账户ID
            symbol: 交易对
            quantity: 数量
            price: 价格
            fee: 手续费
            order_id: 订单ID
            trade_id: 成交ID

        Returns:
            (是否成功, 消息)
        """
        total_cost = price * quantity + fee

        # 1. 扣除账户余额
        cursor.execute(
            """UPDATE paper_trading_accounts
            SET current_balance = current_balance - %s
            WHERE id = %s""",
            (total_cost, account_id)
        )

        # 2. 更新或创建持仓
        cursor.execute(
            "SELECT * FROM paper_trading_positions WHERE account_id = %s AND symbol = %s AND status = 'open'",
            (account_id, symbol)
        )
        position = cursor.fetchone()

        if position:
            # 已有持仓，更新平均成本
            old_quantity = Decimal(str(position['quantity']))
            old_cost = Decimal(str(position['total_cost']))
            new_quantity = old_quantity + quantity
            new_cost = old_cost + total_cost
            new_avg_price = new_cost / new_quantity
            # 计算市值和未实现盈亏（买入时当前价格等于买入价格，未实现盈亏为0）
            market_value = price * new_quantity
            unrealized_pnl = (price - new_avg_price) * new_quantity
            unrealized_pnl_pct = ((price - new_avg_price) / new_avg_price * 100) if new_avg_price > 0 else 0

            cursor.execute(
                """UPDATE paper_trading_positions
                SET quantity = %s,
                    available_quantity = available_quantity + %s,
                    avg_entry_price = %s,
                    total_cost = %s,
                    current_price = %s,
                    market_value = %s,
                    unrealized_pnl = %s,
                    unrealized_pnl_pct = %s,
                    last_update_time = %s
                WHERE id = %s""",
                (new_quantity, quantity, new_avg_price, new_cost, price, 
                 float(market_value), float(unrealized_pnl), float(unrealized_pnl_pct), 
                 datetime.now(), position['id'])
            )
        else:
            # 新建持仓（买入时当前价格等于买入价格，未实现盈亏为0）
            market_value = price * quantity
            unrealized_pnl = Decimal('0')
            unrealized_pnl_pct = Decimal('0')
            
            cursor.execute(
                """INSERT INTO paper_trading_positions
                (account_id, symbol, quantity, available_quantity, avg_entry_price,
                 total_cost, current_price, market_value, unrealized_pnl, unrealized_pnl_pct,
                 first_buy_time, last_update_time, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (account_id, symbol, quantity, quantity, price, total_cost, price,
                 float(market_value), float(unrealized_pnl), float(unrealized_pnl_pct),
                 datetime.now(), datetime.now(), 'open')
            )

        # 3. 创建交易记录
        cursor.execute(
            """INSERT INTO paper_trading_trades
            (account_id, order_id, trade_id, symbol, side, price, quantity,
             total_amount, fee, cost_price, trade_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (account_id, order_id, trade_id, symbol, 'BUY', price, quantity,
             price * quantity, fee, price, datetime.now())
        )

        # 4. 更新账户未实现盈亏、总盈亏和总盈亏百分比
        cursor.execute(
            """UPDATE paper_trading_accounts
            SET unrealized_pnl = COALESCE((
                SELECT SUM(p.unrealized_pnl) 
                FROM paper_trading_positions p 
                WHERE p.account_id = %s AND p.status = 'open'
            ), 0),
                total_profit_loss = realized_pnl + COALESCE((
                    SELECT SUM(p.unrealized_pnl) 
                    FROM paper_trading_positions p 
                    WHERE p.account_id = %s AND p.status = 'open'
                ), 0),
                total_profit_loss_pct = ((realized_pnl + COALESCE((
                    SELECT SUM(p.unrealized_pnl) 
                    FROM paper_trading_positions p 
                    WHERE p.account_id = %s AND p.status = 'open'
                ), 0)) / GREATEST(initial_balance, 1)) * 100
            WHERE id = %s""",
            (account_id, account_id, account_id, account_id)
        )
        
        # 5. 更新总权益（余额 + 持仓市值）
        cursor.execute(
            """UPDATE paper_trading_accounts a
            SET a.total_equity = a.current_balance + COALESCE((
                SELECT SUM(p.market_value) 
                FROM paper_trading_positions p 
                WHERE p.account_id = a.id AND p.status = 'open'
            ), 0)
            WHERE a.id = %s""",
            (account_id,)
        )

        # 6. 记录资金变动
        self._record_balance_change(cursor, account_id, 'trade', -total_cost, order_id,
                                    f"买入 {quantity} {symbol}")

        return True, "买入成功"

    def _execute_sell(self, cursor, account_id: int, symbol: str, quantity: Decimal,
                     price: Decimal, fee: Decimal, order_id: str, trade_id: str) -> Tuple[bool, str]:
        """
        执行卖出操作

        Returns:
            (是否成功, 消息)
        """
        # 1. 获取持仓
        cursor.execute(
            "SELECT * FROM paper_trading_positions WHERE account_id = %s AND symbol = %s AND status = 'open'",
            (account_id, symbol)
        )
        position = cursor.fetchone()

        if not position:
            return False, "没有持仓"

        # 2. 计算盈亏
        avg_cost = Decimal(str(position['avg_entry_price']))
        sell_amount = price * quantity
        cost_amount = avg_cost * quantity
        realized_pnl = sell_amount - cost_amount - fee
        pnl_pct = ((price - avg_cost) / avg_cost * 100)

        # 3. 增加账户余额并更新统计
        cursor.execute(
            """UPDATE paper_trading_accounts
            SET current_balance = current_balance + %s,
                realized_pnl = realized_pnl + %s,
                total_profit_loss = realized_pnl + unrealized_pnl,
                total_trades = total_trades + 1,
                winning_trades = winning_trades + IF(%s > 0, 1, 0),
                losing_trades = losing_trades + IF(%s < 0, 1, 0)
            WHERE id = %s""",
            (sell_amount - fee, realized_pnl, realized_pnl, realized_pnl, account_id)
        )

        # 4. 更新总盈亏百分比和胜率
        cursor.execute(
            """UPDATE paper_trading_accounts
            SET total_profit_loss_pct = ((total_profit_loss / GREATEST(initial_balance, 1)) * 100),
                win_rate = (winning_trades / GREATEST(total_trades, 1)) * 100
            WHERE id = %s""",
            (account_id,)
        )

        # 6. 更新持仓
        new_quantity = Decimal(str(position['quantity'])) - quantity

        if new_quantity <= 0:
            # 完全平仓
            cursor.execute(
                "UPDATE paper_trading_positions SET status = 'closed' WHERE id = %s",
                (position['id'],)
            )
        else:
            # 部分平仓，需要更新剩余持仓的市值和未实现盈亏
            new_total_cost = Decimal(str(position['total_cost'])) - cost_amount
            new_avg_price = new_total_cost / new_quantity
            market_value = price * new_quantity
            unrealized_pnl = (price - new_avg_price) * new_quantity
            unrealized_pnl_pct = ((price - new_avg_price) / new_avg_price * 100) if new_avg_price > 0 else 0
            
            cursor.execute(
                """UPDATE paper_trading_positions
                SET quantity = %s,
                    available_quantity = available_quantity - %s,
                    avg_entry_price = %s,
                    total_cost = %s,
                    current_price = %s,
                    market_value = %s,
                    unrealized_pnl = %s,
                    unrealized_pnl_pct = %s,
                    last_update_time = %s
                WHERE id = %s""",
                (new_quantity, quantity, new_avg_price, new_total_cost, price,
                 float(market_value), float(unrealized_pnl), float(unrealized_pnl_pct),
                 datetime.now(), position['id'])
            )

        # 7. 创建交易记录
        cursor.execute(
            """INSERT INTO paper_trading_trades
            (account_id, order_id, trade_id, symbol, side, price, quantity,
             total_amount, fee, cost_price, realized_pnl, pnl_pct, trade_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (account_id, order_id, trade_id, symbol, 'SELL', price, quantity,
             sell_amount, fee, avg_cost, realized_pnl, pnl_pct, datetime.now())
        )

        # 7.1 更新账户未实现盈亏、总盈亏和总盈亏百分比（卖出后可能还有剩余持仓）
        cursor.execute(
            """UPDATE paper_trading_accounts
            SET unrealized_pnl = COALESCE((
                SELECT SUM(p.unrealized_pnl) 
                FROM paper_trading_positions p 
                WHERE p.account_id = %s AND p.status = 'open'
            ), 0),
                total_profit_loss = realized_pnl + COALESCE((
                    SELECT SUM(p.unrealized_pnl) 
                    FROM paper_trading_positions p 
                    WHERE p.account_id = %s AND p.status = 'open'
                ), 0),
                total_profit_loss_pct = ((realized_pnl + COALESCE((
                    SELECT SUM(p.unrealized_pnl) 
                    FROM paper_trading_positions p 
                    WHERE p.account_id = %s AND p.status = 'open'
                ), 0)) / GREATEST(initial_balance, 1)) * 100
            WHERE id = %s""",
            (account_id, account_id, account_id, account_id)
        )
        
        # 7.2 更新总权益（余额 + 持仓市值）
        cursor.execute(
            """UPDATE paper_trading_accounts a
            SET a.total_equity = a.current_balance + COALESCE((
                SELECT SUM(p.market_value) 
                FROM paper_trading_positions p 
                WHERE p.account_id = a.id AND p.status = 'open'
            ), 0)
            WHERE a.id = %s""",
            (account_id,)
        )

        # 8. 记录资金变动
        self._record_balance_change(cursor, account_id, 'trade', sell_amount - fee, order_id,
                                    f"卖出 {quantity} {symbol}，盈亏: {realized_pnl:.2f} USDT")

        return True, f"卖出成功，盈亏: {realized_pnl:.2f} USDT ({pnl_pct:.2f}%)"

    def _get_position(self, account_id: int, symbol: str) -> Optional[Dict]:
        """获取持仓信息"""
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM paper_trading_positions WHERE account_id = %s AND symbol = %s AND status = 'open'",
                    (account_id, symbol)
                )
                return cursor.fetchone()
        finally:
            conn.close()

    def update_positions_value(self, account_id: int):
        """
        更新所有持仓的市值和盈亏（每次查询都创建新连接，确保获取最新数据）

        Args:
            account_id: 账户ID
        """
        # 每次查询都创建新连接，确保获取最新持仓数据（包括止盈止损）
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
                # 获取所有持仓
                cursor.execute(
                    "SELECT * FROM paper_trading_positions WHERE account_id = %s AND status = 'open'",
                    (account_id,)
                )
                positions = cursor.fetchall()

                total_unrealized_pnl = Decimal('0')

                for pos in positions:
                    symbol = pos['symbol']
                    quantity = Decimal(str(pos['quantity']))
                    avg_cost = Decimal(str(pos['avg_entry_price']))

                    # 获取当前价格（监控止盈止损时使用实时价格）
                    current_price = self.get_current_price(symbol, use_realtime=True)
                    if current_price == 0:
                        continue

                    # 计算市值和盈亏
                    market_value = current_price * quantity
                    unrealized_pnl = (current_price - avg_cost) * quantity
                    unrealized_pnl_pct = ((current_price - avg_cost) / avg_cost * 100)

                    # 检查止盈止损（使用实时价格）
                    stop_loss_price = pos.get('stop_loss_price')
                    take_profit_price = pos.get('take_profit_price')
                    should_close = False
                    close_reason = None
                    
                    # 检查止损
                    if stop_loss_price and Decimal(str(stop_loss_price)) > 0:
                        if current_price <= Decimal(str(stop_loss_price)):
                            should_close = True
                            close_reason = 'stop_loss'
                            logger.info(f"🛑 触发止损: {symbol} @ ${current_price:.8f} (止损价: ${stop_loss_price:.8f})")
                    
                    # 检查止盈
                    if not should_close and take_profit_price and Decimal(str(take_profit_price)) > 0:
                        if current_price >= Decimal(str(take_profit_price)):
                            should_close = True
                            close_reason = 'take_profit'
                            logger.info(f"🎯 触发止盈: {symbol} @ ${current_price:.8f} (止盈价: ${take_profit_price:.8f})")
                    
                    # 如果触发止盈止损，自动平仓
                    if should_close:
                        try:
                            # 获取持仓数量
                            available_qty = Decimal(str(pos['available_quantity']))
                            if available_qty > 0:
                                # 执行卖出平仓（使用 place_order 方法）
                                result = self.place_order(
                                    account_id=account_id,
                                    symbol=symbol,
                                    side='SELL',
                                    quantity=available_qty,
                                    order_type='MARKET',
                                    order_source=close_reason
                                )
                                if result[0]:
                                    logger.info(f"✅ {close_reason} 自动平仓成功: {symbol} {available_qty} @ ${current_price:.8f}")
                                else:
                                    logger.error(f"❌ {close_reason} 自动平仓失败: {symbol} - {result[1]}")
                        except Exception as e:
                            logger.error(f"❌ {close_reason} 自动平仓异常: {symbol} - {e}")
                            import traceback
                            traceback.print_exc()
                        continue  # 跳过更新，因为持仓已平仓
                    
                    # 更新持仓
                    cursor.execute(
                        """UPDATE paper_trading_positions
                        SET current_price = %s,
                            market_value = %s,
                            unrealized_pnl = %s,
                            unrealized_pnl_pct = %s
                        WHERE id = %s""",
                        (float(current_price), float(market_value), float(unrealized_pnl), float(unrealized_pnl_pct), pos['id'])
                    )

                    total_unrealized_pnl += unrealized_pnl

                # 更新账户未实现盈亏、总盈亏和总盈亏百分比
                cursor.execute(
                    """UPDATE paper_trading_accounts
                    SET unrealized_pnl = %s,
                        total_profit_loss = realized_pnl + %s,
                        total_profit_loss_pct = ((realized_pnl + %s) / GREATEST(initial_balance, 1)) * 100
                    WHERE id = %s""",
                    (float(total_unrealized_pnl), float(total_unrealized_pnl), float(total_unrealized_pnl), account_id)
                )

                # 计算总权益（余额 + 持仓市值）
                cursor.execute(
                    """UPDATE paper_trading_accounts a
                    SET a.total_equity = a.current_balance + COALESCE((
                        SELECT SUM(p.market_value) 
                        FROM paper_trading_positions p 
                        WHERE p.account_id = a.id AND p.status = 'open'
                    ), 0)
                    WHERE a.id = %s""",
                    (account_id,)
                    )

                connection.commit()

        finally:
            connection.close()

    def update_position_stop_loss_take_profit(
        self,
        account_id: int,
        symbol: str,
        stop_loss_price: Optional[Decimal] = None,
        take_profit_price: Optional[Decimal] = None
    ) -> Tuple[bool, str]:
        """
        更新持仓的止盈止损
        
        Args:
            account_id: 账户ID
            symbol: 交易对
            stop_loss_price: 止损价格（None表示清除）
            take_profit_price: 止盈价格（None表示清除）
            
        Returns:
            (是否成功, 消息)
        """
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                # 获取持仓
                position = self._get_position(account_id, symbol)
                if not position:
                    return False, f"持仓不存在: {symbol}"
                
                entry_price = Decimal(str(position['avg_entry_price']))
                
                # 验证止损价格
                if stop_loss_price is not None:
                    if stop_loss_price <= 0:
                        return False, "止损价格必须大于0"
                    # 现货只有做多，止损价格应该低于开仓价
                    if stop_loss_price >= entry_price:
                        return False, f"止损价格应该低于开仓价: {stop_loss_price} >= {entry_price}"
                
                # 验证止盈价格
                if take_profit_price is not None:
                    if take_profit_price <= 0:
                        return False, "止盈价格必须大于0"
                    # 现货只有做多，止盈价格应该高于开仓价
                    if take_profit_price <= entry_price:
                        return False, f"止盈价格应该高于开仓价: {take_profit_price} <= {entry_price}"
                
                # 更新止盈止损
                update_fields = []
                update_values = []
                
                if stop_loss_price is not None:
                    update_fields.append("stop_loss_price = %s")
                    update_values.append(stop_loss_price)
                elif stop_loss_price is None:
                    # 清除止损
                    update_fields.append("stop_loss_price = NULL")
                
                if take_profit_price is not None:
                    update_fields.append("take_profit_price = %s")
                    update_values.append(take_profit_price)
                elif take_profit_price is None:
                    # 清除止盈
                    update_fields.append("take_profit_price = NULL")
                
                if not update_fields:
                    return False, "没有需要更新的字段"
                
                update_values.extend([account_id, symbol])
                
                cursor.execute(
                    f"""UPDATE paper_trading_positions
                    SET {', '.join(update_fields)}
                    WHERE account_id = %s AND symbol = %s AND status = 'open'""",
                    update_values
                )
                
                conn.commit()
                
                msg_parts = []
                if stop_loss_price is not None:
                    msg_parts.append(f"止损: ${stop_loss_price:.8f}")
                elif stop_loss_price is None and position.get('stop_loss_price'):
                    msg_parts.append("止损已清除")
                
                if take_profit_price is not None:
                    msg_parts.append(f"止盈: ${take_profit_price:.8f}")
                elif take_profit_price is None and position.get('take_profit_price'):
                    msg_parts.append("止盈已清除")
                
                logger.info(f"✅ 更新止盈止损: {symbol} - {', '.join(msg_parts)}")
                return True, f"止盈止损更新成功: {', '.join(msg_parts)}"
                
        except Exception as e:
            logger.error(f"❌ 更新止盈止损失败: {symbol} - {e}")
            return False, f"更新失败: {str(e)}"
        finally:
            conn.close()

    def _record_balance_change(self, cursor, account_id: int, change_type: str,
                               change_amount: Decimal, order_id: str = None, notes: str = None):
        """记录资金变动历史"""
        # 获取当前账户快照
        cursor.execute("SELECT * FROM paper_trading_accounts WHERE id = %s", (account_id,))
        account = cursor.fetchone()

        cursor.execute(
            """INSERT INTO paper_trading_balance_history
            (account_id, balance, frozen_balance, total_equity, realized_pnl,
             unrealized_pnl, total_pnl, total_pnl_pct, change_type, change_amount,
             related_order_id, notes, snapshot_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (account_id, account['current_balance'], account['frozen_balance'],
             account['total_equity'], account['realized_pnl'], account['unrealized_pnl'],
             account['total_profit_loss'], account['total_profit_loss_pct'],
             change_type, change_amount, order_id, notes, datetime.now())
        )

    def get_account_summary(self, account_id: int) -> Dict:
        """
        获取账户摘要

        Returns:
            账户摘要信息
        """
        try:
            # 更新持仓市值
            self.update_positions_value(account_id)
        except Exception as e:
            logger.error(f"更新持仓市值失败: {e}")
            import traceback
            traceback.print_exc()
            # 继续执行，即使更新失败也返回账户信息

        account = self.get_account(account_id)
        if not account:
            logger.warning(f"账户 {account_id} 不存在")
            return {}

        # 每次查询都创建新连接，确保获取最新持仓数据（包括止盈止损）
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
                # 获取持仓列表
                cursor.execute(
                    """SELECT * FROM paper_trading_positions
                    WHERE account_id = %s AND status = 'open'
                    ORDER BY market_value DESC""",
                    (account_id,)
                )
                positions = cursor.fetchall()
                
                # 转换 Decimal 类型为 float，确保所有数值字段都能正确序列化
                for pos in positions:
                    for key, value in pos.items():
                        if isinstance(value, Decimal):
                            pos[key] = float(value)

                # 获取最近订单
                cursor.execute(
                    """SELECT * FROM paper_trading_orders
                    WHERE account_id = %s
                    ORDER BY created_at DESC LIMIT 10""",
                    (account_id,)
                )
                recent_orders = cursor.fetchall()

                # 获取最近交易
                cursor.execute(
                    """SELECT * FROM paper_trading_trades
                    WHERE account_id = %s
                    ORDER BY trade_time DESC LIMIT 10""",
                    (account_id,)
                )
                recent_trades = cursor.fetchall()

                return {
                    'account': account,
                    'positions': positions,
                    'recent_orders': recent_orders,
                    'recent_trades': recent_trades
                }
        finally:
            connection.close()

    def get_pending_orders(self, account_id: int, executed: bool = False) -> List[Dict]:
        """
        获取待成交订单列表

        Args:
            account_id: 账户ID
            executed: 是否只获取已执行的订单

        Returns:
            待成交订单列表
        """
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                if executed:
                    # 只获取已执行的订单，关联持仓表获取止盈止损价格
                    cursor.execute(
                        """SELECT 
                            o.*,
                            p.stop_loss_price,
                            p.take_profit_price
                        FROM paper_trading_pending_orders o
                        LEFT JOIN paper_trading_positions p ON o.symbol = p.symbol AND o.account_id = p.account_id AND p.status = 'open'
                        WHERE o.account_id = %s AND o.executed = TRUE
                        ORDER BY o.executed_at DESC""",
                        (account_id,)
                    )
                else:
                    # 只获取未执行的订单，且状态不是DELETED，关联持仓表获取止盈止损价格
                    cursor.execute(
                        """SELECT 
                            o.*,
                            p.stop_loss_price,
                            p.take_profit_price
                        FROM paper_trading_pending_orders o
                        LEFT JOIN paper_trading_positions p ON o.symbol = p.symbol AND o.account_id = p.account_id AND p.status = 'open'
                        WHERE o.account_id = %s AND o.executed = FALSE AND o.status != 'DELETED'
                        ORDER BY o.created_at DESC""",
                        (account_id,)
                    )
                orders = cursor.fetchall()
                return orders
        except Exception as e:
            logger.error(f"获取待成交订单失败: {e}")
            return []
        finally:
            conn.close()

    def get_cancelled_orders(self, account_id: int, limit: int = 50) -> List[Dict]:
        """
        获取已取消/已过期的订单列表

        Args:
            account_id: 账户ID
            limit: 返回的最大订单数

        Returns:
            已取消订单列表
        """
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """SELECT *
                    FROM paper_trading_pending_orders
                    WHERE account_id = %s AND status IN ('CANCELLED', 'EXPIRED')
                    ORDER BY updated_at DESC
                    LIMIT %s""",
                    (account_id, limit)
                )
                orders = cursor.fetchall()
                return orders
        except Exception as e:
            logger.error(f"获取已取消订单失败: {e}")
            return []
        finally:
            conn.close()

    def create_pending_order(
        self,
        account_id: int,
        order_id: str,
        symbol: str,
        side: str,
        quantity: Decimal,
        trigger_price: Decimal,
        order_source: str = 'auto',
        stop_loss_price: Optional[Decimal] = None,
        take_profit_price: Optional[Decimal] = None
    ) -> Tuple[bool, str]:
        """
        创建待成交订单

        Args:
            account_id: 账户ID
            order_id: 订单ID
            symbol: 交易对
            side: 订单方向 BUY/SELL
            quantity: 数量
            trigger_price: 触发价格
            order_source: 订单来源

        Returns:
            (是否成功, 消息)
        """
        conn = self._get_connection()
        try:
            # 1. 检查账户是否存在
            account = self.get_account(account_id)
            if not account:
                return False, "账户不存在"

            if account['status'] != 'active':
                return False, "账户未激活"

            # 2. 计算需要冻结的资金或数量
            with conn.cursor() as cursor:
                if side == 'BUY':
                    # 买入：需要冻结 USDT
                    total_cost = trigger_price * quantity
                    fee = total_cost * self.fee_rate
                    frozen_amount = total_cost + fee

                    # 检查余额是否足够
                    if account['current_balance'] < frozen_amount:
                        return False, f"余额不足，需要冻结 {frozen_amount:.2f} USDT，当前余额 {account['current_balance']:.2f} USDT"

                    # 冻结资金
                    cursor.execute(
                        """UPDATE paper_trading_accounts
                        SET current_balance = current_balance - %s,
                            frozen_balance = frozen_balance + %s
                        WHERE id = %s""",
                        (frozen_amount, frozen_amount, account_id)
                    )
                    frozen_quantity = Decimal('0')
                else:
                    # 卖出：需要冻结持仓数量
                    position = self._get_position(account_id, symbol)
                    if not position or position['available_quantity'] < quantity:
                        available = position['available_quantity'] if position else 0
                        return False, f"持仓不足，需要冻结 {quantity} 个，当前可用 {available} 个"

                    # 冻结持仓数量
                    cursor.execute(
                        """UPDATE paper_trading_positions
                        SET available_quantity = available_quantity - %s
                        WHERE account_id = %s AND symbol = %s AND status = 'open'""",
                        (quantity, account_id, symbol)
                    )
                    frozen_amount = Decimal('0')
                    frozen_quantity = quantity

                # 3. 创建待成交订单记录
                cursor.execute(
                    """INSERT INTO paper_trading_pending_orders
                    (account_id, order_id, symbol, side, quantity, trigger_price,
                     frozen_amount, frozen_quantity, status, executed, order_source, 
                     stop_loss_price, take_profit_price, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (account_id, order_id, symbol, side, quantity, trigger_price,
                     frozen_amount, frozen_quantity, 'PENDING', False, order_source,
                     stop_loss_price, take_profit_price, datetime.now())
                )

                conn.commit()
                logger.info(f"创建待成交订单成功: {order_id} - {side} {quantity} {symbol} @ {trigger_price}")
                return True, f"待成交订单创建成功"

        except Exception as e:
            conn.rollback()
            logger.error(f"创建待成交订单失败: {e}")
            return False, f"创建待成交订单失败: {str(e)}"
        finally:
            conn.close()

    def cancel_pending_order(self, account_id: int, order_id: str) -> Tuple[bool, str]:
        """
        撤销待成交订单

        Args:
            account_id: 账户ID
            order_id: 订单ID

        Returns:
            (是否成功, 消息)
        """
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                # 1. 获取待成交订单信息（排除已删除的）
                cursor.execute(
                    """SELECT * FROM paper_trading_pending_orders
                    WHERE account_id = %s AND order_id = %s AND executed = FALSE AND status != 'DELETED'""",
                    (account_id, order_id)
                )
                order = cursor.fetchone()

                if not order:
                    # 检查订单是否存在但状态不对
                    cursor.execute(
                        """SELECT status, executed FROM paper_trading_pending_orders
                        WHERE account_id = %s AND order_id = %s""",
                        (account_id, order_id)
                    )
                    existing_order = cursor.fetchone()
                    if existing_order:
                        logger.warning(f"订单存在但状态不符合撤销条件: order_id={order_id}, status={existing_order.get('status')}, executed={existing_order.get('executed')}")
                        return False, f"订单状态不符合撤销条件（状态: {existing_order.get('status')}, 已执行: {existing_order.get('executed')}）"
                    else:
                        logger.warning(f"订单不存在: account_id={account_id}, order_id={order_id}")
                        return False, "待成交订单不存在、已执行或已删除"

                # 2. 解冻资金或持仓
                if order['side'] == 'BUY':
                    # 买入订单：解冻 USDT
                    frozen_amount = Decimal(str(order['frozen_amount']))
                    cursor.execute(
                        """UPDATE paper_trading_accounts
                        SET current_balance = current_balance + %s,
                            frozen_balance = frozen_balance - %s
                        WHERE id = %s""",
                        (frozen_amount, frozen_amount, account_id)
                    )
                else:
                    # 卖出订单：解冻持仓数量
                    frozen_quantity = Decimal(str(order['frozen_quantity']))
                    cursor.execute(
                        """UPDATE paper_trading_positions
                        SET available_quantity = available_quantity + %s
                        WHERE account_id = %s AND symbol = %s AND status = 'open'""",
                        (frozen_quantity, account_id, order['symbol'])
                    )

                # 3. 软删除：将状态改为DELETED，而不是真正删除
                cursor.execute(
                    """UPDATE paper_trading_pending_orders
                    SET status = 'DELETED', updated_at = NOW()
                    WHERE account_id = %s AND order_id = %s""",
                    (account_id, order_id)
                )

                conn.commit()
                logger.info(f"撤销待成交订单成功: {order_id} (状态已改为DELETED)")
                return True, "待成交订单撤销成功"

        except Exception as e:
            conn.rollback()
            logger.error(f"撤销待成交订单失败: {e}")
            return False, f"撤销待成交订单失败: {str(e)}"
        finally:
            conn.close()
