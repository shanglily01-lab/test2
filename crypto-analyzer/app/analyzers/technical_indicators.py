"""
技术指标分析模块
计算常用技术指标：RSI、MACD、布林带、KDJ、EMA等
使用pandas_ta库简化计算
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional, Tuple
from loguru import logger

try:
    import pandas_ta as ta
except ImportError:
    logger.warning("pandas_ta未安装，请运行: pip install pandas-ta")
    ta = None


class TechnicalIndicators:
    """技术指标计算器"""

    def __init__(self, config: dict = None):
        """
        初始化

        Args:
            config: 指标参数配置
        """
        self.config = config or {}

        # RSI参数
        self.rsi_period = self.config.get('rsi', {}).get('period', 14)
        self.rsi_overbought = self.config.get('rsi', {}).get('overbought', 70)
        self.rsi_oversold = self.config.get('rsi', {}).get('oversold', 30)

        # MACD参数
        self.macd_fast = self.config.get('macd', {}).get('fast', 12)
        self.macd_slow = self.config.get('macd', {}).get('slow', 26)
        self.macd_signal = self.config.get('macd', {}).get('signal', 9)

        # 布林带参数
        self.bb_period = self.config.get('bollinger', {}).get('period', 20)
        self.bb_std = self.config.get('bollinger', {}).get('std', 2)

        # EMA参数
        self.ema_short = self.config.get('ema', {}).get('short', 12)
        self.ema_long = self.config.get('ema', {}).get('long', 26)

    def calculate_rsi(self, df: pd.DataFrame, period: int = None) -> pd.Series:
        """
        计算RSI指标

        Args:
            df: 包含'close'列的DataFrame
            period: RSI周期

        Returns:
            RSI序列
        """
        period = period or self.rsi_period

        if ta:
            try:
                rsi = ta.rsi(df['close'], length=period)
                if rsi is None or rsi.empty:
                    raise ValueError("Empty RSI result")
                return rsi
            except Exception as e:
                logger.debug(f"pandas_ta RSI计算失败: {e}，使用手动计算")
                pass

        # 手动计算RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def calculate_macd(
        self,
        df: pd.DataFrame
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        计算MACD指标

        Args:
            df: 包含'close'列的DataFrame

        Returns:
            (MACD线, 信号线, 柱状图)
        """
        if ta:
            try:
                macd_df = ta.macd(
                    df['close'],
                    fast=self.macd_fast,
                    slow=self.macd_slow,
                    signal=self.macd_signal
                )

                # 检查返回的DataFrame是否为空或缺少列
                if macd_df is None or macd_df.empty:
                    logger.warning("pandas_ta MACD返回空结果，使用手动计算")
                    raise ValueError("Empty MACD result")

                # 尝试多种可能的列名格式（不区分大小写）
                macd_col = None
                signal_col = None
                histogram_col = None

                # 检查所有可能的列名
                for col in macd_df.columns:
                    col_upper = col.upper()
                    if 'MACD_' in col_upper and 'MACDS_' not in col_upper and 'MACDH_' not in col_upper:
                        macd_col = col
                    elif 'MACDS_' in col_upper or 'MACD_SIGNAL' in col_upper or 'SIGNAL' in col_upper:
                        signal_col = col
                    elif 'MACDH_' in col_upper or 'MACD_HISTOGRAM' in col_upper or 'HISTOGRAM' in col_upper:
                        histogram_col = col

                if macd_col and signal_col and histogram_col:
                    logger.debug(f"pandas_ta MACD列名匹配成功: MACD={macd_col}, Signal={signal_col}, Histogram={histogram_col}")
                    return (
                        macd_df[macd_col],
                        macd_df[signal_col],
                        macd_df[histogram_col]
                    )
                else:
                    logger.warning(f"pandas_ta MACD列名不匹配，可用列: {list(macd_df.columns)}，使用手动计算")
                    raise ValueError("Column names mismatch")

            except Exception as e:
                logger.debug(f"pandas_ta MACD计算失败: {e}，使用手动计算")
                # 失败时使用手动计算
                pass

        # 手动计算MACD（fallback或ta不可用时）
        exp1 = df['close'].ewm(span=self.macd_fast, adjust=False).mean()
        exp2 = df['close'].ewm(span=self.macd_slow, adjust=False).mean()
        macd = exp1 - exp2
        signal = macd.ewm(span=self.macd_signal, adjust=False).mean()
        histogram = macd - signal
        return macd, signal, histogram

    def calculate_bollinger_bands(
        self,
        df: pd.DataFrame
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        计算布林带

        Args:
            df: 包含'close'列的DataFrame

        Returns:
            (上轨, 中轨, 下轨)
        """
        if ta:
            try:
                bb_df = ta.bbands(
                    df['close'],
                    length=self.bb_period,
                    std=self.bb_std
                )

                if bb_df is None or bb_df.empty:
                    raise ValueError("Empty result")

                # 动态查找列名（不区分大小写）
                upper_col = None
                middle_col = None
                lower_col = None

                for col in bb_df.columns:
                    col_upper = col.upper()
                    if 'BBU_' in col_upper or 'BB_UPPER' in col_upper or 'UPPER' in col_upper:
                        upper_col = col
                    elif 'BBM_' in col_upper or 'BB_MID' in col_upper or 'BB_MIDDLE' in col_upper:
                        middle_col = col
                    elif 'BBL_' in col_upper or 'BB_LOWER' in col_upper or 'LOWER' in col_upper:
                        lower_col = col

                if upper_col and middle_col and lower_col:
                    logger.debug(f"pandas_ta布林带列名匹配成功: Upper={upper_col}, Middle={middle_col}, Lower={lower_col}")
                    return (bb_df[upper_col], bb_df[middle_col], bb_df[lower_col])
                else:
                    logger.warning(f"pandas_ta布林带列名不匹配，可用列: {list(bb_df.columns)}，使用手动计算")
                    raise ValueError(f"列名不匹配: {list(bb_df.columns)}")

            except Exception as e:
                logger.debug(f"pandas_ta布林带失败: {e}，使用手动计算")
                pass

        # 手动计算布林带
        sma = df['close'].rolling(window=self.bb_period).mean()
        std = df['close'].rolling(window=self.bb_period).std()
        upper = sma + (std * self.bb_std)
        lower = sma - (std * self.bb_std)
        return upper, sma, lower

    def calculate_ema(
        self,
        df: pd.DataFrame,
        period: int
    ) -> pd.Series:
        """
        计算EMA均线

        Args:
            df: 包含'close'列的DataFrame
            period: 周期

        Returns:
            EMA序列
        """
        if ta:
            try:
                ema = ta.ema(df['close'], length=period)
                if ema is None or ema.empty:
                    raise ValueError("Empty EMA result")
                return ema
            except Exception as e:
                logger.debug(f"pandas_ta EMA计算失败: {e}，使用手动计算")
                pass

        return df['close'].ewm(span=period, adjust=False).mean()

    def calculate_kdj(
        self,
        df: pd.DataFrame,
        period: int = 9,
        signal: int = 3
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        计算KDJ指标

        Args:
            df: 包含'high', 'low', 'close'的DataFrame
            period: 周期
            signal: 信号周期

        Returns:
            (K值, D值, J值)
        """
        low_min = df['low'].rolling(window=period).min()
        high_max = df['high'].rolling(window=period).max()

        rsv = (df['close'] - low_min) / (high_max - low_min) * 100

        k = rsv.ewm(com=signal-1, adjust=False).mean()
        d = k.ewm(com=signal-1, adjust=False).mean()
        j = 3 * k - 2 * d

        return k, d, j

    def calculate_atr(
        self,
        df: pd.DataFrame,
        period: int = 14
    ) -> pd.Series:
        """
        计算ATR(平均真实波幅)

        Args:
            df: 包含'high', 'low', 'close'的DataFrame
            period: 周期

        Returns:
            ATR序列
        """
        if ta:
            try:
                atr = ta.atr(df['high'], df['low'], df['close'], length=period)
                if atr is None or atr.empty:
                    raise ValueError("Empty ATR result")
                return atr
            except Exception as e:
                logger.debug(f"pandas_ta ATR计算失败: {e}，使用手动计算")
                pass

        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()

        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        return atr

    def calculate_volume_indicators(
        self,
        df: pd.DataFrame
    ) -> Dict:
        """
        计算成交量指标

        Args:
            df: 包含'volume'和'close'的DataFrame

        Returns:
            成交量指标字典
        """
        # 成交量移动平均
        vol_ma5 = df['volume'].rolling(window=5).mean()
        vol_ma20 = df['volume'].rolling(window=20).mean()

        # 成交量变化率
        vol_change = df['volume'].pct_change() * 100

        # OBV (能量潮)
        obv = (np.sign(df['close'].diff()) * df['volume']).fillna(0).cumsum()

        return {
            'vol_ma5': vol_ma5,
            'vol_ma20': vol_ma20,
            'vol_change': vol_change,
            'obv': obv
        }

    def analyze(self, df: pd.DataFrame) -> Dict:
        """
        计算所有技术指标

        Args:
            df: OHLCV数据，必须包含 ['open', 'high', 'low', 'close', 'volume']

        Returns:
            包含所有指标的字典
        """
        if df is None or len(df) < 50:
            logger.warning("数据不足，无法计算技术指标")
            return {}

        try:
            # RSI
            df['rsi'] = self.calculate_rsi(df)

            # MACD
            df['macd'], df['macd_signal'], df['macd_histogram'] = \
                self.calculate_macd(df)

            # 布林带
            df['bb_upper'], df['bb_middle'], df['bb_lower'] = \
                self.calculate_bollinger_bands(df)

            # EMA
            df['ema_short'] = self.calculate_ema(df, self.ema_short)
            df['ema_long'] = self.calculate_ema(df, self.ema_long)

            # KDJ
            df['kdj_k'], df['kdj_d'], df['kdj_j'] = \
                self.calculate_kdj(df)

            # ATR
            df['atr'] = self.calculate_atr(df)

            # 成交量指标
            vol_indicators = self.calculate_volume_indicators(df)
            for key, value in vol_indicators.items():
                df[key] = value

            # 返回最新值
            latest = df.iloc[-1]

            return {
                'timestamp': latest.get('timestamp'),
                'price': latest['close'],
                'rsi': {
                    'value': latest['rsi'],
                    'overbought': latest['rsi'] > self.rsi_overbought,
                    'oversold': latest['rsi'] < self.rsi_oversold
                },
                'macd': {
                    'macd': latest['macd'],
                    'signal': latest['macd_signal'],
                    'histogram': latest['macd_histogram'],
                    'bullish_cross': self._check_macd_cross(df, 'bullish'),
                    'bearish_cross': self._check_macd_cross(df, 'bearish')
                },
                'bollinger': {
                    'upper': latest['bb_upper'],
                    'middle': latest['bb_middle'],
                    'lower': latest['bb_lower'],
                    'price_position': self._get_bb_position(latest)
                },
                'ema': {
                    'short': latest['ema_short'],
                    'long': latest['ema_long'],
                    'bullish_cross': latest['ema_short'] > latest['ema_long'],
                    'trend': 'up' if latest['ema_short'] > latest['ema_long'] else 'down'
                },
                'kdj': {
                    'k': latest['kdj_k'],
                    'd': latest['kdj_d'],
                    'j': latest['kdj_j'],
                    'overbought': latest['kdj_k'] > 80,
                    'oversold': latest['kdj_k'] < 20
                },
                'volume': {
                    'current': latest['volume'],
                    'ma5': latest['vol_ma5'],
                    'ma20': latest['vol_ma20'],
                    'change_pct': latest['vol_change'],
                    'above_average': latest['volume'] > latest['vol_ma20']
                },
                'atr': latest['atr']
            }

        except Exception as e:
            logger.error(f"计算技术指标失败: {e}")
            return {}

    def _check_macd_cross(self, df: pd.DataFrame, direction: str) -> bool:
        """检查MACD是否发生交叉"""
        if len(df) < 2:
            return False

        current = df.iloc[-1]
        previous = df.iloc[-2]

        if direction == 'bullish':
            # 金叉：MACD从下方穿过信号线
            return (previous['macd'] <= previous['macd_signal'] and
                    current['macd'] > current['macd_signal'])
        else:
            # 死叉：MACD从上方穿过信号线
            return (previous['macd'] >= previous['macd_signal'] and
                    current['macd'] < current['macd_signal'])

    def _get_bb_position(self, row) -> str:
        """获取价格在布林带中的位置"""
        price = row['close']
        upper = row['bb_upper']
        middle = row['bb_middle']
        lower = row['bb_lower']

        if price >= upper:
            return 'above_upper'
        elif price >= middle:
            return 'upper_half'
        elif price >= lower:
            return 'lower_half'
        else:
            return 'below_lower'

    def generate_signals(self, indicators: Dict) -> Dict:
        """
        根据技术指标生成交易信号

        Args:
            indicators: 技术指标字典

        Returns:
            信号字典
        """
        signals = []
        score = 0  # -100 到 +100

        # 1. RSI信号
        rsi = indicators.get('rsi', {})
        if rsi.get('oversold'):
            signals.append("RSI超卖，可能反弹")
            score += 15
        elif rsi.get('overbought'):
            signals.append("RSI超买，可能回调")
            score -= 15
        elif 40 <= rsi.get('value', 50) <= 60:
            signals.append("RSI中性")

        # 2. MACD信号
        macd = indicators.get('macd', {})
        if macd.get('bullish_cross'):
            signals.append("MACD金叉")
            score += 20
        elif macd.get('bearish_cross'):
            signals.append("MACD死叉")
            score -= 20
        elif macd.get('histogram', 0) > 0:
            score += 5

        # 3. 布林带信号
        bb = indicators.get('bollinger', {})
        bb_position = bb.get('price_position', '')
        if bb_position == 'below_lower':
            signals.append("价格跌破布林带下轨")
            score += 15
        elif bb_position == 'above_upper':
            signals.append("价格突破布林带上轨")
            score -= 10

        # 4. EMA趋势信号
        ema = indicators.get('ema', {})
        if ema.get('bullish_cross'):
            signals.append("短期均线上穿长期均线")
            score += 15
        elif ema.get('trend') == 'down':
            score -= 10

        # 5. KDJ信号
        kdj = indicators.get('kdj', {})
        if kdj.get('oversold'):
            signals.append("KDJ超卖")
            score += 10
        elif kdj.get('overbought'):
            signals.append("KDJ超买")
            score -= 10

        # 6. 成交量信号
        volume = indicators.get('volume', {})
        if volume.get('above_average'):
            if score > 0:
                signals.append("成交量放大，支持上涨")
                score += 10
            else:
                signals.append("成交量放大，支持下跌")
                score -= 10

        # 归一化分数到 -100~100
        score = max(min(score, 100), -100)

        # 判断信号类型
        if score >= 60:
            action = 'STRONG_LONG'
            confidence = min(score / 100, 1.0)
        elif score >= 30:
            action = 'LONG'
            confidence = score / 100
        elif score <= -60:
            action = 'STRONG_SHORT'
            confidence = min(abs(score) / 100, 1.0)
        elif score <= -30:
            action = 'SHORT'
            confidence = abs(score) / 100
        else:
            action = 'HOLD'
            confidence = 0.5

        return {
            'action': action,
            'score': score,
            'confidence': round(confidence, 2),
            'signals': signals,
            'indicators': indicators
        }


# 使用示例
async def main():
    """测试技术指标分析"""
    from app.collectors.price_collector import MultiExchangeCollector

    print("=== 技术指标分析测试 ===\n")

    # 初始化采集器
    config = {
        'exchanges': {
            'binance': {
                'enabled': True,
                'api_key': '',
                'api_secret': ''
            }
        }
    }

    collector = MultiExchangeCollector(config)

    # 获取历史数据
    print("1. 获取BTC历史K线数据...")
    df = await collector.fetch_ohlcv('BTC/USDT', timeframe='1h')

    if df is None or len(df) == 0:
        print("获取数据失败")
        return

    print(f"   获取到 {len(df)} 条数据\n")

    # 初始化技术指标分析器
    indicators_config = {
        'rsi': {'period': 14, 'overbought': 70, 'oversold': 30},
        'macd': {'fast': 12, 'slow': 26, 'signal': 9},
        'bollinger': {'period': 20, 'std': 2},
        'ema': {'short': 12, 'long': 26}
    }

    analyzer = TechnicalIndicators(indicators_config)

    # 计算技术指标
    print("2. 计算技术指标...")
    indicators = analyzer.analyze(df)

    print("\n=== 技术指标结果 ===\n")
    print(f"当前价格: ${indicators['price']:,.2f}")
    print(f"\nRSI: {indicators['rsi']['value']:.2f}")
    print(f"  超买: {indicators['rsi']['overbought']}")
    print(f"  超卖: {indicators['rsi']['oversold']}")

    print(f"\nMACD:")
    print(f"  MACD: {indicators['macd']['macd']:.2f}")
    print(f"  信号线: {indicators['macd']['signal']:.2f}")
    print(f"  柱状图: {indicators['macd']['histogram']:.2f}")
    print(f"  金叉: {indicators['macd']['bullish_cross']}")
    print(f"  死叉: {indicators['macd']['bearish_cross']}")

    print(f"\n布林带:")
    print(f"  上轨: ${indicators['bollinger']['upper']:,.2f}")
    print(f"  中轨: ${indicators['bollinger']['middle']:,.2f}")
    print(f"  下轨: ${indicators['bollinger']['lower']:,.2f}")
    print(f"  位置: {indicators['bollinger']['price_position']}")

    print(f"\nEMA:")
    print(f"  短期: ${indicators['ema']['short']:,.2f}")
    print(f"  长期: ${indicators['ema']['long']:,.2f}")
    print(f"  趋势: {indicators['ema']['trend']}")

    # 生成交易信号
    print("\n3. 生成交易信号...\n")
    signal = analyzer.generate_signals(indicators)

    print("=== 交易信号 ===\n")
    print(f"操作建议: {signal['action']}")
    print(f"综合评分: {signal['score']}/100")
    print(f"置信度: {signal['confidence']:.0%}")
    print(f"\n信号列表:")
    for s in signal['signals']:
        print(f"  • {s}")


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
