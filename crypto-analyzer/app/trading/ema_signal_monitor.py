"""
15分钟 EMA 买入信号监控器
监控 EMA 金叉信号，当短期 EMA 向上穿过长期 EMA 时发出买入提醒
"""

import asyncio
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone
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
    ) -> tuple[bool, str]:
        """
        检测金叉信号（买入信号）

        Args:
            short_ema_history: 短期 EMA 历史（最近3个值）
            long_ema_history: 长期 EMA 历史（最近3个值）
            volume_ratio: 当前成交量与平均成交量的比值

        Returns:
            (是否出现金叉, 成交量类型: '放量' 或 '缩量')
        """
        if len(short_ema_history) < 2 or len(long_ema_history) < 2:
            return False, ''

        # 当前值和前一个值
        short_current = short_ema_history[-1]
        short_prev = short_ema_history[-2]
        long_current = long_ema_history[-1]
        long_prev = long_ema_history[-2]

        # 检测金叉：
        # 1. 前一根K线：短期EMA <= 长期EMA
        # 2. 当前K线：短期EMA > 长期EMA（向上穿过）
        is_golden_cross = (
            short_prev <= long_prev and
            short_current > long_current
        )

        # 判断成交量类型：放量（>1）或缩量（<1）
        volume_type = '放量' if volume_ratio > 1 else '缩量'

        return is_golden_cross, volume_type

    def detect_death_cross(
        self,
        short_ema_history: list,
        long_ema_history: list,
        volume_ratio: float
    ) -> tuple[bool, str]:
        """
        检测 EMA 死叉（卖出信号）

        Args:
            short_ema_history: 短期 EMA 历史（最近3个值）
            long_ema_history: 长期 EMA 历史（最近3个值）
            volume_ratio: 当前成交量与平均成交量的比值

        Returns:
            (是否出现死叉, 成交量类型: '放量' 或 '缩量')
        """
        if len(short_ema_history) < 2 or len(long_ema_history) < 2:
            return False, ''

        # 当前值和前一个值
        short_current = short_ema_history[-1]
        short_prev = short_ema_history[-2]
        long_current = long_ema_history[-1]
        long_prev = long_ema_history[-2]

        # 检测死叉：
        # 1. 前一根K线：短期EMA >= 长期EMA
        # 2. 当前K线：短期EMA < 长期EMA（向下穿过）
        is_death_cross = (
            short_prev >= long_prev and
            short_current < long_current
        )

        # 判断成交量类型：放量（>1）或缩量（<1）
        volume_type = '放量' if volume_ratio > 1 else '缩量'

        return is_death_cross, volume_type

    async def save_signal_to_db(self, signal: Dict) -> bool:
        """
        保存EMA信号到数据库

        Args:
            signal: 信号字典

        Returns:
            是否保存成功
        """
        try:
            insert_sql = text("""
                INSERT INTO ema_signals (
                    symbol, timeframe, signal_type, signal_strength,
                    timestamp, price, short_ema, long_ema,
                    ema_config, volume_ratio, volume_type, price_change_pct, ema_distance_pct
                ) VALUES (
                    :symbol, :timeframe, :signal_type, :signal_strength,
                    :timestamp, :price, :short_ema, :long_ema,
                    :ema_config, :volume_ratio, :volume_type, :price_change_pct, :ema_distance_pct
                )
            """)

            # 使用同步session
            session = self.db_service.get_session()
            try:
                session.execute(insert_sql, {
                    'symbol': signal['symbol'],
                    'timeframe': signal['timeframe'],
                    'signal_type': signal['signal_type'],
                    'signal_strength': signal['signal_strength'],
                    'timestamp': signal['timestamp'],
                    'price': float(signal['price']),
                    'short_ema': float(signal['short_ema']),
                    'long_ema': float(signal['long_ema']),
                    'ema_config': signal['ema_config'],
                    'volume_ratio': float(signal['volume_ratio']),
                    'volume_type': signal.get('volume_type', '未知'),
                    'price_change_pct': float(signal['price_change_pct']),
                    'ema_distance_pct': float(signal['ema_distance_pct'])
                })
                session.commit()
                logger.debug(f"✓ 已保存 {signal['symbol']} {signal['signal_type']} 信号到数据库")
                return True
            finally:
                session.close()

        except Exception as e:
            logger.error(f"保存EMA信号到数据库失败: {e}")
            return False

    def calculate_signal_strength(
        self,
        price_change_pct: float,
        volume_ratio: float,
        ema_distance_pct: float,
        signal_type: str = 'BUY'
    ) -> str:
        """
        计算信号强度

        Args:
            price_change_pct: 价格变化百分比（买入时为正值，卖出时为负值）
            volume_ratio: 成交量比率
            ema_distance_pct: EMA 之间的距离百分比
            signal_type: 信号类型 'BUY' 或 'SELL'

        Returns:
            信号强度：'strong', 'medium', 'weak'
        """
        score = 0

        # 价格变化评估（区分买入和卖出）
        if signal_type == 'SELL':
            # 卖出信号：价格下跌幅度越大，信号越强
            price_change_abs = abs(price_change_pct)
            if price_change_abs > 2:
                score += 3
            elif price_change_abs > 1:
                score += 2
            elif price_change_abs > 0.5:
                score += 1
            # 额外加分：如果价格大幅下跌（>3%），说明下跌动能很强
            if price_change_abs > 3:
                score += 1  # 额外奖励分
        else:
            # 买入信号：价格上涨幅度越大，信号越强
            if price_change_pct > 2:
                score += 3
            elif price_change_pct > 1:
                score += 2
            elif price_change_pct > 0.5:
                score += 1
            # 额外加分：如果价格大幅上涨（>3%），说明上涨动能很强
            if price_change_pct > 3:
                score += 1  # 额外奖励分

        # 成交量评估（买入和卖出逻辑相同）
        if volume_ratio > 3:
            score += 3
        elif volume_ratio > 2:
            score += 2
        elif volume_ratio >= self.volume_threshold:
            score += 1

        # EMA 距离评估（越接近越强，买入和卖出逻辑相同）
        if ema_distance_pct < 0.5:
            score += 2
        elif ema_distance_pct < 1:
            score += 1

        # 强度等级判定（提高阈值，只保留高质量信号）
        if signal_type == 'SELL':
            # 卖出信号：提高阈值，只保留高质量信号
            if score >= 8:  # 提高strong阈值
                return 'strong'
            elif score >= 6:  # 提高medium阈值
                return 'medium'
            else:
                return 'weak'
        else:
            # 买入信号：提高阈值，只保留高质量信号
            if score >= 7:  # 提高strong阈值（从6提高到7）
                return 'strong'
            elif score >= 5:  # 提高medium阈值（从4提高到5）
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

            # 检测金叉（买入信号）
            is_golden_cross, buy_volume_type = self.detect_golden_cross(
                short_ema_values,
                long_ema_values,
                volume_ratio
            )

            # 检测死叉（卖出信号）
            is_death_cross, sell_volume_type = self.detect_death_cross(
                short_ema_values,
                long_ema_values,
                volume_ratio
            )

            # 如果没有任何信号，返回 None
            if not is_golden_cross and not is_death_cross:
                return None

            # 确定信号类型和成交量类型
            signal_type = 'BUY' if is_golden_cross else 'SELL'
            volume_type = buy_volume_type if is_golden_cross else sell_volume_type
            signal_key = f"{symbol}_{signal_type}"

            # 使用 UTC+8 北京时间
            utc8_tz = timezone(timedelta(hours=8))
            current_time = datetime.now(utc8_tz)

            # 计算信号详细信息
            current_price = closes[-1]
            price_change_pct = ((closes[-1] - closes[-2]) / closes[-2]) * 100
            ema_distance_pct = abs((short_ema_values[-1] - long_ema_values[-1]) / long_ema_values[-1]) * 100

            # 计算信号强度（传入信号类型，区分买入和卖出）
            signal_strength = self.calculate_signal_strength(
                price_change_pct,  # 保持原始值（买入为正，卖出为负）
                volume_ratio,
                ema_distance_pct,
                signal_type  # 传入信号类型，用于区分评估逻辑
            )

            # 过滤：只保留strong和medium信号，过滤掉weak信号
            if signal_strength == 'weak':
                logger.debug(f"{symbol}: {signal_type}信号强度为weak，已过滤")
                return None

            # 额外过滤条件：价格变化幅度太小或成交量不足的信号
            price_change_abs = abs(price_change_pct)
            if signal_type == 'BUY':
                # 买入信号：价格涨幅太小（<0.3%）或成交量不足（<1.2倍）的信号过滤掉
                if price_change_abs < 0.3 or volume_ratio < 1.2:
                    logger.debug(f"{symbol}: {signal_type}信号价格变化({price_change_pct:.2f}%)或成交量({volume_ratio:.2f}x)不足，已过滤")
                    return None
            else:
                # 卖出信号：价格跌幅太小（<0.3%）或成交量不足（<1.2倍）的信号过滤掉
                if price_change_abs < 0.3 or volume_ratio < 1.2:
                    logger.debug(f"{symbol}: {signal_type}信号价格变化({price_change_pct:.2f}%)或成交量({volume_ratio:.2f}x)不足，已过滤")
                    return None

            # 检查数据库中是否已有相同类型的信号（避免重复保存）
            signal_key = f"{symbol}_{signal_type}"
            last_signal_time = self.signal_history.get(signal_key)
            if last_signal_time:
                time_since_last = current_time - last_signal_time
                if time_since_last < timedelta(hours=4):  # 4小时内不重复提醒（从1小时增加到4小时）
                    logger.debug(f"{symbol}: {signal_type}信号已在 {time_since_last.seconds//3600} 小时前提醒过，已过滤")
                    return None

            # 检查数据库中最近的相同信号（更严格的去重）
            try:
                session = self.db_service.get_session()
                try:
                    check_sql = text("""
                        SELECT timestamp FROM ema_signals
                        WHERE symbol = :symbol
                          AND signal_type = :signal_type
                          AND timestamp >= DATE_SUB(:current_time, INTERVAL 4 HOUR)
                        ORDER BY timestamp DESC
                        LIMIT 1
                    """)
                    result = session.execute(check_sql, {
                        'symbol': symbol,
                        'signal_type': signal_type,
                        'current_time': current_time
                    })
                    existing_signal = result.fetchone()
                    if existing_signal:
                        logger.debug(f"{symbol}: {signal_type}信号在数据库中4小时内已存在，已过滤")
                        return None
                finally:
                    session.close()
            except Exception as e:
                logger.debug(f"检查数据库信号去重失败: {e}")

            # 记录信号时间（使用 UTC+8 北京时间）
            self.signal_history[signal_key] = current_time

            # 构建信号（使用 UTC+8 北京时间）
            signal = {
                'symbol': symbol,
                'timeframe': self.timeframe,
                'signal_type': signal_type,
                'signal_strength': signal_strength,
                'timestamp': current_time,  # 使用 UTC+8 北京时间
                'price': current_price,
                'short_ema': short_ema_values[-1],
                'long_ema': long_ema_values[-1],
                'ema_config': f'EMA{self.short_period}/EMA{self.long_period}',
                'volume_ratio': volume_ratio,
                'volume_type': volume_type,  # 成交量类型：放量或缩量
                'price_change_pct': price_change_pct,
                'ema_distance_pct': ema_distance_pct,
                'details': {
                    'short_ema_prev': short_ema_values[-2],
                    'long_ema_prev': long_ema_values[-2],
                    'avg_volume': avg_volume,
                    'current_volume': current_volume
                }
            }

            # 根据信号类型显示不同的emoji和文字
            if signal_type == 'BUY':
                logger.info(f"🚀 {symbol} 出现 {signal_strength.upper()} 买入信号（金叉）！")
            else:
                logger.info(f"⚠️  {symbol} 出现 {signal_strength.upper()} 卖出信号（死叉）！")

            logger.info(f"   价格: ${current_price:.2f} | 变动: {price_change_pct:+.2f}%")
            logger.info(f"   短期EMA{self.short_period}: {short_ema_values[-1]:.2f} | 长期EMA{self.long_period}: {long_ema_values[-1]:.2f}")
            logger.info(f"   成交量: {volume_type} ({volume_ratio:.2f}x)")

            # 保存信号到数据库
            await self.save_signal_to_db(signal)

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

        # 格式化时间：显示UTC时间和本地时间(UTC+8)
        utc_time = signal['timestamp']
        # 转换为UTC+8本地时间
        local_time = utc_time.astimezone(timezone(timedelta(hours=8)))
        time_str = f"{utc_time.strftime('%Y-%m-%d %H:%M:%S')} UTC (本地: {local_time.strftime('%H:%M:%S')})"

        message = f"""
{emoji} {signal['symbol']} 买入信号 ({signal['signal_strength'].upper()})

⏰ 时间: {time_str}
📊 周期: {signal['timeframe']}
💰 价格: ${signal['price']:.2f} ({signal['price_change_pct']:+.2f}%)

📈 EMA 金叉:
   • 短期 EMA{self.short_period}: {signal['short_ema']:.2f}
   • 长期 EMA{self.long_period}: {signal['long_ema']:.2f}
   • EMA 距离: {signal['ema_distance_pct']:.2f}%

📊 成交量:
   • 当前: {signal['details']['current_volume']:.2f}
   • 平均: {signal['details']['avg_volume']:.2f}
   • 倍数: {signal['volume_ratio']:.2f}x ({signal.get('volume_type', '未知')})

💡 建议: 短期 EMA 向上穿过长期 EMA，考虑买入机会
"""
        return message.strip()
