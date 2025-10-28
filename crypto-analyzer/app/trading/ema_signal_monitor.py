"""
15分钟 EMA 买入信号监控器
监控 EMA 金叉信号，当短期 EMA 向上穿过长期 EMA 时发出买入提醒
"""

import asyncio
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from loguru import logger
import numpy as np
from sqlalchemy import text


class EMASignalMonitor:
    """15分钟 EMA 买入信号监控器"""

    def __init__(self, config: dict, db_service):
        """
        初始化 EMA 监控器

        Args:
            config: 配置字典
            db_service: 数据库服务
        """
        self.config = config
        self.db_service = db_service

        # EMA 配置
        ema_config = config.get('ema_signal', {})
        self.short_period = ema_config.get('short_period', 9)   # 短期 EMA (默认9)
        self.long_period = ema_config.get('long_period', 21)    # 长期 EMA (默认21)
        self.timeframe = ema_config.get('timeframe', '15m')     # 时间周期
        self.volume_threshold = ema_config.get('volume_threshold', 1.5)  # 成交量倍数

        # 监控币种
        self.symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT'])

        # 信号历史（避免重复提醒）
        self.signal_history = {}

        logger.info(f"EMA 信号监控器初始化完成")
        logger.info(f"  短期 EMA: {self.short_period}, 长期 EMA: {self.long_period}")
        logger.info(f"  时间周期: {self.timeframe}")
        logger.info(f"  监控币种: {len(self.symbols)} 个")

    def calculate_ema(self, prices: List[float], period: int) -> Optional[float]:
        """
        计算指数移动平均线 (EMA)

        Args:
            prices: 价格序列（从旧到新）
            period: EMA 周期

        Returns:
            EMA 值
        """
        if len(prices) < period:
            return None

        prices_array = np.array(prices)
        multiplier = 2 / (period + 1)

        # 第一个 EMA 值使用 SMA
        ema = np.mean(prices_array[:period])

        # 计算后续的 EMA
        for price in prices_array[period:]:
            ema = (price - ema) * multiplier + ema

        return float(ema)

    async def get_kline_data(self, symbol: str, limit: int = 100) -> List[Dict]:
        """
        获取 K线数据

        Args:
            symbol: 交易对
            limit: 获取数量

        Returns:
            K线数据列表
        """
        session = self.db_service.get_session()
        try:
            # 数据库中的 symbol 格式是 BTC/USDT（带斜杠），保持原样
            db_symbol = symbol

            # 使用统一的 kline_data 表
            query = text("""
                SELECT open_time, open_price, high_price, low_price, close_price, volume, close_time
                FROM kline_data
                WHERE symbol = :symbol
                AND timeframe = :timeframe
                AND exchange = 'binance'
                ORDER BY open_time DESC
                LIMIT :limit
            """)

            result = session.execute(query, {
                'symbol': db_symbol,
                'timeframe': self.timeframe,
                'limit': limit
            })
            klines = result.fetchall()

            # 转换为字典列表（从旧到新）
            klines_list = []
            for k in reversed(klines):
                klines_list.append({
                    'open_time': k.open_time,
                    'open': float(k.open_price),
                    'high': float(k.high_price),
                    'low': float(k.low_price),
                    'close': float(k.close_price),
                    'volume': float(k.volume),
                    'close_time': k.close_time
                })

            return klines_list

        except Exception as e:
            logger.error(f"获取 K线数据失败 ({symbol}): {e}")
            return []
        finally:
            session.close()

    def detect_golden_cross(
        self,
        short_ema_history: List[float],
        long_ema_history: List[float],
        volume_ratio: float
    ) -> bool:
        """
        检测金叉信号

        Args:
            short_ema_history: 短期 EMA 历史（最近3个值）
            long_ema_history: 长期 EMA 历史（最近3个值）
            volume_ratio: 当前成交量与平均成交量的比值

        Returns:
            是否出现金叉
        """
        if len(short_ema_history) < 2 or len(long_ema_history) < 2:
            return False

        # 当前值和前一个值
        short_current = short_ema_history[-1]
        short_prev = short_ema_history[-2]
        long_current = long_ema_history[-1]
        long_prev = long_ema_history[-2]

        # 检测金叉：
        # 1. 前一根K线：短期EMA <= 长期EMA
        # 2. 当前K线：短期EMA > 长期EMA
        # 3. 成交量放大
        is_golden_cross = (
            short_prev <= long_prev and
            short_current > long_current and
            volume_ratio >= self.volume_threshold
        )

        return is_golden_cross

    def calculate_signal_strength(
        self,
        price_change_pct: float,
        volume_ratio: float,
        ema_distance_pct: float
    ) -> str:
        """
        计算信号强度

        Args:
            price_change_pct: 价格涨幅百分比
            volume_ratio: 成交量比率
            ema_distance_pct: EMA 之间的距离百分比

        Returns:
            信号强度：'strong', 'medium', 'weak'
        """
        score = 0

        # 价格涨幅
        if price_change_pct > 2:
            score += 3
        elif price_change_pct > 1:
            score += 2
        elif price_change_pct > 0.5:
            score += 1

        # 成交量
        if volume_ratio > 3:
            score += 3
        elif volume_ratio > 2:
            score += 2
        elif volume_ratio >= self.volume_threshold:
            score += 1

        # EMA 距离（越接近越强）
        if ema_distance_pct < 0.5:
            score += 2
        elif ema_distance_pct < 1:
            score += 1

        if score >= 6:
            return 'strong'
        elif score >= 4:
            return 'medium'
        else:
            return 'weak'

    async def check_symbol(self, symbol: str) -> Optional[Dict]:
        """
        检查单个交易对的 EMA 信号

        Args:
            symbol: 交易对

        Returns:
            信号字典（如果有信号）或 None
        """
        try:
            # 获取足够的 K线数据
            required_candles = max(self.short_period, self.long_period) + 10
            klines = await self.get_kline_data(symbol, limit=required_candles)

            if len(klines) < required_candles:
                logger.warning(f"{symbol}: K线数据不足 ({len(klines)}/{required_candles})")
                return None

            # 提取收盘价和成交量
            closes = [k['close'] for k in klines]
            volumes = [k['volume'] for k in klines]

            # 计算 EMA
            short_ema_values = []
            long_ema_values = []

            # 计算最近3个周期的 EMA（用于检测金叉）
            for i in range(len(closes) - 3, len(closes)):
                short_ema = self.calculate_ema(closes[:i+1], self.short_period)
                long_ema = self.calculate_ema(closes[:i+1], self.long_period)

                if short_ema is not None and long_ema is not None:
                    short_ema_values.append(short_ema)
                    long_ema_values.append(long_ema)

            if len(short_ema_values) < 2 or len(long_ema_values) < 2:
                return None

            # 计算成交量比率
            avg_volume = np.mean(volumes[-20:])
            current_volume = volumes[-1]
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1

            # 检测金叉
            is_golden_cross = self.detect_golden_cross(
                short_ema_values,
                long_ema_values,
                volume_ratio
            )

            if not is_golden_cross:
                return None

            # 检查是否已经提醒过（避免重复提醒）
            last_signal_time = self.signal_history.get(symbol)
            if last_signal_time:
                time_since_last = datetime.now() - last_signal_time
                if time_since_last < timedelta(hours=1):  # 1小时内不重复提醒
                    logger.debug(f"{symbol}: 金叉信号已在 {time_since_last.seconds//60} 分钟前提醒过")
                    return None

            # 记录信号时间
            self.signal_history[symbol] = datetime.now()

            # 计算信号详细信息
            current_price = closes[-1]
            price_change_pct = ((closes[-1] - closes[-2]) / closes[-2]) * 100
            ema_distance_pct = abs((short_ema_values[-1] - long_ema_values[-1]) / long_ema_values[-1]) * 100

            signal_strength = self.calculate_signal_strength(
                price_change_pct,
                volume_ratio,
                ema_distance_pct
            )

            # 构建信号
            signal = {
                'symbol': symbol,
                'timeframe': self.timeframe,
                'signal_type': 'BUY',
                'signal_strength': signal_strength,
                'timestamp': datetime.now(),
                'price': current_price,
                'short_ema': short_ema_values[-1],
                'long_ema': long_ema_values[-1],
                'ema_config': f'EMA{self.short_period}/EMA{self.long_period}',
                'volume_ratio': volume_ratio,
                'price_change_pct': price_change_pct,
                'ema_distance_pct': ema_distance_pct,
                'details': {
                    'short_ema_prev': short_ema_values[-2],
                    'long_ema_prev': long_ema_values[-2],
                    'avg_volume': avg_volume,
                    'current_volume': current_volume
                }
            }

            logger.info(f"🚀 {symbol} 出现 {signal_strength.upper()} 买入信号！")
            logger.info(f"   价格: ${current_price:.2f} | 涨幅: {price_change_pct:+.2f}%")
            logger.info(f"   短期EMA: {short_ema_values[-1]:.2f} | 长期EMA: {long_ema_values[-1]:.2f}")
            logger.info(f"   成交量放大: {volume_ratio:.2f}x")

            return signal

        except Exception as e:
            logger.error(f"检查 {symbol} 信号失败: {e}")
            return None

    async def scan_all_symbols(self) -> List[Dict]:
        """
        扫描所有交易对

        Returns:
            信号列表
        """
        logger.info(f"开始扫描 {len(self.symbols)} 个交易对的 EMA 信号...")

        signals = []
        for symbol in self.symbols:
            signal = await self.check_symbol(symbol)
            if signal:
                signals.append(signal)

            # 延迟避免过快
            await asyncio.sleep(0.1)

        if signals:
            logger.info(f"✓ 发现 {len(signals)} 个买入信号")
        else:
            logger.debug(f"未发现买入信号")

        return signals

    def format_alert_message(self, signal: Dict) -> str:
        """
        格式化提醒消息

        Args:
            signal: 信号字典

        Returns:
            格式化的消息
        """
        strength_emoji = {
            'strong': '🔥',
            'medium': '⚡',
            'weak': '💡'
        }

        emoji = strength_emoji.get(signal['signal_strength'], '📊')

        message = f"""
{emoji} {signal['symbol']} 买入信号 ({signal['signal_strength'].upper()})

⏰ 时间: {signal['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}
📊 周期: {signal['timeframe']}
💰 价格: ${signal['price']:.2f} ({signal['price_change_pct']:+.2f}%)

📈 EMA 金叉:
   • 短期 EMA{self.short_period}: {signal['short_ema']:.2f}
   • 长期 EMA{self.long_period}: {signal['long_ema']:.2f}
   • EMA 距离: {signal['ema_distance_pct']:.2f}%

📊 成交量:
   • 当前: {signal['details']['current_volume']:.2f}
   • 平均: {signal['details']['avg_volume']:.2f}
   • 放大倍数: {signal['volume_ratio']:.2f}x

💡 建议: 短期 EMA 向上穿过长期 EMA，考虑买入机会
"""
        return message.strip()
