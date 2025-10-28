"""
模拟合约交易系统
Contract Trading Simulator

功能：
- 模拟永续合约交易（支持做多/做空）
- 支持杠杆交易（1-125倍）
- 实时盈亏计算（已实现/未实现）
- 爆仓检测和强制平仓
- 止盈止损功能
- 资金费率模拟
- 完整的交易历史记录
"""

from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass, field
import asyncio
from loguru import logger


class OrderSide(Enum):
    """订单方向"""
    LONG = "LONG"   # 做多（买入开多）
    SHORT = "SHORT"  # 做空（卖出开空）


class OrderType(Enum):
    """订单类型"""
    MARKET = "MARKET"  # 市价单
    LIMIT = "LIMIT"    # 限价单


class OrderStatus(Enum):
    """订单状态"""
    PENDING = "PENDING"      # 待成交
    FILLED = "FILLED"        # 已成交
    CANCELLED = "CANCELLED"  # 已取消
    REJECTED = "REJECTED"    # 已拒绝


class PositionSide(Enum):
    """持仓方向"""
    LONG = "LONG"   # 多头
    SHORT = "SHORT"  # 空头
    BOTH = "BOTH"   # 双向持仓


@dataclass
class Order:
    """订单"""
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: Optional[float] = None  # 限价单价格
    leverage: int = 1
    stop_loss: Optional[float] = None  # 止损价格
    take_profit: Optional[float] = None  # 止盈价格
    status: OrderStatus = OrderStatus.PENDING
    filled_price: Optional[float] = None
    filled_quantity: float = 0
    created_at: datetime = field(default_factory=datetime.now)
    filled_at: Optional[datetime] = None
    fee: float = 0  # 手续费


@dataclass
class Position:
    """持仓"""
    symbol: str
    side: PositionSide
    quantity: float  # 持仓数量（张数）
    entry_price: float  # 开仓均价
    leverage: int  # 杠杆倍数
    liquidation_price: float  # 强平价格
    unrealized_pnl: float = 0  # 未实现盈亏
    margin: float = 0  # 保证金
    opened_at: datetime = field(default_factory=datetime.now)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


@dataclass
class Trade:
    """成交记录"""
    trade_id: str
    order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    fee: float
    pnl: float = 0  # 已实现盈亏
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Account:
    """账户"""
    account_id: str
    balance: float  # 账户余额（USDT）
    equity: float = 0  # 权益 = 余额 + 未实现盈亏
    margin_used: float = 0  # 已用保证金
    margin_available: float = 0  # 可用保证金
    margin_ratio: float = 0  # 保证金率 = 权益 / 已用保证金
    total_pnl: float = 0  # 总盈亏
    total_fee: float = 0  # 总手续费
    created_at: datetime = field(default_factory=datetime.now)


class ContractTradingSimulator:
    """模拟合约交易系统"""

    def __init__(
        self,
        initial_balance: float = 10000,
        maker_fee: float = 0.0002,  # Maker手续费 0.02%
        taker_fee: float = 0.0004,  # Taker手续费 0.04%
        funding_rate: float = 0.0001,  # 资金费率 0.01%
        max_leverage: int = 125,
        maintenance_margin_rate: float = 0.004,  # 维持保证金率 0.4%
    ):
        """
        初始化模拟交易系统

        Args:
            initial_balance: 初始资金（USDT）
            maker_fee: Maker手续费率
            taker_fee: Taker手续费率
            funding_rate: 资金费率（每8小时）
            max_leverage: 最大杠杆倍数
            maintenance_margin_rate: 维持保证金率
        """
        self.account = Account(
            account_id="SIMULATOR_001",
            balance=initial_balance,
            equity=initial_balance,
            margin_available=initial_balance
        )

        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.funding_rate = funding_rate
        self.max_leverage = max_leverage
        self.maintenance_margin_rate = maintenance_margin_rate

        # 订单和持仓
        self.orders: Dict[str, Order] = {}
        self.positions: Dict[str, Position] = {}  # key: symbol
        self.trades: List[Trade] = []

        # 统计
        self.order_counter = 0
        self.trade_counter = 0

        logger.info(f"🎮 模拟合约交易系统初始化完成")
        logger.info(f"   初始资金: ${initial_balance:,.2f} USDT")
        logger.info(f"   最大杠杆: {max_leverage}x")
        logger.info(f"   Maker费率: {maker_fee*100:.3f}%")
        logger.info(f"   Taker费率: {taker_fee*100:.3f}%")

    def _generate_order_id(self) -> str:
        """生成订单ID"""
        self.order_counter += 1
        return f"ORDER_{datetime.now().strftime('%Y%m%d')}_{self.order_counter:06d}"

    def _generate_trade_id(self) -> str:
        """生成成交ID"""
        self.trade_counter += 1
        return f"TRADE_{datetime.now().strftime('%Y%m%d')}_{self.trade_counter:06d}"

    def _calculate_margin(self, quantity: float, price: float, leverage: int) -> float:
        """
        计算所需保证金

        Args:
            quantity: 数量
            price: 价格
            leverage: 杠杆

        Returns:
            保证金金额
        """
        position_value = quantity * price
        margin = position_value / leverage
        return margin

    def _calculate_liquidation_price(
        self,
        side: PositionSide,
        entry_price: float,
        leverage: int,
        maintenance_margin_rate: float
    ) -> float:
        """
        计算强平价格

        Args:
            side: 持仓方向
            entry_price: 开仓价格
            leverage: 杠杆倍数
            maintenance_margin_rate: 维持保证金率

        Returns:
            强平价格
        """
        if side == PositionSide.LONG:
            # 多头强平价 = 开仓价 * (1 - 1/杠杆 + 维持保证金率)
            liquidation_price = entry_price * (1 - 1/leverage + maintenance_margin_rate)
        else:
            # 空头强平价 = 开仓价 * (1 + 1/杠杆 - 维持保证金率)
            liquidation_price = entry_price * (1 + 1/leverage - maintenance_margin_rate)

        return liquidation_price

    def _calculate_unrealized_pnl(
        self,
        side: PositionSide,
        quantity: float,
        entry_price: float,
        current_price: float
    ) -> float:
        """
        计算未实现盈亏

        Args:
            side: 持仓方向
            quantity: 持仓数量
            entry_price: 开仓价格
            current_price: 当前价格

        Returns:
            未实现盈亏
        """
        if side == PositionSide.LONG:
            # 多头盈亏 = (当前价 - 开仓价) * 数量
            pnl = (current_price - entry_price) * quantity
        else:
            # 空头盈亏 = (开仓价 - 当前价) * 数量
            pnl = (entry_price - current_price) * quantity

        return pnl

    def _update_account_equity(self, current_prices: Dict[str, float]):
        """
        更新账户权益

        Args:
            current_prices: 当前价格字典 {symbol: price}
        """
        total_unrealized_pnl = 0

        # 计算所有持仓的未实现盈亏
        for symbol, position in self.positions.items():
            if symbol in current_prices:
                current_price = current_prices[symbol]
                unrealized_pnl = self._calculate_unrealized_pnl(
                    position.side,
                    position.quantity,
                    position.entry_price,
                    current_price
                )
                position.unrealized_pnl = unrealized_pnl
                total_unrealized_pnl += unrealized_pnl

        # 更新账户权益
        self.account.equity = self.account.balance + total_unrealized_pnl
        self.account.margin_available = self.account.equity - self.account.margin_used

        # 计算保证金率
        if self.account.margin_used > 0:
            self.account.margin_ratio = self.account.equity / self.account.margin_used
        else:
            self.account.margin_ratio = float('inf')

    def create_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        leverage: int = 1,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None
    ) -> Optional[Order]:
        """
        创建订单

        Args:
            symbol: 交易对
            side: 订单方向（LONG/SHORT）
            quantity: 数量
            order_type: 订单类型
            price: 限价单价格
            leverage: 杠杆倍数
            stop_loss: 止损价格
            take_profit: 止盈价格

        Returns:
            订单对象或None（如果失败）
        """
        # 验证杠杆
        if leverage < 1 or leverage > self.max_leverage:
            logger.error(f"❌ 杠杆倍数无效: {leverage}x (范围: 1-{self.max_leverage})")
            return None

        # 验证限价单必须有价格
        if order_type == OrderType.LIMIT and price is None:
            logger.error(f"❌ 限价单必须指定价格")
            return None

        # 创建订单
        order = Order(
            order_id=self._generate_order_id(),
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            leverage=leverage,
            stop_loss=stop_loss,
            take_profit=take_profit
        )

        self.orders[order.order_id] = order

        logger.info(f"📝 创建订单: {order.order_id}")
        logger.info(f"   {symbol} | {side.value} | {quantity} 张 | {leverage}x")
        if order_type == OrderType.LIMIT:
            logger.info(f"   限价: ${price}")
        if stop_loss:
            logger.info(f"   止损: ${stop_loss}")
        if take_profit:
            logger.info(f"   止盈: ${take_profit}")

        return order

    async def execute_order(
        self,
        order_id: str,
        current_price: float
    ) -> bool:
        """
        执行订单

        Args:
            order_id: 订单ID
            current_price: 当前市场价格

        Returns:
            是否执行成功
        """
        if order_id not in self.orders:
            logger.error(f"❌ 订单不存在: {order_id}")
            return False

        order = self.orders[order_id]

        if order.status != OrderStatus.PENDING:
            logger.warning(f"⚠️  订单状态不是待成交: {order.status.value}")
            return False

        # 检查限价单价格
        if order.order_type == OrderType.LIMIT:
            if order.side == OrderSide.LONG and current_price > order.price:
                logger.debug(f"限价买单未触发: 当前价 ${current_price} > 限价 ${order.price}")
                return False
            elif order.side == OrderSide.SHORT and current_price < order.price:
                logger.debug(f"限价卖单未触发: 当前价 ${current_price} < 限价 ${order.price}")
                return False

        # 使用当前价或限价
        execution_price = order.price if order.order_type == OrderType.LIMIT else current_price

        # 计算所需保证金
        required_margin = self._calculate_margin(order.quantity, execution_price, order.leverage)

        # 检查保证金是否足够
        if required_margin > self.account.margin_available:
            logger.error(f"❌ 保证金不足")
            logger.error(f"   需要: ${required_margin:,.2f}")
            logger.error(f"   可用: ${self.account.margin_available:,.2f}")
            order.status = OrderStatus.REJECTED
            return False

        # 计算手续费
        fee_rate = self.maker_fee if order.order_type == OrderType.LIMIT else self.taker_fee
        fee = order.quantity * execution_price * fee_rate

        # 检查余额是否足够支付手续费
        if fee > self.account.balance:
            logger.error(f"❌ 余额不足支付手续费")
            order.status = OrderStatus.REJECTED
            return False

        # 扣除手续费
        self.account.balance -= fee
        self.account.total_fee += fee
        order.fee = fee

        # 更新订单状态
        order.status = OrderStatus.FILLED
        order.filled_price = execution_price
        order.filled_quantity = order.quantity
        order.filled_at = datetime.now()

        # 创建或更新持仓
        position_side = PositionSide.LONG if order.side == OrderSide.LONG else PositionSide.SHORT

        if order.symbol in self.positions:
            # 已有持仓 - 加仓或平仓
            position = self.positions[order.symbol]

            if position.side == position_side:
                # 加仓
                total_quantity = position.quantity + order.quantity
                total_value = (position.entry_price * position.quantity +
                              execution_price * order.quantity)
                new_entry_price = total_value / total_quantity

                position.quantity = total_quantity
                position.entry_price = new_entry_price
                position.margin += required_margin

                # 重新计算强平价
                position.liquidation_price = self._calculate_liquidation_price(
                    position.side,
                    position.entry_price,
                    position.leverage,
                    self.maintenance_margin_rate
                )

                logger.info(f"📈 加仓成功")
            else:
                # 平仓（反向订单）
                realized_pnl = self._calculate_unrealized_pnl(
                    position.side,
                    min(order.quantity, position.quantity),
                    position.entry_price,
                    execution_price
                )

                # 更新账户余额
                self.account.balance += realized_pnl
                self.account.total_pnl += realized_pnl

                if order.quantity >= position.quantity:
                    # 完全平仓
                    released_margin = position.margin
                    self.account.margin_used -= released_margin

                    logger.info(f"✅ 平仓成功")
                    logger.info(f"   已实现盈亏: ${realized_pnl:+,.2f}")

                    del self.positions[order.symbol]
                else:
                    # 部分平仓
                    close_ratio = order.quantity / position.quantity
                    released_margin = position.margin * close_ratio
                    self.account.margin_used -= released_margin

                    position.quantity -= order.quantity
                    position.margin -= released_margin

                    logger.info(f"📉 部分平仓")
                    logger.info(f"   已实现盈亏: ${realized_pnl:+,.2f}")
                    logger.info(f"   剩余持仓: {position.quantity} 张")

                # 记录成交
                trade = Trade(
                    trade_id=self._generate_trade_id(),
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.quantity,
                    price=execution_price,
                    fee=fee,
                    pnl=realized_pnl
                )
                self.trades.append(trade)

                return True
        else:
            # 新建持仓
            liquidation_price = self._calculate_liquidation_price(
                position_side,
                execution_price,
                order.leverage,
                self.maintenance_margin_rate
            )

            position = Position(
                symbol=order.symbol,
                side=position_side,
                quantity=order.quantity,
                entry_price=execution_price,
                leverage=order.leverage,
                liquidation_price=liquidation_price,
                margin=required_margin,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit
            )

            self.positions[order.symbol] = position
            self.account.margin_used += required_margin

            logger.info(f"🎯 开仓成功")

        logger.info(f"   成交价: ${execution_price:,.4f}")
        logger.info(f"   数量: {order.quantity} 张")
        logger.info(f"   保证金: ${required_margin:,.2f}")
        logger.info(f"   手续费: ${fee:,.2f}")

        if order.symbol in self.positions:
            pos = self.positions[order.symbol]
            logger.info(f"   强平价: ${pos.liquidation_price:,.4f}")

        # 记录成交
        trade = Trade(
            trade_id=self._generate_trade_id(),
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=execution_price,
            fee=fee
        )
        self.trades.append(trade)

        return True

    def check_liquidation(self, current_prices: Dict[str, float]) -> List[str]:
        """
        检查爆仓

        Args:
            current_prices: 当前价格字典

        Returns:
            被强平的持仓符号列表
        """
        liquidated = []

        for symbol, position in list(self.positions.items()):
            if symbol not in current_prices:
                continue

            current_price = current_prices[symbol]

            # 检查是否触及强平价
            is_liquidated = False
            if position.side == PositionSide.LONG:
                is_liquidated = current_price <= position.liquidation_price
            else:
                is_liquidated = current_price >= position.liquidation_price

            if is_liquidated:
                logger.warning(f"💥 触发强制平仓: {symbol}")
                logger.warning(f"   方向: {position.side.value}")
                logger.warning(f"   当前价: ${current_price:,.4f}")
                logger.warning(f"   强平价: ${position.liquidation_price:,.4f}")

                # 计算平仓盈亏
                realized_pnl = self._calculate_unrealized_pnl(
                    position.side,
                    position.quantity,
                    position.entry_price,
                    position.liquidation_price
                )

                # 扣除保证金（全部损失）
                self.account.balance += realized_pnl
                self.account.total_pnl += realized_pnl
                self.account.margin_used -= position.margin

                logger.warning(f"   损失: ${realized_pnl:,.2f}")

                # 移除持仓
                del self.positions[symbol]
                liquidated.append(symbol)

        return liquidated

    def check_stop_loss_take_profit(
        self,
        current_prices: Dict[str, float]
    ) -> List[Tuple[str, str]]:
        """
        检查止盈止损

        Args:
            current_prices: 当前价格字典

        Returns:
            触发的列表 [(symbol, type), ...]
        """
        triggered = []

        for symbol, position in list(self.positions.items()):
            if symbol not in current_prices:
                continue

            current_price = current_prices[symbol]

            # 检查止损
            if position.stop_loss:
                should_stop_loss = False
                if position.side == PositionSide.LONG:
                    should_stop_loss = current_price <= position.stop_loss
                else:
                    should_stop_loss = current_price >= position.stop_loss

                if should_stop_loss:
                    logger.info(f"🛑 触发止损: {symbol} @ ${current_price:,.4f}")
                    asyncio.create_task(self._close_position(symbol, current_price))
                    triggered.append((symbol, "STOP_LOSS"))
                    continue

            # 检查止盈
            if position.take_profit:
                should_take_profit = False
                if position.side == PositionSide.LONG:
                    should_take_profit = current_price >= position.take_profit
                else:
                    should_take_profit = current_price <= position.take_profit

                if should_take_profit:
                    logger.info(f"🎯 触发止盈: {symbol} @ ${current_price:,.4f}")
                    asyncio.create_task(self._close_position(symbol, current_price))
                    triggered.append((symbol, "TAKE_PROFIT"))

        return triggered

    async def _close_position(self, symbol: str, price: float):
        """
        平仓

        Args:
            symbol: 交易对
            price: 平仓价格
        """
        if symbol not in self.positions:
            return

        position = self.positions[symbol]

        # 创建反向订单
        close_side = OrderSide.SHORT if position.side == PositionSide.LONG else OrderSide.LONG

        order = self.create_order(
            symbol=symbol,
            side=close_side,
            quantity=position.quantity,
            order_type=OrderType.MARKET,
            leverage=position.leverage
        )

        if order:
            await self.execute_order(order.order_id, price)

    def get_account_info(self) -> Dict:
        """获取账户信息"""
        return {
            'account_id': self.account.account_id,
            'balance': self.account.balance,
            'equity': self.account.equity,
            'margin_used': self.account.margin_used,
            'margin_available': self.account.margin_available,
            'margin_ratio': self.account.margin_ratio,
            'total_pnl': self.account.total_pnl,
            'total_fee': self.account.total_fee,
            'positions_count': len(self.positions),
            'orders_count': len([o for o in self.orders.values() if o.status == OrderStatus.PENDING])
        }

    def get_positions(self) -> List[Dict]:
        """获取所有持仓"""
        return [
            {
                'symbol': pos.symbol,
                'side': pos.side.value,
                'quantity': pos.quantity,
                'entry_price': pos.entry_price,
                'leverage': pos.leverage,
                'liquidation_price': pos.liquidation_price,
                'unrealized_pnl': pos.unrealized_pnl,
                'margin': pos.margin,
                'pnl_percentage': (pos.unrealized_pnl / pos.margin * 100) if pos.margin > 0 else 0,
                'opened_at': pos.opened_at.isoformat()
            }
            for pos in self.positions.values()
        ]

    def get_trades(self, limit: int = 100) -> List[Dict]:
        """获取交易历史"""
        return [
            {
                'trade_id': trade.trade_id,
                'order_id': trade.order_id,
                'symbol': trade.symbol,
                'side': trade.side.value,
                'quantity': trade.quantity,
                'price': trade.price,
                'fee': trade.fee,
                'pnl': trade.pnl,
                'timestamp': trade.timestamp.isoformat()
            }
            for trade in self.trades[-limit:]
        ]

    def get_statistics(self) -> Dict:
        """获取交易统计"""
        total_trades = len(self.trades)
        winning_trades = len([t for t in self.trades if t.pnl > 0])
        losing_trades = len([t for t in self.trades if t.pnl < 0])

        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        total_profit = sum([t.pnl for t in self.trades if t.pnl > 0])
        total_loss = sum([t.pnl for t in self.trades if t.pnl < 0])

        return {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': win_rate,
            'total_profit': total_profit,
            'total_loss': total_loss,
            'net_pnl': self.account.total_pnl,
            'total_fee': self.account.total_fee,
            'roi': (self.account.total_pnl / 10000 * 100) if self.account.balance > 0 else 0
        }
