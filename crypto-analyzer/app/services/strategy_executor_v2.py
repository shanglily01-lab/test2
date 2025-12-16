"""
策略执行器 V2 - 简化版
根据需求文档重新设计的策略执行逻辑

核心功能：
1. 开仓信号：金叉/死叉、强信号、连续趋势、震荡反向
2. 平仓信号：金叉反转（不检查强度）、趋势反转、移动止盈、硬止损
3. EMA+MA方向一致性过滤
4. 移动止盈（跟踪止盈）
"""

import asyncio
import pymysql
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from loguru import logger
from app.services.position_validator import PositionValidator


class StrategyExecutorV2:
    """V2版策略执行器 - 简化逻辑"""

    # 策略参数常量（来自需求文档）
    MIN_SIGNAL_STRENGTH = 0.15  # 最小开仓强度阈值 (%)
    HIGH_SIGNAL_STRENGTH = 0.5  # 高强度阈值，立即开仓 (%)
    OSCILLATION_RANGE = 0.5  # 震荡区间判断幅度 (%)
    OSCILLATION_BARS = 4  # 震荡判断连续K线数
    TREND_CONFIRM_BARS_5M = 3  # 5M连续放大K线数
    STRENGTH_MONITOR_DELAY = 30  # 强度监控开始时间（分钟）
    STRENGTH_WEAKEN_COUNT = 3  # 强度减弱连续次数

    # 止损止盈参数
    HARD_STOP_LOSS = 2.5  # 硬止损 (%)
    TRAILING_ACTIVATE = 1.5  # 移动止盈启动阈值 (%)
    TRAILING_CALLBACK = 0.5  # 移动止盈回撤 (%)
    MAX_TAKE_PROFIT = 8.0  # 最大止盈 (%)

    # 移动止损参数
    TRAILING_STOP_LOSS_ACTIVATE = 0.5  # 移动止损启动阈值：盈利达到0.5%时开始移动止损
    TRAILING_STOP_LOSS_DISTANCE = 1.0  # 移动止损距离：止损价与当前价的距离 (%)

    # 成交量阈值
    VOLUME_SHRINK_THRESHOLD = 0.8  # 缩量阈值 (<80%)
    VOLUME_EXPAND_THRESHOLD = 1.2  # 放量阈值 (>120%)

    def __init__(self, db_config: Dict, futures_engine=None, live_engine=None):
        """
        初始化策略执行器V2

        Args:
            db_config: 数据库配置
            futures_engine: 模拟交易引擎
            live_engine: 实盘交易引擎
        """
        self.db_config = db_config
        self.futures_engine = futures_engine
        self.live_engine = live_engine
        self.LOCAL_TZ = timezone(timedelta(hours=8))

        # 冷却时间记录
        self.last_entry_time = {}  # {symbol_direction: datetime}

        # 初始化开仓前检查器
        self.position_validator = PositionValidator(db_config, futures_engine)

    def get_local_time(self) -> datetime:
        """获取本地时间（UTC+8）"""
        return datetime.now(self.LOCAL_TZ).replace(tzinfo=None)

    def get_db_connection(self):
        """获取数据库连接"""
        return pymysql.connect(
            **self.db_config,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10,
            read_timeout=30,
            write_timeout=30
        )

    # ==================== 技术指标计算 ====================

    def calculate_ema(self, prices: List[float], period: int) -> List[float]:
        """计算EMA"""
        if len(prices) < period:
            return []

        multiplier = 2 / (period + 1)
        ema_values = [sum(prices[:period]) / period]  # 初始SMA

        for price in prices[period:]:
            ema = (price - ema_values[-1]) * multiplier + ema_values[-1]
            ema_values.append(ema)

        return ema_values

    def calculate_ma(self, prices: List[float], period: int) -> List[float]:
        """计算MA"""
        if len(prices) < period:
            return []

        ma_values = []
        for i in range(period - 1, len(prices)):
            ma = sum(prices[i - period + 1:i + 1]) / period
            ma_values.append(ma)

        return ma_values

    def calculate_rsi(self, prices: List[float], period: int = 14) -> List[float]:
        """计算RSI指标"""
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

    def calculate_macd(self, prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict:
        """
        计算MACD指标

        Returns:
            {
                'macd': List[float],  # MACD线 (DIF)
                'signal': List[float],  # 信号线 (DEA)
                'histogram': List[float]  # 柱状图 (MACD柱)
            }
        """
        if len(prices) < slow + signal:
            return {'macd': [], 'signal': [], 'histogram': []}

        ema_fast = self.calculate_ema(prices, fast)
        ema_slow = self.calculate_ema(prices, slow)

        # 对齐EMA长度
        offset = slow - fast
        ema_fast = ema_fast[offset:]

        # 计算MACD线 (DIF)
        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]

        # 计算信号线 (DEA)
        signal_line = self.calculate_ema(macd_line, signal)

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

    def calculate_kdj(self, klines: List[Dict], period: int = 9) -> Dict:
        """
        计算KDJ指标

        Args:
            klines: K线数据，包含 high_price, low_price, close_price
            period: 计算周期

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

    def get_ema_data(self, symbol: str, timeframe: str, limit: int = 100) -> Dict:
        """
        获取EMA数据

        Returns:
            {
                'ema9': float,
                'ema26': float,
                'ema_diff': float,  # EMA9 - EMA26
                'ema_diff_pct': float,  # |EMA9 - EMA26| / EMA26 * 100
                'ma10': float,
                'current_price': float,
                'prev_ema9': float,
                'prev_ema26': float,
                'klines': List[Dict]
            }
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT timestamp, open_price, high_price, low_price, close_price, volume
                FROM kline_data
                WHERE symbol = %s AND timeframe = %s AND exchange = 'binance_futures'
                ORDER BY timestamp DESC
                LIMIT %s
            """, (symbol, timeframe, limit))

            klines = list(reversed(cursor.fetchall()))

            if len(klines) < 30:
                return None

            close_prices = [float(k['close_price']) for k in klines]

            # 计算EMA9, EMA26, MA10
            ema9_values = self.calculate_ema(close_prices, 9)
            ema26_values = self.calculate_ema(close_prices, 26)
            ma10_values = self.calculate_ma(close_prices, 10)

            if not ema9_values or not ema26_values or not ma10_values:
                return None

            ema9 = ema9_values[-1]
            ema26 = ema26_values[-1]
            ma10 = ma10_values[-1]
            current_price = close_prices[-1]

            # 前一根K线的EMA值（用于判断金叉/死叉）
            prev_ema9 = ema9_values[-2] if len(ema9_values) >= 2 else ema9
            prev_ema26 = ema26_values[-2] if len(ema26_values) >= 2 else ema26

            ema_diff = ema9 - ema26
            ema_diff_pct = abs(ema_diff) / ema26 * 100 if ema26 != 0 else 0

            return {
                'ema9': ema9,
                'ema26': ema26,
                'ema_diff': ema_diff,
                'ema_diff_pct': ema_diff_pct,
                'ma10': ma10,
                'current_price': current_price,
                'prev_ema9': prev_ema9,
                'prev_ema26': prev_ema26,
                'klines': klines,
                'ema9_values': ema9_values,
                'ema26_values': ema26_values
            }

        finally:
            cursor.close()
            conn.close()

    # ==================== 信号检测 ====================

    def check_ema_ma_consistency(self, ema_data: Dict, direction: str) -> Tuple[bool, str]:
        """
        检查EMA+MA方向一致性

        Args:
            ema_data: EMA数据
            direction: 'long' 或 'short'

        Returns:
            (是否一致, 原因说明)
        """
        ema9 = ema_data['ema9']
        ema26 = ema_data['ema26']
        ma10 = ema_data['ma10']
        price = ema_data['current_price']

        if direction == 'long':
            # 做多：EMA9 > EMA26 且 价格 > MA10
            ema_ok = ema9 > ema26
            ma_ok = price > ma10

            if not ema_ok:
                return False, f"EMA方向不符合做多(EMA9={ema9:.4f} <= EMA26={ema26:.4f})"
            if not ma_ok:
                return False, f"MA方向不符合做多(价格{price:.4f} <= MA10={ma10:.4f})"
            return True, "EMA+MA方向一致(做多)"

        else:  # short
            # 做空：EMA9 < EMA26 且 价格 < MA10
            ema_ok = ema9 < ema26
            ma_ok = price < ma10

            if not ema_ok:
                return False, f"EMA方向不符合做空(EMA9={ema9:.4f} >= EMA26={ema26:.4f})"
            if not ma_ok:
                return False, f"MA方向不符合做空(价格{price:.4f} >= MA10={ma10:.4f})"
            return True, "EMA+MA方向一致(做空)"

    def check_golden_death_cross(self, ema_data: Dict) -> Tuple[Optional[str], str]:
        """
        检测金叉/死叉信号

        Returns:
            (信号方向 'long'/'short'/None, 信号描述)
        """
        ema9 = ema_data['ema9']
        ema26 = ema_data['ema26']
        prev_ema9 = ema_data['prev_ema9']
        prev_ema26 = ema_data['prev_ema26']
        ema_diff_pct = ema_data['ema_diff_pct']

        # 金叉：前一根EMA9 <= EMA26，当前EMA9 > EMA26
        is_golden_cross = prev_ema9 <= prev_ema26 and ema9 > ema26

        # 死叉：前一根EMA9 >= EMA26，当前EMA9 < EMA26
        is_death_cross = prev_ema9 >= prev_ema26 and ema9 < ema26

        if is_golden_cross:
            if ema_diff_pct < self.MIN_SIGNAL_STRENGTH:
                return None, f"金叉信号强度不足({ema_diff_pct:.3f}% < {self.MIN_SIGNAL_STRENGTH}%)"
            return 'long', f"金叉信号(强度{ema_diff_pct:.3f}%)"

        if is_death_cross:
            if ema_diff_pct < self.MIN_SIGNAL_STRENGTH:
                return None, f"死叉信号强度不足({ema_diff_pct:.3f}% < {self.MIN_SIGNAL_STRENGTH}%)"
            return 'short', f"死叉信号(强度{ema_diff_pct:.3f}%)"

        return None, "无金叉/死叉信号"

    def check_sustained_trend(self, symbol: str) -> Tuple[Optional[str], str]:
        """
        检测连续趋势信号
        需要15M和5M周期EMA差值同时放大

        Returns:
            (信号方向 'long'/'short'/None, 信号描述)
        """
        # 获取15M数据
        ema_15m = self.get_ema_data(symbol, '15m', 50)
        if not ema_15m:
            return None, "15M数据不足"

        # 获取5M数据
        ema_5m = self.get_ema_data(symbol, '5m', 50)
        if not ema_5m:
            return None, "5M数据不足"

        # 检查15M趋势方向
        ema_diff_15m = ema_15m['ema_diff']
        is_uptrend = ema_diff_15m > 0

        # 检查15M差值是否在合理范围内
        ema_diff_pct_15m = ema_15m['ema_diff_pct']
        if ema_diff_pct_15m < self.MIN_SIGNAL_STRENGTH:
            return None, f"15M趋势强度不足({ema_diff_pct_15m:.3f}%)"

        # 检查5M连续3根K线差值放大
        ema9_values = ema_5m['ema9_values']
        ema26_values = ema_5m['ema26_values']

        if len(ema9_values) < 4 or len(ema26_values) < 4:
            return None, "5M EMA数据不足"

        # 计算最近4根K线的EMA差值
        diff_values = []
        for i in range(-4, 0):
            diff = abs(ema9_values[i] - ema26_values[i])
            diff_values.append(diff)

        # 检查是否连续放大（后3根比前1根大，且后面的比前面的大）
        expanding = True
        for i in range(1, len(diff_values)):
            if diff_values[i] <= diff_values[i-1]:
                expanding = False
                break

        if not expanding:
            return None, f"5M差值未连续放大: {[f'{d:.6f}' for d in diff_values]}"

        # 检查EMA+MA方向一致性
        direction = 'long' if is_uptrend else 'short'
        consistent, reason = self.check_ema_ma_consistency(ema_15m, direction)
        if not consistent:
            return None, reason

        return direction, f"连续趋势信号({direction}, 15M差值{ema_diff_pct_15m:.3f}%, 5M连续放大)"

    def check_oscillation_reversal(self, symbol: str) -> Tuple[Optional[str], str]:
        """
        检测震荡区间反向开仓信号
        条件：连续4根同向K线 + 幅度<0.5% + 成交量条件

        Returns:
            (信号方向 'long'/'short'/None, 信号描述)
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            # 获取最近8根15M K线
            cursor.execute("""
                SELECT timestamp, open_price, high_price, low_price, close_price, volume
                FROM kline_data
                WHERE symbol = %s AND timeframe = '15m' AND exchange = 'binance_futures'
                ORDER BY timestamp DESC
                LIMIT 8
            """, (symbol,))

            klines = cursor.fetchall()

            if len(klines) < 8:
                return None, "K线数据不足"

            # 检查最近4根K线是否连续同向
            recent_4 = klines[:4]  # 最近4根

            all_bullish = all(float(k['close_price']) > float(k['open_price']) for k in recent_4)
            all_bearish = all(float(k['close_price']) < float(k['open_price']) for k in recent_4)

            if not all_bullish and not all_bearish:
                return None, "非连续同向K线"

            # 检查幅度是否<0.5%
            highs = [float(k['high_price']) for k in recent_4]
            lows = [float(k['low_price']) for k in recent_4]
            max_high = max(highs)
            min_low = min(lows)
            range_pct = (max_high - min_low) / min_low * 100 if min_low > 0 else 100

            if range_pct >= self.OSCILLATION_RANGE:
                return None, f"幅度过大({range_pct:.2f}% >= {self.OSCILLATION_RANGE}%)"

            # 检查成交量条件
            volumes = [float(k['volume']) for k in klines]
            current_volume = volumes[0]
            prev_avg_volume = sum(volumes[1:5]) / 4  # 前4根均值

            if prev_avg_volume == 0:
                return None, "成交量数据异常"

            volume_ratio = current_volume / prev_avg_volume

            if all_bullish:
                # 连续阳线 → 成交量缩量 → 做空
                if volume_ratio >= self.VOLUME_SHRINK_THRESHOLD:
                    return None, f"成交量未缩量({volume_ratio:.2f} >= {self.VOLUME_SHRINK_THRESHOLD})"

                # 检查EMA+MA方向一致性
                ema_data = self.get_ema_data(symbol, '15m', 50)
                if ema_data:
                    consistent, reason = self.check_ema_ma_consistency(ema_data, 'short')
                    if not consistent:
                        return None, reason

                return 'short', f"震荡反向做空(连续{self.OSCILLATION_BARS}阳线+缩量{volume_ratio:.2f})"

            else:  # all_bearish
                # 连续阴线 → 成交量放量 → 做多
                if volume_ratio <= self.VOLUME_EXPAND_THRESHOLD:
                    return None, f"成交量未放量({volume_ratio:.2f} <= {self.VOLUME_EXPAND_THRESHOLD})"

                # 检查EMA+MA方向一致性
                ema_data = self.get_ema_data(symbol, '15m', 50)
                if ema_data:
                    consistent, reason = self.check_ema_ma_consistency(ema_data, 'long')
                    if not consistent:
                        return None, reason

                return 'long', f"震荡反向做多(连续{self.OSCILLATION_BARS}阴线+放量{volume_ratio:.2f})"

        finally:
            cursor.close()
            conn.close()

    # ==================== 技术指标过滤器 ====================

    def check_rsi_filter(self, symbol: str, direction: str, strategy: Dict) -> Tuple[bool, str]:
        """
        RSI过滤器检查

        Args:
            symbol: 交易对
            direction: 'long' 或 'short'
            strategy: 策略配置

        Returns:
            (是否通过, 原因说明)
        """
        rsi_config = strategy.get('rsiFilter', {})
        if not rsi_config.get('enabled', False):
            return True, "RSI过滤器未启用"

        # 获取K线数据计算RSI
        ema_data = self.get_ema_data(symbol, '15m', 50)
        if not ema_data or 'klines' not in ema_data:
            return True, "RSI数据不足，跳过过滤"

        close_prices = [float(k['close_price']) for k in ema_data['klines']]
        rsi_values = self.calculate_rsi(close_prices, 14)

        if not rsi_values:
            return True, "RSI计算失败，跳过过滤"

        current_rsi = rsi_values[-1]

        long_max = rsi_config.get('longMax', 70)
        short_min = rsi_config.get('shortMin', 30)

        if direction == 'long':
            # 做多时RSI不能太高（超买）
            if current_rsi > long_max:
                return False, f"RSI过滤失败: 做多RSI={current_rsi:.1f} > {long_max}(超买)"
            return True, f"RSI过滤通过: 做多RSI={current_rsi:.1f} <= {long_max}"
        else:  # short
            # 做空时RSI不能太低（超卖）
            if current_rsi < short_min:
                return False, f"RSI过滤失败: 做空RSI={current_rsi:.1f} < {short_min}(超卖)"
            return True, f"RSI过滤通过: 做空RSI={current_rsi:.1f} >= {short_min}"

    def check_macd_filter(self, symbol: str, direction: str, strategy: Dict) -> Tuple[bool, str]:
        """
        MACD过滤器检查

        Args:
            symbol: 交易对
            direction: 'long' 或 'short'
            strategy: 策略配置

        Returns:
            (是否通过, 原因说明)
        """
        macd_config = strategy.get('macdFilter', {})
        if not macd_config.get('enabled', False):
            return True, "MACD过滤器未启用"

        # 获取K线数据计算MACD
        ema_data = self.get_ema_data(symbol, '15m', 50)
        if not ema_data or 'klines' not in ema_data:
            return True, "MACD数据不足，跳过过滤"

        close_prices = [float(k['close_price']) for k in ema_data['klines']]
        macd_data = self.calculate_macd(close_prices)

        if not macd_data['histogram']:
            return True, "MACD计算失败，跳过过滤"

        current_histogram = macd_data['histogram'][-1]
        current_macd = macd_data['macd'][-1] if macd_data['macd'] else 0

        long_require_positive = macd_config.get('longRequirePositive', True)
        short_require_negative = macd_config.get('shortRequireNegative', True)

        if direction == 'long':
            # 做多时要求MACD柱为正（或MACD线在零轴上方）
            if long_require_positive and current_histogram < 0:
                return False, f"MACD过滤失败: 做多要求MACD柱>0，当前={current_histogram:.6f}"
            return True, f"MACD过滤通过: 做多MACD柱={current_histogram:.6f}"
        else:  # short
            # 做空时要求MACD柱为负（或MACD线在零轴下方）
            if short_require_negative and current_histogram > 0:
                return False, f"MACD过滤失败: 做空要求MACD柱<0，当前={current_histogram:.6f}"
            return True, f"MACD过滤通过: 做空MACD柱={current_histogram:.6f}"

    def check_kdj_filter(self, symbol: str, direction: str, strategy: Dict) -> Tuple[bool, str]:
        """
        KDJ过滤器检查

        Args:
            symbol: 交易对
            direction: 'long' 或 'short'
            strategy: 策略配置

        Returns:
            (是否通过, 原因说明)
        """
        kdj_config = strategy.get('kdjFilter', {})
        if not kdj_config.get('enabled', False):
            return True, "KDJ过滤器未启用"

        # 获取K线数据计算KDJ
        ema_data = self.get_ema_data(symbol, '15m', 50)
        if not ema_data or 'klines' not in ema_data:
            return True, "KDJ数据不足，跳过过滤"

        kdj_data = self.calculate_kdj(ema_data['klines'])

        if not kdj_data['k']:
            return True, "KDJ计算失败，跳过过滤"

        current_k = kdj_data['k'][-1]
        current_d = kdj_data['d'][-1]

        long_max_k = kdj_config.get('longMaxK', 80)
        short_min_k = kdj_config.get('shortMinK', 20)

        if direction == 'long':
            # 做多时K值不能太高（超买区域）
            if current_k > long_max_k:
                return False, f"KDJ过滤失败: 做多K={current_k:.1f} > {long_max_k}(超买)"
            return True, f"KDJ过滤通过: 做多K={current_k:.1f} <= {long_max_k}"
        else:  # short
            # 做空时K值不能太低（超卖区域）
            if current_k < short_min_k:
                return False, f"KDJ过滤失败: 做空K={current_k:.1f} < {short_min_k}(超卖)"
            return True, f"KDJ过滤通过: 做空K={current_k:.1f} >= {short_min_k}"

    def check_price_distance_limit(self, symbol: str, direction: str, current_price: float,
                                    ema_data: Dict, strategy: Dict) -> Tuple[bool, str]:
        """
        价格距离EMA限制检查（防追涨杀跌）

        Args:
            symbol: 交易对
            direction: 'long' 或 'short'
            current_price: 当前价格
            ema_data: EMA数据
            strategy: 策略配置

        Returns:
            (是否通过, 原因说明)
        """
        price_limit_config = strategy.get('priceDistanceLimit', {})
        if not price_limit_config.get('enabled', False):
            return True, "价格距离限制未启用"

        ema9 = ema_data.get('ema9', 0)
        if ema9 <= 0:
            return True, "EMA9数据异常，跳过检查"

        # 计算价格与EMA9的偏离百分比
        price_distance_pct = (current_price - ema9) / ema9 * 100

        max_above_ema = price_limit_config.get('maxAboveEMA', 1.0)
        max_below_ema = price_limit_config.get('maxBelowEMA', 1.0)

        if direction == 'long':
            # 做多时，价格不能高于EMA太多（防止追涨）
            if price_distance_pct > max_above_ema:
                return False, f"价格距离限制: 做多价格偏离EMA9 +{price_distance_pct:.2f}% > +{max_above_ema}%（追涨风险）"
            return True, f"价格距离检查通过: 偏离EMA9 {price_distance_pct:+.2f}%"
        else:  # short
            # 做空时，价格不能低于EMA太多（防止杀跌）
            if price_distance_pct < -max_below_ema:
                return False, f"价格距离限制: 做空价格偏离EMA9 {price_distance_pct:.2f}% < -{max_below_ema}%（杀跌风险）"
            return True, f"价格距离检查通过: 偏离EMA9 {price_distance_pct:+.2f}%"

    def detect_market_regime(self, symbol: str) -> Tuple[str, Dict]:
        """
        检测市场行情状态

        Returns:
            (行情状态, 详细信息)
            状态: 'strong_uptrend', 'weak_uptrend', 'ranging', 'weak_downtrend', 'strong_downtrend'
        """
        ema_data = self.get_ema_data(symbol, '15m', 100)
        if not ema_data:
            return 'ranging', {'reason': '数据不足'}

        ema_diff_pct = ema_data['ema_diff_pct']
        ema_diff = ema_data['ema_diff']
        current_price = ema_data['current_price']
        ma10 = ema_data['ma10']

        # 判断趋势方向
        is_uptrend = ema_diff > 0
        price_above_ma = current_price > ma10

        # 判断趋势强度
        if ema_diff_pct >= 0.5:
            strength = 'strong'
        elif ema_diff_pct >= 0.15:
            strength = 'weak'
        else:
            strength = 'none'

        info = {
            'ema_diff_pct': ema_diff_pct,
            'ema_diff': ema_diff,
            'price_above_ma': price_above_ma,
            'current_price': current_price,
            'ma10': ma10
        }

        if strength == 'none':
            return 'ranging', info

        if is_uptrend:
            if strength == 'strong' and price_above_ma:
                return 'strong_uptrend', info
            else:
                return 'weak_uptrend', info
        else:
            if strength == 'strong' and not price_above_ma:
                return 'strong_downtrend', info
            else:
                return 'weak_downtrend', info

    def check_adaptive_regime(self, symbol: str, direction: str, strategy: Dict) -> Tuple[bool, str]:
        """
        自适应行情模式检查

        Args:
            symbol: 交易对
            direction: 'long' 或 'short'
            strategy: 策略配置

        Returns:
            (是否允许开仓, 原因说明)
        """
        if not strategy.get('adaptiveRegime', False):
            return True, "行情自适应未启用"

        regime_params = strategy.get('regimeParams', {})
        if not regime_params:
            return True, "行情参数未配置"

        # 检测当前行情状态
        regime, info = self.detect_market_regime(symbol)

        # 获取该行情下的配置
        regime_config = regime_params.get(regime, {})
        allow_direction = regime_config.get('allowDirection', 'both')

        # 检查是否允许该方向开仓
        if allow_direction == 'none':
            return False, f"行情自适应: {regime} 模式禁止开仓"

        if allow_direction == 'long_only' and direction != 'long':
            return False, f"行情自适应: {regime} 模式只允许做多"

        if allow_direction == 'short_only' and direction != 'short':
            return False, f"行情自适应: {regime} 模式只允许做空"

        return True, f"行情自适应通过: {regime} 模式允许 {direction}"

    def check_sustained_trend_entry(self, symbol: str, direction: str, strategy: Dict) -> Tuple[bool, str]:
        """
        持续趋势中开仓检查（错过金叉/死叉后仍可在趋势中开仓）

        Args:
            symbol: 交易对
            direction: 'long' 或 'short'
            strategy: 策略配置

        Returns:
            (是否可以开仓, 原因说明)
        """
        sustained_config = strategy.get('sustainedTrend', {})
        if not sustained_config.get('enabled', False):
            return False, "持续趋势开仓未启用"

        ema_data = self.get_ema_data(symbol, '15m', 50)
        if not ema_data:
            return False, "EMA数据不足"

        ema_diff_pct = ema_data['ema_diff_pct']
        ema_diff = ema_data['ema_diff']

        min_strength = sustained_config.get('minStrength', 0.15)
        max_strength = sustained_config.get('maxStrength', 1.0)
        require_ma10_confirm = sustained_config.get('requireMA10Confirm', True)
        require_price_confirm = sustained_config.get('requirePriceConfirm', True)

        # 检查趋势方向是否匹配
        is_uptrend = ema_diff > 0
        if direction == 'long' and not is_uptrend:
            return False, "持续趋势: 方向不匹配，非上升趋势"
        if direction == 'short' and is_uptrend:
            return False, "持续趋势: 方向不匹配，非下降趋势"

        # 检查趋势强度范围
        if ema_diff_pct < min_strength:
            return False, f"持续趋势: 强度不足 {ema_diff_pct:.3f}% < {min_strength}%"
        if ema_diff_pct > max_strength:
            return False, f"持续趋势: 强度过大 {ema_diff_pct:.3f}% > {max_strength}%（可能反转）"

        # MA10确认
        if require_ma10_confirm:
            ma10 = ema_data['ma10']
            ema10 = self.calculate_ema([float(k['close_price']) for k in ema_data['klines']], 10)
            if ema10:
                current_ema10 = ema10[-1]
                if direction == 'long' and current_ema10 < ma10:
                    return False, f"持续趋势: MA10/EMA10不确认上升趋势"
                if direction == 'short' and current_ema10 > ma10:
                    return False, f"持续趋势: MA10/EMA10不确认下降趋势"

        # 价格确认
        if require_price_confirm:
            current_price = ema_data['current_price']
            ema9 = ema_data['ema9']
            if direction == 'long' and current_price < ema9:
                return False, f"持续趋势: 价格未确认上升趋势（价格{current_price:.4f} < EMA9 {ema9:.4f}）"
            if direction == 'short' and current_price > ema9:
                return False, f"持续趋势: 价格未确认下降趋势（价格{current_price:.4f} > EMA9 {ema9:.4f}）"

        # 检查冷却时间
        cooldown_minutes = sustained_config.get('cooldownMinutes', 60)
        cooldown_key = f"{symbol}_{direction}_sustained"
        last_entry = self.last_entry_time.get(cooldown_key)

        if last_entry:
            elapsed = (self.get_local_time() - last_entry).total_seconds() / 60
            if elapsed < cooldown_minutes:
                return False, f"持续趋势: 冷却中，还需等待 {cooldown_minutes - elapsed:.0f} 分钟"

        return True, f"持续趋势开仓通过: 强度{ema_diff_pct:.3f}%在{min_strength}%~{max_strength}%范围内"

    def apply_all_filters(self, symbol: str, direction: str, current_price: float,
                          ema_data: Dict, strategy: Dict) -> Tuple[bool, List[str]]:
        """
        应用所有技术指标过滤器

        Args:
            symbol: 交易对
            direction: 'long' 或 'short'
            current_price: 当前价格
            ema_data: EMA数据
            strategy: 策略配置

        Returns:
            (是否通过所有过滤, 过滤结果列表)
        """
        filter_results = []
        all_passed = True

        # 1. RSI过滤
        passed, reason = self.check_rsi_filter(symbol, direction, strategy)
        filter_results.append(f"RSI: {reason}")
        if not passed:
            all_passed = False

        # 2. MACD过滤
        passed, reason = self.check_macd_filter(symbol, direction, strategy)
        filter_results.append(f"MACD: {reason}")
        if not passed:
            all_passed = False

        # 3. KDJ过滤
        passed, reason = self.check_kdj_filter(symbol, direction, strategy)
        filter_results.append(f"KDJ: {reason}")
        if not passed:
            all_passed = False

        # 4. 价格距离限制
        passed, reason = self.check_price_distance_limit(symbol, direction, current_price, ema_data, strategy)
        filter_results.append(f"价格距离: {reason}")
        if not passed:
            all_passed = False

        # 5. 行情自适应
        passed, reason = self.check_adaptive_regime(symbol, direction, strategy)
        filter_results.append(f"行情自适应: {reason}")
        if not passed:
            all_passed = False

        return all_passed, filter_results

    # ==================== 平仓信号检测 ====================

    def check_cross_reversal(self, position: Dict, ema_data: Dict) -> Tuple[bool, str]:
        """
        检测金叉/死叉反转信号（不检查强度，直接平仓）

        Args:
            position: 持仓信息
            ema_data: 当前EMA数据

        Returns:
            (是否需要平仓, 原因)
        """
        position_side = position.get('position_side', 'LONG')

        ema9 = ema_data['ema9']
        ema26 = ema_data['ema26']
        prev_ema9 = ema_data['prev_ema9']
        prev_ema26 = ema_data['prev_ema26']

        if position_side == 'LONG':
            # 持多仓 + 死叉 → 立即平仓
            is_death_cross = prev_ema9 >= prev_ema26 and ema9 < ema26
            if is_death_cross:
                return True, "死叉反转平仓(不检查强度)"

            # 趋势反转：EMA9 < EMA26
            if ema9 < ema26:
                return True, "趋势反转平仓(EMA9 < EMA26)"

        else:  # SHORT
            # 持空仓 + 金叉 → 立即平仓
            is_golden_cross = prev_ema9 <= prev_ema26 and ema9 > ema26
            if is_golden_cross:
                return True, "金叉反转平仓(不检查强度)"

            # 趋势反转：EMA9 > EMA26
            if ema9 > ema26:
                return True, "趋势反转平仓(EMA9 > EMA26)"

        return False, ""

    def check_trailing_stop(self, position: Dict, current_price: float) -> Tuple[bool, str, Dict]:
        """
        检测移动止盈（跟踪止盈）

        Args:
            position: 持仓信息
            current_price: 当前价格

        Returns:
            (是否需要平仓, 原因, 需要更新的字段)
        """
        entry_price = float(position.get('entry_price') or 0)
        position_side = position.get('position_side', 'LONG')
        max_profit_pct = float(position.get('max_profit_pct') or 0)
        trailing_activated = position.get('trailing_stop_activated', False)

        if entry_price <= 0:
            return False, "", {}

        # 计算当前盈亏百分比
        if position_side == 'LONG':
            current_pnl_pct = (current_price - entry_price) / entry_price * 100
        else:
            current_pnl_pct = (entry_price - current_price) / entry_price * 100

        updates = {}

        # 更新最高盈利
        if current_pnl_pct > max_profit_pct:
            updates['max_profit_pct'] = current_pnl_pct
            max_profit_pct = current_pnl_pct

        # 检查是否触发最大止盈
        if current_pnl_pct >= self.MAX_TAKE_PROFIT:
            return True, f"最大止盈平仓(盈利{current_pnl_pct:.2f}% >= {self.MAX_TAKE_PROFIT}%)", updates

        # 检查是否激活移动止盈
        if not trailing_activated and max_profit_pct >= self.TRAILING_ACTIVATE:
            updates['trailing_stop_activated'] = True
            trailing_activated = True
            logger.info(f"移动止盈已激活: 最高盈利{max_profit_pct:.2f}% >= {self.TRAILING_ACTIVATE}%")

        # 移动止盈已激活，检查回撤
        if trailing_activated:
            callback_pct = max_profit_pct - current_pnl_pct
            if callback_pct >= self.TRAILING_CALLBACK:
                return True, f"移动止盈平仓(从最高{max_profit_pct:.2f}%回撤{callback_pct:.2f}%)", updates

        return False, "", updates

    def check_hard_stop_loss(self, position: Dict, current_price: float) -> Tuple[bool, str]:
        """
        检测硬止损

        Args:
            position: 持仓信息
            current_price: 当前价格

        Returns:
            (是否需要平仓, 原因)
        """
        entry_price = float(position.get('entry_price') or 0)
        position_side = position.get('position_side', 'LONG')

        if entry_price <= 0:
            return False, ""

        # 计算当前盈亏百分比
        if position_side == 'LONG':
            current_pnl_pct = (current_price - entry_price) / entry_price * 100
        else:
            current_pnl_pct = (entry_price - current_price) / entry_price * 100

        if current_pnl_pct <= -self.HARD_STOP_LOSS:
            return True, f"硬止损平仓(亏损{abs(current_pnl_pct):.2f}% >= {self.HARD_STOP_LOSS}%)"

        return False, ""

    def check_trend_weakening(self, position: Dict, ema_data: Dict) -> Tuple[bool, str]:
        """
        检测趋势减弱（开仓后30分钟开始监控）

        当EMA差值连续3次减弱时，触发平仓

        Args:
            position: 持仓信息
            ema_data: 当前EMA数据

        Returns:
            (是否需要平仓, 原因)
        """
        entry_time = position.get('entry_time') or position.get('created_at')
        if not entry_time:
            return False, ""

        # 检查是否超过30分钟
        if isinstance(entry_time, str):
            entry_time = datetime.strptime(entry_time, '%Y-%m-%d %H:%M:%S')

        elapsed_minutes = (self.get_local_time() - entry_time).total_seconds() / 60

        if elapsed_minutes < self.STRENGTH_MONITOR_DELAY:
            return False, f"监控等待中({elapsed_minutes:.0f}/{self.STRENGTH_MONITOR_DELAY}分钟)"

        # 获取开仓时的EMA差值
        entry_ema_diff = float(position.get('entry_ema_diff') or 0)
        if entry_ema_diff <= 0:
            return False, "无开仓时EMA差值记录"

        current_ema_diff_pct = ema_data['ema_diff_pct']
        position_side = position.get('position_side', 'LONG')

        # 检查趋势方向是否仍然正确
        ema_diff = ema_data['ema_diff']
        if position_side == 'LONG' and ema_diff < 0:
            return True, f"趋势反转平仓(做多但EMA9 < EMA26)"
        if position_side == 'SHORT' and ema_diff > 0:
            return True, f"趋势反转平仓(做空但EMA9 > EMA26)"

        # 检查强度是否减弱到开仓时的50%以下
        if current_ema_diff_pct < entry_ema_diff * 0.5:
            return True, f"趋势减弱平仓(当前强度{current_ema_diff_pct:.3f}% < 开仓时{entry_ema_diff:.3f}%的50%)"

        return False, f"趋势强度正常(当前{current_ema_diff_pct:.3f}%, 开仓时{entry_ema_diff:.3f}%)"

    def check_smart_exit(self, position: Dict, current_price: float, ema_data: Dict,
                          strategy: Dict) -> Tuple[bool, str, Dict]:
        """
        智能出场检测（整合所有出场逻辑）

        检测顺序（按优先级）：
        1. 硬止损 (-2.5%)
        2. 最大止盈 (+8%)
        3. 金叉/死叉反转
        4. 趋势减弱
        5. 移动止盈回撤

        Args:
            position: 持仓信息
            current_price: 当前价格
            ema_data: EMA数据
            strategy: 策略配置

        Returns:
            (是否需要平仓, 原因, 需要更新的字段)
        """
        updates = {}

        entry_price = float(position.get('entry_price') or 0)
        position_side = position.get('position_side', 'LONG')

        if entry_price <= 0:
            return False, "", updates

        # 计算当前盈亏百分比
        if position_side == 'LONG':
            current_pnl_pct = (current_price - entry_price) / entry_price * 100
        else:
            current_pnl_pct = (entry_price - current_price) / entry_price * 100

        # 获取策略配置的止损止盈参数（如果有）
        stop_loss_pct = strategy.get('stopLossPercent') or strategy.get('stopLoss') or self.HARD_STOP_LOSS
        max_take_profit = strategy.get('takeProfitPercent') or strategy.get('takeProfit') or self.MAX_TAKE_PROFIT

        # 移动止盈参数：优先从 smartStopLoss.trailingStopLoss 读取（前端格式），其次从顶层读取
        smart_stop_loss = strategy.get('smartStopLoss', {})
        trailing_config = smart_stop_loss.get('trailingStopLoss', {})
        trailing_activate = strategy.get('trailingActivate') or trailing_config.get('activatePct') or self.TRAILING_ACTIVATE
        trailing_callback = strategy.get('trailingCallback') or trailing_config.get('distancePct') or self.TRAILING_CALLBACK

        # 移动止损参数：从前端 smartStopLoss.trailingStopLoss 读取
        # 前端字段: enabled, activatePct, distancePct, stepPct
        trailing_sl_config = smart_stop_loss.get('trailingStopLoss', {})
        trailing_sl_enabled = trailing_sl_config.get('enabled', False)
        trailing_sl_activate = strategy.get('trailingStopLossActivate') or trailing_sl_config.get('activatePct') or self.TRAILING_STOP_LOSS_ACTIVATE
        trailing_sl_distance = strategy.get('trailingStopLossDistance') or trailing_sl_config.get('distancePct') or self.TRAILING_STOP_LOSS_DISTANCE

        # 获取当前止损价
        current_stop_loss = float(position.get('stop_loss_price') or 0)

        # 0. 移动止损检查（在硬止损之前）
        # 当启用移动止损且盈利达到阈值时，动态调整止损价
        if trailing_sl_enabled and current_pnl_pct >= trailing_sl_activate and current_stop_loss > 0:
            if position_side == 'LONG':
                # 做多：止损价 = 当前价 - 距离%
                new_stop_loss = current_price * (1 - trailing_sl_distance / 100)
                if new_stop_loss > current_stop_loss:
                    updates['stop_loss_price'] = new_stop_loss
                    logger.info(f"移动止损上移: {position.get('symbol')} 做多, 盈利{current_pnl_pct:.2f}%, 止损从{current_stop_loss:.4f}上移到{new_stop_loss:.4f}")
            else:
                # 做空：止损价 = 当前价 + 距离%
                new_stop_loss = current_price * (1 + trailing_sl_distance / 100)
                if new_stop_loss < current_stop_loss:
                    updates['stop_loss_price'] = new_stop_loss
                    logger.info(f"移动止损下移: {position.get('symbol')} 做空, 盈利{current_pnl_pct:.2f}%, 止损从{current_stop_loss:.4f}下移到{new_stop_loss:.4f}")

        # 1. 检查是否触发止损价（包括移动止损后的价格）
        updated_stop_loss = updates.get('stop_loss_price', current_stop_loss)
        if updated_stop_loss > 0:
            # 判断是移动止损还是普通止损（通过盈亏判断：盈利时触发的是移动止损）
            is_trailing_stop = current_pnl_pct > 0
            stop_type = "移动止损" if is_trailing_stop else "止损"
            if position_side == 'LONG' and current_price <= updated_stop_loss:
                return True, f"{stop_type}平仓(价格{current_price:.4f} <= 止损价{updated_stop_loss:.4f})", updates
            elif position_side == 'SHORT' and current_price >= updated_stop_loss:
                return True, f"{stop_type}平仓(价格{current_price:.4f} >= 止损价{updated_stop_loss:.4f})", updates

        # 2. 硬止损检查（百分比止损，作为后备）
        if current_pnl_pct <= -stop_loss_pct:
            return True, f"硬止损平仓(亏损{abs(current_pnl_pct):.2f}% >= {stop_loss_pct}%)", updates

        # 3. 最大止盈检查
        if current_pnl_pct >= max_take_profit:
            return True, f"最大止盈平仓(盈利{current_pnl_pct:.2f}% >= {max_take_profit}%)", updates

        # 4. 金叉/死叉反转检查
        close_needed, close_reason = self.check_cross_reversal(position, ema_data)
        if close_needed:
            return True, close_reason, updates

        # 5. 趋势减弱检查
        close_needed, close_reason = self.check_trend_weakening(position, ema_data)
        if close_needed:
            return True, close_reason, updates

        # 6. 移动止盈检查
        max_profit_pct = float(position.get('max_profit_pct') or 0)
        trailing_activated = position.get('trailing_stop_activated') or False

        # 更新最高盈利
        if current_pnl_pct > max_profit_pct:
            updates['max_profit_pct'] = current_pnl_pct
            max_profit_pct = current_pnl_pct

            # 更新最高/最低价格
            if position_side == 'LONG':
                updates['max_profit_price'] = current_price
            else:
                updates['max_profit_price'] = current_price

        # 检查是否激活移动止盈
        if not trailing_activated and max_profit_pct >= trailing_activate:
            updates['trailing_stop_activated'] = True
            trailing_activated = True

            # 计算并记录当前的止损价格
            if position_side == 'LONG':
                trailing_stop_price = current_price * (1 - trailing_callback / 100)
            else:
                trailing_stop_price = current_price * (1 + trailing_callback / 100)
            updates['trailing_stop_price'] = trailing_stop_price

            logger.info(f"移动止盈已激活: 最高盈利{max_profit_pct:.2f}% >= {trailing_activate}%，止损价={trailing_stop_price:.4f}")

        # 移动止盈已激活，检查回撤
        if trailing_activated:
            callback_pct = max_profit_pct - current_pnl_pct
            if callback_pct >= trailing_callback:
                return True, f"移动止盈平仓(从最高{max_profit_pct:.2f}%回撤{callback_pct:.2f}% >= {trailing_callback}%)", updates

            # 更新移动止损价格
            if position_side == 'LONG':
                new_trailing_price = current_price * (1 - trailing_callback / 100)
                current_trailing_price = float(position.get('trailing_stop_price') or 0)
                if new_trailing_price > current_trailing_price:
                    updates['trailing_stop_price'] = new_trailing_price
            else:
                new_trailing_price = current_price * (1 + trailing_callback / 100)
                current_trailing_price = float(position.get('trailing_stop_price') or float('inf'))
                if new_trailing_price < current_trailing_price:
                    updates['trailing_stop_price'] = new_trailing_price

        return False, "", updates

    # ==================== 开仓执行 ====================

    async def execute_open_position(self, symbol: str, direction: str, signal_type: str,
                                     strategy: Dict, account_id: int = 2,
                                     signal_reason: str = None) -> Dict:
        """
        执行开仓

        Args:
            symbol: 交易对
            direction: 'long' 或 'short'
            signal_type: 信号类型
            strategy: 策略配置
            account_id: 账户ID
            signal_reason: 开仓原因详情

        Returns:
            执行结果
        """
        try:
            # ========== 开仓前检查 ==========
            pre_check = self.position_validator.validate_before_open(symbol, direction)
            if not pre_check['allow_open']:
                logger.info(f"[开仓前检查] 🚫 {symbol} {direction} 被拦截: {pre_check['reason']}")
                return {'success': False, 'error': f"开仓前检查未通过: {pre_check['reason']}"}

            leverage = strategy.get('leverage', 10)
            position_size_pct = strategy.get('positionSizePct', 5)  # 账户资金的5%
            sync_live = strategy.get('syncLive', False)

            # 获取当前价格
            ema_data = self.get_ema_data(symbol, '15m', 50)
            if not ema_data:
                return {'success': False, 'error': '获取价格数据失败'}

            current_price = ema_data['current_price']
            ema_diff_pct = ema_data['ema_diff_pct']

            # 计算开仓数量
            conn = self.get_db_connection()
            cursor = conn.cursor()

            try:
                cursor.execute("""
                    SELECT current_balance FROM paper_trading_accounts WHERE id = %s
                """, (account_id,))
                account = cursor.fetchone()

                if not account:
                    return {'success': False, 'error': '账户不存在'}

                balance = float(account['current_balance'])
                margin = balance * (position_size_pct / 100)
                notional = margin * leverage
                quantity = notional / current_price

                # 检查是否已有同方向持仓
                position_side = 'LONG' if direction == 'long' else 'SHORT'
                cursor.execute("""
                    SELECT id FROM futures_positions
                    WHERE account_id = %s AND symbol = %s AND position_side = %s AND status = 'open'
                """, (account_id, symbol, position_side))

                existing = cursor.fetchone()
                if existing:
                    return {'success': False, 'error': f'已有{position_side}持仓'}

            finally:
                cursor.close()
                conn.close()

            # 执行模拟开仓
            if self.futures_engine:
                # 转换方向格式：long -> LONG, short -> SHORT
                position_side = direction.upper()

                result = self.futures_engine.open_position(
                    account_id=account_id,
                    symbol=symbol,
                    position_side=position_side,
                    quantity=Decimal(str(quantity)),
                    leverage=leverage,
                    stop_loss_pct=Decimal(str(self.HARD_STOP_LOSS)),
                    take_profit_pct=Decimal(str(self.MAX_TAKE_PROFIT)),
                    source='strategy',
                    strategy_id=strategy.get('id')
                )

                if result.get('success'):
                    position_id = result.get('position_id')

                    # 更新开仓时的EMA差值和开仓原因
                    conn = self.get_db_connection()
                    cursor = conn.cursor()
                    try:
                        cursor.execute("""
                            UPDATE futures_positions
                            SET entry_signal_type = %s, entry_ema_diff = %s, entry_reason = %s
                            WHERE id = %s
                        """, (signal_type, ema_diff_pct, signal_reason, position_id))
                        conn.commit()
                    except Exception as e:
                        logger.warning(f"更新开仓信号类型失败: {e}")
                    finally:
                        cursor.close()
                        conn.close()

                    logger.info(f"✅ {symbol} 开仓成功: {direction} {quantity:.8f} @ {current_price:.4f}, 信号:{signal_type}")

                    # 同步实盘
                    if sync_live and self.live_engine:
                        await self._sync_live_open(symbol, direction, quantity, leverage, strategy)

                    return {
                        'success': True,
                        'position_id': position_id,
                        'direction': direction,
                        'quantity': quantity,
                        'price': current_price,
                        'signal_type': signal_type
                    }
                else:
                    return {'success': False, 'error': result.get('error', '开仓失败')}

            return {'success': False, 'error': '交易引擎未初始化'}

        except Exception as e:
            logger.error(f"开仓执行失败: {e}")
            return {'success': False, 'error': str(e)}

    async def _sync_live_open(self, symbol: str, direction: str, quantity: float,
                              leverage: int, strategy: Dict):
        """同步实盘开仓"""
        try:
            if not self.live_engine:
                return

            live_quantity_pct = strategy.get('liveQuantityPct', 10)
            live_quantity = quantity * (live_quantity_pct / 100)

            # 调用实盘引擎开仓
            result = await self.live_engine.open_position(
                symbol=symbol,
                direction=direction,
                quantity=live_quantity,
                leverage=leverage,
                stop_loss_pct=self.HARD_STOP_LOSS,
                take_profit_pct=self.MAX_TAKE_PROFIT
            )

            if result.get('success'):
                logger.info(f"✅ {symbol} 实盘同步开仓成功")
            else:
                logger.warning(f"⚠️ {symbol} 实盘同步开仓失败: {result.get('error')}")

        except Exception as e:
            logger.error(f"实盘同步开仓异常: {e}")

    # ==================== 平仓执行 ====================

    async def execute_close_position(self, position: Dict, reason: str,
                                      strategy: Dict) -> Dict:
        """
        执行平仓

        Args:
            position: 持仓信息
            reason: 平仓原因
            strategy: 策略配置

        Returns:
            执行结果
        """
        try:
            position_id = position.get('id')
            symbol = position.get('symbol')
            sync_live = strategy.get('syncLive', False)

            if self.futures_engine:
                result = self.futures_engine.close_position(
                    position_id=position_id,
                    reason=reason
                )

                if result.get('success'):
                    logger.info(f"✅ {symbol} 平仓成功: {reason}")

                    # 同步实盘平仓
                    if sync_live and self.live_engine:
                        await self._sync_live_close(position, strategy)

                    return {
                        'success': True,
                        'position_id': position_id,
                        'reason': reason,
                        'realized_pnl': result.get('realized_pnl')
                    }
                else:
                    return {'success': False, 'error': result.get('error', '平仓失败')}

            return {'success': False, 'error': '交易引擎未初始化'}

        except Exception as e:
            logger.error(f"平仓执行失败: {e}")
            return {'success': False, 'error': str(e)}

    async def _sync_live_close(self, position: Dict, strategy: Dict):
        """同步实盘平仓"""
        try:
            if not self.live_engine:
                return

            symbol = position.get('symbol')
            position_side = position.get('position_side')

            result = await self.live_engine.close_position_by_symbol(
                symbol=symbol,
                position_side=position_side
            )

            if result.get('success'):
                logger.info(f"✅ {symbol} 实盘同步平仓成功")
            else:
                logger.warning(f"⚠️ {symbol} 实盘同步平仓失败: {result.get('error')}")

        except Exception as e:
            logger.error(f"实盘同步平仓异常: {e}")

    # ==================== 主执行逻辑 ====================

    async def execute_strategy(self, strategy: Dict, account_id: int = 2) -> Dict:
        """
        执行策略

        Args:
            strategy: 策略配置
            account_id: 账户ID

        Returns:
            执行结果
        """
        results = []
        symbols = strategy.get('symbols', [])
        buy_directions = strategy.get('buyDirection', ['long', 'short'])

        for symbol in symbols:
            try:
                result = await self._execute_symbol(symbol, strategy, buy_directions, account_id)
                results.append(result)
            except Exception as e:
                logger.error(f"执行 {symbol} 策略失败: {e}")
                results.append({
                    'symbol': symbol,
                    'success': False,
                    'error': str(e)
                })

        return {
            'strategy_id': strategy.get('id'),
            'strategy_name': strategy.get('name'),
            'results': results,
            'timestamp': self.get_local_time().strftime('%Y-%m-%d %H:%M:%S')
        }

    async def _execute_symbol(self, symbol: str, strategy: Dict,
                               buy_directions: List[str], account_id: int) -> Dict:
        """执行单个交易对的策略"""
        debug_info = []

        # 1. 获取EMA数据
        ema_data = self.get_ema_data(symbol, '15m', 50)
        if not ema_data:
            return {'symbol': symbol, 'error': 'EMA数据不足', 'debug': debug_info}

        current_price = ema_data['current_price']
        debug_info.append(f"当前价格: {current_price:.4f}")
        debug_info.append(f"EMA9: {ema_data['ema9']:.4f}, EMA26: {ema_data['ema26']:.4f}")
        debug_info.append(f"EMA差值: {ema_data['ema_diff_pct']:.3f}%")

        # 2. 检查现有持仓，处理平仓（使用智能出场检测）
        positions = self._get_open_positions(symbol, account_id)
        close_results = []

        for position in positions:
            # 使用智能出场检测（整合所有出场逻辑）
            close_needed, close_reason, updates = self.check_smart_exit(
                position, current_price, ema_data, strategy
            )

            # 更新持仓信息（如最高盈利、移动止损价格等）
            if updates:
                self._update_position(position['id'], updates)
                if updates.get('trailing_stop_activated'):
                    debug_info.append(f"✨ 移动止盈已激活，最高盈利={updates.get('max_profit_pct', 0):.2f}%")

            # 执行平仓
            if close_needed:
                result = await self.execute_close_position(position, close_reason, strategy)
                close_results.append(result)
                debug_info.append(f"平仓: {close_reason}")

        # 3. 如果无持仓，检查开仓信号
        open_result = None
        if not positions or all(p.get('status') == 'closed' for p in positions):
            # 3.1 检查金叉/死叉信号
            signal, signal_desc = self.check_golden_death_cross(ema_data)
            debug_info.append(f"金叉/死叉: {signal_desc}")

            if signal and signal in buy_directions:
                # 检查EMA+MA一致性
                consistent, reason = self.check_ema_ma_consistency(ema_data, signal)
                debug_info.append(f"EMA+MA一致性: {reason}")

                if consistent:
                    # 应用所有技术指标过滤器
                    filters_passed, filter_results = self.apply_all_filters(
                        symbol, signal, current_price, ema_data, strategy
                    )
                    debug_info.extend(filter_results)

                    if filters_passed:
                        # 构建开仓原因
                        entry_reason = f"金叉/死叉信号: {reason}, EMA差值:{ema_data['ema_diff_pct']:.3f}%"
                        open_result = await self.execute_open_position(
                            symbol, signal, 'golden_cross' if signal == 'long' else 'death_cross',
                            strategy, account_id, signal_reason=entry_reason
                        )
                    else:
                        debug_info.append("⚠️ 技术指标过滤器未通过，跳过开仓")

            # 3.2 检查连续趋势信号（原有的5M放大检测）
            if not open_result or not open_result.get('success'):
                signal, signal_desc = self.check_sustained_trend(symbol)
                debug_info.append(f"连续趋势(5M放大): {signal_desc}")

                if signal and signal in buy_directions:
                    # 应用所有技术指标过滤器
                    filters_passed, filter_results = self.apply_all_filters(
                        symbol, signal, current_price, ema_data, strategy
                    )
                    debug_info.extend(filter_results)

                    if filters_passed:
                        # 构建开仓原因
                        entry_reason = f"连续趋势(5M放大): {signal_desc}"
                        open_result = await self.execute_open_position(
                            symbol, signal, 'sustained_trend', strategy, account_id,
                            signal_reason=entry_reason
                        )
                    else:
                        debug_info.append("⚠️ 技术指标过滤器未通过，跳过开仓")

            # 3.3 检查持续趋势开仓（错过金叉/死叉后仍可在趋势中开仓）
            if not open_result or not open_result.get('success'):
                for direction in buy_directions:
                    can_entry, sustained_reason = self.check_sustained_trend_entry(symbol, direction, strategy)
                    debug_info.append(f"持续趋势({direction}): {sustained_reason}")

                    if can_entry:
                        # 应用所有技术指标过滤器
                        filters_passed, filter_results = self.apply_all_filters(
                            symbol, direction, current_price, ema_data, strategy
                        )
                        debug_info.extend(filter_results)

                        if filters_passed:
                            # 构建开仓原因
                            entry_reason = f"持续趋势入场({direction}): {sustained_reason}"
                            open_result = await self.execute_open_position(
                                symbol, direction, 'sustained_trend_entry', strategy, account_id,
                                signal_reason=entry_reason
                            )
                            if open_result and open_result.get('success'):
                                # 记录持续趋势开仓时间（用于冷却）
                                cooldown_key = f"{symbol}_{direction}_sustained"
                                self.last_entry_time[cooldown_key] = self.get_local_time()
                                break
                        else:
                            debug_info.append("⚠️ 技术指标过滤器未通过，跳过开仓")

            # 3.4 检查震荡反向信号
            if not open_result or not open_result.get('success'):
                signal, signal_desc = self.check_oscillation_reversal(symbol)
                debug_info.append(f"震荡反向: {signal_desc}")

                if signal and signal in buy_directions:
                    # 应用所有技术指标过滤器
                    filters_passed, filter_results = self.apply_all_filters(
                        symbol, signal, current_price, ema_data, strategy
                    )
                    debug_info.extend(filter_results)

                    if filters_passed:
                        # 构建开仓原因
                        entry_reason = f"震荡反向信号: {signal_desc}"
                        open_result = await self.execute_open_position(
                            symbol, signal, 'oscillation_reversal', strategy, account_id,
                            signal_reason=entry_reason
                        )
                    else:
                        debug_info.append("⚠️ 技术指标过滤器未通过，跳过开仓")

        return {
            'symbol': symbol,
            'current_price': current_price,
            'ema_diff_pct': ema_data['ema_diff_pct'],
            'positions': len(positions),
            'close_results': close_results,
            'open_result': open_result,
            'debug': debug_info
        }

    def _get_open_positions(self, symbol: str, account_id: int) -> List[Dict]:
        """获取开仓持仓"""
        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT * FROM futures_positions
                WHERE account_id = %s AND symbol = %s AND status = 'open'
            """, (account_id, symbol))

            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

    def _update_position(self, position_id: int, updates: Dict):
        """更新持仓信息"""
        if not updates:
            return

        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            set_clauses = []
            values = []
            for key, value in updates.items():
                set_clauses.append(f"{key} = %s")
                values.append(value)

            values.append(position_id)

            cursor.execute(f"""
                UPDATE futures_positions
                SET {', '.join(set_clauses)}
                WHERE id = %s
            """, values)

            conn.commit()
        finally:
            cursor.close()
            conn.close()


    # ==================== 策略加载和调度 ====================

    def _load_strategies(self) -> List[Dict]:
        """从数据库加载启用的策略"""
        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT id, name, config, account_id, enabled, market_type
                FROM trading_strategies
                WHERE enabled = 1
                ORDER BY id
            """)

            strategies = []
            for row in cursor.fetchall():
                try:
                    import json
                    config = json.loads(row['config']) if row['config'] else {}
                    config['id'] = row['id']
                    config['name'] = row['name']
                    config['account_id'] = row.get('account_id', 2)
                    config['market_type'] = row.get('market_type', 'test')
                    strategies.append(config)
                except Exception as e:
                    logger.warning(f"解析策略配置失败 (ID={row['id']}): {e}")

            return strategies

        except Exception as e:
            logger.error(f"加载策略失败: {e}")
            return []
        finally:
            cursor.close()
            conn.close()

    async def check_and_execute_strategies(self):
        """检查并执行所有启用的策略（调度器接口）"""
        try:
            strategies = self._load_strategies()

            if not strategies:
                logger.debug("没有启用的策略")
                return

            logger.info(f"📊 V2执行器: 检查 {len(strategies)} 个策略")

            for strategy in strategies:
                try:
                    account_id = strategy.get('account_id', 2)
                    strategy_name = strategy.get('name', '未知')
                    logger.debug(f"执行策略: {strategy_name}")

                    result = await self.execute_strategy(strategy, account_id=account_id)

                    # 记录执行结果
                    for r in result.get('results', []):
                        symbol = r.get('symbol')
                        if r.get('open_result') and r['open_result'].get('success'):
                            logger.info(f"✅ {symbol} 开仓成功: {r['open_result'].get('signal_type')}")
                        if r.get('close_results'):
                            for cr in r['close_results']:
                                if cr.get('success'):
                                    logger.info(f"✅ {symbol} 平仓成功: {cr.get('reason')}")

                except Exception as e:
                    logger.error(f"执行策略失败 ({strategy.get('name')}): {e}")

        except Exception as e:
            logger.error(f"检查策略出错: {e}")

    async def run_loop(self, interval: int = 5):
        """运行策略执行循环"""
        self.running = True
        logger.info(f"🔄 V2策略执行器已启动（间隔: {interval}秒）")

        try:
            while self.running:
                try:
                    await self.check_and_execute_strategies()
                except Exception as e:
                    logger.error(f"策略执行循环出错: {e}")

                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("V2策略执行服务已取消")
            raise
        finally:
            self.running = False

    def start(self, interval: int = 5):
        """启动后台任务"""
        if hasattr(self, 'running') and self.running:
            logger.warning("V2策略执行器已在运行")
            return

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        self.task = loop.create_task(self.run_loop(interval))
        logger.info(f"V2策略执行器已启动（间隔: {interval}秒）")

    def stop(self):
        """停止后台任务"""
        self.running = False
        if hasattr(self, 'task') and self.task and not self.task.done():
            self.task.cancel()
        logger.info("V2策略执行器已停止")


# 创建全局实例
_strategy_executor_v2: Optional[StrategyExecutorV2] = None


def get_strategy_executor_v2() -> Optional[StrategyExecutorV2]:
    """获取全局执行器实例"""
    return _strategy_executor_v2


def init_strategy_executor_v2(db_config: Dict, futures_engine=None, live_engine=None) -> StrategyExecutorV2:
    """初始化全局执行器实例"""
    global _strategy_executor_v2
    _strategy_executor_v2 = StrategyExecutorV2(db_config, futures_engine, live_engine)
    return _strategy_executor_v2
