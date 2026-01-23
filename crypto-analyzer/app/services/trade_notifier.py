"""
实盘交易通知服务
通过Telegram发送开仓、平仓、止损止盈等交易通知
"""

import requests
import logging
from typing import Dict, Optional
from datetime import datetime
from decimal import Decimal

logger = logging.getLogger(__name__)


class TradeNotifier:
    """实盘交易通知器"""

    def __init__(self, config: Dict):
        """
        初始化通知器

        Args:
            config: 配置字典，需要包含 notifications.telegram 配置
        """
        self.config = config
        notifications_config = config.get('notifications', {})
        telegram_config = notifications_config.get('telegram', {})

        self.enabled = telegram_config.get('enabled', False)
        self.bot_token = telegram_config.get('bot_token', '')
        self.chat_id = str(telegram_config.get('chat_id', ''))  # 确保是字符串

        # 通知事件过滤
        notify_events = telegram_config.get('notify_events', [])
        self.notify_open = 'live_open' in notify_events or 'all' in notify_events
        self.notify_close = 'live_close' in notify_events or 'all' in notify_events
        self.notify_stop_loss = 'stop_loss' in notify_events or 'all' in notify_events
        self.notify_take_profit = 'take_profit' in notify_events or 'all' in notify_events

        if self.enabled and self.bot_token and self.chat_id:
            logger.info(f"✅ 实盘交易Telegram通知已启用 (chat_id: {self.chat_id[:6]}...)")
        else:
            logger.info("ℹ️ 实盘交易Telegram通知未启用")

    def _send_telegram(self, message: str, parse_mode: str = 'HTML') -> bool:
        """
        发送Telegram消息

        Args:
            message: 消息内容
            parse_mode: 解析模式 (HTML/Markdown)

        Returns:
            是否发送成功
        """
        if not self.enabled:
            logger.debug(f"Telegram通知未启用 (enabled={self.enabled})")
            return False
        if not self.bot_token:
            logger.warning(f"Telegram bot_token未配置")
            return False
        if not self.chat_id:
            logger.warning(f"Telegram chat_id未配置")
            return False

        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = {
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': parse_mode
            }

            response = requests.post(url, data=data, timeout=10)
            response.raise_for_status()

            logger.debug(f"Telegram通知发送成功")
            return True

        except Exception as e:
            logger.warning(f"Telegram通知发送失败: {e}")
            return False

    def notify_open_position(
        self,
        symbol: str,
        direction: str,
        quantity: float,
        entry_price: float,
        leverage: int = 1,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        margin: Optional[float] = None,
        strategy_name: Optional[str] = None,
        order_type: str = 'MARKET'
    ):
        """
        通知开仓

        Args:
            symbol: 交易对
            direction: 方向 (long/short/LONG/SHORT)
            quantity: 数量
            entry_price: 入场价格
            leverage: 杠杆
            stop_loss_price: 止损价
            take_profit_price: 止盈价
            margin: 保证金
            strategy_name: 策略名称
            order_type: 订单类型 (MARKET/LIMIT)
        """
        if not self.notify_open:
            logger.debug(f"开仓通知已禁用 (notify_open={self.notify_open})")
            return

        logger.info(f"准备发送开仓通知: {symbol} {direction} {quantity} @ {entry_price}")
        direction_lower = direction.lower()
        direction_emoji = "🟢" if direction_lower == 'long' else "🔴"
        direction_text = "做多" if direction_lower == 'long' else "做空"
        order_type_text = "市价" if order_type == 'MARKET' else "限价"

        # 计算持仓价值
        position_value = quantity * entry_price

        message = f"""
{direction_emoji} <b>【实盘开仓】{symbol}</b>

📌 方向: {direction_text}
💰 数量: {quantity:.6f}
💵 价格: ${entry_price:,.4f} ({order_type_text})
📊 杠杆: {leverage}x
💎 持仓价值: ${position_value:,.2f}
"""

        if margin:
            message += f"🔐 保证金: ${margin:,.2f}\n"

        if stop_loss_price:
            sl_pct = abs((stop_loss_price - entry_price) / entry_price * 100)
            message += f"🛡️ 止损: ${stop_loss_price:,.4f} ({sl_pct:.2f}%)\n"

        if take_profit_price:
            tp_pct = abs((take_profit_price - entry_price) / entry_price * 100)
            message += f"🎯 止盈: ${take_profit_price:,.4f} ({tp_pct:.2f}%)\n"

        if strategy_name:
            message += f"📋 策略: {strategy_name}\n"

        message += f"\n⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}"

        result = self._send_telegram(message)
        if result:
            logger.info(f"✅ 开仓通知已发送: {symbol}")
        else:
            logger.warning(f"⚠️ 开仓通知发送失败: {symbol}")

    def notify_close_position(
        self,
        symbol: str,
        direction: str,
        quantity: float,
        entry_price: float,
        exit_price: float,
        pnl: float,
        pnl_pct: float,
        reason: str = 'manual',
        hold_time: Optional[str] = None,
        strategy_name: Optional[str] = None,
        is_paper: bool = False
    ):
        """
        通知平仓

        Args:
            symbol: 交易对
            direction: 方向
            quantity: 数量
            entry_price: 入场价格
            exit_price: 出场价格
            pnl: 盈亏金额
            pnl_pct: 盈亏百分比
            reason: 平仓原因 (manual/stop_loss/take_profit/signal_reverse/liquidation)
            hold_time: 持仓时间
            strategy_name: 策略名称
            is_paper: 是否为模拟盘
        """
        # 模拟盘不发送通知
        if is_paper:
            logger.debug(f"模拟盘平仓不发送通知: {symbol}")
            return

        # 根据平仓原因判断是否通知
        if reason == 'stop_loss' and not self.notify_stop_loss:
            return
        if reason == 'take_profit' and not self.notify_take_profit:
            return
        if reason not in ['stop_loss', 'take_profit'] and not self.notify_close:
            return

        direction_lower = direction.lower()
        direction_text = "多单" if direction_lower == 'long' else "空单"

        # 盈亏emoji
        if pnl > 0:
            pnl_emoji = "💰"
            result_text = "盈利"
        elif pnl < 0:
            pnl_emoji = "💸"
            result_text = "亏损"
        else:
            pnl_emoji = "➖"
            result_text = "平本"

        # 平仓原因文本
        reason_map = {
            'manual': '手动平仓',
            'stop_loss': '🛡️ 止损触发',
            'take_profit': '🎯 止盈触发',
            'signal_reverse': '📊 信号反转',
            'liquidation': '⚠️ 强制平仓',
            'backtest_end': '回测结束'
        }
        reason_text = reason_map.get(reason, reason)

        # 区分模拟盘和实盘
        trade_type = "模拟盘平仓" if is_paper else "实盘平仓"

        message = f"""
{pnl_emoji} <b>【{trade_type}】{symbol}</b>

📌 类型: {direction_text}
📍 原因: {reason_text}
💵 入场价: ${entry_price:,.4f}
💵 出场价: ${exit_price:,.4f}
📊 数量: {quantity:.6f}

<b>{result_text}: {'+' if pnl > 0 else ''}{pnl:.2f} USDT ({pnl_pct:+.2f}%)</b>
"""

        if hold_time:
            message += f"⏱️ 持仓时间: {hold_time}\n"

        if strategy_name:
            message += f"📋 策略: {strategy_name}\n"

        message += f"\n⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}"

        self._send_telegram(message)

    def notify_order_placed(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        order_type: str = 'LIMIT'
    ):
        """
        通知限价单挂单

        Args:
            symbol: 交易对
            side: 买卖方向 (BUY/SELL)
            quantity: 数量
            price: 限价
            order_type: 订单类型
        """
        if not self.notify_open:
            return

        side_emoji = "🟢" if side == 'BUY' else "🔴"
        side_text = "买入" if side == 'BUY' else "卖出"

        message = f"""
📝 <b>【限价单挂单】{symbol}</b>

📌 方向: {side_text}
💰 数量: {quantity:.6f}
💵 限价: ${price:,.4f}
📋 类型: {order_type}

⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}
"""

        self._send_telegram(message)

    def notify_order_filled(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        order_type: str = 'LIMIT'
    ):
        """
        通知订单成交（限价单成交时）

        Args:
            symbol: 交易对
            side: 买卖方向 (BUY/SELL)
            quantity: 成交数量
            price: 成交价格
            order_type: 订单类型
        """
        if not self.notify_open:
            return

        side_emoji = "🟢" if side == 'BUY' else "🔴"
        side_text = "买入" if side == 'BUY' else "卖出"

        message = f"""
{side_emoji} <b>【订单成交】{symbol}</b>

📌 方向: {side_text}
💰 数量: {quantity:.6f}
💵 价格: ${price:,.4f}
📋 类型: {order_type}

⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}
"""

        self._send_telegram(message)

    def notify_stop_loss_set(
        self,
        symbol: str,
        direction: str,
        stop_price: float,
        quantity: float
    ):
        """
        通知止损单设置成功

        Args:
            symbol: 交易对
            direction: 方向 (long/short/LONG/SHORT)
            stop_price: 止损价格
            quantity: 数量
        """
        if not self.notify_stop_loss:
            logger.debug(f"止损通知已禁用 (notify_stop_loss={self.notify_stop_loss})")
            return

        direction_lower = direction.lower()
        direction_text = "多单" if direction_lower == 'long' else "空单"

        message = f"""
🛡️ <b>【止损单已设置】{symbol}</b>

📌 类型: {direction_text}
💰 数量: {quantity:.6f}
💵 止损价: ${stop_price:,.4f}

⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}
"""

        result = self._send_telegram(message)
        if result:
            logger.info(f"✅ 止损通知已发送: {symbol}")
        else:
            logger.warning(f"⚠️ 止损通知发送失败: {symbol}")

    def notify_take_profit_set(
        self,
        symbol: str,
        direction: str,
        take_profit_price: float,
        quantity: float
    ):
        """
        通知止盈单设置成功

        Args:
            symbol: 交易对
            direction: 方向 (long/short/LONG/SHORT)
            take_profit_price: 止盈价格
            quantity: 数量
        """
        if not self.notify_take_profit:
            logger.debug(f"止盈通知已禁用 (notify_take_profit={self.notify_take_profit})")
            return

        direction_lower = direction.lower()
        direction_text = "多单" if direction_lower == 'long' else "空单"

        message = f"""
🎯 <b>【止盈单已设置】{symbol}</b>

📌 类型: {direction_text}
💰 数量: {quantity:.6f}
💵 止盈价: ${take_profit_price:,.4f}

⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}
"""

        result = self._send_telegram(message)
        if result:
            logger.info(f"✅ 止盈通知已发送: {symbol}")
        else:
            logger.warning(f"⚠️ 止盈通知发送失败: {symbol}")

    def notify_error(self, symbol: str, error_type: str, error_message: str):
        """
        通知交易错误

        Args:
            symbol: 交易对
            error_type: 错误类型
            error_message: 错误信息
        """
        message = f"""
⚠️ <b>【交易错误】{symbol}</b>

❌ 类型: {error_type}
📝 信息: {error_message}

⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}
"""

        self._send_telegram(message)

    def notify_balance_update(
        self,
        total_equity: float,
        available_balance: float,
        unrealized_pnl: float
    ):
        """
        通知余额更新（可选，用于定期汇报）

        Args:
            total_equity: 账户权益
            available_balance: 可用余额
            unrealized_pnl: 未实现盈亏
        """
        pnl_emoji = "📈" if unrealized_pnl >= 0 else "📉"

        message = f"""
💼 <b>【账户状态】</b>

💰 总权益: ${total_equity:,.2f}
💵 可用余额: ${available_balance:,.2f}
{pnl_emoji} 未实现盈亏: {'+' if unrealized_pnl >= 0 else ''}{unrealized_pnl:,.2f}

⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}
"""

        self._send_telegram(message)


# 全局通知器实例（延迟初始化）
_trade_notifier: Optional[TradeNotifier] = None


def get_trade_notifier(config: Dict = None) -> Optional[TradeNotifier]:
    """
    获取交易通知器单例

    Args:
        config: 配置字典（首次调用时需要）

    Returns:
        TradeNotifier 实例
    """
    global _trade_notifier

    if _trade_notifier is None and config is not None:
        _trade_notifier = TradeNotifier(config)

    return _trade_notifier


def init_trade_notifier(config: Dict) -> TradeNotifier:
    """
    初始化交易通知器

    Args:
        config: 配置字典

    Returns:
        TradeNotifier 实例
    """
    global _trade_notifier
    _trade_notifier = TradeNotifier(config)
    return _trade_notifier
