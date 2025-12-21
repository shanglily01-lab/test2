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
from app.utils.indicators import calculate_ema, calculate_ma, calculate_rsi, calculate_macd, calculate_kdj
from app.utils.db import create_connection


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
        self.live_engine_error = None  # 实盘引擎初始化错误
        self.LOCAL_TZ = timezone(timedelta(hours=8))

        # 加载配置文件
        self._load_margin_config()

        # 如果没有传入 live_engine，自动初始化（与V1保持一致）
        if self.live_engine is None:
            self._init_live_engine()

        # 将实盘引擎绑定到模拟引擎，用于同步平仓（与V1保持一致）
        if self.futures_engine and self.live_engine:
            self.futures_engine.live_engine = self.live_engine
            logger.info("✅ V2: 已将实盘引擎绑定到模拟引擎，支持同步平仓")

        # 冷却时间记录
        self.last_entry_time = {}  # {symbol_direction: datetime}

        # 初始化开仓前检查器（并设置 strategy_executor 用于待开仓自检后的开仓）
        self.position_validator = PositionValidator(db_config, futures_engine, strategy_executor=self)

    def _load_margin_config(self):
        """加载保证金配置"""
        try:
            from app.utils.config_loader import load_config
            config = load_config()
            margin_config = config.get('signals', {}).get('margin', {})

            # 模拟盘配置
            paper_config = margin_config.get('paper', {})
            self.paper_margin_mode = paper_config.get('mode', 'fixed')
            self.paper_margin_fixed = paper_config.get('fixed_amount', 100)
            self.paper_margin_percent = paper_config.get('percent', 1)

            # 实盘配置
            live_config = margin_config.get('live', {})
            self.live_margin_mode = live_config.get('mode', 'fixed')
            self.live_margin_fixed = live_config.get('fixed_amount', 100)
            self.live_margin_percent = live_config.get('percent', 1)

            logger.info(f"✅ 保证金配置已加载: 模拟盘={self.paper_margin_mode}({self.paper_margin_fixed}U/{self.paper_margin_percent}%), "
                       f"实盘={self.live_margin_mode}({self.live_margin_fixed}U/{self.live_margin_percent}%)")
        except Exception as e:
            logger.warning(f"加载保证金配置失败，使用默认值: {e}")
            self.paper_margin_mode = 'fixed'
            self.paper_margin_fixed = 100
            self.paper_margin_percent = 1
            self.live_margin_mode = 'fixed'
            self.live_margin_fixed = 100
            self.live_margin_percent = 1

    def calculate_margin(self, is_live: bool = False, account_balance: float = None) -> float:
        """
        计算开仓保证金

        Args:
            is_live: 是否实盘
            account_balance: 账户余额（百分比模式需要）

        Returns:
            保证金金额 (USDT)
        """
        if is_live:
            mode = self.live_margin_mode
            fixed = self.live_margin_fixed
            percent = self.live_margin_percent
        else:
            mode = self.paper_margin_mode
            fixed = self.paper_margin_fixed
            percent = self.paper_margin_percent

        if mode == 'percent' and account_balance:
            margin = account_balance * percent / 100
            logger.debug(f"保证金计算: {mode}模式, 余额={account_balance}, 百分比={percent}%, 保证金={margin:.2f}")
        else:
            margin = fixed
            logger.debug(f"保证金计算: fixed模式, 保证金={margin:.2f}")

        return margin

    def _init_live_engine(self):
        """初始化实盘交易引擎（与V1保持一致）"""
        try:
            from app.trading.binance_futures_engine import BinanceFuturesEngine
            from app.services.trade_notifier import init_trade_notifier
            from app.utils.config_loader import load_config

            # 加载配置并初始化通知服务
            config = load_config()
            trade_notifier = init_trade_notifier(config)

            self.live_engine = BinanceFuturesEngine(self.db_config, trade_notifier=trade_notifier)
            logger.info("✅ V2: 实盘交易引擎自动初始化成功")
        except Exception as e:
            self.live_engine_error = str(e)
            logger.error(f"❌ V2: 实盘交易引擎初始化失败（实盘功能不可用）: {e}")

    def get_local_time(self) -> datetime:
        """获取本地时间（UTC+8）"""
        return datetime.now(self.LOCAL_TZ).replace(tzinfo=None)

    def get_db_connection(self):
        """获取数据库连接"""
        return create_connection(self.db_config)

    # ==================== 技术指标计算（使用公共模块）====================
    # 保留方法签名以保持向后兼容，内部调用公共模块

    def calculate_ema(self, prices: List[float], period: int) -> List[float]:
        """计算EMA - 委托给公共模块"""
        return calculate_ema(prices, period)

    def calculate_ma(self, prices: List[float], period: int) -> List[float]:
        """计算MA - 委托给公共模块"""
        return calculate_ma(prices, period)

    def calculate_rsi(self, prices: List[float], period: int = 14) -> List[float]:
        """计算RSI - 委托给公共模块"""
        return calculate_rsi(prices, period)

    def calculate_macd(self, prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict:
        """计算MACD - 委托给公共模块"""
        return calculate_macd(prices, fast, slow, signal)

    def calculate_kdj(self, klines: List[Dict], period: int = 9) -> Dict:
        """计算KDJ - 委托给公共模块"""
        return calculate_kdj(klines, period)

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

            # 金叉/死叉判断应使用已收盘的K线数据：
            # - klines[-1] 是当前未收盘K线（数据会变化）
            # - klines[-2] 是最近一根已收盘K线
            # - klines[-3] 是倒数第二根已收盘K线
            # 所以：
            # - ema9_values[-2] / ema26_values[-2] 是最近已收盘K线的EMA
            # - ema9_values[-3] / ema26_values[-3] 是倒数第二根已收盘K线的EMA
            prev_ema9 = ema9_values[-3] if len(ema9_values) >= 3 else ema9_values[-2] if len(ema9_values) >= 2 else ema9
            prev_ema26 = ema26_values[-3] if len(ema26_values) >= 3 else ema26_values[-2] if len(ema26_values) >= 2 else ema26
            # 用于金叉/死叉判断的当前EMA（已收盘K线）
            confirmed_ema9 = ema9_values[-2] if len(ema9_values) >= 2 else ema9
            confirmed_ema26 = ema26_values[-2] if len(ema26_values) >= 2 else ema26

            ema_diff = ema9 - ema26
            ema_diff_pct = abs(ema_diff) / ema26 * 100 if ema26 != 0 else 0
            # 已确认的EMA差值（用于信号强度判断）
            confirmed_ema_diff = confirmed_ema9 - confirmed_ema26
            confirmed_ema_diff_pct = abs(confirmed_ema_diff) / confirmed_ema26 * 100 if confirmed_ema26 != 0 else 0

            return {
                'ema9': ema9,
                'ema26': ema26,
                'ema_diff': ema_diff,
                'ema_diff_pct': ema_diff_pct,
                'ma10': ma10,
                'current_price': current_price,
                'prev_ema9': prev_ema9,
                'prev_ema26': prev_ema26,
                # 已收盘K线的EMA（用于金叉/死叉判断）
                'confirmed_ema9': confirmed_ema9,
                'confirmed_ema26': confirmed_ema26,
                'confirmed_ema_diff_pct': confirmed_ema_diff_pct,
                'klines': klines,
                'ema9_values': ema9_values,
                'ema26_values': ema26_values
            }

        finally:
            cursor.close()
            conn.close()

    def get_ema_data_5m(self, symbol: str, limit: int = 50) -> Optional[Dict]:
        """
        获取5M周期的EMA数据（用于智能止损）

        Returns:
            {
                'ema9': float,
                'ema26': float,
                'prev_ema9': float,  # 上一根K线的EMA9
                'prev_ema26': float,  # 上一根K线的EMA26
                'is_golden_cross': bool,  # 是否金叉
                'is_death_cross': bool,  # 是否死叉
                'ema_diff_pct': float,  # EMA差距百分比
                'current_price': float
            }
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT timestamp, close_price
                FROM kline_data
                WHERE symbol = %s AND timeframe = '5m' AND exchange = 'binance_futures'
                ORDER BY timestamp DESC
                LIMIT %s
            """, (symbol, limit))

            klines = list(reversed(cursor.fetchall()))

            if len(klines) < 30:
                logger.debug(f"[5M EMA] {symbol} K线数据不足: {len(klines)} < 30")
                return None

            close_prices = [float(k['close_price']) for k in klines]

            # 计算EMA9, EMA26
            ema9_values = self.calculate_ema(close_prices, 9)
            ema26_values = self.calculate_ema(close_prices, 26)

            if not ema9_values or not ema26_values:
                return None

            # 当前EMA（最新已收盘K线）
            ema9 = ema9_values[-2] if len(ema9_values) >= 2 else ema9_values[-1]
            ema26 = ema26_values[-2] if len(ema26_values) >= 2 else ema26_values[-1]

            # 上一根K线的EMA
            prev_ema9 = ema9_values[-3] if len(ema9_values) >= 3 else ema9_values[-2] if len(ema9_values) >= 2 else ema9
            prev_ema26 = ema26_values[-3] if len(ema26_values) >= 3 else ema26_values[-2] if len(ema26_values) >= 2 else ema26

            current_price = close_prices[-1]

            # 金叉：之前 EMA9 <= EMA26，现在 EMA9 > EMA26
            is_golden_cross = prev_ema9 <= prev_ema26 and ema9 > ema26

            # 死叉：之前 EMA9 >= EMA26，现在 EMA9 < EMA26
            is_death_cross = prev_ema9 >= prev_ema26 and ema9 < ema26

            # EMA差距百分比（用于强度判断）
            ema_diff_pct = abs(ema9 - ema26) / ema26 * 100 if ema26 != 0 else 0

            return {
                'ema9': ema9,
                'ema26': ema26,
                'prev_ema9': prev_ema9,
                'prev_ema26': prev_ema26,
                'is_golden_cross': is_golden_cross,
                'is_death_cross': is_death_cross,
                'ema_diff_pct': ema_diff_pct,
                'current_price': current_price
            }

        except Exception as e:
            logger.error(f"[5M EMA] {symbol} 获取数据失败: {e}")
            return None

        finally:
            cursor.close()
            conn.close()

    def check_5m_signal_stop_loss(self, position: Dict, current_pnl_pct: float,
                                   strategy: Dict) -> Tuple[bool, str]:
        """
        5M信号智能止损检测

        逻辑：
        - 当持仓处于亏损状态（current_pnl_pct < 0）
        - 且5M周期趋势与持仓方向相反
        - 且趋势强度足够（EMA差距 > 阈值）
        - 则触发智能止损

        两种模式：
        1. crossOnly=True: 只在交叉发生时触发（更保守）
        2. crossOnly=False: 趋势反向+强度足够就触发（默认，更敏感）

        Args:
            position: 持仓信息
            current_pnl_pct: 当前盈亏百分比
            strategy: 策略配置

        Returns:
            (是否需要止损, 原因)
        """
        symbol = position.get('symbol', '')
        position_side = position.get('position_side', 'LONG')

        # 获取策略配置
        smart_stop_loss = strategy.get('smartStopLoss', {})
        signal_stop_config = smart_stop_loss.get('signalStopLoss', {})

        # 是否启用5M信号止损（默认启用）
        enabled = signal_stop_config.get('enabled', True)
        if not enabled:
            return False, ""

        # 检查是否处于亏损状态（只要亏损就检查）
        if current_pnl_pct >= 0:
            # 盈利或持平，不检查5M信号止损
            return False, ""

        # 获取5M EMA数据
        ema_5m = self.get_ema_data_5m(symbol)
        if not ema_5m:
            return False, ""

        ema9 = ema_5m['ema9']
        ema26 = ema_5m['ema26']

        # 强度阈值：EMA差距百分比需要达到0.07%才触发止损
        min_ema_diff_pct = 0.07

        # 做多持仓亏损 + 5M EMA处于死叉状态（EMA9 < EMA26）+ 强度足够 → 立即止损
        if position_side == 'LONG' and ema9 < ema26:
            ema_diff_pct = (ema26 - ema9) / ema26 * 100
            if ema_diff_pct >= min_ema_diff_pct:
                reason = f"5M EMA死叉状态止损(亏损{abs(current_pnl_pct):.2f}%, EMA9={ema9:.6f} < EMA26={ema26:.6f}, 差{ema_diff_pct:.2f}%)"
                logger.info(f"🔴 [智能止损] {symbol} {reason}")
                return True, reason

        # 做空持仓亏损 + 5M EMA处于金叉状态（EMA9 > EMA26）+ 强度足够 → 立即止损
        if position_side == 'SHORT' and ema9 > ema26:
            ema_diff_pct = (ema9 - ema26) / ema26 * 100
            if ema_diff_pct >= min_ema_diff_pct:
                reason = f"5M EMA金叉状态止损(亏损{abs(current_pnl_pct):.2f}%, EMA9={ema9:.6f} > EMA26={ema26:.6f}, 差{ema_diff_pct:.2f}%)"
                logger.info(f"🟢 [智能止损] {symbol} {reason}")
                return True, reason

        return False, ""

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
        检测金叉/死叉信号（使用已收盘K线判断，避免误判）

        Returns:
            (信号方向 'long'/'short'/None, 信号描述)
        """
        # 使用已收盘K线的EMA判断金叉/死叉
        ema9 = ema_data.get('confirmed_ema9', ema_data['ema9'])
        ema26 = ema_data.get('confirmed_ema26', ema_data['ema26'])
        prev_ema9 = ema_data['prev_ema9']
        prev_ema26 = ema_data['prev_ema26']
        # 使用已收盘K线的EMA差值
        ema_diff_pct = ema_data.get('confirmed_ema_diff_pct', ema_data['ema_diff_pct'])

        # 金叉：前一根EMA9 <= EMA26，当前EMA9 > EMA26（已收盘确认）
        is_golden_cross = prev_ema9 <= prev_ema26 and ema9 > ema26

        # 死叉：前一根EMA9 >= EMA26，当前EMA9 < EMA26（已收盘确认）
        is_death_cross = prev_ema9 >= prev_ema26 and ema9 < ema26

        # 金叉/死叉需要检查信号强度
        if is_golden_cross:
            if ema_diff_pct < self.MIN_SIGNAL_STRENGTH:
                return None, f"金叉信号强度不足({ema_diff_pct:.3f}% < {self.MIN_SIGNAL_STRENGTH}%)"
            return 'long', f"金叉信号(已收盘确认,强度{ema_diff_pct:.3f}%)"

        if is_death_cross:
            if ema_diff_pct < self.MIN_SIGNAL_STRENGTH:
                return None, f"死叉信号强度不足({ema_diff_pct:.3f}% < {self.MIN_SIGNAL_STRENGTH}%)"
            return 'short', f"死叉信号(已收盘确认,强度{ema_diff_pct:.3f}%)"

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

    # ==================== 限价单信号检测 ====================

    def check_limit_entry_signal(self, symbol: str, ema_data: Dict, strategy: Dict,
                                  strategy_id: int) -> Tuple[Optional[str], str]:
        """
        检测限价单开仓信号
        条件：EMA趋势强度 > 0.25% 且方向一致 + 无PENDING限价单 + 不在冷却期

        Args:
            symbol: 交易对
            ema_data: EMA数据
            strategy: 策略配置
            strategy_id: 策略ID

        Returns:
            (信号方向 'long'/'short'/None, 信号描述)
        """
        # 检查是否启用限价单开关
        enable_limit_order = strategy.get('enableLimitOrder', True)  # 默认启用（兼容旧配置）
        if not enable_limit_order:
            return None, "限价单已禁用"

        # 检查是否配置了限价
        long_price_type = strategy.get('longPrice', 'market')
        short_price_type = strategy.get('shortPrice', 'market')

        if long_price_type == 'market' and short_price_type == 'market':
            return None, "限价单未配置"

        # 获取EMA数据
        ema9 = ema_data['ema9']
        ema26 = ema_data['ema26']
        ema_diff = ema_data['ema_diff']
        ema_diff_pct = ema_data['ema_diff_pct']
        current_price = ema_data['current_price']
        ma10 = ema_data['ma10']

        # 限价单要求更强的趋势强度（0.25%）
        LIMIT_ORDER_MIN_STRENGTH = 0.25

        if ema_diff_pct < LIMIT_ORDER_MIN_STRENGTH:
            return None, f"限价单信号强度不足({ema_diff_pct:.3f}% < {LIMIT_ORDER_MIN_STRENGTH}%)"

        # 判断方向
        if ema_diff > 0:  # EMA9 > EMA26, 上升趋势
            direction = 'long'
            price_type = long_price_type
        else:  # EMA9 < EMA26, 下降趋势
            direction = 'short'
            price_type = short_price_type

        # 如果该方向没有配置限价单，跳过
        if price_type == 'market':
            return None, f"{direction}方向未配置限价单"

        # 检查EMA+MA方向一致性
        if direction == 'long':
            if current_price <= ma10:
                return None, f"限价单做多: 价格{current_price:.4f} <= MA10{ma10:.4f}, 趋势不一致"
        else:
            if current_price >= ma10:
                return None, f"限价单做空: 价格{current_price:.4f} >= MA10{ma10:.4f}, 趋势不一致"

        # 检查是否已有open持仓（防止重复开仓）
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            position_side = 'LONG' if direction == 'long' else 'SHORT'

            cursor.execute("""
                SELECT id FROM futures_positions
                WHERE symbol = %s AND strategy_id = %s
                AND position_side = %s AND status = 'open'
                LIMIT 1
            """, (symbol, strategy_id, position_side))

            existing_pos = cursor.fetchone()
            cursor.close()
            conn.close()

            if existing_pos:
                return None, f"已有{position_side}持仓(ID:{existing_pos['id']}), 不再创建限价单"

        except Exception as e:
            logger.warning(f"{symbol} 检查已有持仓失败: {e}")

        # 检查是否已有PENDING限价单
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            position_side = 'LONG' if direction == 'long' else 'SHORT'
            order_side = f'OPEN_{position_side}'

            cursor.execute("""
                SELECT id, created_at FROM futures_orders
                WHERE symbol = %s AND strategy_id = %s
                AND side = %s AND status = 'PENDING'
                ORDER BY created_at DESC LIMIT 1
            """, (symbol, strategy_id, order_side))

            pending_order = cursor.fetchone()
            if pending_order:
                cursor.close()
                conn.close()
                return None, f"已有PENDING限价单(ID:{pending_order['id']})"

            # 检查限价单冷却期（30分钟）
            LIMIT_ORDER_COOLDOWN_MINUTES = 30
            cooldown_start = self.get_local_time() - timedelta(minutes=LIMIT_ORDER_COOLDOWN_MINUTES)

            # 检查是否有最近超时/取消的限价单
            cursor.execute("""
                SELECT id, created_at, status FROM futures_orders
                WHERE symbol = %s AND strategy_id = %s
                AND side = %s AND status IN ('CANCELLED', 'EXPIRED')
                AND updated_at >= %s
                ORDER BY updated_at DESC LIMIT 1
            """, (symbol, strategy_id, order_side, cooldown_start))

            cancelled_order = cursor.fetchone()
            cursor.close()
            conn.close()

            if cancelled_order:
                return None, f"限价单冷却中(最近有超时/取消订单ID:{cancelled_order['id']})"

        except Exception as e:
            logger.warning(f"{symbol} 检查限价单状态失败: {e}")
            return None, f"检查限价单状态失败: {e}"

        return direction, f"限价单信号({direction}, 强度{ema_diff_pct:.3f}%)"

    async def execute_limit_order(self, symbol: str, direction: str, strategy: Dict,
                                   account_id: int, ema_data: Dict) -> Dict:
        """
        执行限价单开仓（不需要自检，直接挂单）

        Args:
            symbol: 交易对
            direction: 'long' 或 'short'
            strategy: 策略配置
            account_id: 账户ID
            ema_data: EMA数据

        Returns:
            执行结果
        """
        try:
            current_price = ema_data['current_price']
            leverage = strategy.get('leverage', 10)
            sync_live = strategy.get('syncLive', False)

            # 获取限价配置
            if direction == 'long':
                price_type = strategy.get('longPrice', 'market')
            else:
                price_type = strategy.get('shortPrice', 'market')

            # 计算限价
            limit_price = self._calculate_limit_price(current_price, price_type, direction)
            if limit_price is None:
                return {'success': False, 'error': '无法计算限价'}

            # 计算开仓保证金（从配置读取）
            margin = self.calculate_margin(is_live=False)
            notional = margin * leverage
            quantity = notional / limit_price

            # 止损止盈
            stop_loss_pct = strategy.get('stopLossPercent') or strategy.get('stopLoss') or self.HARD_STOP_LOSS
            take_profit_pct = strategy.get('takeProfitPercent') or strategy.get('takeProfit') or self.MAX_TAKE_PROFIT

            # 执行模拟挂单
            if self.futures_engine:
                position_side = direction.upper()

                result = self.futures_engine.open_position(
                    account_id=account_id,
                    symbol=symbol,
                    position_side=position_side,
                    quantity=Decimal(str(quantity)),
                    leverage=leverage,
                    limit_price=Decimal(str(limit_price)),  # 限价单
                    stop_loss_pct=Decimal(str(stop_loss_pct)),
                    take_profit_pct=Decimal(str(take_profit_pct)),
                    source='strategy_limit',
                    strategy_id=strategy.get('id')
                )

                if result.get('success'):
                    position_id = result.get('position_id')
                    order_id = result.get('order_id')

                    # 检查是否是 PENDING 状态（未成交）还是立即成交
                    is_pending = result.get('status') == 'PENDING'

                    if is_pending:
                        # PENDING 状态：限价单已挂出，等待成交
                        timeout_minutes = strategy.get('limitOrderTimeoutMinutes', 30)
                        logger.info(f"📋 {symbol} 限价单已挂出: {direction} {quantity:.8f} @ {limit_price:.4f} "
                                   f"(市价:{current_price:.4f}, 偏离:{((limit_price-current_price)/current_price*100):+.2f}%), "
                                   f"超时:{timeout_minutes}分钟")
                        # 注意：PENDING 限价单创建时不同步实盘，等模拟盘成交后再同步
                        # 实盘同步在 futures_limit_order_executor.py 中处理
                    else:
                        # 立即成交：限价单条件已满足，直接开仓
                        entry_price = result.get('entry_price', limit_price)
                        logger.info(f"✅ {symbol} 限价单立即成交: {direction} {quantity:.8f} @ {entry_price:.4f} "
                                   f"(限价:{limit_price:.4f})")

                        # 如果策略启用实盘同步，需要同步到实盘
                        if sync_live and self.live_engine and position_id:
                            try:
                                await self._sync_limit_order_to_live(
                                    symbol=symbol,
                                    direction=direction,
                                    strategy=strategy,
                                    entry_price=entry_price,
                                    quantity=quantity,
                                    leverage=leverage,
                                    stop_loss_pct=stop_loss_pct,
                                    take_profit_pct=take_profit_pct,
                                    paper_position_id=position_id
                                )
                            except Exception as live_ex:
                                logger.error(f"[同步实盘] ❌ {symbol} {direction} 限价单立即成交同步失败: {live_ex}")

                    return {
                        'success': True,
                        'position_id': position_id,
                        'order_id': order_id,
                        'direction': direction,
                        'quantity': quantity,
                        'limit_price': limit_price,
                        'signal_type': 'limit_order',
                        'is_pending': is_pending
                    }
                else:
                    return {'success': False, 'error': result.get('error', '挂单失败')}

            return {'success': False, 'error': '交易引擎未初始化'}

        except Exception as e:
            logger.error(f"限价单执行失败: {e}")
            return {'success': False, 'error': str(e)}

    async def _sync_limit_order_to_live(self, symbol: str, direction: str, strategy: Dict,
                                         entry_price: float, quantity: float, leverage: int,
                                         stop_loss_pct: float, take_profit_pct: float,
                                         paper_position_id: int = None) -> int:
        """
        同步限价单立即成交到实盘

        当限价单条件已满足并立即成交时，同步开仓到实盘

        Args:
            symbol: 交易对
            direction: 'long' 或 'short'
            strategy: 策略配置
            entry_price: 入场价格
            quantity: 模拟盘开仓数量（不使用，实盘数量单独计算）
            leverage: 杠杆倍数
            stop_loss_pct: 止损百分比
            take_profit_pct: 止盈百分比
            paper_position_id: 模拟盘持仓ID（用于关联）

        Returns:
            实盘持仓ID，失败返回None
        """
        try:
            if not self.live_engine:
                return None

            # 从配置读取实盘保证金（支持固定金额或百分比模式）
            live_balance = None
            if self.live_margin_mode == 'percent':
                try:
                    balance_info = self.live_engine.get_account_balance()
                    live_balance = float(balance_info.get('available', 0)) if balance_info else None
                except Exception as e:
                    logger.warning(f"获取实盘余额失败: {e}")

            live_margin = self.calculate_margin(is_live=True, account_balance=live_balance)

            # 使用入场价格计算实盘开仓数量: 数量 = 保证金 * 杠杆 / 价格
            live_quantity = (live_margin * leverage) / float(entry_price)

            logger.info(f"[同步实盘-限价单立即成交] {symbol} 保证金={live_margin}U, 杠杆={leverage}x, "
                       f"入场价={entry_price}, 数量={live_quantity:.4f}")

            # 调用实盘引擎开仓（市价执行），传入模拟盘持仓ID用于关联
            position_side = 'LONG' if direction == 'long' else 'SHORT'
            result = self.live_engine.open_position(
                account_id=2,  # 实盘账户ID
                symbol=symbol,
                position_side=position_side,
                quantity=Decimal(str(live_quantity)),
                leverage=leverage,
                stop_loss_pct=Decimal(str(stop_loss_pct)),
                take_profit_pct=Decimal(str(take_profit_pct)),
                source='limit_order_sync',
                paper_position_id=paper_position_id
            )

            if result.get('success'):
                live_position_id = result.get('position_id')
                logger.info(f"[同步实盘-限价单立即成交] ✅ {symbol} {direction} 成功, 实盘持仓ID: {live_position_id}")

                # 更新模拟盘持仓记录，关联实盘持仓ID
                if paper_position_id and live_position_id:
                    try:
                        conn = self.get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute("""
                            UPDATE futures_positions
                            SET live_position_id = %s
                            WHERE id = %s
                        """, (live_position_id, paper_position_id))
                        conn.commit()
                        logger.debug(f"[同步实盘-限价单立即成交] 已更新模拟盘持仓 {paper_position_id} 关联实盘 {live_position_id}")
                    except Exception as db_ex:
                        logger.warning(f"[同步实盘-限价单立即成交] 更新关联ID失败: {db_ex}")

                return live_position_id
            else:
                logger.warning(f"[同步实盘-限价单立即成交] ⚠️ {symbol} {direction} 失败: {result.get('error')}")
                return None

        except Exception as e:
            logger.error(f"[同步实盘-限价单立即成交] ❌ {symbol} {direction} 异常: {e}")
            return None

    async def check_and_cancel_timeout_orders(self, strategy: Dict, account_id: int = 2):
        """
        检查并取消超时的限价单

        Args:
            strategy: 策略配置
            account_id: 账户ID
        """
        try:
            timeout_minutes = strategy.get('limitOrderTimeoutMinutes', 30)
            timeout_threshold = self.get_local_time() - timedelta(minutes=timeout_minutes)

            conn = self.get_db_connection()
            cursor = conn.cursor()

            # 查找超时的PENDING限价单
            cursor.execute("""
                SELECT fo.id, fo.symbol, fo.side, fo.price, fo.created_at, fp.id as position_id
                FROM futures_orders fo
                LEFT JOIN futures_positions fp ON fo.position_id = fp.id
                WHERE fo.strategy_id = %s AND fo.status = 'PENDING'
                AND fo.order_type = 'LIMIT' AND fo.created_at < %s
            """, (strategy.get('id'), timeout_threshold))

            timeout_orders = cursor.fetchall()

            for order in timeout_orders:
                order_id = order['id']
                symbol = order['symbol']
                position_id = order.get('position_id')

                logger.info(f"⏰ {symbol} 限价单超时，取消订单(ID:{order_id})")

                # 更新订单状态为EXPIRED
                cursor.execute("""
                    UPDATE futures_orders SET status = 'EXPIRED', updated_at = NOW()
                    WHERE id = %s
                """, (order_id,))

                # 如果有关联的持仓，也标记为取消
                if position_id:
                    cursor.execute("""
                        UPDATE futures_positions SET status = 'cancelled', updated_at = NOW()
                        WHERE id = %s AND status = 'pending'
                    """, (position_id,))

                conn.commit()

                # 同步取消实盘限价单
                if strategy.get('syncLive') and self.live_engine:
                    try:
                        success, message = self.live_engine.cancel_pending_order(symbol)
                        if "没有" not in message:
                            # 只有真正取消了订单才输出日志
                            logger.info(f"✅ {symbol} 实盘限价单已取消: {message}")
                        else:
                            # 没有实盘挂单，静默处理
                            logger.debug(f"{symbol} {message}")
                    except Exception as e:
                        logger.warning(f"⚠️ {symbol} 取消实盘限价单失败: {e}")

            cursor.close()
            conn.close()

            if timeout_orders:
                logger.info(f"📋 已取消 {len(timeout_orders)} 个超时限价单")

        except Exception as e:
            logger.error(f"检查超时限价单失败: {e}")

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
        # RSI过滤器默认启用，防止超买追多、超卖追空
        if rsi_config.get('enabled', True) == False:
            return True, "RSI过滤器已禁用"

        # 使用5M K线计算RSI（更实时，5分钟收盘一次，减少滞后）
        ema_data_5m = self.get_ema_data(symbol, '5m', 50)
        if not ema_data_5m or 'klines' not in ema_data_5m:
            return True, "RSI数据不足(5M)，跳过过滤"

        close_prices = [float(k['close_price']) for k in ema_data_5m['klines']]
        rsi_values = self.calculate_rsi(close_prices, 14)

        if not rsi_values:
            return True, "RSI计算失败，跳过过滤"

        current_rsi = rsi_values[-1]

        # 从策略配置读取RSI阈值，默认65/35
        long_max = rsi_config.get('longMax', 65)   # 做多时RSI上限
        short_min = rsi_config.get('shortMin', 35)  # 做空时RSI下限

        if direction == 'long':
            # 做多时RSI不能太高（超买）
            if current_rsi > long_max:
                return False, f"RSI过滤失败: 做多RSI(5M)={current_rsi:.1f} > {long_max}(超买)"
            return True, f"RSI过滤通过: 做多RSI(5M)={current_rsi:.1f} <= {long_max}"
        else:  # short
            # 做空时RSI不能太低（超卖）
            if current_rsi < short_min:
                return False, f"RSI过滤失败: 做空RSI(5M)={current_rsi:.1f} < {short_min}(超卖)"
            return True, f"RSI过滤通过: 做空RSI(5M)={current_rsi:.1f} >= {short_min}"

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

    def check_entry_cooldown(self, symbol: str, direction: str, strategy: Dict, strategy_id: int) -> Tuple[bool, str]:
        """
        检查全局开仓冷却时间

        Args:
            symbol: 交易对
            direction: 'long' 或 'short'
            strategy: 策略配置
            strategy_id: 策略ID

        Returns:
            (是否在冷却中, 原因说明)
        """
        entry_cooldown = strategy.get('entryCooldown', {})
        if not entry_cooldown.get('enabled', True):  # 默认启用
            return False, "开仓冷却未启用"

        cooldown_minutes = entry_cooldown.get('minutes', 30)  # 默认30分钟
        per_direction = entry_cooldown.get('perDirection', True)  # 默认按方向独立冷却

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            current_time = self.get_local_time()
            cooldown_start = current_time - timedelta(minutes=cooldown_minutes)

            # 注意：futures_positions 表使用 position_side 字段（LONG/SHORT）
            position_side = 'LONG' if direction.lower() == 'long' else 'SHORT'
            # futures_orders 表使用 side 字段（OPEN_LONG/OPEN_SHORT）
            order_side = f'OPEN_{position_side}'

            # 1. 先检查是否有 PENDING 状态的限价单（未成交）
            # 注意：限价单写入 futures_orders 表，status='PENDING'
            cursor.execute("""
                SELECT created_at, side FROM futures_orders
                WHERE symbol = %s AND strategy_id = %s
                AND side = %s AND status = 'PENDING'
                ORDER BY created_at DESC LIMIT 1
            """, (symbol, strategy_id, order_side))

            pending_order = cursor.fetchone()
            if pending_order:
                cursor.close()
                conn.close()
                return True, f"已有PENDING限价单等待成交"

            # 2. 查询冷却期内的开仓记录
            if per_direction:
                # 按方向独立冷却：只查同方向的开仓
                cursor.execute("""
                    SELECT created_at, position_side FROM futures_positions
                    WHERE symbol = %s AND strategy_id = %s
                    AND position_side = %s AND created_at >= %s
                    ORDER BY created_at DESC LIMIT 1
                """, (symbol, strategy_id, position_side, cooldown_start))
            else:
                # 全局冷却：查任意方向的开仓
                cursor.execute("""
                    SELECT created_at, position_side FROM futures_positions
                    WHERE symbol = %s AND strategy_id = %s
                    AND created_at >= %s
                    ORDER BY created_at DESC LIMIT 1
                """, (symbol, strategy_id, cooldown_start))

            recent_entry = cursor.fetchone()
            cursor.close()
            conn.close()

            if recent_entry:
                entry_time = recent_entry['created_at']
                last_direction = recent_entry['position_side']
                time_since_entry = (current_time - entry_time).total_seconds() / 60
                remaining_cooldown = cooldown_minutes - time_since_entry

                direction_text = f"同方向({last_direction})" if per_direction else "任意方向"
                return True, f"开仓冷却中: 距离上次{direction_text}开仓仅{time_since_entry:.0f}分钟，还需等待{remaining_cooldown:.0f}分钟"

            return False, "冷却检查通过"

        except Exception as e:
            logger.warning(f"{symbol} 检查开仓冷却失败: {e}")
            return False, f"冷却检查异常: {e}"

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

        # 1. RSI过滤（防止超买追多、超卖追空）
        passed, reason = self.check_rsi_filter(symbol, direction, strategy)
        filter_results.append(f"RSI: {reason}")
        if not passed:
            all_passed = False

        # # 2. MACD过滤
        # passed, reason = self.check_macd_filter(symbol, direction, strategy)
        # filter_results.append(f"MACD: {reason}")
        # if not passed:
        #     all_passed = False

        # # 3. KDJ过滤
        # passed, reason = self.check_kdj_filter(symbol, direction, strategy)
        # filter_results.append(f"KDJ: {reason}")
        # if not passed:
        #     all_passed = False

        # # 4. 价格距离限制
        # passed, reason = self.check_price_distance_limit(symbol, direction, current_price, ema_data, strategy)
        # filter_results.append(f"价格距离: {reason}")
        # if not passed:
        #     all_passed = False

        # # 5. 行情自适应
        # passed, reason = self.check_adaptive_regime(symbol, direction, strategy)
        # filter_results.append(f"行情自适应: {reason}")
        # if not passed:
        #     all_passed = False

        return all_passed, filter_results

    # ==================== 平仓信号检测 ====================

    def check_cross_reversal(self, position: Dict, ema_data: Dict) -> Tuple[bool, str]:
        """
        检测金叉/死叉反转信号（使用已收盘K线判断，避免误判）

        Args:
            position: 持仓信息
            ema_data: 当前EMA数据

        Returns:
            (是否需要平仓, 原因)
        """
        position_side = position.get('position_side', 'LONG')

        # 使用已收盘K线的EMA判断金叉/死叉，避免未收盘K线波动导致误判
        ema9 = ema_data.get('confirmed_ema9', ema_data['ema9'])
        ema26 = ema_data.get('confirmed_ema26', ema_data['ema26'])
        prev_ema9 = ema_data['prev_ema9']
        prev_ema26 = ema_data['prev_ema26']

        # 平仓不检查信号强度，趋势已变应尽快平仓

        if position_side == 'LONG':
            # 持多仓 + 死叉 → 立即平仓
            is_death_cross = prev_ema9 >= prev_ema26 and ema9 < ema26
            if is_death_cross:
                return True, "死叉反转平仓(已收盘确认)"

            # 趋势反转：EMA9 < EMA26（已收盘确认）
            if ema9 < ema26:
                return True, "趋势反转平仓(EMA9 < EMA26)"

        else:  # SHORT
            # 持空仓 + 金叉 → 立即平仓
            is_golden_cross = prev_ema9 <= prev_ema26 and ema9 > ema26
            if is_golden_cross:
                return True, "金叉反转平仓(已收盘确认)"

            # 趋势反转：EMA9 > EMA26（已收盘确认）
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

    def _calculate_limit_price(self, current_price: float, price_type: str, direction: str) -> Optional[float]:
        """
        根据价格类型计算限价

        Args:
            current_price: 当前市价
            price_type: 价格类型 (market, market_minus_0_2, market_plus_0_6, etc.)
            direction: 方向 (long/short)

        Returns:
            限价，如果是市价则返回None
        """
        if price_type == 'market':
            return None

        # 解析价格类型
        # 做多: market_minus_X 表示市价减X%（更低的买入价）
        # 做空: market_plus_X 表示市价加X%（更高的卖出价）
        price_adjustments = {
            'market_minus_0_2': -0.2,
            'market_minus_0_4': -0.4,
            'market_minus_0_6': -0.6,
            'market_minus_0_8': -0.8,
            'market_minus_1': -1.0,
            'market_plus_0_2': 0.2,
            'market_plus_0_4': 0.4,
            'market_plus_0_6': 0.6,
            'market_plus_0_8': 0.8,
            'market_plus_1': 1.0,
        }

        adjustment_pct = price_adjustments.get(price_type)
        if adjustment_pct is None:
            logger.warning(f"未知的价格类型: {price_type}, 使用市价")
            return None

        # 计算限价
        limit_price = current_price * (1 + adjustment_pct / 100)
        return limit_price

    def check_trend_weakening(self, position: Dict, ema_data: Dict, current_price: float = None) -> Tuple[bool, str]:
        """
        检测趋势减弱（开仓后30分钟开始监控，且仅在盈利时触发）

        当EMA差值连续3次减弱时，触发平仓

        Args:
            position: 持仓信息
            ema_data: 当前EMA数据
            current_price: 当前价格（用于判断盈亏）

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

        # 使用已收盘K线的EMA数据，避免未收盘K线波动导致误判
        confirmed_ema_diff_pct = ema_data.get('confirmed_ema_diff_pct', ema_data['ema_diff_pct'])
        position_side = position.get('position_side', 'LONG')

        # 注意：趋势反转的检查已经在 check_cross_reversal 中完成
        # 这里只检查趋势减弱（强度下降），不再重复检查趋势反转
        # check_cross_reversal 使用已收盘K线判断，更准确

        # 检查强度是否减弱到开仓时的50%以下（使用已收盘K线数据）
        if confirmed_ema_diff_pct < entry_ema_diff * 0.5:
            # 需要满足最小盈利要求才触发趋势减弱平仓
            # 避免刚开始盈利就被平仓的情况
            MIN_PROFIT_FOR_TREND_EXIT = 1.0  # 最小盈利1%才触发趋势减弱平仓

            if current_price:
                entry_price = float(position.get('entry_price', 0))
                if entry_price > 0:
                    if position_side == 'LONG':
                        pnl_pct = (current_price - entry_price) / entry_price * 100
                    else:
                        pnl_pct = (entry_price - current_price) / entry_price * 100

                    if pnl_pct < 0:
                        return False, f"趋势减弱但仍亏损({pnl_pct:.2f}%)，继续持有"

                    if pnl_pct < MIN_PROFIT_FOR_TREND_EXIT:
                        return False, f"趋势减弱但盈利不足({pnl_pct:.2f}%<{MIN_PROFIT_FOR_TREND_EXIT}%)，继续持有"

            return True, f"趋势减弱平仓(当前强度{confirmed_ema_diff_pct:.3f}% < 开仓时{entry_ema_diff:.3f}%的50%，已收盘确认)"

        return False, f"趋势强度正常(当前{confirmed_ema_diff_pct:.3f}%, 开仓时{entry_ema_diff:.3f}%)"

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
        symbol = position.get('symbol', '')
        if position_side == 'LONG':
            current_pnl_pct = (current_price - entry_price) / entry_price * 100
        else:
            current_pnl_pct = (entry_price - current_price) / entry_price * 100

        # 每5分钟输出一次调试日志（避免刷屏）
        position_id = position.get('id')
        max_profit_pct = float(position.get('max_profit_pct') or 0)
        if position_id and position_id % 10 == 0:  # 只对部分持仓输出
            logger.debug(f"[智能出场] {symbol} 当前盈亏={current_pnl_pct:.2f}%, 最高盈利={max_profit_pct:.2f}%")

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

        # 获取冷却时间配置
        trailing_cooldown_minutes = strategy.get('trailingCooldownMinutes', 15)
        open_time = position.get('open_time')
        in_cooldown = False
        if open_time:
            from datetime import datetime, timedelta, timezone
            local_tz = timezone(timedelta(hours=8))
            now = datetime.now(local_tz).replace(tzinfo=None)
            if isinstance(open_time, datetime):
                elapsed_minutes = (now - open_time).total_seconds() / 60
                if elapsed_minutes < trailing_cooldown_minutes:
                    in_cooldown = True

        # 0. 移动止损检查（在硬止损之前）
        # 当启用移动止损且盈利达到阈值时，动态调整止损价
        # 最小移动阈值：只有当新止损价变动超过0.1%时才更新，避免频繁微小调整
        # 冷却期内不执行移动止损
        min_move_pct = 0.1
        if trailing_sl_enabled and current_pnl_pct >= trailing_sl_activate and current_stop_loss > 0 and not in_cooldown:
            if position_side == 'LONG':
                # 做多：止损价 = 当前价 - 距离%
                new_stop_loss = current_price * (1 - trailing_sl_distance / 100)
                # 入场价保护：做多时止损价不能超过入场价，否则盈利时会触发"止损"
                if new_stop_loss >= entry_price:
                    logger.debug(f"移动止损跳过: {position.get('symbol')} 做多, 新止损{new_stop_loss:.6f} >= 入场价{entry_price:.6f}")
                else:
                    move_pct = abs(new_stop_loss - current_stop_loss) / current_stop_loss * 100
                    if new_stop_loss > current_stop_loss and move_pct >= min_move_pct:
                        updates['stop_loss_price'] = new_stop_loss
                        logger.info(f"移动止损上移: {position.get('symbol')} 做多, 盈利{current_pnl_pct:.2f}%, 止损从{current_stop_loss:.6f}上移到{new_stop_loss:.6f} (移动{move_pct:.2f}%)")
            else:
                # 做空：止损价 = 当前价 + 距离%
                new_stop_loss = current_price * (1 + trailing_sl_distance / 100)
                # 入场价保护：做空时止损价不能低于入场价，否则盈利时会触发"止损"
                if new_stop_loss <= entry_price:
                    logger.debug(f"移动止损跳过: {position.get('symbol')} 做空, 新止损{new_stop_loss:.6f} <= 入场价{entry_price:.6f}")
                else:
                    move_pct = abs(current_stop_loss - new_stop_loss) / current_stop_loss * 100
                    if new_stop_loss < current_stop_loss and move_pct >= min_move_pct:
                        updates['stop_loss_price'] = new_stop_loss
                        logger.info(f"移动止损下移: {position.get('symbol')} 做空, 盈利{current_pnl_pct:.2f}%, 止损从{current_stop_loss:.6f}下移到{new_stop_loss:.6f} (移动{move_pct:.2f}%)")

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

        # 2.5 5M信号智能止损（亏损时检测5M反向交叉）
        # 注意：冷却期内不检查5M信号止损
        if not in_cooldown:
            close_needed, close_reason = self.check_5m_signal_stop_loss(position, current_pnl_pct, strategy)
            if close_needed:
                return True, close_reason, updates

        # 3. 最大止盈检查
        if current_pnl_pct >= max_take_profit:
            return True, f"最大止盈平仓(盈利{current_pnl_pct:.2f}% >= {max_take_profit}%)", updates

        # 4. 金叉/死叉反转检查
        close_needed, close_reason = self.check_cross_reversal(position, ema_data)
        if close_needed:
            return True, close_reason, updates

        # 5. 趋势减弱检查（传入当前价格用于判断盈亏，亏损时不触发）
        close_needed, close_reason = self.check_trend_weakening(position, ema_data, current_price)
        if close_needed:
            return True, close_reason, updates

        # 6. 移动止盈检查
        max_profit_pct = float(position.get('max_profit_pct') or 0)
        trailing_activated = position.get('trailing_stop_activated') or False

        # 更新最高盈利（只在有明显变化时更新，避免浮点数精度导致重复更新）
        # 最小变化阈值：0.01%
        if current_pnl_pct > max_profit_pct + 0.01:
            updates['max_profit_pct'] = current_pnl_pct
            logger.info(f"[盈利更新] {symbol} 最高盈利更新: {max_profit_pct:.2f}% -> {current_pnl_pct:.2f}%")
            max_profit_pct = current_pnl_pct

            # 更新最高价格
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
            symbol = position.get('symbol', '')
            if position_side == 'LONG':
                new_trailing_price = current_price * (1 - trailing_callback / 100)
                current_trailing_price = float(position.get('trailing_stop_price') or 0)
                if new_trailing_price > current_trailing_price:
                    updates['trailing_stop_price'] = new_trailing_price
                    logger.info(f"[移动止盈] {symbol} 做多 止损价上移: {current_trailing_price:.6f} -> {new_trailing_price:.6f} (当前价={current_price:.4f})")
            else:
                new_trailing_price = current_price * (1 + trailing_callback / 100)
                current_trailing_price = float(position.get('trailing_stop_price') or float('inf'))
                if new_trailing_price < current_trailing_price:
                    updates['trailing_stop_price'] = new_trailing_price
                    logger.info(f"[移动止盈] {symbol} 做空 止损价下移: {current_trailing_price:.6f} -> {new_trailing_price:.6f} (当前价={current_price:.4f})")

        return False, "", updates

    # ==================== 开仓执行 ====================

    async def execute_open_position(self, symbol: str, direction: str, signal_type: str,
                                     strategy: Dict, account_id: int = 2,
                                     signal_reason: str = None, force_market: bool = False,
                                     is_dual_call: bool = False) -> Dict:
        """
        执行开仓（或创建待开仓记录）

        Args:
            symbol: 交易对
            direction: 'long' 或 'short'
            signal_type: 信号类型
            strategy: 策略配置
            account_id: 账户ID
            signal_reason: 开仓原因详情
            force_market: 强制市价开仓（跳过自检）
            is_dual_call: 是否是双向模式的内部调用（避免递归）

        Returns:
            执行结果
        """
        try:
            # 获取当前价格和EMA数据
            ema_data = self.get_ema_data(symbol, '15m', 50)
            if not ema_data:
                return {'success': False, 'error': '获取价格数据失败'}

            current_price = ema_data['current_price']

            # ========== 双向对比模式：强制开启 ==========
            dual_mode = True
            if dual_mode and not is_dual_call:
                logger.info(f"🔀 {symbol} 双向对比模式启动，同时开正向({direction})和反向仓位")

                dual_results = []

                # 1. 开正向仓（原信号方向）
                正向_signal_type = f"{signal_type}_正向"
                正向_reason = f"[正向]{signal_reason}" if signal_reason else "[正向]双向对比"
                result_正向 = await self._do_open_position(
                    symbol=symbol,
                    direction=direction,
                    signal_type=正向_signal_type,
                    strategy=strategy,
                    account_id=account_id,
                    signal_reason=正向_reason,
                    current_price=current_price,
                    ema_data=ema_data,
                    is_dual_mode=True
                )
                dual_results.append({'type': '正向', 'direction': direction, 'result': result_正向})
                logger.info(f"🔀 {symbol} 正向({direction})开仓结果: {result_正向.get('success')}")

                # 2. 开反向仓（相反方向）
                reverse_direction = 'short' if direction == 'long' else 'long'
                反向_signal_type = f"{signal_type}_反向"
                反向_reason = f"[反向]{signal_reason}" if signal_reason else "[反向]双向对比"
                # 反向仓位使用更宽松的止盈止损（避免和正向重叠导致秒平）
                reverse_strategy = strategy.copy()
                reverse_strategy['stopLoss'] = 5  # 反向止损5%
                reverse_strategy['takeProfit'] = 10  # 反向止盈10%
                result_反向 = await self._do_open_position(
                    symbol=symbol,
                    direction=reverse_direction,
                    signal_type=反向_signal_type,
                    strategy=reverse_strategy,
                    account_id=account_id,
                    signal_reason=反向_reason,
                    current_price=current_price,
                    ema_data=ema_data,
                    is_dual_mode=True
                )
                dual_results.append({'type': '反向', 'direction': reverse_direction, 'result': result_反向})
                logger.info(f"🔀 {symbol} 反向({reverse_direction})开仓结果: {result_反向.get('success')}")

                # 返回双向结果
                success_count = sum(1 for r in dual_results if r['result'].get('success'))
                return {
                    'success': success_count > 0,
                    'dual_mode': True,
                    'dual_results': dual_results,
                    'message': f"双向开仓完成: {success_count}/2 成功"
                }

            # ========== 强制市价开仓（反转信号）或金叉/死叉信号直接市价开仓 ==========
            is_cross_signal = signal_type in ('golden_cross', 'death_cross', 'ema_crossover', 'reversal_cross')
            cross_signal_force_market = strategy.get('crossSignalForceMarket', True)

            if force_market or (is_cross_signal and cross_signal_force_market):
                # 反转信号或金叉/死叉信号直接市价开仓，不走自检
                log_msg = "反转信号" if force_market else "金叉/死叉信号"
                logger.info(f"⚡ {symbol} {direction} {log_msg}，直接市价开仓")
                return await self._do_open_position(
                    symbol=symbol,
                    direction=direction,
                    signal_type=signal_type,
                    strategy=strategy,
                    account_id=account_id,
                    signal_reason=signal_reason,
                    current_price=current_price,
                    ema_data=ema_data
                )

            # 其他信号（sustained_trend等）走自检流程
            from app.services.position_validator import get_position_validator

            position_validator = get_position_validator()
            if position_validator:
                # 创建待开仓记录，由自检服务验证后开仓
                result = position_validator.create_pending_position(
                    symbol=symbol,
                    direction=direction,
                    signal_type=signal_type,
                    signal_price=current_price,
                    ema_data=ema_data,
                    strategy=strategy,
                    account_id=account_id,
                    signal_reason=signal_reason
                )

                if result.get('success'):
                    logger.info(f"📋 {symbol} {direction} 信号已进入自检队列，pending_id={result.get('pending_id')}")
                    return {'success': True, 'pending': True, 'pending_id': result.get('pending_id')}
                else:
                    # 可能是已有相同的待开仓信号
                    return {'success': False, 'error': result.get('error', '创建待开仓记录失败')}
            else:
                logger.warning(f"⚠️ 自检服务未初始化，直接市价开仓")
                # 自检服务未初始化，回退到直接开仓
                return await self._do_open_position(
                    symbol=symbol,
                    direction=direction,
                    signal_type=signal_type,
                    strategy=strategy,
                    account_id=account_id,
                    signal_reason=signal_reason,
                    current_price=current_price,
                    ema_data=ema_data
                )

        except Exception as e:
            logger.error(f"开仓执行失败: {e}")
            return {'success': False, 'error': str(e)}

    async def _do_open_position(self, symbol: str, direction: str, signal_type: str,
                                 strategy: Dict, account_id: int, signal_reason: str,
                                 current_price: float, ema_data: Dict,
                                 is_dual_mode: bool = False) -> Dict:
        """
        执行实际的开仓操作（被 execute_open_position 和待开仓自检调用）

        Args:
            symbol: 交易对
            direction: 'long' 或 'short'
            signal_type: 信号类型
            strategy: 策略配置
            account_id: 账户ID
            signal_reason: 开仓原因
            current_price: 当前价格
            ema_data: EMA数据
            is_dual_mode: 是否是双向对比模式（保证金减半）

        Returns:
            执行结果
        """
        try:
            leverage = strategy.get('leverage', 10)
            position_size_pct = strategy.get('positionSizePct', 1)  # 账户资金的1%
            sync_live = strategy.get('syncLive', False)

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
                # 从配置读取保证金（支持固定金额或百分比模式）
                margin = self.calculate_margin(is_live=False, account_balance=balance)

                # 双向对比模式：保证金减半（正向+反向各用一半）
                if is_dual_mode:
                    margin = margin / 2
                    logger.info(f"🔀 {symbol} 双向模式保证金减半: {margin:.2f}")

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

                # 从策略配置读取止损止盈，没有则用默认值
                stop_loss_pct = strategy.get('stopLossPercent') or strategy.get('stopLoss') or self.HARD_STOP_LOSS
                take_profit_pct = strategy.get('takeProfitPercent') or strategy.get('takeProfit') or self.MAX_TAKE_PROFIT

                # ========== 市价单开仓 ==========
                # 信号触发 → 自检 → 通过后市价开单

                result = self.futures_engine.open_position(
                    account_id=account_id,
                    symbol=symbol,
                    position_side=position_side,
                    quantity=Decimal(str(quantity)),
                    leverage=leverage,
                    limit_price=None,  # 统一使用市价单
                    stop_loss_pct=Decimal(str(stop_loss_pct)),
                    take_profit_pct=Decimal(str(take_profit_pct)),
                    source='strategy',
                    strategy_id=strategy.get('id')
                )

                if result.get('success'):
                    position_id = result.get('position_id')
                    order_type = result.get('order_type', 'MARKET')
                    order_status = result.get('status', 'FILLED')

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

                    # 同步实盘（市价单立即成交，直接同步）
                    live_position_id = None
                    if sync_live and self.live_engine:
                        live_position_id = await self._sync_live_open(symbol, direction, quantity, leverage, strategy, position_id)
                    elif sync_live and not self.live_engine:
                        logger.warning(f"⚠️ [开仓] {symbol} sync_live=True 但 live_engine 未初始化，无法同步实盘！")

                    # 保存实盘持仓ID到模拟盘持仓
                    if live_position_id:
                        try:
                            conn = self.get_db_connection()
                            cursor = conn.cursor()
                            cursor.execute(
                                "UPDATE futures_positions SET live_position_id = %s WHERE id = %s",
                                (live_position_id, position_id)
                            )
                            conn.commit()
                            cursor.close()
                            conn.close()
                        except Exception as e:
                            logger.warning(f"保存实盘持仓ID失败: {e}")

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
            logger.error(f"执行开仓失败: {e}")
            return {'success': False, 'error': str(e)}

    async def _sync_live_open(self, symbol: str, direction: str, quantity: float,
                              leverage: int, strategy: Dict, paper_position_id: int = None) -> int:
        """
        同步实盘开仓

        Returns:
            实盘持仓ID，失败返回None
        """
        try:
            if not self.live_engine:
                return None

            # 从配置读取实盘保证金（支持固定金额或百分比模式）
            # 百分比模式需要获取实盘账户余额
            live_balance = None
            if self.live_margin_mode == 'percent':
                try:
                    balance_info = self.live_engine.get_account_balance()
                    live_balance = float(balance_info.get('available', 0)) if balance_info else None
                except Exception as e:
                    logger.warning(f"获取实盘余额失败: {e}")

            live_margin = self.calculate_margin(is_live=True, account_balance=live_balance)

            # 获取当前价格
            current_price = self.live_engine.get_current_price(symbol)
            if not current_price or current_price <= 0:
                logger.warning(f"⚠️ {symbol} 无法获取当前价格，跳过实盘同步")
                return None

            # 根据保证金计算开仓数量: 数量 = 保证金 * 杠杆 / 价格
            live_quantity = (live_margin * leverage) / float(current_price)

            logger.info(f"[实盘同步] {symbol} 保证金={live_margin}U, 杠杆={leverage}x, 价格={current_price}, 数量={live_quantity:.4f}")

            # 从策略配置读取止损止盈，没有则用默认值
            stop_loss_pct = strategy.get('stopLossPercent') or strategy.get('stopLoss') or self.HARD_STOP_LOSS
            take_profit_pct = strategy.get('takeProfitPercent') or strategy.get('takeProfit') or self.MAX_TAKE_PROFIT

            # 调用实盘引擎开仓，传入模拟盘持仓ID用于关联
            position_side = 'LONG' if direction == 'long' else 'SHORT'
            result = self.live_engine.open_position(
                account_id=2,  # 实盘账户ID
                symbol=symbol,
                position_side=position_side,
                quantity=Decimal(str(live_quantity)),
                leverage=leverage,
                stop_loss_pct=Decimal(str(stop_loss_pct)),
                take_profit_pct=Decimal(str(take_profit_pct)),
                source='strategy_sync',
                paper_position_id=paper_position_id
            )

            if result.get('success'):
                live_position_id = result.get('position_id')
                logger.info(f"✅ {symbol} 实盘同步开仓成功, 实盘持仓ID: {live_position_id}")
                return live_position_id
            else:
                logger.warning(f"⚠️ {symbol} 实盘同步开仓失败: {result.get('error')}")
                return None

        except Exception as e:
            logger.error(f"实盘同步开仓异常: {e}")
            return None

    # 注意：移动止损和移动止盈不同步到实盘
    # 实盘的止损止盈在开仓时一次性设置，由币安交易所自动执行
    # 模拟盘的移动止损/止盈只在模拟盘内部维护

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

                    # 注意: 实盘同步平仓已在 futures_engine.close_position 内部处理
                    # 无需再次调用 _sync_live_close，避免重复平仓

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
        """同步实盘平仓（只平对应的实盘持仓，而不是所有同方向持仓）"""
        try:
            if not self.live_engine:
                return

            symbol = position.get('symbol')
            position_side = position.get('position_side')
            live_position_id = position.get('live_position_id')

            if live_position_id:
                # 根据关联的实盘持仓ID平仓（精确平仓）
                result = self.live_engine.close_position(
                    position_id=live_position_id
                )
                if result.get('success'):
                    logger.info(f"✅ {symbol} 实盘同步平仓成功 (持仓ID: {live_position_id})")
                else:
                    logger.warning(f"⚠️ {symbol} 实盘同步平仓失败: {result.get('error')}")
            else:
                # 没有关联ID时，回退到按交易对平仓（兼容旧数据）
                logger.warning(f"⚠️ {symbol} 无关联实盘持仓ID，使用按交易对平仓")
                result = self.live_engine.close_position_by_symbol(
                    symbol=symbol,
                    position_side=position_side
                )
                if result.get('success'):
                    logger.info(f"✅ {symbol} 实盘同步平仓成功 (按交易对)")
                else:
                    logger.warning(f"⚠️ {symbol} 实盘同步平仓失败: {result.get('error')}")

        except Exception as e:
            logger.error(f"实盘同步平仓异常: {e}")

    # ==================== 主执行逻辑 ====================

    async def quick_update_positions(self, strategy: Dict, account_id: int = 2):
        """
        快速更新所有持仓的盈亏（不需要完整EMA计算）
        用于高频监控移动止盈/止损
        优化：批量获取价格，减少数据库查询次数
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            # 获取所有开放持仓（包含 open_time 用于冷却时间检查）
            cursor.execute("""
                SELECT id, symbol, position_side, entry_price, max_profit_pct,
                       trailing_stop_activated, trailing_stop_price, stop_loss_price, open_time
                FROM futures_positions
                WHERE account_id = %s AND status = 'open'
            """, (account_id,))
            positions = cursor.fetchall()

            if not positions:
                return

            # 收集所有需要查询价格的符号
            symbols = list(set(p['symbol'] for p in positions))
            if not symbols:
                return

            # 批量获取所有符号的实时价格
            # 优先使用实盘引擎的实时API（毫秒级），回退到price_data表（5秒延迟）
            price_map = {}
            for symbol in symbols:
                try:
                    if self.live_engine:
                        # 使用实盘引擎的实时价格API
                        price = self.live_engine.get_current_price(symbol)
                        if price and price > 0:
                            price_map[symbol] = float(price)
                            continue
                except Exception as e:
                    logger.debug(f"获取 {symbol} 实时价格失败: {e}")

                # 回退：从数据库获取
                cursor.execute("""
                    SELECT price FROM price_data
                    WHERE symbol = %s
                    ORDER BY timestamp DESC LIMIT 1
                """, (symbol,))
                row = cursor.fetchone()
                if row:
                    price_map[symbol] = float(row['price'])

            # 获取移动止盈参数
            raw_activate = strategy.get('trailingActivate')
            raw_callback = strategy.get('trailingCallback')
            trailing_activate = raw_activate if raw_activate is not None else self.TRAILING_ACTIVATE
            trailing_callback = raw_callback if raw_callback is not None else self.TRAILING_CALLBACK

            # 配置已验证正确 (raw=1/0.3)，不再输出刷屏日志

            for position in positions:
                symbol = position['symbol']
                position_id = position['id']
                position_side = position['position_side']
                entry_price = float(position['entry_price'])
                max_profit_pct = float(position.get('max_profit_pct') or 0)
                trailing_activated = position.get('trailing_stop_activated') or False

                # 从价格映射获取当前价格（提前获取，用于恢复激活状态时计算止损价）
                current_price = price_map.get(symbol)
                if not current_price:
                    continue

                # 计算当前盈亏
                if position_side == 'LONG':
                    current_pnl_pct = (current_price - entry_price) / entry_price * 100
                else:
                    current_pnl_pct = (entry_price - current_price) / entry_price * 100

                updates = {}

                # 获取策略止损参数
                stop_loss_pct = strategy.get('stopLossPercent') or strategy.get('stopLoss') or self.HARD_STOP_LOSS

                # 快速检查硬止损（不受冷却时间限制）
                if current_pnl_pct <= -stop_loss_pct:
                    close_reason = f"硬止损平仓(亏损{abs(current_pnl_pct):.2f}% >= {stop_loss_pct}%)"
                    logger.info(f"🚨 [快速监控] {symbol} {close_reason}")
                    await self.execute_close_position(position, close_reason, strategy)
                    continue  # 已平仓，跳过后续处理

                # 检查冷却时间（冷却期内不检查移动止盈/止损）
                trailing_cooldown_minutes = strategy.get('trailingCooldownMinutes', 15)
                open_time = position.get('open_time')
                in_cooldown = False
                if open_time:
                    now = self.get_local_time()
                    if isinstance(open_time, datetime):
                        elapsed_minutes = (now - open_time).total_seconds() / 60
                        if elapsed_minutes < trailing_cooldown_minutes:
                            in_cooldown = True

                # 更新最高盈利（只在有明显变化时更新，避免浮点数精度导致重复更新）
                # 最小变化阈值：0.01%
                if current_pnl_pct > max_profit_pct + 0.01:
                    updates['max_profit_pct'] = current_pnl_pct
                    updates['max_profit_price'] = current_price
                    if not in_cooldown:
                        logger.info(f"[快速更新] {symbol} 最高盈利: {max_profit_pct:.2f}% -> {current_pnl_pct:.2f}%")
                    max_profit_pct = current_pnl_pct

                # 冷却期内只更新最高盈利，跳过移动止盈检查
                if in_cooldown:
                    if updates:
                        self._update_position(position_id, updates)
                    continue

                # 检查是否激活移动止盈
                if not trailing_activated and max_profit_pct >= trailing_activate:
                    updates['trailing_stop_activated'] = True
                    trailing_activated = True
                    if position_side == 'LONG':
                        trailing_stop_price = current_price * (1 - trailing_callback / 100)
                    else:
                        trailing_stop_price = current_price * (1 + trailing_callback / 100)
                    updates['trailing_stop_price'] = trailing_stop_price
                    logger.info(f"🎯 [快速更新] {symbol} 移动止盈激活! 盈利={max_profit_pct:.2f}%, 止损价={trailing_stop_price:.6f}")
                    # 注意：激活后不要return，继续往下检查是否已经回撤需要平仓

                # 移动止盈已激活，检查是否触发平仓或更新止损价格
                if trailing_activated:
                    # 检查移动止盈回撤是否触发平仓
                    callback_pct = max_profit_pct - current_pnl_pct
                    if callback_pct >= trailing_callback:
                        # 触发移动止盈平仓！
                        close_reason = f"移动止盈平仓(从最高{max_profit_pct:.2f}%回撤{callback_pct:.2f}% >= {trailing_callback}%)"
                        logger.info(f"🚨 [快速监控] {symbol} {close_reason}")

                        # 先更新数据库
                        if updates:
                            self._update_position(position_id, updates)

                        # 立即执行平仓
                        await self.execute_close_position(position, close_reason, strategy)
                        continue  # 已平仓，跳过后续处理

                    # 未触发平仓，更新止损价格
                    current_trailing = float(position.get('trailing_stop_price') or 0)
                    if position_side == 'LONG':
                        new_trailing = current_price * (1 - trailing_callback / 100)
                        if new_trailing > current_trailing:
                            updates['trailing_stop_price'] = new_trailing
                            logger.info(f"[快速更新] {symbol} 做多止损上移: {current_trailing:.6f} -> {new_trailing:.6f}")
                    else:
                        new_trailing = current_price * (1 + trailing_callback / 100)
                        if current_trailing == 0 or new_trailing < current_trailing:
                            updates['trailing_stop_price'] = new_trailing
                            logger.info(f"[快速更新] {symbol} 做空止损下移: {current_trailing:.6f} -> {new_trailing:.6f}")

                # 写入数据库（只在有更新时）
                if updates:
                    self._update_position(position_id, updates)

        finally:
            cursor.close()
            conn.close()

    async def execute_strategy(self, strategy: Dict, account_id: int = 2) -> Dict:
        """
        执行策略

        Args:
            strategy: 策略配置
            account_id: 账户ID

        Returns:
            执行结果
        """
        # 首先检查并取消超时的限价单
        await self.check_and_cancel_timeout_orders(strategy, account_id)

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
        debug_info.append(f"允许方向: {buy_directions}")

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

                # 注意：移动止损和移动止盈不同步到实盘
                # 实盘的止损止盈由币安交易所自动执行

            # 执行平仓
            if close_needed:
                result = await self.execute_close_position(position, close_reason, strategy)
                close_results.append(result)
                debug_info.append(f"平仓: {close_reason}")
                # 标记该仓位已平仓（内存中）
                position['status'] = 'closed'
                # 记录反转平仓信息（用于跳过冷却）
                position['close_reason'] = close_reason
                logger.info(f"📝 {symbol} 平仓完成，设置 close_reason={close_reason}")

        # 3. 如果无持仓或所有仓位都已平仓，检查开仓信号
        # 注意：平仓后 position['status'] 已在上面更新为 'closed'
        open_result = None
        strategy_id = strategy.get('id')
        has_open_position = any(p.get('status') == 'open' for p in positions)

        # 调试日志：输出所有持仓的状态
        if close_results:
            for p in positions:
                logger.info(f"[状态检查] {symbol} id={p.get('id')}, status={p.get('status')}, close_reason={p.get('close_reason')}")

        # 检查是否刚刚发生了金叉/死叉反转平仓（跳过所有检查，立即市价开仓）
        # 注意：只有"金叉反转平仓"和"死叉反转平仓"才是绝佳买入时机，"趋势反转平仓"不算
        reversal_direction = None  # 反转后应开仓的方向
        for p in positions:
            p_status = p.get('status')
            p_reason = p.get('close_reason', '')
            # 只在有平仓时输出日志
            if close_results:
                logger.info(f"[反转检测] {symbol} 持仓id={p.get('id')}, status={p_status}, close_reason={p_reason}")
            if p_status == 'closed':
                if '金叉反转平仓' in p_reason:
                    reversal_direction = 'long'
                    logger.info(f"🔄 {symbol} 检测到金叉反转平仓，准备开多")
                    break
                elif '死叉反转平仓' in p_reason:
                    reversal_direction = 'short'
                    logger.info(f"🔄 {symbol} 检测到死叉反转平仓，准备开空")
                    break

        # 只在有平仓发生时输出日志
        if close_results:
            logger.info(f"[反转判断] {symbol} positions={len(positions)}, has_open={has_open_position}, reversal={reversal_direction}")
        if not positions or not has_open_position:
            # ⚡ 优先处理反转平仓后的立即开仓（不受 buyDirection 限制，但需检查信号强度）
            if reversal_direction:
                logger.info(f"🔄 {symbol} 反转开仓: {reversal_direction}, buy_directions={buy_directions}")

                # 检查信号强度（使用已收盘K线的EMA差值，和普通金叉/死叉开仓逻辑一致）
                ema_diff_pct = ema_data.get('confirmed_ema_diff_pct', ema_data['ema_diff_pct'])
                if ema_diff_pct < self.MIN_SIGNAL_STRENGTH:
                    logger.info(f"🔄 {symbol} 反转开仓跳过: 信号强度不足({ema_diff_pct:.3f}% < {self.MIN_SIGNAL_STRENGTH}%，已收盘确认)")
                else:
                    entry_reason = f"reversal_entry(已收盘确认): EMA_diff:{ema_diff_pct:.3f}%"
                    try:
                        open_result = await self.execute_open_position(
                            symbol, reversal_direction, 'reversal_cross',
                            strategy, account_id, signal_reason=entry_reason,
                            force_market=True
                        )
                        logger.info(f"🔄 {symbol} 反转开仓结果: {open_result}")
                    except Exception as e:
                        logger.error(f"❌ {symbol} 反转开仓异常: {e}")
                        import traceback
                        traceback.print_exc()

            # 3.1 检查金叉/死叉信号（非反转情况）
            # 金叉/死叉是趋势反转的强信号，不受RSI等过滤器限制
            if not open_result or not open_result.get('success'):
                signal, signal_desc = self.check_golden_death_cross(ema_data)
                debug_info.append(f"金叉/死叉: {signal_desc}")

                if signal and signal in buy_directions:
                    # 正常流程：检查EMA+MA一致性
                    consistent, reason = self.check_ema_ma_consistency(ema_data, signal)
                    debug_info.append(f"EMA+MA一致性: {reason}")

                    if consistent:
                        # 金叉/死叉信号跳过RSI过滤器和开仓冷却，直接开仓
                        debug_info.append("✅ 金叉/死叉信号跳过RSI过滤器和开仓冷却")

                        # 构建开仓原因
                        entry_reason = f"crossover: {reason}, EMA_diff:{ema_data['ema_diff_pct']:.3f}%"
                        open_result = await self.execute_open_position(
                            symbol, signal, 'golden_cross' if signal == 'long' else 'death_cross',
                            strategy, account_id, signal_reason=entry_reason
                        )
                        debug_info.append(f"📊 金叉/死叉开仓结果: {open_result}")

            # 3.2 检查连续趋势信号（原有的5M放大检测）
            if not open_result or not open_result.get('success'):
                signal, signal_desc = self.check_sustained_trend(symbol)
                debug_info.append(f"连续趋势(5M放大): {signal_desc}")

                if signal and signal in buy_directions:
                    debug_info.append(f"✅ 连续趋势信号匹配方向: signal={signal}")
                    # 应用所有技术指标过滤器
                    filters_passed, filter_results = self.apply_all_filters(
                        symbol, signal, current_price, ema_data, strategy
                    )
                    debug_info.extend(filter_results)
                    debug_info.append(f"📋 过滤器结果: filters_passed={filters_passed}")

                    if filters_passed:
                        # 检查开仓冷却
                        in_cooldown, cooldown_msg = self.check_entry_cooldown(symbol, signal, strategy, strategy_id)
                        debug_info.append(f"⏰ 冷却检查: in_cooldown={in_cooldown}, msg={cooldown_msg}")
                        if in_cooldown:
                            debug_info.append(f"⏳ {cooldown_msg}")
                        else:
                            # 构建开仓原因
                            entry_reason = f"sustained_5m: {signal_desc}"
                            debug_info.append(f"🚀 准备执行开仓: {entry_reason}")
                            open_result = await self.execute_open_position(
                                symbol, signal, 'sustained_trend', strategy, account_id,
                                signal_reason=entry_reason
                            )
                            debug_info.append(f"📊 开仓结果: {open_result}")
                    else:
                        debug_info.append("⚠️ 技术指标过滤器未通过，跳过开仓")

            # 3.3 检查持续趋势开仓（错过金叉/死叉后仍可在趋势中开仓）
            if not open_result or not open_result.get('success'):
                for direction in buy_directions:
                    can_entry, sustained_reason = self.check_sustained_trend_entry(symbol, direction, strategy)

                    if can_entry:
                        # 应用所有技术指标过滤器
                        filters_passed, filter_results = self.apply_all_filters(
                            symbol, direction, current_price, ema_data, strategy
                        )
                        debug_info.extend(filter_results)

                        if filters_passed:
                            # 检查开仓冷却
                            in_cooldown, cooldown_msg = self.check_entry_cooldown(symbol, direction, strategy, strategy_id)
                            if in_cooldown:
                                debug_info.append(f"⏳ {cooldown_msg}")
                            else:
                                # 构建开仓原因
                                entry_reason = f"sustained_entry({direction}): {sustained_reason}"
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
                        # 检查开仓冷却
                        in_cooldown, cooldown_msg = self.check_entry_cooldown(symbol, signal, strategy, strategy_id)
                        if in_cooldown:
                            debug_info.append(f"⏳ {cooldown_msg}")
                        else:
                            # 构建开仓原因
                            entry_reason = f"oscillation_reversal: {signal_desc}"
                            open_result = await self.execute_open_position(
                                symbol, signal, 'oscillation_reversal', strategy, account_id,
                                signal_reason=entry_reason
                            )
                    else:
                        debug_info.append("⚠️ 技术指标过滤器未通过，跳过开仓")

            # 3.5 检查限价单信号（无需自检，直接挂单）
            if not open_result or not open_result.get('success'):
                limit_signal, limit_desc = self.check_limit_entry_signal(symbol, ema_data, strategy, strategy_id)
                debug_info.append(f"限价单信号: {limit_desc}")

                if limit_signal and limit_signal in buy_directions:
                    # 限价单不需要应用技术指标过滤器，直接执行
                    open_result = await self.execute_limit_order(
                        symbol, limit_signal, strategy, account_id, ema_data
                    )
                    if open_result and open_result.get('success'):
                        debug_info.append(f"✅ 限价单已挂出: {limit_signal} @ {open_result.get('limit_price', 0):.4f}")

        # 信号检测日志全部改为debug级别，只有开仓成功/失败才打印info
        logger.debug(f"📊 [{symbol}] 信号检测 | 价格:{current_price:.4f} | EMA9:{ema_data['ema9']:.4f} EMA26:{ema_data['ema26']:.4f} | 差值:{ema_data['ema_diff_pct']:.3f}%")
        for dbg in debug_info:
            logger.debug(f"   [{symbol}] {dbg}")

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

            sql = f"""
                UPDATE futures_positions
                SET {', '.join(set_clauses)}
                WHERE id = %s
            """
            cursor.execute(sql, values)
            conn.commit()

            # 记录更新日志
            if 'max_profit_pct' in updates or 'trailing_stop_activated' in updates:
                logger.info(f"[DB更新] position_id={position_id}, updates={updates}")
        finally:
            cursor.close()
            conn.close()


    # ==================== 策略加载和调度 ====================

    def get_active_strategies(self) -> List[Dict]:
        """获取所有启用的策略（公开方法）"""
        return self._load_strategies()

    def _load_strategies(self) -> List[Dict]:
        """从数据库加载启用的策略"""
        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT id, name, config, account_id, enabled, market_type, sync_live
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
                    # 数据库列 sync_live 优先级高于 JSON config 中的 syncLive
                    db_sync_live = row.get('sync_live')
                    if db_sync_live is not None:
                        config['syncLive'] = bool(db_sync_live)
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

            logger.debug(f"📊 V2执行器: 检查 {len(strategies)} 个策略")

            for strategy in strategies:
                try:
                    account_id = strategy.get('account_id', 2)
                    strategy_name = strategy.get('name', '未知')
                    logger.debug(f"执行策略: {strategy_name}")

                    result = await self.execute_strategy(strategy, account_id=account_id)

                    # 记录执行结果
                    for r in result.get('results', []):
                        symbol = r.get('symbol')
                        # 排除 pending=True 的情况（待开仓自检），只记录真正开仓成功的
                        if r.get('open_result') and r['open_result'].get('success') and not r['open_result'].get('pending'):
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
