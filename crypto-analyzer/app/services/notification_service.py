"""
通知服务
支持多种通知方式：日志、文件、邮件、Telegram等
"""

import os
from typing import Dict, List
from datetime import datetime
from loguru import logger
from pathlib import Path


class NotificationService:
    """通知服务"""

    def __init__(self, config: dict):
        """
        初始化通知服务

        Args:
            config: 配置字典
        """
        self.config = config
        self.notification_config = config.get('notification', {})

        # 通知方式配置
        self.enable_log = self.notification_config.get('log', True)
        self.enable_file = self.notification_config.get('file', True)
        self.enable_email = self.notification_config.get('email', False)
        self.enable_telegram = self.notification_config.get('telegram', False)

        # 文件通知配置
        if self.enable_file:
            self.alert_file = Path(self.notification_config.get(
                'alert_file',
                'signals/ema_alerts.txt'
            ))
            self.alert_file.parent.mkdir(parents=True, exist_ok=True)

        logger.info("通知服务初始化完成")
        logger.info(f"  日志通知: {'启用' if self.enable_log else '禁用'}")
        logger.info(f"  文件通知: {'启用' if self.enable_file else '禁用'}")
        logger.info(f"  邮件通知: {'启用' if self.enable_email else '禁用'}")
        logger.info(f"  Telegram通知: {'启用' if self.enable_telegram else '禁用'}")

    def send_alert(self, message: str, title: str = "交易信号", level: str = "info"):
        """
        发送提醒

        Args:
            message: 消息内容
            title: 标题
            level: 级别 (info, warning, error)
        """
        # 1. 日志通知
        if self.enable_log:
            self._log_alert(message, level)

        # 2. 文件通知
        if self.enable_file:
            self._file_alert(message, title)

        # 3. 邮件通知
        if self.enable_email:
            self._email_alert(message, title)

        # 4. Telegram 通知
        if self.enable_telegram:
            self._telegram_alert(message, title)

    def _log_alert(self, message: str, level: str):
        """日志通知"""
        if level == "error":
            logger.error(f"\n{message}")
        elif level == "warning":
            logger.warning(f"\n{message}")
        else:
            logger.info(f"\n{message}")

    def _file_alert(self, message: str, title: str):
        """文件通知"""
        try:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            with open(self.alert_file, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"[{timestamp}] {title}\n")
                f.write(f"{'-'*60}\n")
                f.write(f"{message}\n")
                f.write(f"{'='*60}\n\n")
            logger.debug(f"信号已保存到: {self.alert_file}")
        except Exception as e:
            logger.error(f"保存信号到文件失败: {e}")

    def _email_alert(self, message: str, title: str):
        """邮件通知"""
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            email_config = self.notification_config.get('email_config', {})
            smtp_server = email_config.get('smtp_server')
            smtp_port = email_config.get('smtp_port', 587)
            sender = email_config.get('sender')
            password = email_config.get('password')
            receivers = email_config.get('receivers', [])

            if not all([smtp_server, sender, password, receivers]):
                logger.warning("邮件配置不完整，跳过邮件通知")
                return

            # 创建邮件
            msg = MIMEMultipart()
            msg['From'] = sender
            msg['To'] = ', '.join(receivers)
            msg['Subject'] = title

            # 添加正文
            msg.attach(MIMEText(message, 'plain', 'utf-8'))

            # 发送邮件
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(sender, password)
                server.send_message(msg)

            logger.info(f"邮件通知已发送到 {len(receivers)} 个收件人")

        except Exception as e:
            logger.error(f"发送邮件通知失败: {e}")

    def _telegram_alert(self, message: str, title: str):
        """Telegram 通知"""
        try:
            import requests

            telegram_config = self.notification_config.get('telegram_config', {})
            bot_token = telegram_config.get('bot_token')
            chat_id = telegram_config.get('chat_id')

            if not all([bot_token, chat_id]):
                logger.warning("Telegram 配置不完整，跳过 Telegram 通知")
                return

            # 格式化消息
            full_message = f"<b>{title}</b>\n\n{message}"

            # 发送消息
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            data = {
                'chat_id': chat_id,
                'text': full_message,
                'parse_mode': 'HTML'
            }

            response = requests.post(url, data=data, timeout=10)
            response.raise_for_status()

            logger.info("Telegram 通知已发送")

        except Exception as e:
            logger.error(f"发送 Telegram 通知失败: {e}")

    def send_ema_signal(self, signal: Dict, message: str):
        """
        发送 EMA 信号通知

        Args:
            signal: 信号字典
            message: 格式化的消息
        """
        strength = signal.get('signal_strength', 'medium').upper()
        symbol = signal.get('symbol', 'UNKNOWN')

        title = f"🚀 {symbol} EMA买入信号 ({strength})"

        # 根据强度设置级别
        level = "warning" if strength == "STRONG" else "info"

        self.send_alert(message, title, level)

    def send_batch_signals(self, signals: List[Dict], formatter_func):
        """
        批量发送信号

        Args:
            signals: 信号列表
            formatter_func: 消息格式化函数
        """
        if not signals:
            return

        # 按强度分组
        strong_signals = [s for s in signals if s['signal_strength'] == 'strong']
        medium_signals = [s for s in signals if s['signal_strength'] == 'medium']
        weak_signals = [s for s in signals if s['signal_strength'] == 'weak']

        # 发送强信号
        for signal in strong_signals:
            message = formatter_func(signal)
            self.send_ema_signal(signal, message)

        # 如果有多个中等信号，合并发送
        if len(medium_signals) > 3:
            summary = self._create_summary(medium_signals)
            self.send_alert(summary, "📊 EMA中等买入信号汇总", "info")
        else:
            for signal in medium_signals:
                message = formatter_func(signal)
                self.send_ema_signal(signal, message)

        # 弱信号只记录到文件
        if weak_signals and self.enable_file:
            for signal in weak_signals:
                message = formatter_func(signal)
                self._file_alert(message, f"{signal['symbol']} 弱买入信号")

    def _create_summary(self, signals: List[Dict]) -> str:
        """创建信号汇总"""
        summary_lines = [f"发现 {len(signals)} 个中等强度买入信号：\n"]

        for signal in signals:
            summary_lines.append(
                f"• {signal['symbol']}: "
                f"${signal['price']:.2f} "
                f"({signal['price_change_pct']:+.2f}%) "
                f"成交量{signal['volume_ratio']:.1f}x"
            )

        return '\n'.join(summary_lines)
