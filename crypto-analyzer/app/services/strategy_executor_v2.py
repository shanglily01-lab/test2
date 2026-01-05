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
    MIN_SIGNAL_STRENGTH = 0.05  # 最小开仓强度阈值 (%) - 限价单模式下放宽
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

        # 反转预警冷却记录 {symbol: {'cooldown_until': datetime, 'direction': 'long'/'short', 'reason': str}}
        self._reversal_cooldowns = {}

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
            self.paper_margin_fixed = paper_config.get('fixed_amount', 200)
            self.paper_margin_percent = paper_config.get('percent', 1)

            # 实盘配置
            live_config = margin_config.get('live', {})
            self.live_margin_mode = live_config.get('mode', 'fixed')
            self.live_margin_fixed = live_config.get('fixed_amount', 200)
            self.live_margin_percent = live_config.get('percent', 1)

            logger.info(f"✅ 保证金配置已加载: 模拟盘={self.paper_margin_mode}({self.paper_margin_fixed}U/{self.paper_margin_percent}%), "
                       f"实盘={self.live_margin_mode}({self.live_margin_fixed}U/{self.live_margin_percent}%)")
        except Exception as e:
            logger.warning(f"加载保证金配置失败，使用默认值: {e}")
            self.paper_margin_mode = 'fixed'
            self.paper_margin_fixed = 200
            self.paper_margin_percent = 1
            self.live_margin_mode = 'fixed'
            self.live_margin_fixed = 200
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

    # ==================== 统一保护机制 ====================

    def check_min_holding_duration(self, position: Dict, min_minutes: int = 15) -> Tuple[bool, float]:
        """
        检查是否满足最小持仓时间要求

        Args:
            position: 持仓信息字典
            min_minutes: 最小持仓分钟数，默认15分钟

        Returns:
            (是否满足要求, 已持仓分钟数)
            - True: 已满足最小持仓时间
            - False: 未满足，不应平仓
        """
        open_time = position.get('open_time')
        if not open_time:
            return True, 0

        now = self.get_local_time()
        if isinstance(open_time, datetime):
            duration_minutes = (now - open_time).total_seconds() / 60
            return duration_minutes >= min_minutes, duration_minutes

        return True, 0

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

    # ==================== 反转预警机制 ====================

    def _check_reversal_warning(self, symbol: str, direction: str, ema_data: Dict, strategy: Dict) -> Tuple[bool, str]:
        """
        检测反转预警信号 - 检测斜率的突然剧变

        核心逻辑：
        真正危险的不是斜率方向变化，而是斜率发生"质变"——突然剧烈变化
        例如：斜率从 -0.5% 突然变成 +0.3%，变化幅度达到 0.8%，这才是危险信号

        反转预警条件（满足任一即触发）：
        1. 斜率突变：斜率变化的绝对值超过阈值（不管方向，只看变化幅度）
        2. EMA差距快速收窄：差距收窄速度超过阈值，说明即将交叉

        触发后：
        - 进入冷却期，暂停该方向开仓
        - 直到出现明确的金叉（做多）或死叉（做空）才解除冷却

        Args:
            symbol: 交易对
            direction: 'long' 或 'short'
            ema_data: EMA数据
            strategy: 策略配置

        Returns:
            (是否触发反转预警, 原因)
        """
        # 获取反转预警配置
        reversal_warning = strategy.get('reversalWarning', {})
        enabled = reversal_warning.get('enabled', True)  # 默认启用

        if not enabled:
            return False, ""

        # 配置参数
        # slopeChangeThreshold: 斜率突变阈值(%)，斜率变化幅度超过此值触发预警
        # 例如: 阈值0.3表示斜率从-0.5%变到+0.3%（变化0.8%）会触发
        slope_change_threshold = reversal_warning.get('slopeChangeThreshold', 0.3)  # 斜率突变阈值(%)
        diff_shrink_threshold = reversal_warning.get('diffShrinkThreshold', 30)  # 差距收窄阈值(%)
        cooldown_minutes = reversal_warning.get('cooldownMinutes', 30)  # 冷却时间(分钟)

        ema9_values = ema_data.get('ema9_values', [])
        ema26_values = ema_data.get('ema26_values', [])

        if len(ema9_values) < 5 or len(ema26_values) < 5:
            return False, ""

        now = datetime.now(self.LOCAL_TZ).replace(tzinfo=None)
        warning_triggered = False
        warning_reason = ""

        # 计算最近几根K线的EMA9斜率（使用已收盘的K线）
        # ema9_values[-2] 是最近已收盘K线，[-3]是前一根，[-4]是再前一根
        ema9_current = ema9_values[-2]  # 最近已收盘
        ema9_prev1 = ema9_values[-3]    # 前一根
        ema9_prev2 = ema9_values[-4]    # 再前一根

        ema26_current = ema26_values[-2]
        ema26_prev1 = ema26_values[-3]

        # 计算EMA9斜率（相对于价格的百分比变化）
        slope_current = (ema9_current - ema9_prev1) / ema9_prev1 * 100 if ema9_prev1 > 0 else 0
        slope_prev = (ema9_prev1 - ema9_prev2) / ema9_prev2 * 100 if ema9_prev2 > 0 else 0

        # 计算斜率突变幅度（关键：斜率变化的绝对值）
        slope_change = abs(slope_current - slope_prev)

        # 计算EMA差距变化
        diff_current = ema9_current - ema26_current
        diff_prev = ema9_prev1 - ema26_prev1
        diff_current_pct = abs(diff_current) / ema26_current * 100 if ema26_current > 0 else 0
        diff_prev_pct = abs(diff_prev) / ema26_prev1 * 100 if ema26_prev1 > 0 else 0

        # 差距收窄速度（百分比）
        if diff_prev_pct > 0:
            shrink_rate = (diff_prev_pct - diff_current_pct) / diff_prev_pct * 100
        else:
            shrink_rate = 0

        if direction.lower() == 'short':
            # 做空时的反转预警：
            # 1. 斜率突变且向不利方向（斜率变大，说明价格加速上涨）
            #    - 只有当斜率向上突变（当前斜率比之前更正/更大）才危险
            slope_sudden_change = slope_change > slope_change_threshold and slope_current > slope_prev
            # 2. 差距快速收窄（EMA9向上靠近EMA26，即将金叉）
            #    - 但如果已经金叉了（diff_current > 0），就不应该触发这个预警
            diff_shrinking = diff_current < 0 and shrink_rate > diff_shrink_threshold

            if slope_sudden_change:
                warning_triggered = True
                warning_reason = f"EMA9斜率突变: {slope_prev:.3f}% -> {slope_current:.3f}% (变化{slope_change:.3f}%)"
            elif diff_shrinking:
                warning_triggered = True
                warning_reason = f"EMA差距快速收窄: {shrink_rate:.1f}%"

        else:  # direction == 'long'
            # 做多时的反转预警：
            # 1. 斜率突变且向不利方向（斜率变小，说明价格加速下跌）
            #    - 只有当斜率向下突变（当前斜率比之前更负/更小）才危险
            slope_sudden_change = slope_change > slope_change_threshold and slope_current < slope_prev
            # 2. 差距快速收窄（EMA9向下靠近EMA26，即将死叉）
            #    - 但如果已经死叉了（diff_current < 0），就不应该触发这个预警
            diff_shrinking = diff_current > 0 and shrink_rate > diff_shrink_threshold

            if slope_sudden_change:
                warning_triggered = True
                warning_reason = f"EMA9斜率突变: {slope_prev:.3f}% -> {slope_current:.3f}% (变化{slope_change:.3f}%)"
            elif diff_shrinking:
                warning_triggered = True
                warning_reason = f"EMA差距快速收窄: {shrink_rate:.1f}%"

        if warning_triggered:
            # 设置冷却期
            cooldown_until = now + timedelta(minutes=cooldown_minutes)
            self._reversal_cooldowns[symbol] = {
                'cooldown_until': cooldown_until,
                'direction': direction.lower(),
                'reason': warning_reason,
                'created_at': now
            }
            logger.warning(f"⚠️ [反转预警] {symbol} {direction}: {warning_reason}，冷却{cooldown_minutes}分钟直到明确交叉")

        return warning_triggered, warning_reason

    def _check_reversal_cooldown(self, symbol: str, direction: str, ema_data: Dict) -> Tuple[bool, str]:
        """
        检查是否在反转冷却期内

        冷却期解除条件：
        - 做空方向：出现明确的死叉（EMA9下穿EMA26）
        - 做多方向：出现明确的金叉（EMA9上穿EMA26）

        Args:
            symbol: 交易对
            direction: 'long' 或 'short'
            ema_data: EMA数据

        Returns:
            (是否在冷却期, 原因)
        """
        cooldown_info = self._reversal_cooldowns.get(symbol)
        if not cooldown_info:
            return False, ""

        # 检查冷却方向是否匹配
        if cooldown_info['direction'] != direction.lower():
            return False, ""

        now = datetime.now(self.LOCAL_TZ).replace(tzinfo=None)

        # 检查是否超时（强制解除冷却）
        if now > cooldown_info['cooldown_until']:
            logger.info(f"[反转冷却] {symbol} {direction} 冷却超时，已解除")
            del self._reversal_cooldowns[symbol]
            return False, ""

        # 检查是否出现明确的金叉/死叉（解除冷却）
        ema9_values = ema_data.get('ema9_values', [])
        ema26_values = ema_data.get('ema26_values', [])

        if len(ema9_values) >= 3 and len(ema26_values) >= 3:
            # 使用已收盘的K线判断交叉
            ema9_curr = ema9_values[-2]
            ema26_curr = ema26_values[-2]
            ema9_prev = ema9_values[-3]
            ema26_prev = ema26_values[-3]

            if direction.lower() == 'short':
                # 做空需要死叉解除冷却
                is_death_cross = ema9_prev > ema26_prev and ema9_curr < ema26_curr
                if is_death_cross:
                    logger.info(f"✅ [反转冷却] {symbol} 出现死叉，解除做空冷却")
                    del self._reversal_cooldowns[symbol]
                    return False, ""
            else:
                # 做多需要金叉解除冷却
                is_golden_cross = ema9_prev < ema26_prev and ema9_curr > ema26_curr
                if is_golden_cross:
                    logger.info(f"✅ [反转冷却] {symbol} 出现金叉，解除做多冷却")
                    del self._reversal_cooldowns[symbol]
                    return False, ""

        # 仍在冷却期
        remaining = (cooldown_info['cooldown_until'] - now).total_seconds() / 60
        return True, f"反转冷却中({remaining:.0f}分钟): {cooldown_info['reason']}"

    def _check_close_cooldown(self, symbol: str, direction: str, strategy: Dict) -> Tuple[bool, str]:
        """
        检查平仓冷却期（从数据库查询最近平仓记录）

        Args:
            symbol: 交易对
            direction: 方向 ('long' 或 'short')
            strategy: 策略配置

        Returns:
            (是否在冷却期, 原因)
        """
        from datetime import datetime, timezone, timedelta
        import pymysql

        # 读取策略配置
        cooldown_minutes = strategy.get('closeReopenCooldownMinutes', 15)  # 默认15分钟
        apply_to_same_direction = strategy.get('closeReopenSameDirectionOnly', False)  # 默认false

        # 如果冷却时间为0，表示禁用冷却
        if cooldown_minutes <= 0:
            return False, ""

        try:
            # 从数据库查询最近的平仓记录
            connection = pymysql.connect(**self.db_config)
            cursor = connection.cursor(pymysql.cursors.DictCursor)

            # 查询最近的平仓（只看该交易对）
            if apply_to_same_direction:
                # 只限制同方向
                query = """
                    SELECT position_side, close_time
                    FROM futures_positions
                    WHERE symbol = %s
                      AND position_side = %s
                      AND status = 'closed'
                      AND close_time IS NOT NULL
                    ORDER BY close_time DESC
                    LIMIT 1
                """
                cursor.execute(query, (symbol, direction.upper()))
            else:
                # 限制所有方向
                query = """
                    SELECT position_side, close_time
                    FROM futures_positions
                    WHERE symbol = %s
                      AND status = 'closed'
                      AND close_time IS NOT NULL
                    ORDER BY close_time DESC
                    LIMIT 1
                """
                cursor.execute(query, (symbol,))

            result = cursor.fetchone()
            cursor.close()
            connection.close()

            if not result:
                return False, ""

            close_time = result['close_time']
            closed_direction = result['position_side'].lower()

            # 计算冷却时间（数据库存储的是UTC+8本地时间）
            local_tz = timezone(timedelta(hours=8))
            now = datetime.now(local_tz).replace(tzinfo=None)

            # 确保 close_time 是 datetime 对象
            if isinstance(close_time, str):
                close_time = datetime.strptime(close_time, '%Y-%m-%d %H:%M:%S')

            elapsed_minutes = (now - close_time).total_seconds() / 60

            if elapsed_minutes < cooldown_minutes:
                remaining = cooldown_minutes - elapsed_minutes
                direction_text = f"{direction}方向" if apply_to_same_direction else ""
                return True, f"平仓冷却中({remaining:.0f}分钟, 刚平仓{closed_direction}{direction_text})"
            else:
                return False, ""

        except Exception as e:
            logger.error(f"检查平仓冷却失败: {e}")
            return False, ""

    def _cancel_pending_orders_for_direction(self, symbol: str, direction: str):
        """
        取消指定方向的待成交订单，并平仓模拟盘该方向持仓

        干预措施：
        1. 取消模拟盘待成交订单（数据库记录）
        2. 平仓模拟盘该方向的持仓

        Args:
            symbol: 交易对
            direction: 'long' 或 'short'
        """
        position_side = 'LONG' if direction.lower() == 'long' else 'SHORT'
        order_side = f'OPEN_{position_side}'

        # ========== 1. 取消模拟盘待成交订单 ==========
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            # 查询待取消的订单
            cursor.execute("""
                SELECT id, order_id FROM futures_orders
                WHERE symbol = %s AND side = %s AND status = 'PENDING'
            """, (symbol, order_side))
            pending_orders = cursor.fetchall()

            if pending_orders:
                # 更新订单状态为取消
                cursor.execute("""
                    UPDATE futures_orders
                    SET status = 'CANCELLED', updated_at = NOW(), notes = CONCAT(IFNULL(notes, ''), ' | 反转预警取消')
                    WHERE symbol = %s AND side = %s AND status = 'PENDING'
                """, (symbol, order_side))
                conn.commit()
                logger.warning(f"⚠️ [反转预警] 取消 {symbol} {direction} 方向 {len(pending_orders)} 个模拟盘待成交订单")

            cursor.close()
            conn.close()

        except Exception as e:
            logger.error(f"取消模拟盘待成交订单失败: {e}")

        # ========== 2. 平仓模拟盘该方向持仓 ==========
        if self.futures_engine:
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor(pymysql.cursors.DictCursor)

                # 查询该方向的持仓
                cursor.execute("""
                    SELECT id, symbol, position_side, quantity, entry_price
                    FROM futures_positions
                    WHERE symbol = %s AND position_side = %s AND status = 'open'
                """, (symbol, position_side))
                positions = cursor.fetchall()

                cursor.close()
                conn.close()

                for pos in positions:
                    try:
                        result = self.futures_engine.close_position(
                            position_id=pos['id'],
                            reason='reversal_warning'
                        )
                        if result.get('success'):
                            logger.warning(f"⚠️ [反转预警] 平仓模拟盘持仓: {symbol} {direction} 仓位ID={pos['id']}")
                        else:
                            logger.error(f"平仓模拟盘持仓失败: {pos['id']} - {result.get('error')}")
                    except Exception as e:
                        logger.error(f"平仓模拟盘持仓异常: {pos['id']} - {e}")

            except Exception as e:
                logger.error(f"查询/平仓模拟盘持仓失败: {e}")

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

        # 强度阈值：从配置读取，默认0.15%
        min_ema_diff_pct = signal_stop_config.get('minEmaDiffPct', 0.15)

        # 做多持仓亏损 + 5M EMA处于死叉状态（EMA9 < EMA26）+ 强度足够 → 立即止损
        if position_side == 'LONG' and ema9 < ema26:
            ema_diff_pct = (ema26 - ema9) / ema26 * 100
            if ema_diff_pct >= min_ema_diff_pct:
                reason = f"5m_death_cross_sl|loss:{abs(current_pnl_pct):.2f}%|diff:{ema_diff_pct:.2f}%"
                logger.info(f"🔴 [Smart SL] {symbol} {reason}")
                return True, reason

        # 做空持仓亏损 + 5M EMA处于金叉状态（EMA9 > EMA26）+ 强度足够 → 立即止损
        if position_side == 'SHORT' and ema9 > ema26:
            ema_diff_pct = (ema9 - ema26) / ema26 * 100
            if ema_diff_pct >= min_ema_diff_pct:
                reason = f"5m_golden_cross_sl|loss:{abs(current_pnl_pct):.2f}%|diff:{ema_diff_pct:.2f}%"
                logger.info(f"🟢 [Smart SL] {symbol} {reason}")
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

    def check_golden_death_cross(self, symbol: str, ema_data_15m: Dict, ema_data_1h: Dict, strategy: Dict = None) -> Tuple[Optional[str], str]:
        """
        检测金叉/死叉信号（双周期确认：15M信号 + 1H方向）

        策略：
        - 15M出现金叉 + 1H也是多头趋势 → 做多
        - 15M出现死叉 + 1H也是空头趋势 → 做空
        - 方向冲突时跳过，避免逆大趋势交易

        Args:
            symbol: 交易对
            ema_data_15m: 15M周期EMA数据（用于检测金叉/死叉信号）
            ema_data_1h: 1H周期EMA数据（用于确认大趋势方向）
            strategy: 策略配置（用于获取minSignalStrength）

        Returns:
            (信号方向 'long'/'short'/None, 信号描述)
        """
        # 15M：检测金叉/死叉信号
        ema9_15m = ema_data_15m.get('confirmed_ema9', ema_data_15m['ema9'])
        ema26_15m = ema_data_15m.get('confirmed_ema26', ema_data_15m['ema26'])
        prev_ema9_15m = ema_data_15m['prev_ema9']
        prev_ema26_15m = ema_data_15m['prev_ema26']
        ema_diff_pct_15m = ema_data_15m.get('confirmed_ema_diff_pct', ema_data_15m['ema_diff_pct'])

        # 1H：确认大趋势方向
        ema9_1h = ema_data_1h['ema9']
        ema26_1h = ema_data_1h['ema26']

        # 金叉/死叉使用独立的最小强度阈值（默认0.01%，比普通信号0.05%低）
        crossover_min_strength = 0.01
        if strategy:
            min_signal_strength = strategy.get('minSignalStrength', {})
            if isinstance(min_signal_strength, dict):
                crossover_min_strength = min_signal_strength.get('crossover', 0.01)

        # 15M金叉：前一根EMA9 <= EMA26，当前EMA9 > EMA26
        is_golden_cross_15m = prev_ema9_15m <= prev_ema26_15m and ema9_15m > ema26_15m

        # 15M死叉：前一根EMA9 >= EMA26，当前EMA9 < EMA26
        is_death_cross_15m = prev_ema9_15m >= prev_ema26_15m and ema9_15m < ema26_15m

        # 1H方向判断
        is_bullish_1h = ema9_1h > ema26_1h  # 1H多头
        is_bearish_1h = ema9_1h < ema26_1h  # 1H空头

        # 双周期确认：15M金叉 + 1H多头
        if is_golden_cross_15m:
            if ema_diff_pct_15m < crossover_min_strength:
                return None, f"15M金叉信号强度不足({ema_diff_pct_15m:.3f}% < {crossover_min_strength}%)"

            if is_bullish_1h:
                return 'long', f"15M金叉+1H多头确认(15M强度{ema_diff_pct_15m:.3f}%)"
            else:
                return None, f"15M金叉但1H空头，方向冲突跳过(15M:{ema_diff_pct_15m:.3f}%, 1H EMA9<EMA26)"

        # 双周期确认：15M死叉 + 1H空头
        if is_death_cross_15m:
            if ema_diff_pct_15m < crossover_min_strength:
                return None, f"15M死叉信号强度不足({ema_diff_pct_15m:.3f}% < {crossover_min_strength}%)"

            if is_bearish_1h:
                return 'short', f"15M死叉+1H空头确认(15M强度{ema_diff_pct_15m:.3f}%)"
            else:
                return None, f"15M死叉但1H多头，方向冲突跳过(15M:{ema_diff_pct_15m:.3f}%, 1H EMA9>EMA26)"

        return None, "无金叉/死叉信号"

    def check_sustained_trend(self, symbol: str, strategy: Dict = None) -> Tuple[Optional[str], str]:
        """
        检测连续趋势信号
        双周期确认：15M和5M周期EMA差值同时放大 + 1H方向确认

        Args:
            symbol: 交易对
            strategy: 策略配置（用于获取minSignalStrength）

        Returns:
            (信号方向 'long'/'short'/None, 信号描述)
        """
        # 获取1H数据（用于方向确认）
        ema_1h = self.get_ema_data(symbol, '1h', 50)
        if not ema_1h:
            return None, "1H数据不足"

        # 获取15M数据
        ema_15m = self.get_ema_data(symbol, '15m', 50)
        if not ema_15m:
            return None, "15M数据不足"

        # 获取5M数据
        ema_5m = self.get_ema_data(symbol, '5m', 50)
        if not ema_5m:
            return None, "5M数据不足"

        # 从策略配置获取最小信号强度
        min_strength = self.MIN_SIGNAL_STRENGTH
        if strategy:
            min_signal_strength = strategy.get('minSignalStrength', {})
            if isinstance(min_signal_strength, dict):
                min_strength = min_signal_strength.get('ema9_26', self.MIN_SIGNAL_STRENGTH)

        # 检查15M趋势方向
        ema_diff_15m = ema_15m['ema_diff']
        is_uptrend_15m = ema_diff_15m > 0

        # 1H方向确认
        ema9_1h = ema_1h['ema9']
        ema26_1h = ema_1h['ema26']
        is_bullish_1h = ema9_1h > ema26_1h
        is_bearish_1h = ema9_1h < ema26_1h

        # 双周期确认：15M方向必须与1H方向一致
        if is_uptrend_15m and not is_bullish_1h:
            return None, f"连续趋势: 15M多头但1H空头，方向冲突跳过（1H EMA9={ema9_1h:.8f} < EMA26={ema26_1h:.8f}）"
        if not is_uptrend_15m and not is_bearish_1h:
            return None, f"连续趋势: 15M空头但1H多头，方向冲突跳过（1H EMA9={ema9_1h:.8f} > EMA26={ema26_1h:.8f}）"

        # 检查15M差值是否在合理范围内
        ema_diff_pct_15m = ema_15m['ema_diff_pct']
        if ema_diff_pct_15m < min_strength:
            return None, f"15M趋势强度不足({ema_diff_pct_15m:.3f}% < {min_strength}%)"

        # 检查5M连续3根K线差值放大（限价单模式下放宽为3根）
        ema9_values = ema_5m['ema9_values']
        ema26_values = ema_5m['ema26_values']

        if len(ema9_values) < 3 or len(ema26_values) < 3:
            return None, "5M EMA数据不足"

        # 计算最近3根K线的EMA差值
        diff_values = []
        for i in range(-3, 0):
            diff = abs(ema9_values[i] - ema26_values[i])
            diff_values.append(diff)

        # 检查是否连续放大
        expanding = True
        for i in range(1, len(diff_values)):
            if diff_values[i] <= diff_values[i-1]:
                expanding = False
                break

        if not expanding:
            return None, f"5M差值未连续放大: {[f'{d:.6f}' for d in diff_values]}"

        # 检查EMA+MA方向一致性
        direction = 'long' if is_uptrend_15m else 'short'
        consistent, reason = self.check_ema_ma_consistency(ema_15m, direction)
        if not consistent:
            return None, reason

        # 1H方向确认信息
        ema_diff_pct_1h = abs(ema9_1h - ema26_1h) / ema26_1h * 100
        direction_1h = "多头" if is_bullish_1h else "空头"

        return direction, f"连续趋势信号({direction}, 15M差值{ema_diff_pct_15m:.3f}%, 5M连续放大, 1H{direction_1h}确认)"

    def check_oscillation_reversal(self, symbol: str) -> Tuple[Optional[str], str]:
        """
        检测震荡区间反向开仓信号
        双周期确认：连续4根同向K线 + 幅度<0.5% + 成交量条件 + 1H方向确认

        Returns:
            (信号方向 'long'/'short'/None, 信号描述)
        """
        # 获取1H数据（用于方向确认）
        ema_1h = self.get_ema_data(symbol, '1h', 50)
        if not ema_1h:
            return None, "1H数据不足"

        ema9_1h = ema_1h['ema9']
        ema26_1h = ema_1h['ema26']
        is_bullish_1h = ema9_1h > ema26_1h
        is_bearish_1h = ema9_1h < ema26_1h

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

                # 1H方向确认（震荡反转做空需要1H也是空头）
                if not is_bearish_1h:
                    return None, f"震荡反转: 15M连续阳线做空但1H多头，方向冲突跳过（1H EMA9={ema9_1h:.8f} > EMA26={ema26_1h:.8f}）"

                # 检查EMA+MA方向一致性
                ema_data = self.get_ema_data(symbol, '15m', 50)
                if ema_data:
                    consistent, reason = self.check_ema_ma_consistency(ema_data, 'short')
                    if not consistent:
                        return None, reason

                ema_diff_pct_1h = abs(ema9_1h - ema26_1h) / ema26_1h * 100
                return 'short', f"震荡反向做空(连续{self.OSCILLATION_BARS}阳线+缩量{volume_ratio:.2f}, 1H空头确认{ema_diff_pct_1h:.3f}%)"

            else:  # all_bearish
                # 连续阴线 → 成交量放量 → 做多
                if volume_ratio <= self.VOLUME_EXPAND_THRESHOLD:
                    return None, f"成交量未放量({volume_ratio:.2f} <= {self.VOLUME_EXPAND_THRESHOLD})"

                # 1H方向确认（震荡反转做多需要1H也是多头）
                if not is_bullish_1h:
                    return None, f"震荡反转: 15M连续阴线做多但1H空头，方向冲突跳过（1H EMA9={ema9_1h:.8f} < EMA26={ema26_1h:.8f}）"

                # 检查EMA+MA方向一致性
                ema_data = self.get_ema_data(symbol, '15m', 50)
                if ema_data:
                    consistent, reason = self.check_ema_ma_consistency(ema_data, 'long')
                    if not consistent:
                        return None, reason

                ema_diff_pct_1h = abs(ema9_1h - ema26_1h) / ema26_1h * 100
                return 'long', f"震荡反向做多(连续{self.OSCILLATION_BARS}阴线+放量{volume_ratio:.2f}, 1H多头确认{ema_diff_pct_1h:.3f}%)"

        finally:
            cursor.close()
            conn.close()

    # ==================== 限价单信号检测 ====================

    def check_limit_entry_signal(self, symbol: str, ema_data: Dict, strategy: Dict,
                                  strategy_id: int) -> Tuple[Optional[str], str]:
        """
        检测限价单开仓信号
        双周期确认：15M趋势强度 + 1H方向确认
        条件：EMA趋势强度 > 0.25% 且方向一致 + 无PENDING限价单 + 不在冷却期

        Args:
            symbol: 交易对
            ema_data: 1H EMA数据（用于方向确认）
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

        # 获取15M和1H的EMA数据
        ema_data_15m = self.get_ema_data(symbol, '15m', 50)
        ema_data_1h = ema_data  # 传入的是1H数据

        if not ema_data_15m or not ema_data_1h:
            return None, "EMA数据不足"

        # 15M: 用于计算趋势强度
        ema_diff_15m = ema_data_15m['ema_diff']
        ema_diff_pct_15m = ema_data_15m['ema_diff_pct']

        # 1H: 用于确认趋势方向
        ema9_1h = ema_data_1h['ema9']
        ema26_1h = ema_data_1h['ema26']
        is_bullish_1h = ema9_1h > ema26_1h
        is_bearish_1h = ema9_1h < ema26_1h

        current_price = ema_data_1h['current_price']

        # 从策略配置获取最小信号强度，默认0.25%（限价单要求更强的趋势）
        min_signal_strength = strategy.get('minSignalStrength', {})
        if isinstance(min_signal_strength, dict):
            min_strength = min_signal_strength.get('ema9_26', 0.25)
        else:
            min_strength = 0.25

        if ema_diff_pct_15m < min_strength:
            return None, f"限价单信号强度不足(15M {ema_diff_pct_15m:.3f}% < {min_strength}%)"

        # 判断15M方向
        if ema_diff_15m > 0:  # 15M上升趋势
            direction = 'long'
            price_type = long_price_type
        else:  # 15M下降趋势
            direction = 'short'
            price_type = short_price_type

        # 如果该方向没有配置限价单，跳过
        if price_type == 'market':
            return None, f"{direction}方向未配置限价单"

        # 双周期确认：1H方向必须与15M方向一致
        if direction == 'long' and not is_bullish_1h:
            return None, f"限价单: 15M多头但1H空头，方向冲突跳过（1H EMA9={ema9_1h:.8f} < EMA26={ema26_1h:.8f}）"
        if direction == 'short' and not is_bearish_1h:
            return None, f"限价单: 15M空头但1H多头，方向冲突跳过（1H EMA9={ema9_1h:.8f} > EMA26={ema26_1h:.8f}）"

        # 注：已移除MA方向检查，因为限价单使用回调入场策略（做多限价低于市价0.6%）
        # 当限价单触发时，价格自然会低于/高于MA10，这是预期行为

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

        # 检查平仓后的冷却时间（使用统一的开仓冷却检查）
        in_cooldown, cooldown_msg = self.check_entry_cooldown(symbol, direction, strategy, strategy_id)
        if in_cooldown:
            return None, f"限价单{cooldown_msg}"

        # 1H方向确认信息
        ema_diff_pct_1h = abs(ema9_1h - ema26_1h) / ema26_1h * 100
        direction_1h = "多头" if is_bullish_1h else "空头"

        return direction, f"限价单信号({direction}, 15M强度{ema_diff_pct_15m:.3f}%, 1H{direction_1h}确认)"

    async def execute_limit_order(self, symbol: str, direction: str, strategy: Dict,
                                   account_id: int, ema_data: Dict) -> Dict:
        """
        执行限价单开仓（不需要自检，直接挂单）
        一次性批量创建多个限价单直到达到上限

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
            position_side = direction.upper()

            # 获取限价配置
            if direction == 'long':
                price_type = strategy.get('longPrice', 'market')
            else:
                price_type = strategy.get('shortPrice', 'market')

            # 计算限价
            # 如果是 market 类型，使用默认 0.6% 回调
            if price_type == 'market':
                if direction == 'long':
                    limit_price = current_price * (1 - 0.6 / 100)  # 做多：市价减0.6%
                else:
                    limit_price = current_price * (1 + 0.6 / 100)  # 做空：市价加0.6%
            else:
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
                # ========== 挂限价单 ==========
                # 查询当前方向已有多少持仓+挂单
                entry_cooldown = strategy.get('entryCooldown', {})
                max_positions = entry_cooldown.get('maxPositionsPerDirection', 1)

                conn = self.get_db_connection()
                cursor = conn.cursor()
                try:
                    # 查询当前币种、当前方向的 open 持仓数量
                    cursor.execute("""
                        SELECT COUNT(*) as count FROM futures_positions
                        WHERE symbol = %s AND position_side = %s AND status = 'open'
                    """, (symbol, position_side))
                    open_count = cursor.fetchone()['count']

                    # 查询当前币种、当前方向的 PENDING 限价单数量
                    order_side = f'OPEN_{position_side}'
                    cursor.execute("""
                        SELECT COUNT(*) as count FROM futures_orders
                        WHERE symbol = %s AND side = %s AND status = 'PENDING'
                    """, (symbol, order_side))
                    pending_count = cursor.fetchone()['count']
                finally:
                    cursor.close()
                    conn.close()

                # 计算还能开多少单
                current_total = open_count + pending_count
                orders_to_create = max(0, max_positions - current_total)

                if orders_to_create == 0:
                    return {'success': False, 'error': f'{symbol} {position_side}方向已达上限{max_positions}'}

                logger.info(f"📊 {symbol} {position_side}: 当前{open_count}持仓+{pending_count}挂单，将创建{orders_to_create}个限价单")

                # 创建多个限价单
                created_orders = []
                # 限价单信号类型
                entry_signal_type = 'limit_order_trend'  # 限价单趋势跟踪
                ema_diff_pct = ema_data.get('ema_diff_pct', 0)
                entry_reason = f"限价单({direction}, EMA强度{ema_diff_pct:.3f}%, 回调入场)"

                for i in range(orders_to_create):
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
                        strategy_id=strategy.get('id'),
                        entry_signal_type=entry_signal_type,
                        entry_reason=entry_reason
                    )

                    if result.get('success'):
                        position_id = result.get('position_id')
                        order_id = result.get('order_id')
                        is_pending = result.get('status') == 'PENDING'

                        created_orders.append({
                            'position_id': position_id,
                            'order_id': order_id,
                            'is_pending': is_pending
                        })

                        if is_pending:
                            timeout_minutes = strategy.get('limitOrderTimeoutMinutes', 30)
                            actual_offset_pct = (limit_price - current_price) / current_price * 100
                            logger.info(f"📋 {symbol} 限价单#{i+1}已挂出: {direction} {quantity:.8f} @ {limit_price:.4f} "
                                       f"(偏离:{actual_offset_pct:+.2f}%), 超时:{timeout_minutes}分钟")
                        else:
                            entry_price = result.get('entry_price', limit_price)
                            logger.info(f"✅ {symbol} 限价单#{i+1}立即成交: {direction} @ {entry_price:.4f}")

                            # 同步实盘
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
                                    logger.error(f"[同步实盘] ❌ {symbol} 限价单#{i+1}同步失败: {live_ex}")
                    else:
                        logger.warning(f"❌ {symbol} 限价单#{i+1}创建失败: {result.get('error')}")

                if created_orders:
                    logger.info(f"✅ {symbol} 批量创建{len(created_orders)}个限价单完成")
                    return {
                        'success': True,
                        'position_id': created_orders[0]['position_id'],
                        'order_id': created_orders[0]['order_id'],
                        'direction': direction,
                        'quantity': quantity,
                        'limit_price': limit_price,
                        'signal_type': 'limit_order',
                        'is_pending': created_orders[0]['is_pending'],
                        'total_orders': len(created_orders)
                    }
                else:
                    return {'success': False, 'error': '所有限价单创建失败'}

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

        # 从策略配置读取RSI阈值
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

        双周期确认：15M趋势 + 1H方向确认

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

        # 获取15M和1H的EMA数据
        ema_data_15m = self.get_ema_data(symbol, '15m', 50)
        ema_data_1h = self.get_ema_data(symbol, '1h', 50)

        if not ema_data_15m or not ema_data_1h:
            return False, "EMA数据不足"

        # 15M: 用于计算趋势强度
        ema_diff_pct_15m = ema_data_15m['ema_diff_pct']
        ema_diff_15m = ema_data_15m['ema_diff']

        # 1H: 用于确认趋势方向
        ema9_1h = ema_data_1h['ema9']
        ema26_1h = ema_data_1h['ema26']
        is_bullish_1h = ema9_1h > ema26_1h
        is_bearish_1h = ema9_1h < ema26_1h

        min_strength = sustained_config.get('minStrength', 0.15)
        max_strength = sustained_config.get('maxStrength', 1.0)
        require_ma10_confirm = sustained_config.get('requireMA10Confirm', True)
        require_price_confirm = sustained_config.get('requirePriceConfirm', True)

        # 检查15M趋势方向是否匹配
        is_uptrend_15m = ema_diff_15m > 0
        if direction == 'long' and not is_uptrend_15m:
            return False, "持续趋势: 15M方向不匹配，非上升趋势"
        if direction == 'short' and is_uptrend_15m:
            return False, "持续趋势: 15M方向不匹配，非下降趋势"

        # 双周期确认：1H方向必须与开仓方向一致
        if direction == 'long' and not is_bullish_1h:
            return False, f"持续趋势: 1H空头，方向冲突跳过（1H EMA9={ema9_1h:.8f} < EMA26={ema26_1h:.8f}）"
        if direction == 'short' and not is_bearish_1h:
            return False, f"持续趋势: 1H多头，方向冲突跳过（1H EMA9={ema9_1h:.8f} > EMA26={ema26_1h:.8f}）"

        # 检查趋势强度范围（使用15M数据）
        if ema_diff_pct_15m < min_strength:
            return False, f"持续趋势: 强度不足 {ema_diff_pct_15m:.3f}% < {min_strength}%"
        if ema_diff_pct_15m > max_strength:
            return False, f"持续趋势: 强度过大 {ema_diff_pct_15m:.3f}% > {max_strength}%（可能反转）"

        # MA10确认（使用15M数据）
        if require_ma10_confirm:
            ma10 = ema_data_15m['ma10']
            ema10 = self.calculate_ema([float(k['close_price']) for k in ema_data_15m['klines']], 10)
            if ema10:
                current_ema10 = ema10[-1]
                if direction == 'long' and current_ema10 < ma10:
                    return False, f"持续趋势: MA10/EMA10不确认上升趋势"
                if direction == 'short' and current_ema10 > ma10:
                    return False, f"持续趋势: MA10/EMA10不确认下降趋势"

        # 价格确认（使用15M数据）
        if require_price_confirm:
            current_price = ema_data_15m['current_price']
            ema9 = ema_data_15m['ema9']
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

        # 1H方向确认信息
        ema_diff_pct_1h = abs(ema9_1h - ema26_1h) / ema26_1h * 100
        direction_1h = "多头" if is_bullish_1h else "空头"

        return True, f"持续趋势开仓通过: 15M强度{ema_diff_pct_15m:.3f}%在{min_strength}%~{max_strength}%范围内, 1H{direction_1h}确认(强度{ema_diff_pct_1h:.3f}%)"

    def check_entry_cooldown(self, symbol: str, direction: str, strategy: Dict, strategy_id: int) -> Tuple[bool, str]:
        """
        检查开仓限制（持仓数量 + 时间冷却）

        1. 每个币种、每个方向最多同时开 maxPositionsPerDirection 个单（默认1个）
        2. 平仓后需要等待 minutes 分钟才能再次开仓（默认30分钟）

        Args:
            symbol: 交易对
            direction: 'long' 或 'short'
            strategy: 策略配置
            strategy_id: 策略ID（仅用于日志，不参与限制计算）

        Returns:
            (是否被限制, 原因说明)
        """
        entry_cooldown = strategy.get('entryCooldown', {})
        if not entry_cooldown.get('enabled', True):  # 默认启用
            return False, "开仓限制未启用"

        # 每个方向最多同时开几个单（默认1个）
        max_positions_per_direction = entry_cooldown.get('maxPositionsPerDirection', 1)
        # 平仓后冷却时间（分钟，默认30分钟）
        cooldown_minutes = entry_cooldown.get('minutes', 30)

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            # 注意：futures_positions 表使用 position_side 字段（LONG/SHORT）
            position_side = 'LONG' if direction.lower() == 'long' else 'SHORT'
            # futures_orders 表使用 side 字段（OPEN_LONG/OPEN_SHORT）
            order_side = f'OPEN_{position_side}'

            # 1. 查询当前币种、当前方向的 open 持仓数量（不区分策略）
            cursor.execute("""
                SELECT COUNT(*) as count FROM futures_positions
                WHERE symbol = %s AND position_side = %s AND status = 'open'
            """, (symbol, position_side))

            open_count = cursor.fetchone()['count']

            # 2. 查询当前币种、当前方向的 PENDING 限价单数量（不区分策略）
            cursor.execute("""
                SELECT COUNT(*) as count FROM futures_orders
                WHERE symbol = %s AND side = %s AND status = 'PENDING'
            """, (symbol, order_side))

            pending_count = cursor.fetchone()['count']

            total_count = open_count + pending_count

            if total_count >= max_positions_per_direction:
                cursor.close()
                conn.close()
                return True, f"{symbol} {position_side}方向已有{open_count}个持仓+{pending_count}个挂单，达到上限{max_positions_per_direction}"

            # 3. 检查时间冷却：查询最近一次平仓时间
            cursor.execute("""
                SELECT close_time
                FROM futures_positions
                WHERE symbol = %s AND position_side = %s AND status = 'closed'
                ORDER BY close_time DESC
                LIMIT 1
            """, (symbol, position_side))

            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if result and result['close_time']:
                from datetime import datetime, timezone, timedelta
                last_close_time = result['close_time']

                # 确保时间对象有时区信息
                local_tz = timezone(timedelta(hours=8))
                now = datetime.now(local_tz).replace(tzinfo=None)

                if isinstance(last_close_time, datetime):
                    minutes_since_close = (now - last_close_time).total_seconds() / 60

                    if minutes_since_close < cooldown_minutes:
                        remaining_minutes = cooldown_minutes - minutes_since_close
                        return True, f"{symbol} {position_side}方向冷却中: 上次平仓于{last_close_time.strftime('%H:%M:%S')}，还需等待{remaining_minutes:.1f}分钟（冷却时间{cooldown_minutes}分钟）"

            return False, f"{symbol} {position_side}方向: {open_count}个持仓+{pending_count}个挂单，未达上限{max_positions_per_direction}，冷却时间已过"

        except Exception as e:
            logger.warning(f"{symbol} 检查开仓限制失败: {e}")
            return False, f"检查异常: {e}"

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
        symbol = position.get('symbol', '')

        # 计算当前盈亏百分比
        entry_price = float(position.get('entry_price') or 0)
        current_price = ema_data.get('current_price', 0)

        if entry_price <= 0 or current_price <= 0:
            return False, ""

        if position_side == 'LONG':
            current_pnl_pct = (current_price - entry_price) / entry_price * 100
        else:  # SHORT
            current_pnl_pct = (entry_price - current_price) / entry_price * 100

        # 使用已收盘K线的EMA判断金叉/死叉，避免未收盘K线波动导致误判
        ema9 = ema_data.get('confirmed_ema9', ema_data['ema9'])
        ema26 = ema_data.get('confirmed_ema26', ema_data['ema26'])
        prev_ema9 = ema_data['prev_ema9']
        prev_ema26 = ema_data['prev_ema26']

        # 平仓策略：只有在盈利或盈亏平衡时才执行金叉/死叉平仓
        # 亏损时给仓位翻盘的机会，避免过早止损

        if position_side == 'LONG':
            # 持多仓 + 死叉 → 检查是否盈利
            is_death_cross = prev_ema9 >= prev_ema26 and ema9 < ema26
            if is_death_cross:
                if current_pnl_pct >= 0:
                    return True, "death_cross_reversal"
                else:
                    logger.info(f"{symbol} 死叉信号出现但持仓亏损{current_pnl_pct:.2f}%，不平仓，给予翻盘机会")
                    return False, ""

            # 趋势反转：EMA9 < EMA26（已收盘确认）→ 检查是否盈利
            if ema9 < ema26:
                if current_pnl_pct >= 0:
                    return True, "trend_reversal_bearish"
                else:
                    logger.debug(f"{symbol} 趋势转跌但持仓亏损{current_pnl_pct:.2f}%，不平仓")
                    return False, ""

        else:  # SHORT
            # 持空仓 + 金叉 → 检查是否盈利
            is_golden_cross = prev_ema9 <= prev_ema26 and ema9 > ema26
            if is_golden_cross:
                if current_pnl_pct >= 0:
                    return True, "golden_cross_reversal"
                else:
                    logger.info(f"{symbol} 金叉信号出现但持仓亏损{current_pnl_pct:.2f}%，不平仓，给予翻盘机会")
                    return False, ""

            # 趋势反转：EMA9 > EMA26（已收盘确认）→ 检查是否盈利
            if ema9 > ema26:
                if current_pnl_pct >= 0:
                    return True, "trend_reversal_bullish"
                else:
                    logger.debug(f"{symbol} 趋势转涨但持仓亏损{current_pnl_pct:.2f}%，不平仓")
                    return False, ""

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
            return True, f"max_take_profit|pnl:{current_pnl_pct:.2f}%", updates

        # 检查是否激活移动止盈
        if not trailing_activated and max_profit_pct >= self.TRAILING_ACTIVATE:
            updates['trailing_stop_activated'] = True
            trailing_activated = True
            logger.info(f"Trailing TP activated: max_pnl={max_profit_pct:.2f}% >= {self.TRAILING_ACTIVATE}%")

        # 移动止盈已激活，检查回撤
        if trailing_activated:
            callback_pct = max_profit_pct - current_pnl_pct
            if callback_pct >= self.TRAILING_CALLBACK:
                # 添加最小持仓时间保护（15分钟），避免刚开仓就被移动止盈平掉
                satisfied, duration = self.check_min_holding_duration(position, 15)
                if not satisfied:
                    symbol = position.get('symbol', '')
                    logger.debug(f"{symbol} 移动止盈被跳过: 持仓时长{duration:.1f}分钟 < 15分钟")
                    return False, "", updates

                return True, f"trailing_take_profit|max:{max_profit_pct:.2f}%|cb:{callback_pct:.2f}%", updates

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
            return True, f"hard_stop_loss|loss:{abs(current_pnl_pct):.2f}%"

        return False, ""

    def check_ema_diff_take_profit(self, position: Dict, ema_data: Dict,
                                    current_pnl_pct: float, strategy: Dict) -> Tuple[bool, str]:
        """
        EMA差值止盈检测（使用15分钟周期）

        当EMA9与EMA26的差值百分比**收窄**到阈值以下时，说明趋势减弱，触发止盈平仓。
        逻辑：开仓时EMA差值大（趋势强），持仓期间差值缩小说明趋势减弱，应该止盈。

        Args:
            position: 持仓信息
            ema_data: EMA数据（15m周期）
            current_pnl_pct: 当前盈亏百分比
            strategy: 策略配置

        Returns:
            (是否需要平仓, 原因)
        """
        # 获取EMA差值止盈配置
        ema_diff_tp = strategy.get('emaDiffTakeProfit', {})
        if not ema_diff_tp.get('enabled', False):
            return False, ""

        threshold = ema_diff_tp.get('threshold', 0.5)  # EMA差值阈值，默认0.5%
        min_profit_pct = ema_diff_tp.get('minProfitPct', 1.5)  # 最小盈利要求，默认1.5%
        min_loss_pct = ema_diff_tp.get('minLossPct', -0.8)  # 最小亏损要求，默认-0.8%

        # 检查是否达到触发条件：盈利 >= 1.5% 或 亏损 <= -0.8%
        # -0.8% ~ 1.5% 之间不触发任何平仓逻辑，给仓位发展空间
        if min_loss_pct <= current_pnl_pct < min_profit_pct:
            return False, ""

        # 使用传入的15m周期EMA数据
        if not ema_data:
            return False, ""

        ema9 = ema_data.get('ema9')
        ema26 = ema_data.get('ema26')

        if ema9 is None or ema26 is None or ema26 == 0:
            return False, ""

        # 计算当前EMA差值百分比
        ema_diff_pct = abs((ema9 - ema26) / ema26 * 100)

        symbol = position.get('symbol', '')
        position_side = position.get('position_side', 'LONG')

        # 获取开仓时的EMA差值（如果有记录）
        entry_ema_diff = position.get('entry_ema_diff')
        if entry_ema_diff is not None:
            entry_ema_diff_pct = abs(float(entry_ema_diff))
        else:
            # 没有记录开仓时的EMA差值，使用阈值的2倍作为默认值
            entry_ema_diff_pct = threshold * 2

        # 检查EMA方向是否仍然支持持仓方向
        # 做多时EMA9应该 > EMA26，做空时EMA9应该 < EMA26
        ema_supports_position = (position_side == 'LONG' and ema9 > ema26) or \
                                (position_side == 'SHORT' and ema9 < ema26)

        # EMA差值收窄止盈：当差值缩小到阈值以下，且盈利达标时止盈
        # 条件：当前差值 < 阈值，说明趋势减弱
        if ema_diff_pct < threshold:
            # 添加最小持仓时间保护（15分钟），避免刚开仓就被平掉
            satisfied, duration = self.check_min_holding_duration(position, 15)
            if not satisfied:
                logger.debug(f"{symbol} EMA差值收窄止盈被跳过: 持仓时长{duration:.1f}分钟 < 15分钟")
                return False, ""

            return True, f"ema_diff_narrowing_tp|diff:{ema_diff_pct:.2f}%|pnl:{current_pnl_pct:.2f}%"

        # EMA方向反转止盈：趋势已经反转，但还有盈利时止盈
        # 添加最小持仓时间保护（15分钟），避免刚开仓就被平掉
        if not ema_supports_position and current_pnl_pct >= min_profit_pct:
            # 检查持仓时长
            satisfied, duration = self.check_min_holding_duration(position, 15)
            if not satisfied:
                logger.debug(f"{symbol} EMA方向反转止盈被跳过: 持仓时长{duration:.1f}分钟 < 15分钟")
                return False, ""

            return True, f"ema_direction_reversal_tp|pnl:{current_pnl_pct:.2f}%"

        return False, ""

    def _calculate_ema_values(self, prices: list, period: int) -> list:
        """计算EMA值列表"""
        if len(prices) < period:
            return []

        ema_values = []
        multiplier = 2 / (period + 1)

        # 第一个EMA使用SMA
        sma = sum(prices[:period]) / period
        ema_values.append(sma)

        # 计算后续的EMA
        for i in range(period, len(prices)):
            ema = (prices[i] - ema_values[-1]) * multiplier + ema_values[-1]
            ema_values.append(ema)

        return ema_values

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
            'market_minus_1_2': -1.2,
            'market_minus_1_4': -1.4,
            'market_plus_0_2': 0.2,
            'market_plus_0_4': 0.4,
            'market_plus_0_6': 0.6,
            'market_plus_0_8': 0.8,
            'market_plus_1': 1.0,
            'market_plus_1_2': 1.2,
            'market_plus_1_4': 1.4,
        }

        adjustment_pct = price_adjustments.get(price_type)
        if adjustment_pct is None:
            logger.warning(f"未知的价格类型: {price_type}, 使用市价")
            return None

        # 计算限价
        limit_price = current_price * (1 + adjustment_pct / 100)
        return limit_price

    def check_trend_weakening(self, position: Dict, ema_data: Dict, current_price: float = None, strategy: Dict = None) -> Tuple[bool, str]:
        """
        检测趋势减弱（开仓后30分钟开始监控，且仅在盈利时触发）

        当EMA差值连续3次减弱时，触发平仓

        Args:
            position: 持仓信息
            ema_data: 当前EMA数据
            current_price: 当前价格（用于判断盈亏）
            strategy: 策略配置（用于读取趋势减弱平仓配置）

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

        # 从策略配置读取趋势减弱平仓参数
        trend_exit_config = {}
        if strategy:
            trend_exit_config = strategy.get('trendWeakeningExit', {})

        # 是否启用（默认启用）
        trend_exit_enabled = trend_exit_config.get('enabled', True)
        if not trend_exit_enabled:
            return False, "trend_weakening_disabled"

        # EMA差值收窄阈值（默认0.5=50%）
        trend_exit_ema_threshold = trend_exit_config.get('emaDiffThreshold', 0.5)

        # 最小盈利要求（默认1.0%）
        trend_exit_min_profit = trend_exit_config.get('minProfitPct', 1.0)

        # 检查强度是否减弱到配置的阈值以下（使用已收盘K线数据）
        if confirmed_ema_diff_pct < entry_ema_diff * trend_exit_ema_threshold:
            # 需要满足最小盈利要求才触发趋势减弱平仓
            # 避免刚开始盈利就被平仓的情况

            if current_price:
                entry_price = float(position.get('entry_price', 0))
                if entry_price > 0:
                    if position_side == 'LONG':
                        pnl_pct = (current_price - entry_price) / entry_price * 100
                    else:
                        pnl_pct = (entry_price - current_price) / entry_price * 100

                    if pnl_pct < 0:
                        return False, f"trend_weakening_but_losing|pnl:{pnl_pct:.2f}%"

                    if pnl_pct < trend_exit_min_profit:
                        return False, f"trend_weakening_insufficient_profit|pnl:{pnl_pct:.2f}%|min:{trend_exit_min_profit}%"

            return True, f"trend_weakening|curr:{confirmed_ema_diff_pct:.3f}%|entry:{entry_ema_diff:.3f}%|threshold:{trend_exit_ema_threshold*100}%"

        return False, f"trend_normal|curr:{confirmed_ema_diff_pct:.3f}%|entry:{entry_ema_diff:.3f}%|threshold:{trend_exit_ema_threshold*100}%"

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
        # 注意：开仓后15分钟内不检查止损价触发，防止开仓即止损
        # 但硬止损(-2.5%)不受此限制，作为紧急止损
        updated_stop_loss = updates.get('stop_loss_price', current_stop_loss)
        if updated_stop_loss > 0:
            # 判断是移动止损还是普通止损（通过盈亏判断：盈利时触发的是移动止损）
            is_trailing_stop = current_pnl_pct > 0
            stop_type = "trailing_stop_loss" if is_trailing_stop else "stop_loss"

            # 冷却期保护：开仓后15分钟内不检查普通止损价触发
            if in_cooldown and not is_trailing_stop:
                # 在冷却期内，只有硬止损(-2.5%)可以触发，普通止损价(-1.93%)被跳过
                satisfied, duration = self.check_min_holding_duration(position, trailing_cooldown_minutes)
                if not satisfied:
                    logger.debug(f"{symbol} 止损价触发被跳过: 持仓时长{duration:.1f}分钟 < {trailing_cooldown_minutes}分钟")
                else:
                    if position_side == 'LONG' and current_price <= updated_stop_loss:
                        return True, f"{stop_type}|price:{current_price:.4f}|sl:{updated_stop_loss:.4f}", updates
                    elif position_side == 'SHORT' and current_price >= updated_stop_loss:
                        return True, f"{stop_type}|price:{current_price:.4f}|sl:{updated_stop_loss:.4f}", updates
            else:
                # 非冷却期，或者是移动止损，正常检查
                if position_side == 'LONG' and current_price <= updated_stop_loss:
                    return True, f"{stop_type}|price:{current_price:.4f}|sl:{updated_stop_loss:.4f}", updates
                elif position_side == 'SHORT' and current_price >= updated_stop_loss:
                    return True, f"{stop_type}|price:{current_price:.4f}|sl:{updated_stop_loss:.4f}", updates

        # 2. 硬止损检查（百分比止损，作为后备）
        # 硬止损不受冷却期限制，作为紧急止损
        if current_pnl_pct <= -stop_loss_pct:
            return True, f"hard_stop_loss|loss:{abs(current_pnl_pct):.2f}%", updates

        # 2.5 5M信号智能止损（亏损时检测5M反向交叉）
        # 注意：冷却期内不检查5M信号止损
        if not in_cooldown:
            close_needed, close_reason = self.check_5m_signal_stop_loss(position, current_pnl_pct, strategy)
            if close_needed:
                return True, close_reason, updates

        # 3. 最大止盈检查
        if current_pnl_pct >= max_take_profit:
            return True, f"max_take_profit|pnl:{current_pnl_pct:.2f}%", updates

        # 3.5 EMA差值止盈检查
        close_needed, close_reason = self.check_ema_diff_take_profit(position, ema_data, current_pnl_pct, strategy)
        if close_needed:
            return True, close_reason, updates

        # 4. 金叉/死叉反转检查（冷却期内跳过，避免刚开仓就被反转信号平掉）
        if not in_cooldown:
            close_needed, close_reason = self.check_cross_reversal(position, ema_data)
            if close_needed:
                return True, close_reason, updates

        # 5. 趋势减弱检查（传入当前价格用于判断盈亏，亏损时不触发）
        close_needed, close_reason = self.check_trend_weakening(position, ema_data, current_price, strategy)
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

            logger.info(f"Trailing TP activated: max_pnl={max_profit_pct:.2f}% >= {trailing_activate}%, sl_price={trailing_stop_price:.4f}")

        # 移动止盈已激活，检查回撤
        if trailing_activated:
            callback_pct = max_profit_pct - current_pnl_pct
            if callback_pct >= trailing_callback:
                return True, f"trailing_take_profit|max:{max_profit_pct:.2f}%|cb:{callback_pct:.2f}%", updates

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

    # ==================== 待开仓自检 ====================

    def _validate_pending_entry(self, symbol: str, direction: str, ema_data: Dict,
                                  strategy: Dict) -> Tuple[bool, str]:
        """
        待开仓自检：在开仓前验证各项条件

        自检项目：
        1. EMA方向确认 - EMA9和EMA26方向与开仓方向一致
        2. MA方向确认 - 价格与MA10的关系符合开仓方向
        3. 震荡市检查 - 检测是否处于震荡区间
        4. 趋势末端检查 - 检测是否处于趋势末端
        5. EMA收敛检查 - EMA差值是否在收窄
        6. 最小EMA差值检查 - EMA差值是否大于阈值

        Args:
            symbol: 交易对
            direction: 开仓方向 'long' 或 'short'
            ema_data: 15M EMA数据
            strategy: 策略配置

        Returns:
            (是否通过, 拒绝原因)
        """
        pending_validation = strategy.get('pendingValidation', {})

        # 获取EMA数据
        ema9 = ema_data.get('ema9')
        ema26 = ema_data.get('ema26')
        ma10 = ema_data.get('ma10')
        current_price = ema_data.get('current_price')
        ema_diff_pct = ema_data.get('ema_diff_pct', 0)

        # 使用已收盘K线的EMA差值（更准确）
        confirmed_ema_diff_pct = ema_data.get('confirmed_ema_diff_pct', ema_diff_pct)

        reject_reasons = []

        # 1. EMA方向确认
        if pending_validation.get('require_ema_confirm', True):
            if direction == 'long':
                if ema9 <= ema26:
                    reject_reasons.append(f"EMA方向不符(EMA9={ema9:.4f} <= EMA26={ema26:.4f})")
            else:  # short
                if ema9 >= ema26:
                    reject_reasons.append(f"EMA方向不符(EMA9={ema9:.4f} >= EMA26={ema26:.4f})")

        # 注：已移除MA方向检查（require_ma_confirm），因为限价单使用回调入场策略
        # 做多限价低于市价0.6%，触发时价格自然会低于MA10，这是预期行为

        # 2. 震荡市检查
        if pending_validation.get('check_ranging', True):
            # 简单震荡检测：EMA差值很小
            ranging_threshold = 0.1  # 0.1%以下视为震荡
            if confirmed_ema_diff_pct < ranging_threshold:
                reject_reasons.append(f"震荡市(EMA差值{confirmed_ema_diff_pct:.3f}% < {ranging_threshold}%)")

        # 4. 趋势末端检查
        if pending_validation.get('check_trend_end', True):
            # 通过比较当前EMA差值与前一个K线的差值来判断趋势是否减弱
            prev_ema9 = ema_data.get('prev_ema9')
            prev_ema26 = ema_data.get('prev_ema26')
            if prev_ema9 and prev_ema26 and prev_ema26 != 0:
                prev_diff_pct = abs((prev_ema9 - prev_ema26) / prev_ema26 * 100)
                # 如果差值减小超过30%，可能是趋势末端
                if prev_diff_pct > 0 and confirmed_ema_diff_pct < prev_diff_pct * 0.7:
                    reject_reasons.append(f"趋势末端(差值缩小{((prev_diff_pct - confirmed_ema_diff_pct) / prev_diff_pct * 100):.1f}%)")

        # 5. EMA收敛检查
        if pending_validation.get('check_ema_converging', True):
            # 检查EMA是否在收敛（差值持续缩小）
            confirmed_ema9 = ema_data.get('confirmed_ema9', ema9)
            confirmed_ema26 = ema_data.get('confirmed_ema26', ema26)
            prev_ema9 = ema_data.get('prev_ema9')
            prev_ema26 = ema_data.get('prev_ema26')

            if prev_ema9 and prev_ema26:
                current_diff = abs(confirmed_ema9 - confirmed_ema26)
                prev_diff = abs(prev_ema9 - prev_ema26)

                # 如果差值在缩小，说明EMA在收敛
                if current_diff < prev_diff:
                    shrink_pct = (prev_diff - current_diff) / prev_diff * 100 if prev_diff > 0 else 0
                    # 收窄超过30%时拒绝开仓
                    shrink_threshold = 30
                    if shrink_pct >= shrink_threshold:
                        reject_reasons.append(f"EMA收敛(收窄{shrink_pct:.1f}% >= {shrink_threshold}%)")

        # 6. 最小EMA差值检查
        min_ema_diff_pct = pending_validation.get('min_ema_diff_pct', 0.05)
        if confirmed_ema_diff_pct < min_ema_diff_pct:
            reject_reasons.append(f"弱趋势(EMA差值{confirmed_ema_diff_pct:.3f}% < {min_ema_diff_pct}%)")

        # 汇总结果
        if reject_reasons:
            return False, "; ".join(reject_reasons)

        return True, ""

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
            # ========== 信号去重检查（同一K线周期内不重复触发）==========
            position_side = 'LONG' if direction.lower() == 'long' else 'SHORT'
            signal_key = f"{symbol}_{position_side}"

            # 获取当前15分钟K线的开始时间作为去重key
            now = datetime.now(self.LOCAL_TZ).replace(tzinfo=None)
            kline_start = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
            kline_key = f"{signal_key}_{kline_start.strftime('%Y%m%d%H%M')}"

            # 检查是否在同一K线周期内已触发过信号
            if not hasattr(self, '_signal_triggered'):
                self._signal_triggered = {}

            if kline_key in self._signal_triggered:
                # 静默跳过，不打印日志（避免日志刷屏）
                return {'success': False, 'error': f'当前K线周期内已触发过{direction}信号', 'skipped': True}

            # ========== 检查是否已达持仓+挂单上限 ==========
            entry_cooldown = strategy.get('entryCooldown', {})
            max_positions = entry_cooldown.get('maxPositionsPerDirection', 1)

            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()

                # 查询当前币种、当前方向的 open 持仓数量
                cursor.execute("""
                    SELECT COUNT(*) as count FROM futures_positions
                    WHERE symbol = %s AND position_side = %s AND status = 'open'
                """, (symbol, position_side))
                open_count = cursor.fetchone()['count']

                # 查询当前币种、当前方向的 PENDING 限价单数量
                order_side = f'OPEN_{position_side}'
                cursor.execute("""
                    SELECT COUNT(*) as count FROM futures_orders
                    WHERE symbol = %s AND side = %s AND status = 'PENDING'
                """, (symbol, order_side))
                pending_count = cursor.fetchone()['count']

                cursor.close()
                conn.close()

                # 如果已达上限，直接返回（不打印日志）
                if open_count + pending_count >= max_positions:
                    return {'success': False, 'error': f'{symbol} {direction}方向已达上限{max_positions}', 'skipped': True}

            except Exception as e:
                logger.warning(f"检查持仓上限失败: {e}")

            # 获取当前价格和EMA数据
            ema_data = self.get_ema_data(symbol, '15m', 50)
            if not ema_data:
                return {'success': False, 'error': '获取价格数据失败'}

            current_price = ema_data['current_price']

            # ========== 反转预警检测 ==========
            # 1. 先检查是否在反转冷却期内
            in_cooldown, cooldown_reason = self._check_reversal_cooldown(symbol, direction, ema_data)
            if in_cooldown:
                return {'success': False, 'error': cooldown_reason, 'reversal_cooldown': True}

            # 1.5 检查是否在平仓冷却期内
            in_close_cooldown, close_cooldown_reason = self._check_close_cooldown(symbol, direction, strategy)
            if in_close_cooldown:
                return {'success': False, 'error': close_cooldown_reason, 'close_cooldown': True}

            # 2. 检测是否触发反转预警
            reversal_warning = strategy.get('reversalWarning', {})
            if reversal_warning.get('enabled', True):  # 默认启用
                warning_triggered, warning_reason = self._check_reversal_warning(symbol, direction, ema_data, strategy)
                if warning_triggered:
                    # 取消该方向的待成交订单
                    self._cancel_pending_orders_for_direction(symbol, direction)
                    return {'success': False, 'error': f'反转预警: {warning_reason}', 'reversal_warning': True}

            # ========== 待开仓自检 ==========
            pending_validation = strategy.get('pendingValidation', {})
            validation_enabled = pending_validation.get('enabled', False)

            # 强制市价开仓时跳过自检
            if validation_enabled and not force_market:
                passed, reject_reason = self._validate_pending_entry(
                    symbol, direction, ema_data, strategy
                )
                if not passed:
                    logger.info(f"🚫 {symbol} 待开仓自检未通过: {reject_reason}")
                    # 标记信号已触发，避免同一K线周期内重复打印日志
                    self._signal_triggered[kline_key] = now
                    return {'success': False, 'error': f'自检未通过: {reject_reason}', 'validation_failed': True}

            # 标记当前K线周期已触发信号（在实际创建订单前标记）
            self._signal_triggered[kline_key] = now

            # 清理过期的信号记录（保留最近1小时的）
            expired_keys = [k for k, v in self._signal_triggered.items()
                          if (now - v).total_seconds() > 3600]
            for k in expired_keys:
                del self._signal_triggered[k]

            # 执行开仓
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

                # ========== 限价单开仓（原市价单改为限价单）==========
                # 信号触发 → 自检 → 通过后一次性挂多个限价单等待回调
                # 使用策略配置的 longPrice / shortPrice 参数
                # 30分钟未成交自动取消

                # 获取策略配置的限价参数（所有信号类型统一使用限价单）
                if direction == 'long':
                    price_type = strategy.get('longPrice', 'market_minus_0_6')
                else:
                    price_type = strategy.get('shortPrice', 'market_plus_0_6')

                # 计算限价
                limit_price = self._calculate_limit_price(current_price, price_type, direction)
                if limit_price is None:
                        # 如果配置为 market，使用当前价格（立即成交）
                        limit_price = current_price
                        logger.info(f"💰 {symbol} 使用市价开仓: {limit_price:.8f}")

                # 根据限价重新计算数量
                quantity = notional / limit_price

                # ========== 挂限价单 ==========
                # 查询当前方向已有多少持仓+挂单
                entry_cooldown = strategy.get('entryCooldown', {})
                max_positions = entry_cooldown.get('maxPositionsPerDirection', 1)

                conn = self.get_db_connection()
                cursor = conn.cursor()
                try:
                    # 查询当前币种、当前方向的 open 持仓数量
                    cursor.execute("""
                        SELECT COUNT(*) as count FROM futures_positions
                        WHERE symbol = %s AND position_side = %s AND status = 'open'
                    """, (symbol, position_side))
                    open_count = cursor.fetchone()['count']

                    # 查询当前币种、当前方向的 PENDING 限价单数量
                    order_side = f'OPEN_{position_side}'
                    cursor.execute("""
                        SELECT COUNT(*) as count FROM futures_orders
                        WHERE symbol = %s AND side = %s AND status = 'PENDING'
                    """, (symbol, order_side))
                    pending_count = cursor.fetchone()['count']
                finally:
                    cursor.close()
                    conn.close()

                # 计算还能开多少单
                current_total = open_count + pending_count
                orders_to_create = max(0, max_positions - current_total)

                if orders_to_create == 0:
                    return {'success': False, 'error': f'{symbol} {position_side}方向已达上限{max_positions}'}

                logger.info(f"📊 {symbol} {position_side}: 当前{open_count}持仓+{pending_count}挂单，将创建{orders_to_create}个限价单")

                # 创建多个限价单
                created_orders = []
                # 确保 signal_reason 有默认值
                reason_text = signal_reason or signal_type or 'strategy_signal'
                for i in range(orders_to_create):
                    result = self.futures_engine.open_position(
                        account_id=account_id,
                        symbol=symbol,
                        position_side=position_side,
                        quantity=Decimal(str(quantity)),
                        leverage=leverage,
                        limit_price=Decimal(str(limit_price)),  # 使用限价单
                        stop_loss_pct=Decimal(str(stop_loss_pct)),
                        take_profit_pct=Decimal(str(take_profit_pct)),
                        source='strategy_limit',  # 标记为策略限价单
                        strategy_id=strategy.get('id'),
                        entry_signal_type=signal_type,  # 开仓信号类型
                        entry_reason=f"{reason_text} (#{i+1}/{orders_to_create})"  # 开仓原因
                    )

                    if result.get('success'):
                        position_id = result.get('position_id')
                        order_id = result.get('order_id')
                        is_pending = result.get('status') == 'PENDING'

                        # 更新开仓时的EMA差值（只有当持仓已创建时才更新）
                        # 注意：entry_signal_type 和 entry_reason 已在 open_position 调用时传递
                        if position_id:
                            conn = self.get_db_connection()
                            cursor = conn.cursor()
                            try:
                                cursor.execute("""
                                    UPDATE futures_positions
                                    SET entry_ema_diff = %s
                                    WHERE id = %s
                                """, (ema_diff_pct, position_id))
                                conn.commit()
                            except Exception as e:
                                logger.warning(f"更新开仓EMA差值失败: {e}")
                            finally:
                                cursor.close()
                                conn.close()

                        created_orders.append({
                            'position_id': position_id,
                            'order_id': order_id,
                            'is_pending': is_pending
                        })

                        if is_pending:
                            # PENDING 状态：限价单已挂出，等待成交
                            timeout_minutes = strategy.get('limitOrderTimeoutMinutes', 30)
                            actual_offset_pct = (limit_price - current_price) / current_price * 100
                            logger.info(f"📋 {symbol} 限价单#{i+1}已挂出: {direction} {quantity:.8f} @ {limit_price:.4f} "
                                       f"(偏离:{actual_offset_pct:+.2f}%), 超时:{timeout_minutes}分钟")
                        else:
                            # 立即成交
                            entry_price = result.get('entry_price', limit_price)
                            logger.info(f"✅ {symbol} 限价单#{i+1}立即成交: {direction} @ {entry_price:.4f}")

                            # 同步实盘（立即成交时才同步）
                            if sync_live and self.live_engine:
                                live_position_id = await self._sync_live_open(symbol, direction, quantity, leverage, strategy, position_id)
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
                    else:
                        logger.warning(f"❌ {symbol} 限价单#{i+1}创建失败: {result.get('error')}")

                if created_orders:
                    logger.info(f"✅ {symbol} 批量创建{len(created_orders)}个限价单完成")
                    return {
                        'success': True,
                        'position_id': created_orders[0]['position_id'],
                        'order_id': created_orders[0]['order_id'],
                        'direction': direction,
                        'quantity': quantity,
                        'limit_price': limit_price,
                        'price': current_price,
                        'signal_type': signal_type,
                        'is_pending': created_orders[0]['is_pending'],
                        'total_orders': len(created_orders)
                    }
                else:
                    return {'success': False, 'error': '所有限价单创建失败'}

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
                    # 平仓冷却检查会直接从数据库 futures_positions 表查询最近平仓时间

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
                    close_reason = f"hard_stop_loss|loss:{abs(current_pnl_pct):.2f}%"
                    logger.info(f"🚨 [Fast Monitor] {symbol} {close_reason}")
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
                        close_reason = f"trailing_take_profit|max:{max_profit_pct:.2f}%|cb:{callback_pct:.2f}%"
                        logger.info(f"🚨 [Fast Monitor] {symbol} {close_reason}")

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

        # 1. 获取双周期EMA数据
        # 1H：用于判断大趋势方向
        ema_data_1h = self.get_ema_data(symbol, '1h', 50)
        if not ema_data_1h:
            return {'symbol': symbol, 'error': '1H EMA数据不足', 'debug': debug_info}

        # 15M：用于检测金叉/死叉信号
        ema_data_15m = self.get_ema_data(symbol, '15m', 50)
        if not ema_data_15m:
            return {'symbol': symbol, 'error': '15M EMA数据不足', 'debug': debug_info}

        current_price = ema_data_1h['current_price']
        debug_info.append(f"当前价格: {current_price:.4f}")
        debug_info.append(f"1H EMA9: {ema_data_1h['ema9']:.4f}, EMA26: {ema_data_1h['ema26']:.4f}, 差值: {ema_data_1h['ema_diff_pct']:.3f}%")
        debug_info.append(f"15M EMA9: {ema_data_15m['ema9']:.4f}, EMA26: {ema_data_15m['ema26']:.4f}, 差值: {ema_data_15m['ema_diff_pct']:.3f}%")

        # 为了兼容性，保留ema_data变量指向1H数据（用于平仓逻辑）
        ema_data = ema_data_1h

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
                if 'golden_cross_reversal' in p_reason:
                    reversal_direction = 'long'
                    logger.info(f"🔄 {symbol} Golden cross reversal detected, preparing LONG")
                    break
                elif 'death_cross_reversal' in p_reason:
                    reversal_direction = 'short'
                    logger.info(f"🔄 {symbol} Death cross reversal detected, preparing SHORT")
                    break

        # 只在有平仓发生时输出日志
        if close_results:
            logger.info(f"[反转判断] {symbol} positions={len(positions)}, has_open={has_open_position}, reversal={reversal_direction}")
        if not positions or not has_open_position:
            # ⚡ 优先处理反转平仓后的立即开仓（不受 buyDirection 限制，但需检查信号强度 + 1H方向确认）
            if reversal_direction:
                logger.info(f"🔄 {symbol} 反转开仓: {reversal_direction}, buy_directions={buy_directions}")

                # 1H方向确认（与金叉/死叉、持续趋势一致的双周期确认）
                ema9_1h = ema_data_1h['ema9']
                ema26_1h = ema_data_1h['ema26']
                is_bullish_1h = ema9_1h > ema26_1h
                is_bearish_1h = ema9_1h < ema26_1h

                # 检查1H方向是否与反转方向一致
                skip_reversal = False
                if reversal_direction == 'long' and not is_bullish_1h:
                    logger.info(f"🔄 {symbol} 反转开仓跳过: 15M金叉但1H空头，方向冲突（1H EMA9={ema9_1h:.8f} < EMA26={ema26_1h:.8f}）")
                    skip_reversal = True
                elif reversal_direction == 'short' and not is_bearish_1h:
                    logger.info(f"🔄 {symbol} 反转开仓跳过: 15M死叉但1H多头，方向冲突（1H EMA9={ema9_1h:.8f} > EMA26={ema26_1h:.8f}）")
                    skip_reversal = True

                if not skip_reversal:
                    # 从策略配置获取最小信号强度
                    min_signal_strength = strategy.get('minSignalStrength', {})
                    if isinstance(min_signal_strength, dict):
                        min_strength = min_signal_strength.get('ema9_26', self.MIN_SIGNAL_STRENGTH)
                    else:
                        min_strength = self.MIN_SIGNAL_STRENGTH

                    # 检查信号强度（使用15M的EMA差值）
                    ema_diff_pct_15m = ema_data_15m.get('confirmed_ema_diff_pct', ema_data_15m['ema_diff_pct'])
                    if ema_diff_pct_15m < min_strength:
                        logger.info(f"🔄 {symbol} 反转开仓跳过: 信号弱 (15M {ema_diff_pct_15m:.3f}% < {min_strength}%)")
                    else:
                        ema_diff_pct_1h = abs(ema9_1h - ema26_1h) / ema26_1h * 100
                        direction_1h = "多头" if is_bullish_1h else "空头"
                        entry_reason = f"reversal_entry|15M强度:{ema_diff_pct_15m:.3f}%|1H{direction_1h}确认({ema_diff_pct_1h:.3f}%)"
                        try:
                            open_result = await self.execute_open_position(
                                symbol, reversal_direction, 'reversal_cross',
                                strategy, account_id, signal_reason=entry_reason,
                                force_market=False  # 改为限价单开仓，等待回调
                            )
                            logger.info(f"🔄 {symbol} 反转开仓结果: {open_result}")
                        except Exception as e:
                            logger.error(f"❌ {symbol} 反转开仓异常: {e}")
                            import traceback
                            traceback.print_exc()

            # 3.1 检查金叉/死叉信号（非反转情况）
            # 双周期确认：15M金叉/死叉 + 1H方向确认
            if not open_result or not open_result.get('success'):
                signal, signal_desc = self.check_golden_death_cross(symbol, ema_data_15m, ema_data_1h, strategy)
                debug_info.append(f"金叉/死叉: {signal_desc}")

                if signal and signal in buy_directions:
                    # 正常流程：检查EMA+MA一致性（使用15M数据）
                    consistent, reason = self.check_ema_ma_consistency(ema_data_15m, signal)
                    debug_info.append(f"EMA+MA一致性: {reason}")

                    if consistent:
                        # 金叉/死叉信号跳过RSI过滤器和开仓冷却，但使用限价单等待回调
                        debug_info.append("✅ 双周期确认通过，使用限价单等待回调")

                        # 构建开仓原因
                        entry_reason = f"crossover: {signal_desc}, 15M_diff:{ema_data_15m['ema_diff_pct']:.3f}%"
                        open_result = await self.execute_open_position(
                            symbol, signal, 'golden_cross' if signal == 'long' else 'death_cross',
                            strategy, account_id, signal_reason=entry_reason,
                            force_market=False  # 改为限价单开仓，等待回调
                        )
                        debug_info.append(f"📊 金叉/死叉开仓结果: {open_result}")

            # 3.2 检查连续趋势信号（原有的5M放大检测）
            if not open_result or not open_result.get('success'):
                signal, signal_desc = self.check_sustained_trend(symbol, strategy)
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
