"""
Paper trade notifications via Telegram.
Live open/close are not sent. Ops alerts still use send_message.
All Telegram copy is English.
"""

import requests
import logging
from typing import Dict, Optional
from datetime import datetime

from app.utils.position_time import utc_now_naive

logger = logging.getLogger(__name__)


def format_hold_time_from_open(open_time) -> Optional[str]:
    """Build an English hold-duration string from open_time."""
    if not open_time:
        return None
    try:
        if isinstance(open_time, str):
            open_time = datetime.strptime(open_time[:19], "%Y-%m-%d %H:%M:%S")
        hold_duration = utc_now_naive() - open_time
        hours, remainder = divmod(hold_duration.total_seconds(), 3600)
        minutes = int(remainder // 60)
        if hours >= 24:
            days = int(hours // 24)
            hours = int(hours % 24)
            return f"{days}d {hours}h {minutes}m"
        if hours >= 1:
            return f"{int(hours)}h {minutes}m"
        return f"{minutes}m"
    except Exception:
        return None


class TradeNotifier:
    """Telegram notifier for paper trades and ops alerts."""

    def __init__(self, config: Dict):
        self.config = config
        notifications_config = config.get("notifications", {})
        telegram_config = notifications_config.get("telegram", {})

        self.enabled = telegram_config.get("enabled", False)
        self.bot_token = telegram_config.get("bot_token", "")
        self.chat_id = str(telegram_config.get("chat_id", ""))

        notify_events = telegram_config.get("notify_events", [])
        self.notify_open = (
            "paper_open" in notify_events
            or "live_open" in notify_events
            or "all" in notify_events
        )
        self.notify_close = (
            "paper_close" in notify_events
            or "live_close" in notify_events
            or "all" in notify_events
        )
        self.notify_stop_loss = "stop_loss" in notify_events or "all" in notify_events
        self.notify_take_profit = "take_profit" in notify_events or "all" in notify_events

        if self.enabled and self.bot_token and self.chat_id:
            logger.info(f"Paper-trade Telegram enabled (chat_id: {self.chat_id[:6]}...)")
        else:
            logger.info("Paper-trade Telegram disabled")

    def _send_telegram(self, message: str, parse_mode: str = "HTML") -> bool:
        if not self.enabled:
            logger.debug(f"Telegram disabled (enabled={self.enabled})")
            return False
        if not self.bot_token:
            logger.warning("Telegram bot_token not configured")
            return False
        if not self.chat_id:
            logger.warning("Telegram chat_id not configured")
            return False

        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode,
            }
            response = requests.post(url, data=data, timeout=10)
            response.raise_for_status()
            logger.debug("Telegram message sent")
            return True
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")
            return False

    def send_message(self, text: str) -> bool:
        """Send a free-form message (circuit breakers / ops alerts)."""
        return self._send_telegram(text, parse_mode="HTML")

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
        order_type: str = "MARKET",
        is_paper: bool = False,
    ):
        """Notify a paper open. Live opens are not sent."""
        if not is_paper:
            logger.debug(f"Skip Telegram for live open: {symbol}")
            return
        if not self.notify_open:
            logger.debug(f"Open notify disabled (notify_open={self.notify_open})")
            return

        logger.info(f"Sending paper open notify: {symbol} {direction} {quantity} @ {entry_price}")
        direction_lower = direction.lower()
        direction_emoji = "🟢" if direction_lower == "long" else "🔴"
        direction_text = "LONG" if direction_lower == "long" else "SHORT"
        order_type_text = "Market" if order_type == "MARKET" else "Limit"
        position_value = quantity * entry_price

        message = f"""
{direction_emoji} <b>[OPEN] {symbol}</b>

📌 Side: {direction_text}
💰 Qty: {quantity:.6f}
💵 Price: ${entry_price:,.4f} ({order_type_text})
📊 Leverage: {leverage}x
💎 Notional: ${position_value:,.2f}
"""

        if margin:
            message += f"🔐 Margin: ${margin:,.2f}\n"

        if stop_loss_price:
            sl_pct = abs((stop_loss_price - entry_price) / entry_price * 100)
            message += f"🛡️ Stop loss: ${stop_loss_price:,.4f} ({sl_pct:.2f}%)\n"

        if take_profit_price:
            tp_pct = abs((take_profit_price - entry_price) / entry_price * 100)
            message += f"🎯 Take profit: ${take_profit_price:,.4f} ({tp_pct:.2f}%)\n"

        if strategy_name:
            message += f"📋 Strategy: {strategy_name}\n"

        message += f"\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        result = self._send_telegram(message)
        if result:
            logger.info(f"Paper open notify sent: {symbol}")
        else:
            logger.warning(f"Paper open notify failed: {symbol}")

    def notify_close_position(
        self,
        symbol: str,
        direction: str,
        quantity: float,
        entry_price: float,
        exit_price: float,
        pnl: float,
        pnl_pct: float,
        reason: str = "manual",
        hold_time: Optional[str] = None,
        strategy_name: Optional[str] = None,
        is_paper: bool = False,
    ):
        """Notify a paper close. Live closes are not sent."""
        if not is_paper:
            logger.debug(f"Skip Telegram for live close: {symbol}")
            return

        if reason == "stop_loss" and not self.notify_stop_loss:
            return
        if reason == "take_profit" and not self.notify_take_profit:
            return
        if reason not in ["stop_loss", "take_profit"] and not self.notify_close:
            return

        direction_lower = direction.lower()
        direction_text = "LONG" if direction_lower == "long" else "SHORT"

        if pnl > 0:
            pnl_emoji = "💰"
            result_text = "Profit"
        elif pnl < 0:
            pnl_emoji = "💸"
            result_text = "Loss"
        else:
            pnl_emoji = "➖"
            result_text = "Breakeven"

        reason_map = {
            "manual": "Manual close",
            "stop_loss": "🛡️ Stop loss",
            "take_profit": "🎯 Take profit",
            "signal_reverse": "📊 Signal reverse",
            "liquidation": "⚠️ Liquidation",
            "backtest_end": "Backtest end",
            "paper_sync": "🔄 Paper sync close on live",
            "manual_clear_open": "Manual clear",
        }
        reason_lower = (reason or "").lower()
        if reason in reason_map:
            reason_text = reason_map[reason]
        elif reason_lower.startswith("paper_sync"):
            reason_text = reason_map["paper_sync"]
        elif "advisor" in reason_lower or reason_lower.startswith("gemini_advisor"):
            reason_text = "🤖 Hold advisor SELL"
        else:
            reason_text = (reason or "manual")[:120]

        message = f"""
{pnl_emoji} <b>[CLOSE] {symbol}</b>

📌 Side: {direction_text}
📍 Reason: {reason_text}
💵 Entry: ${entry_price:,.4f}
💵 Exit: ${exit_price:,.4f}
📊 Qty: {quantity:.6f}

<b>{result_text}: {'+' if pnl > 0 else ''}{pnl:.2f} USDT ({pnl_pct:+.2f}%)</b>
"""

        if hold_time:
            message += f"⏱️ Hold: {hold_time}\n"

        if strategy_name:
            message += f"📋 Strategy: {strategy_name}\n"

        message += f"\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        result = self._send_telegram(message)
        if result:
            logger.info(f"Close notify sent: {symbol}")
        else:
            logger.warning(f"Close notify failed: {symbol}")
        return result

    def notify_order_placed(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        order_type: str = "LIMIT",
    ):
        if not self.notify_open:
            return

        side_text = "BUY" if side == "BUY" else "SELL"
        message = f"""
📝 <b>[LIMIT ORDER] {symbol}</b>

📌 Side: {side_text}
💰 Qty: {quantity:.6f}
💵 Limit: ${price:,.4f}
📋 Type: {order_type}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        self._send_telegram(message)

    def notify_order_filled(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        order_type: str = "LIMIT",
    ):
        if not self.notify_open:
            return

        side_emoji = "🟢" if side == "BUY" else "🔴"
        side_text = "BUY" if side == "BUY" else "SELL"
        message = f"""
{side_emoji} <b>[FILLED] {symbol}</b>

📌 Side: {side_text}
💰 Qty: {quantity:.6f}
💵 Price: ${price:,.4f}
📋 Type: {order_type}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        self._send_telegram(message)

    def notify_stop_loss_set(
        self,
        symbol: str,
        direction: str,
        stop_price: float,
        quantity: float,
    ):
        if not self.notify_stop_loss:
            logger.debug(f"Stop-loss notify disabled (notify_stop_loss={self.notify_stop_loss})")
            return

        direction_lower = direction.lower()
        direction_text = "LONG" if direction_lower == "long" else "SHORT"
        message = f"""
🛡️ <b>[STOP LOSS SET] {symbol}</b>

📌 Side: {direction_text}
💰 Qty: {quantity:.6f}
💵 Stop: ${stop_price:,.4f}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        result = self._send_telegram(message)
        if result:
            logger.info(f"Stop-loss notify sent: {symbol}")
        else:
            logger.warning(f"Stop-loss notify failed: {symbol}")

    def notify_take_profit_set(
        self,
        symbol: str,
        direction: str,
        take_profit_price: float,
        quantity: float,
    ):
        if not self.notify_take_profit:
            logger.debug(f"Take-profit notify disabled (notify_take_profit={self.notify_take_profit})")
            return

        direction_lower = direction.lower()
        direction_text = "LONG" if direction_lower == "long" else "SHORT"
        message = f"""
🎯 <b>[TAKE PROFIT SET] {symbol}</b>

📌 Side: {direction_text}
💰 Qty: {quantity:.6f}
💵 TP: ${take_profit_price:,.4f}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        result = self._send_telegram(message)
        if result:
            logger.info(f"Take-profit notify sent: {symbol}")
        else:
            logger.warning(f"Take-profit notify failed: {symbol}")

    def notify_error(self, symbol: str, error_type: str, error_message: str):
        message = f"""
⚠️ <b>[TRADE ERROR] {symbol}</b>

❌ Type: {error_type}
📝 Detail: {error_message}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        self._send_telegram(message)

    def notify_balance_update(
        self,
        total_equity: float,
        available_balance: float,
        unrealized_pnl: float,
    ):
        pnl_emoji = "📈" if unrealized_pnl >= 0 else "📉"
        message = f"""
💼 <b>[ACCOUNT]</b>

💰 Equity: ${total_equity:,.2f}
💵 Available: ${available_balance:,.2f}
{pnl_emoji} Unrealized PnL: {'+' if unrealized_pnl >= 0 else ''}{unrealized_pnl:,.2f}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        self._send_telegram(message)


_trade_notifier: Optional[TradeNotifier] = None


def _load_project_notifier_config() -> Optional[Dict]:
    try:
        from pathlib import Path
        from app.utils.config_loader import load_config

        root = Path(__file__).resolve().parent.parent.parent
        return load_config(root / "config.yaml")
    except Exception as e:
        logger.warning(f"Failed to load TradeNotifier config: {e}")
        return None


def get_trade_notifier(config: Dict = None) -> Optional[TradeNotifier]:
    global _trade_notifier

    if _trade_notifier is None:
        if config is not None:
            _trade_notifier = TradeNotifier(config)
        else:
            cfg = _load_project_notifier_config()
            if cfg is not None:
                _trade_notifier = TradeNotifier(cfg)

    return _trade_notifier


def init_trade_notifier(config: Dict) -> TradeNotifier:
    global _trade_notifier
    _trade_notifier = TradeNotifier(config)
    return _trade_notifier
