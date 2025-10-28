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
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                if account_id:
                    cursor.execute(
                        "SELECT * FROM paper_trading_accounts WHERE id = %s",
                        (account_id,)
                    )
                else:
                    cursor.execute(
                        "SELECT * FROM paper_trading_accounts WHERE is_default = TRUE LIMIT 1"
                    )
                return cursor.fetchone()
        finally:
            conn.close()

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

    def get_current_price(self, symbol: str) -> Decimal:
        """
        获取当前市场价格（优化版：优先使用缓存）

        Args:
            symbol: 交易对

        Returns:
            当前价格
        """
        # 优先使用价格缓存服务（避免数据库阻塞）
        if self.price_cache_service:
            price = self.price_cache_service.get_price(symbol)

            if price > 0:
                logger.debug(f"✅ {symbol} 从缓存获取价格: {price}")
                return price
            else:
                logger.warning(f"⚠️  {symbol} 缓存未命中，回退到数据库查询")

        # 回退到数据库查询（如果缓存不可用或未命中）
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
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
                    logger.debug(f"{symbol} 从数据库获取价格: {price}")
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
                    logger.debug(f"{symbol} 从K线获取价格: {price}")
                    return price

                logger.warning(f"找不到 {symbol} 的价格数据")
                return Decimal('0')
        finally:
            conn.close()

    def place_order(self,
                   account_id: int,
                   symbol: str,
                   side: str,
                   quantity: Decimal,
                   order_type: str = 'MARKET',
                   price: Decimal = None,
                   order_source: str = 'manual',
                   signal_id: int = None) -> Tuple[bool, str, Optional[str]]:
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
            if order_type == 'MARKET':
                exec_price = self.get_current_price(symbol)
                if exec_price == 0:
                    return False, f"无法获取 {symbol} 的市场价格", None
            else:
                exec_price = price

            # 3. 计算交易金额和手续费
            total_amount = exec_price * quantity
            fee = total_amount * self.fee_rate

            # 4. 检查资金和持仓
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

            # 5. 生成订单ID
            order_id = f"ORDER_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
            trade_id = f"TRADE_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"

            with conn.cursor() as cursor:
                # 6. 创建订单记录
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

                # 8. 提交事务
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

            cursor.execute(
                """UPDATE paper_trading_positions
                SET quantity = %s,
                    available_quantity = available_quantity + %s,
                    avg_entry_price = %s,
                    total_cost = %s,
                    current_price = %s,
                    last_update_time = %s
                WHERE id = %s""",
                (new_quantity, quantity, new_avg_price, new_cost, price, datetime.now(), position['id'])
            )
        else:
            # 新建持仓
            cursor.execute(
                """INSERT INTO paper_trading_positions
                (account_id, symbol, quantity, available_quantity, avg_entry_price,
                 total_cost, current_price, first_buy_time, last_update_time, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (account_id, symbol, quantity, quantity, price, total_cost, price,
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

        # 4. 记录资金变动
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

        # 3. 增加账户余额
        cursor.execute(
            """UPDATE paper_trading_accounts
            SET current_balance = current_balance + %s,
                realized_pnl = realized_pnl + %s,
                total_profit_loss = total_profit_loss + %s,
                total_trades = total_trades + 1,
                winning_trades = winning_trades + IF(%s > 0, 1, 0),
                losing_trades = losing_trades + IF(%s < 0, 1, 0)
            WHERE id = %s""",
            (sell_amount - fee, realized_pnl, realized_pnl, realized_pnl, realized_pnl, account_id)
        )

        # 4. 更新胜率
        cursor.execute(
            """UPDATE paper_trading_accounts
            SET win_rate = (winning_trades / GREATEST(total_trades, 1)) * 100
            WHERE id = %s""",
            (account_id,)
        )

        # 5. 更新持仓
        new_quantity = Decimal(str(position['quantity'])) - quantity

        if new_quantity <= 0:
            # 完全平仓
            cursor.execute(
                "UPDATE paper_trading_positions SET status = 'closed' WHERE id = %s",
                (position['id'],)
            )
        else:
            # 部分平仓
            new_total_cost = Decimal(str(position['total_cost'])) - cost_amount
            cursor.execute(
                """UPDATE paper_trading_positions
                SET quantity = %s,
                    available_quantity = available_quantity - %s,
                    total_cost = %s,
                    current_price = %s,
                    last_update_time = %s
                WHERE id = %s""",
                (new_quantity, quantity, new_total_cost, price, datetime.now(), position['id'])
            )

        # 6. 创建交易记录
        cursor.execute(
            """INSERT INTO paper_trading_trades
            (account_id, order_id, trade_id, symbol, side, price, quantity,
             total_amount, fee, cost_price, realized_pnl, pnl_pct, trade_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (account_id, order_id, trade_id, symbol, 'SELL', price, quantity,
             sell_amount, fee, avg_cost, realized_pnl, pnl_pct, datetime.now())
        )

        # 7. 记录资金变动
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
        更新所有持仓的市值和盈亏

        Args:
            account_id: 账户ID
        """
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
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

                    # 获取当前价格
                    current_price = self.get_current_price(symbol)
                    if current_price == 0:
                        continue

                    # 计算市值和盈亏
                    market_value = current_price * quantity
                    unrealized_pnl = (current_price - avg_cost) * quantity
                    unrealized_pnl_pct = ((current_price - avg_cost) / avg_cost * 100)

                    # 更新持仓
                    cursor.execute(
                        """UPDATE paper_trading_positions
                        SET current_price = %s,
                            market_value = %s,
                            unrealized_pnl = %s,
                            unrealized_pnl_pct = %s
                        WHERE id = %s""",
                        (current_price, market_value, unrealized_pnl, unrealized_pnl_pct, pos['id'])
                    )

                    total_unrealized_pnl += unrealized_pnl

                # 更新账户
                cursor.execute(
                    """UPDATE paper_trading_accounts
                    SET unrealized_pnl = %s,
                        total_profit_loss = realized_pnl + %s,
                        total_profit_loss_pct = ((realized_pnl + %s) / initial_balance) * 100
                    WHERE id = %s""",
                    (total_unrealized_pnl, total_unrealized_pnl, total_unrealized_pnl, account_id)
                )

                # 计算总权益
                cursor.execute(
                    """SELECT
                        current_balance,
                        COALESCE(SUM(market_value), 0) as total_position_value
                    FROM paper_trading_accounts a
                    LEFT JOIN paper_trading_positions p ON a.id = p.account_id AND p.status = 'open'
                    WHERE a.id = %s
                    GROUP BY a.id, a.current_balance""",
                    (account_id,)
                )
                result = cursor.fetchone()
                if result:
                    total_equity = Decimal(str(result['current_balance'])) + Decimal(str(result['total_position_value'] or 0))
                    cursor.execute(
                        "UPDATE paper_trading_accounts SET total_equity = %s WHERE id = %s",
                        (total_equity, account_id)
                    )

                conn.commit()

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
        # 更新持仓市值
        self.update_positions_value(account_id)

        account = self.get_account(account_id)
        if not account:
            return {}

        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                # 获取持仓列表
                cursor.execute(
                    """SELECT * FROM paper_trading_positions
                    WHERE account_id = %s AND status = 'open'
                    ORDER BY market_value DESC""",
                    (account_id,)
                )
                positions = cursor.fetchall()

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
            conn.close()
