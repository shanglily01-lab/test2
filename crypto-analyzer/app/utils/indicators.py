"""
技术指标计算模块
抽取公共的EMA、MA、RSI、MACD、KDJ计算逻辑
"""

from typing import List, Dict, Optional


def calculate_ema(prices: List[float], period: int) -> List[float]:
    """
    计算EMA（指数移动平均线）

    Args:
        prices: 价格列表（从旧到新）
        period: EMA周期

    Returns:
        EMA值列表
    """
    if len(prices) < period:
        return []

    multiplier = 2 / (period + 1)
    ema_values = [sum(prices[:period]) / period]  # 初始SMA

    for price in prices[period:]:
        ema = (price - ema_values[-1]) * multiplier + ema_values[-1]
        ema_values.append(ema)

    return ema_values


def calculate_ma(prices: List[float], period: int) -> List[float]:
    """
    计算MA（简单移动平均线）

    Args:
        prices: 价格列表（从旧到新）
        period: MA周期

    Returns:
        MA值列表
    """
    if len(prices) < period:
        return []

    ma_values = []
    for i in range(period - 1, len(prices)):
        ma = sum(prices[i - period + 1:i + 1]) / period
        ma_values.append(ma)

    return ma_values


def calculate_rsi(prices: List[float], period: int = 14) -> List[float]:
    """
    计算RSI指标

    Args:
        prices: 价格列表（从旧到新）
        period: RSI周期，默认14

    Returns:
        RSI值列表
    """
    if len(prices) < period + 1:
        return []

    rsi_values = []
    gains = []
    losses = []

    # 计算价格变化
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    # 计算初始平均涨跌幅
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # 计算第一个RSI
    if avg_loss == 0:
        rsi_values.append(100)
    else:
        rs = avg_gain / avg_loss
        rsi_values.append(100 - (100 / (1 + rs)))

    # 使用平滑方法计算后续RSI
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            rsi_values.append(100)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100 - (100 / (1 + rs)))

    return rsi_values


def calculate_macd(prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict:
    """
    计算MACD指标

    Args:
        prices: 价格列表（从旧到新）
        fast: 快线周期，默认12
        slow: 慢线周期，默认26
        signal: 信号线周期，默认9

    Returns:
        {
            'macd': List[float],      # MACD线 (DIF)
            'signal': List[float],    # 信号线 (DEA)
            'histogram': List[float]  # 柱状图
        }
    """
    if len(prices) < slow + signal:
        return {'macd': [], 'signal': [], 'histogram': []}

    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)

    # 对齐EMA长度
    offset = slow - fast
    ema_fast = ema_fast[offset:]

    # 计算MACD线 (DIF)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]

    # 计算信号线 (DEA)
    signal_line = calculate_ema(macd_line, signal)

    # 对齐MACD线长度
    macd_offset = signal - 1
    macd_aligned = macd_line[macd_offset:]

    # 计算柱状图
    histogram = [m - s for m, s in zip(macd_aligned, signal_line)]

    return {
        'macd': macd_aligned,
        'signal': signal_line,
        'histogram': histogram
    }


def calculate_kdj(klines: List[Dict], period: int = 9) -> Dict:
    """
    计算KDJ指标

    Args:
        klines: K线数据，包含 high_price, low_price, close_price
        period: 计算周期，默认9

    Returns:
        {
            'k': List[float],
            'd': List[float],
            'j': List[float]
        }
    """
    if len(klines) < period:
        return {'k': [], 'd': [], 'j': []}

    k_values = []
    d_values = []
    j_values = []

    prev_k = 50
    prev_d = 50

    for i in range(period - 1, len(klines)):
        # 获取周期内的最高价和最低价
        highs = [float(k['high_price']) for k in klines[i - period + 1:i + 1]]
        lows = [float(k['low_price']) for k in klines[i - period + 1:i + 1]]
        close = float(klines[i]['close_price'])

        highest = max(highs)
        lowest = min(lows)

        # 计算RSV
        if highest == lowest:
            rsv = 50
        else:
            rsv = (close - lowest) / (highest - lowest) * 100

        # 计算K、D、J
        k = 2/3 * prev_k + 1/3 * rsv
        d = 2/3 * prev_d + 1/3 * k
        j = 3 * k - 2 * d

        k_values.append(k)
        d_values.append(d)
        j_values.append(j)

        prev_k = k
        prev_d = d

    return {
        'k': k_values,
        'd': d_values,
        'j': j_values
    }


def get_single_ema(prices: List[float], period: int) -> float:
    """
    计算单个EMA值（用于binance_futures_engine）

    Args:
        prices: 价格列表（从旧到新）
        period: EMA周期

    Returns:
        EMA值，数据不足返回0.0
    """
    if not prices or len(prices) < period:
        return 0.0

    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period  # 初始SMA

    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema

    return ema
