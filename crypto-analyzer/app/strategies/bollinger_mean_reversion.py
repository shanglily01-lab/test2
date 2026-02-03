"""
布林带均值回归策略
适用于震荡市环境的核心策略
"""

import pymysql
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Optional, List
import logging

logger = logging.getLogger(__name__)


class BollingerMeanReversionStrategy:
    """布林带均值回归策略"""

    def __init__(self, db_config: dict):
        self.db_config = db_config

        # 策略参数
        self.bb_period = 20  # 布林带周期
        self.bb_std = 2.0    # 标准差倍数
        self.rsi_period = 14  # RSI周期
        self.rsi_oversold = 30  # RSI超卖线
        self.rsi_overbought = 70  # RSI超买线

        # 趋势过滤参数
        self.ema_fast = 9    # 快速EMA周期
        self.ema_slow = 26   # 慢速EMA周期
        self.trend_strength_threshold = 0.5  # 趋势强度阈值(%)

    def calculate_indicators(self, symbol: str, timeframe: str = '15m') -> Optional[Dict]:
        """
        计算技术指标

        Args:
            symbol: 交易对
            timeframe: K线周期

        Returns:
            指标字典或None
        """
        try:
            conn = pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)
            cursor = conn.cursor()

            # 获取足够的K线数据
            lookback = max(self.bb_period, self.rsi_period) + 50
            cursor.execute("""
                SELECT
                    open_time, close_price, high_price, low_price, volume
                FROM kline_data
                WHERE symbol = %s
                AND timeframe = %s
                AND exchange = 'binance_futures'
                ORDER BY open_time DESC
                LIMIT %s
            """, (symbol, timeframe, lookback))

            klines = cursor.fetchall()
            cursor.close()
            conn.close()

            if len(klines) < self.bb_period + 10:
                return None

            # 反转数据(从旧到新)
            klines = klines[::-1]

            # 提取收盘价
            closes = np.array([float(k['close_price']) for k in klines])
            highs = np.array([float(k['high_price']) for k in klines])
            lows = np.array([float(k['low_price']) for k in klines])
            volumes = np.array([float(k['volume']) for k in klines])

            # 计算布林带
            bb_upper, bb_middle, bb_lower = self._calculate_bollinger_bands(closes)

            # 计算RSI
            rsi = self._calculate_rsi(closes)

            # 计算EMA (用于趋势判断)
            ema_fast = self._calculate_ema(closes, self.ema_fast)
            ema_slow = self._calculate_ema(closes, self.ema_slow)

            # 当前值
            current_price = closes[-1]
            current_rsi = rsi[-1]
            current_volume = volumes[-1]
            avg_volume = np.mean(volumes[-20:])
            current_ema_fast = ema_fast[-1]
            current_ema_slow = ema_slow[-1]

            # 计算价格相对于布林带的位置 (0-1之间)
            bb_position = (current_price - bb_lower[-1]) / (bb_upper[-1] - bb_lower[-1])

            # 判断成交量是否放大
            volume_surge = current_volume > avg_volume * 1.2

            # === 趋势判断 ===
            # 1. EMA趋势方向
            ema_diff_pct = ((current_ema_fast - current_ema_slow) / current_ema_slow) * 100

            # 2. 最近N根K线的连续性
            recent_closes = closes[-5:]  # 最近5根K线
            uptrend_bars = sum(1 for i in range(1, len(recent_closes)) if recent_closes[i] > recent_closes[i-1])
            downtrend_bars = sum(1 for i in range(1, len(recent_closes)) if recent_closes[i] < recent_closes[i-1])

            # 3. 判断是否有明显趋势
            has_uptrend = (ema_diff_pct > self.trend_strength_threshold) and (uptrend_bars >= 3)
            has_downtrend = (ema_diff_pct < -self.trend_strength_threshold) and (downtrend_bars >= 3)

            return {
                'symbol': symbol,
                'current_price': current_price,
                'bb_upper': bb_upper[-1],
                'bb_middle': bb_middle[-1],
                'bb_lower': bb_lower[-1],
                'bb_position': bb_position,  # 0=下轨, 0.5=中轨, 1=上轨
                'rsi': current_rsi,
                'volume_surge': volume_surge,
                'avg_volume': avg_volume,
                'current_volume': current_volume,
                'ema_fast': current_ema_fast,
                'ema_slow': current_ema_slow,
                'ema_diff_pct': ema_diff_pct,
                'has_uptrend': has_uptrend,
                'has_downtrend': has_downtrend,
                'uptrend_bars': uptrend_bars,
                'downtrend_bars': downtrend_bars
            }

        except Exception as e:
            logger.error(f"计算指标失败 {symbol}: {e}")
            return None

    def _calculate_bollinger_bands(self, closes: np.ndarray) -> tuple:
        """计算布林带"""
        middle = np.convolve(closes, np.ones(self.bb_period)/self.bb_period, mode='valid')

        # 计算标准差
        std = np.array([
            np.std(closes[i:i+self.bb_period])
            for i in range(len(closes) - self.bb_period + 1)
        ])

        upper = middle + (std * self.bb_std)
        lower = middle - (std * self.bb_std)

        # 填充前面的NaN值
        pad_length = len(closes) - len(middle)
        upper = np.concatenate([np.full(pad_length, np.nan), upper])
        middle = np.concatenate([np.full(pad_length, np.nan), middle])
        lower = np.concatenate([np.full(pad_length, np.nan), lower])

        return upper, middle, lower

    def _calculate_rsi(self, closes: np.ndarray) -> np.ndarray:
        """计算RSI"""
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gains = np.convolve(gains, np.ones(self.rsi_period)/self.rsi_period, mode='valid')
        avg_losses = np.convolve(losses, np.ones(self.rsi_period)/self.rsi_period, mode='valid')

        rs = avg_gains / (avg_losses + 1e-10)
        rsi = 100 - (100 / (1 + rs))

        # 填充前面的NaN
        pad_length = len(closes) - len(rsi)
        rsi = np.concatenate([np.full(pad_length, 50), rsi])

        return rsi

    def _calculate_ema(self, closes: np.ndarray, period: int) -> np.ndarray:
        """
        计算指数移动平均线(EMA)

        Args:
            closes: 收盘价数组
            period: EMA周期

        Returns:
            EMA数组
        """
        ema = np.zeros_like(closes)
        ema[0] = closes[0]
        multiplier = 2.0 / (period + 1)

        for i in range(1, len(closes)):
            ema[i] = (closes[i] * multiplier) + (ema[i-1] * (1 - multiplier))

        return ema

    def generate_signal(
        self,
        symbol: str,
        big4_signal: str,
        timeframe: str = '15m'
    ) -> Optional[Dict]:
        """
        生成交易信号

        Args:
            symbol: 交易对
            big4_signal: Big4市场信号
            timeframe: K线周期

        Returns:
            信号字典或None
        """
        # 只在震荡市(NEUTRAL)环境下运行
        if big4_signal != 'NEUTRAL':
            return None

        indicators = self.calculate_indicators(symbol, timeframe)
        if not indicators:
            return None

        signal = None
        score = 0
        reason_parts = []

        # === 做多信号: 价格触及下轨 + RSI超卖 ===
        if indicators['bb_position'] < 0.15:  # 接近或突破下轨
            if indicators['rsi'] < self.rsi_oversold:  # RSI超卖

                # ⚠️ 趋势过滤: 不在明显下跌趋势中做多
                if indicators['has_downtrend']:
                    logger.debug(f"[TREND_FILTER] {symbol} LONG被过滤: 存在下跌趋势 (EMA差:{indicators['ema_diff_pct']:.2f}%, 连续阴线:{indicators['downtrend_bars']})")
                    return None

                signal = 'LONG'
                score = 60
                reason_parts.append('价格触及布林带下轨')
                reason_parts.append(f"RSI超卖({indicators['rsi']:.1f})")

                # 成交量放大加分
                if indicators['volume_surge']:
                    score += 15
                    reason_parts.append('成交量放大')

                # 价格越接近下轨,分数越高
                if indicators['bb_position'] < 0.05:
                    score += 10
                    reason_parts.append('触及下轨')

        # === 做空信号: 价格触及上轨 + RSI超买 ===
        elif indicators['bb_position'] > 0.85:  # 接近或突破上轨
            if indicators['rsi'] > self.rsi_overbought:  # RSI超买

                # ⚠️ 趋势过滤: 不在明显上涨趋势中做空
                if indicators['has_uptrend']:
                    logger.debug(f"[TREND_FILTER] {symbol} SHORT被过滤: 存在上涨趋势 (EMA差:{indicators['ema_diff_pct']:.2f}%, 连续阳线:{indicators['uptrend_bars']})")
                    return None

                signal = 'SHORT'
                score = 60
                reason_parts.append('价格触及布林带上轨')
                reason_parts.append(f"RSI超买({indicators['rsi']:.1f})")

                # 成交量放大加分
                if indicators['volume_surge']:
                    score += 15
                    reason_parts.append('成交量放大')

                # 价格越接近上轨,分数越高
                if indicators['bb_position'] > 0.95:
                    score += 10
                    reason_parts.append('触及上轨')

        if signal:
            # 计算止盈止损价格
            if signal == 'LONG':
                # 目标: 回归中轨
                take_profit_price = indicators['bb_middle']
                stop_loss_price = indicators['current_price'] * 0.98  # 2%止损
            else:  # SHORT
                take_profit_price = indicators['bb_middle']
                stop_loss_price = indicators['current_price'] * 1.02  # 2%止损

            return {
                'symbol': symbol,
                'signal': signal,
                'score': min(score, 100),
                'strategy': 'bollinger_mean_reversion',
                'entry_price': indicators['current_price'],
                'take_profit_price': take_profit_price,
                'stop_loss_price': stop_loss_price,
                'bb_upper': indicators['bb_upper'],
                'bb_middle': indicators['bb_middle'],
                'bb_lower': indicators['bb_lower'],
                'rsi': indicators['rsi'],
                'reason': ' + '.join(reason_parts),
                'timeframe': timeframe
            }

        return None

    def check_exit_signal(self, position: Dict, current_indicators: Dict) -> Optional[str]:
        """
        检查平仓信号

        Args:
            position: 持仓信息
            current_indicators: 当前指标

        Returns:
            平仓原因或None
        """
        if not current_indicators:
            return None

        current_price = current_indicators['current_price']
        entry_price = float(position.get('entry_price', 0))
        position_side = position.get('position_side')

        if position_side == 'LONG':
            # 做多平仓条件
            # 1. 价格回归中轨附近(止盈)
            if current_indicators['bb_position'] > 0.45:
                return 'take_profit_mean_reversion'

            # 2. 价格继续下跌突破下轨(止损)
            if current_price < entry_price * 0.98:
                return 'stop_loss'

        elif position_side == 'SHORT':
            # 做空平仓条件
            # 1. 价格回归中轨附近(止盈)
            if current_indicators['bb_position'] < 0.55:
                return 'take_profit_mean_reversion'

            # 2. 价格继续上涨突破上轨(止损)
            if current_price > entry_price * 1.02:
                return 'stop_loss'

        return None
