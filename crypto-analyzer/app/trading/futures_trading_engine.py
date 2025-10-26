"""
模拟合约交易引擎
支持多空双向交易、杠杆、止盈止损
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from loguru import logger
import pymysql


class FuturesTradingEngine:
    """模拟合约交易引擎"""

    def __init__(self, db_config: dict):
        """初始化合约交易引擎"""
        self.db_config = db_config
        self.connection = None
        self._connect_db()

    def _connect_db(self):
        """连接数据库"""
        try:
            self.connection = pymysql.connect(
                host=self.db_config.get('host', 'localhost'),
                port=self.db_config.get('port', 3306),
                user=self.db_config.get('user', 'root'),
                password=self.db_config.get('password', ''),
                database=self.db_config.get('database', 'binance-data'),
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
            logger.info("合约交易引擎数据库连接成功")
        except Exception as e:
            logger.error(f"数据库连接失败: {e}")
            raise

    def _get_cursor(self):
        """获取数据库游标"""
        if not self.connection or not self.connection.open:
            self._connect_db()
        return self.connection.cursor()

    def get_current_price(self, symbol: str) -> Decimal:
        """
        获取当前市场价格

        Args:
            symbol: 交易对

        Returns:
            当前价格
        """
        cursor = self._get_cursor()
        try:
            # 尝试从1分钟K线获取最新价格
            cursor.execute(
                """SELECT close_price FROM kline_data
                WHERE symbol = %s AND timeframe = '1m'
                ORDER BY timestamp DESC LIMIT 1""",
                (symbol,)
            )
            result = cursor.fetchone()
            if result and result['close_price']:
                return Decimal(str(result['close_price']))

            # 回退到价格表
            cursor.execute(
                """SELECT price FROM price_data
                WHERE symbol = %s
                ORDER BY timestamp DESC LIMIT 1""",
                (symbol,)
            )
            result = cursor.fetchone()
            if result and result['price']:
                return Decimal(str(result['price']))

            raise ValueError(f"无法获取{symbol}的价格")

        except Exception as e:
            logger.error(f"获取价格失败: {e}")
            raise

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
        stop_loss_pct: Optional[Decimal] = None,
        take_profit_pct: Optional[Decimal] = None,
        source: str = 'manual',
        signal_id: Optional[int] = None
    ) -> Dict:
        """
        开仓

        Args:
            account_id: 账户ID
            symbol: 交易对
            position_side: LONG(多头) 或 SHORT(空头)
            quantity: 开仓数量（币数）
            leverage: 杠杆倍数
            stop_loss_pct: 止损百分比
            take_profit_pct: 止盈百分比
            source: 来源
            signal_id: 信号ID

        Returns:
            开仓结果
        """
        cursor = self._get_cursor()

        try:
            # 1. 获取当前价格
            current_price = self.get_current_price(symbol)

            # 2. 计算名义价值和所需保证金
            notional_value = current_price * quantity
            margin_required = notional_value / Decimal(leverage)

            # 3. 计算手续费 (0.04%)
            fee_rate = Decimal('0.0004')
            fee = notional_value * fee_rate

            # 4. 检查账户余额
            cursor.execute(
                "SELECT current_balance FROM paper_trading_accounts WHERE id = %s",
                (account_id,)
            )
            account = cursor.fetchone()
            if not account:
                raise ValueError(f"账户 {account_id} 不存在")

            available_balance = Decimal(str(account['current_balance']))
            if available_balance < (margin_required + fee):
                raise ValueError(
                    f"余额不足。需要: {margin_required + fee:.2f} USDT, "
                    f"可用: {available_balance:.2f} USDT"
                )

            # 5. 计算强平价和止盈止损价
            liquidation_price = self.calculate_liquidation_price(
                current_price, position_side, leverage
            )

            if stop_loss_pct:
                if position_side == 'LONG':
                    stop_loss_price = current_price * (1 - stop_loss_pct / 100)
                else:
                    stop_loss_price = current_price * (1 + stop_loss_pct / 100)
            else:
                stop_loss_price = None

            if take_profit_pct:
                if position_side == 'LONG':
                    take_profit_price = current_price * (1 + take_profit_pct / 100)
                else:
                    take_profit_price = current_price * (1 - take_profit_pct / 100)
            else:
                take_profit_price = None

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
                float(current_price), float(current_price), float(liquidation_price),
                float(stop_loss_price) if stop_loss_price else None,
                float(take_profit_price) if take_profit_price else None,
                float(stop_loss_pct) if stop_loss_pct else None,
                float(take_profit_pct) if take_profit_pct else None,
                datetime.now(), source, signal_id
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
                    %s, 'MARKET', %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, 'FILLED',
                    %s, %s,
                    %s, %s
                )
            """

            cursor.execute(order_sql, (
                account_id, order_id, position_id, symbol,
                side, leverage,
                float(current_price), float(quantity), float(quantity),
                float(margin_required), float(notional_value), float(notional_value),
                float(fee), float(fee_rate),
                float(current_price), datetime.now(),
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
                symbol, side, float(current_price), float(quantity), float(notional_value),
                leverage, float(margin_required), float(fee), float(fee_rate),
                float(current_price), datetime.now()
            ))

            # 9. 更新账户余额
            new_balance = available_balance - margin_required - fee
            cursor.execute(
                """UPDATE paper_trading_accounts
                SET current_balance = %s, frozen_balance = frozen_balance + %s
                WHERE id = %s""",
                (float(new_balance), float(margin_required), account_id)
            )

            self.connection.commit()

            logger.info(
                f"开仓成功: {symbol} {position_side} {quantity} @ {current_price}, "
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
                'entry_price': float(current_price),
                'leverage': leverage,
                'margin': float(margin_required),
                'fee': float(fee),
                'liquidation_price': float(liquidation_price),
                'stop_loss_price': float(stop_loss_price) if stop_loss_price else None,
                'take_profit_price': float(take_profit_price) if take_profit_price else None,
                'message': f"开{position_side}仓成功"
            }

        except Exception as e:
            self.connection.rollback()
            logger.error(f"开仓失败: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f"开仓失败: {str(e)}"
            }

    def close_position(
        self,
        position_id: int,
        close_quantity: Optional[Decimal] = None,
        reason: str = 'manual'
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
        cursor = self._get_cursor()

        try:
            # 1. 获取持仓信息
            cursor.execute(
                """SELECT * FROM futures_positions WHERE id = %s AND status = 'open'""",
                (position_id,)
            )
            position = cursor.fetchone()

            if not position:
                raise ValueError(f"持仓 {position_id} 不存在或已平仓")

            symbol = position['symbol']
            position_side = position['position_side']
            account_id = position['account_id']
            entry_price = Decimal(str(position['entry_price']))
            quantity = Decimal(str(position['quantity']))
            leverage = position['leverage']
            margin = Decimal(str(position['margin']))

            # 如果没指定平仓数量，则全部平仓
            if close_quantity is None:
                close_quantity = quantity

            if close_quantity > quantity:
                raise ValueError(f"平仓数量{close_quantity}大于持仓数量{quantity}")

            # 2. 获取当前价格
            current_price = self.get_current_price(symbol)

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
            pnl_pct = (pnl / open_value) * 100

            # ROI = 盈亏 / 保证金 (杠杆收益率)
            position_margin = margin * (close_quantity / quantity)
            roi = (pnl / position_margin) * 100

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
                float(current_price), datetime.now(),
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
                float(entry_price), datetime.now()
            ))

            # 7. 更新持仓状态
            if close_quantity == quantity:
                # 全部平仓
                cursor.execute(
                    """UPDATE futures_positions
                    SET status = 'closed', close_time = %s,
                        realized_pnl = %s
                    WHERE id = %s""",
                    (datetime.now(), float(realized_pnl), position_id)
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

            # 8. 更新账户余额
            cursor.execute(
                """UPDATE paper_trading_accounts
                SET current_balance = current_balance + %s + %s,
                    frozen_balance = frozen_balance - %s,
                    realized_pnl = realized_pnl + %s
                WHERE id = %s""",
                (float(released_margin), float(realized_pnl), float(released_margin),
                 float(realized_pnl), account_id)
            )

            self.connection.commit()

            logger.info(
                f"平仓成功: {symbol} {position_side} {close_quantity} @ {current_price}, "
                f"盈亏{realized_pnl:.2f} USDT ({pnl_pct:.2f}%), ROI {roi:.2f}%"
            )

            return {
                'success': True,
                'order_id': order_id,
                'trade_id': trade_id,
                'symbol': symbol,
                'position_side': position_side,
                'close_quantity': float(close_quantity),
                'close_price': float(current_price),
                'entry_price': float(entry_price),
                'realized_pnl': float(realized_pnl),
                'pnl_pct': float(pnl_pct),
                'roi': float(roi),
                'fee': float(fee),
                'message': f"平仓成功，盈亏{realized_pnl:.2f} USDT ({pnl_pct:.2f}%)"
            }

        except Exception as e:
            self.connection.rollback()
            logger.error(f"平仓失败: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f"平仓失败: {str(e)}"
            }

    def get_open_positions(self, account_id: int) -> List[Dict]:
        """获取账户的所有持仓"""
        cursor = self._get_cursor()

        cursor.execute(
            """SELECT * FROM futures_positions
            WHERE account_id = %s AND status = 'open'
            ORDER BY open_time DESC""",
            (account_id,)
        )

        positions = cursor.fetchall()

        # 更新每个持仓的当前盈亏
        for pos in positions:
            try:
                current_price = self.get_current_price(pos['symbol'])
                entry_price = Decimal(str(pos['entry_price']))
                quantity = Decimal(str(pos['quantity']))

                if pos['position_side'] == 'LONG':
                    unrealized_pnl = (current_price - entry_price) * quantity
                else:
                    unrealized_pnl = (entry_price - current_price) * quantity

                pos['current_price'] = float(current_price)
                pos['unrealized_pnl'] = float(unrealized_pnl)
                pos['unrealized_pnl_pct'] = float((unrealized_pnl / (entry_price * quantity)) * 100)
            except:
                pass

        return positions

    def __del__(self):
        """关闭数据库连接"""
        if self.connection and self.connection.open:
            self.connection.close()
