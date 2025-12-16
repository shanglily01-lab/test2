"""
模拟盘开单自检服务
在开仓后进行二次验证，如果发现开单不合理，自动平仓避免损失
"""

import asyncio
import logging
import pymysql
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from decimal import Decimal
import pytz

logger = logging.getLogger(__name__)


class PositionValidator:
    """模拟盘开单自检服务"""

    # 配置参数
    VALIDATION_CONFIG = {
        'enabled': True,
        'first_check_delay': 30,       # 首次检查延迟（秒）
        'check_interval': 30,          # 检查间隔（秒）
        'validation_window': 900,      # 验证窗口（15分钟）
        'quick_loss_threshold': 0.5,   # 快速止损阈值（%）
        'quick_loss_window': 120,      # 快速止损窗口（2分钟）
        'ranging_volatility': 1.0,     # 震荡市波动阈值（%）
        'trend_exhaustion_threshold': 0.3,  # 趋势末端阈值（%）
        'signal_decay_threshold': 30,  # 信号衰减阈值（%）
        'immediate_reversal_threshold': 0.3,  # 逆势阈值（%）
        'min_issues_to_close': 2,      # 触发平仓的最小问题数
    }

    LOCAL_TZ = pytz.timezone('Asia/Shanghai')

    def __init__(self, db_config: Dict, futures_engine=None, trade_notifier=None):
        """
        初始化自检服务

        Args:
            db_config: 数据库配置
            futures_engine: 模拟盘交易引擎
            trade_notifier: Telegram 通知服务
        """
        self.db_config = db_config
        self.futures_engine = futures_engine
        self.trade_notifier = trade_notifier
        self.running = False
        self.task = None
        # 记录已验证过的持仓（避免重复平仓）
        self.validated_positions = set()

    def get_local_time(self):
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

    async def start(self):
        """启动自检服务"""
        if self.running:
            logger.warning("[自检服务] 服务已在运行中")
            return

        self.running = True
        self.task = asyncio.create_task(self._validation_loop())
        logger.info("[自检服务] ✅ 开单自检服务已启动")

    async def stop(self):
        """停止自检服务"""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("[自检服务] 🛑 开单自检服务已停止")

    async def _validation_loop(self):
        """自检主循环"""
        while self.running:
            try:
                await self._check_new_positions()
            except Exception as e:
                logger.error(f"[自检服务] 检查循环出错: {e}")

            await asyncio.sleep(self.VALIDATION_CONFIG['check_interval'])

    async def _check_new_positions(self):
        """检查新开的持仓"""
        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            # 获取验证窗口内的新持仓
            validation_window = self.VALIDATION_CONFIG['validation_window']
            min_delay = self.VALIDATION_CONFIG['first_check_delay']

            cursor.execute("""
                SELECT
                    id, symbol, position_side, entry_price, quantity,
                    margin, leverage, entry_signal_type, entry_ema_diff,
                    unrealized_pnl, created_at
                FROM futures_positions
                WHERE account_id = 2
                AND status = 'open'
                AND created_at > NOW() - INTERVAL %s SECOND
                AND created_at < NOW() - INTERVAL %s SECOND
                ORDER BY created_at DESC
            """, (validation_window, min_delay))

            positions = cursor.fetchall()

            for position in positions:
                position_id = position['id']

                # 跳过已验证过的持仓
                if position_id in self.validated_positions:
                    continue

                # 验证持仓
                result = await self.validate_position(position)

                if result['should_close']:
                    await self.close_invalid_position(position, result['issues'])
                    self.validated_positions.add(position_id)
                elif result['issues']:
                    # 有问题但不够严重，记录警告
                    logger.warning(f"[自检服务] ⚠️ {position['symbol']} 持仓存在问题: {', '.join(result['issues'])}")

        finally:
            cursor.close()
            conn.close()

    async def validate_position(self, position: Dict) -> Dict:
        """
        验证单个持仓的合理性

        Returns:
            {
                'should_close': bool,
                'issues': List[str],
                'score': int  # 问题严重程度得分
            }
        """
        issues = []
        symbol = position['symbol']
        direction = position['position_side'].lower()
        entry_price = float(position['entry_price'])
        entry_ema_diff = float(position['entry_ema_diff']) if position['entry_ema_diff'] else 0
        created_at = position['created_at']

        # 获取当前市场数据
        ema_data = self._get_ema_data(symbol, '15m')
        if not ema_data:
            return {'should_close': False, 'issues': ['无法获取市场数据'], 'score': 0}

        current_price = ema_data['current_price']

        # 计算当前盈亏
        if direction == 'long':
            pnl_pct = (current_price - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - current_price) / entry_price * 100

        # 计算开仓时间
        now = self.get_local_time()
        hold_seconds = (now - created_at).total_seconds()

        # ========== 检查1: 快速止损 ==========
        quick_loss_window = self.VALIDATION_CONFIG['quick_loss_window']
        quick_loss_threshold = self.VALIDATION_CONFIG['quick_loss_threshold']

        if hold_seconds <= quick_loss_window and pnl_pct <= -quick_loss_threshold:
            issues.append(f"快速亏损({pnl_pct:.2f}%在{hold_seconds:.0f}秒内)")

        # ========== 检查2: 震荡市追单 (已移至开仓前检查) ==========
        # is_ranging, reason = self._check_ranging_market(symbol, ema_data)
        # if is_ranging:
        #     issues.append(reason)

        # ========== 检查3: 趋势末端开仓 (已移至开仓前检查) ==========
        # is_exhausted, reason = self._check_trend_exhaustion(symbol, direction, entry_price, ema_data)
        # if is_exhausted:
        #     issues.append(reason)

        # ========== 检查4: 逆势开仓（价格立即反向）==========
        is_reversal, reason = self._check_immediate_reversal(position, current_price, hold_seconds)
        if is_reversal:
            issues.append(reason)

        # ========== 检查5: 信号强度衰减 ==========
        is_decayed, reason = self._check_signal_decay(entry_ema_diff, ema_data)
        if is_decayed:
            issues.append(reason)

        # ========== 检查6: 多周期不一致 (暂时禁用，下行周期无法开多) ==========
        # is_inconsistent, reason = self._check_multi_timeframe_consistency(symbol, direction)
        # if is_inconsistent:
        #     issues.append(reason)

        # 决定是否平仓
        min_issues = self.VALIDATION_CONFIG['min_issues_to_close']
        should_close = len(issues) >= min_issues

        # 快速亏损单独触发平仓
        if hold_seconds <= quick_loss_window and pnl_pct <= -quick_loss_threshold:
            should_close = True

        return {
            'should_close': should_close,
            'issues': issues,
            'score': len(issues)
        }

    def _check_ranging_market(self, symbol: str, ema_data: Dict) -> Tuple[bool, str]:
        """
        检测震荡市追单

        条件：
        - 最近8根K线的价格波动 < 1%
        - EMA差值 < 0.15%
        """
        klines = ema_data.get('klines', [])
        if len(klines) < 8:
            return False, ""

        # 取最近8根K线
        recent_klines = klines[-8:]
        highs = [float(k['high_price']) for k in recent_klines]
        lows = [float(k['low_price']) for k in recent_klines]

        max_high = max(highs)
        min_low = min(lows)
        volatility = (max_high - min_low) / min_low * 100 if min_low > 0 else 0

        ema_diff_pct = ema_data.get('ema_diff_pct', 0)

        ranging_threshold = self.VALIDATION_CONFIG['ranging_volatility']

        if volatility < ranging_threshold and ema_diff_pct < 0.15:
            return True, f"震荡市追单(波动{volatility:.2f}%,EMA差{ema_diff_pct:.2f}%)"

        return False, ""

    def _check_trend_exhaustion(self, symbol: str, direction: str, entry_price: float,
                                 ema_data: Dict) -> Tuple[bool, str]:
        """
        检测趋势末端开仓

        条件：
        - 做多时：价格接近近期高点（距离 < 0.3%）
        - 做空时：价格接近近期低点（距离 < 0.3%）
        """
        klines = ema_data.get('klines', [])
        if len(klines) < 20:
            return False, ""

        # 取最近20根K线
        recent_klines = klines[-20:]
        highs = [float(k['high_price']) for k in recent_klines]
        lows = [float(k['low_price']) for k in recent_klines]

        max_high = max(highs)
        min_low = min(lows)

        threshold = self.VALIDATION_CONFIG['trend_exhaustion_threshold']

        if direction == 'long':
            # 做多时检查是否接近高点
            distance_to_high = (max_high - entry_price) / entry_price * 100
            if distance_to_high < threshold:
                return True, f"趋势末端做多(距高点{distance_to_high:.2f}%)"
        else:
            # 做空时检查是否接近低点
            distance_to_low = (entry_price - min_low) / entry_price * 100
            if distance_to_low < threshold:
                return True, f"趋势末端做空(距低点{distance_to_low:.2f}%)"

        return False, ""

    def _check_immediate_reversal(self, position: Dict, current_price: float,
                                   hold_seconds: float) -> Tuple[bool, str]:
        """
        检测开仓后立即反向

        条件：
        - 开仓后2分钟内
        - 价格反向移动超过0.3%
        """
        if hold_seconds > self.VALIDATION_CONFIG['quick_loss_window']:
            return False, ""

        direction = position['position_side'].lower()
        entry_price = float(position['entry_price'])

        threshold = self.VALIDATION_CONFIG['immediate_reversal_threshold']

        if direction == 'long':
            # 做多后价格下跌
            change_pct = (current_price - entry_price) / entry_price * 100
            if change_pct < -threshold:
                return True, f"开仓后立即下跌({change_pct:.2f}%)"
        else:
            # 做空后价格上涨
            change_pct = (entry_price - current_price) / entry_price * 100
            if change_pct < -threshold:
                return True, f"开仓后立即上涨({-change_pct:.2f}%)"

        return False, ""

    def _check_signal_decay(self, entry_ema_diff: float, ema_data: Dict) -> Tuple[bool, str]:
        """
        检测信号强度衰减

        条件：
        - EMA差值相比开仓时收窄超过30%
        """
        if entry_ema_diff <= 0:
            return False, ""

        current_ema_diff = ema_data.get('ema_diff_pct', 0)
        decay_threshold = self.VALIDATION_CONFIG['signal_decay_threshold']

        decay_pct = (entry_ema_diff - current_ema_diff) / entry_ema_diff * 100

        if decay_pct > decay_threshold:
            return True, f"信号衰减({decay_pct:.0f}%,{entry_ema_diff:.2f}%→{current_ema_diff:.2f}%)"

        return False, ""

    def _check_multi_timeframe_consistency(self, symbol: str, direction: str) -> Tuple[bool, str]:
        """
        检测多周期一致性

        条件：
        - 15M和1H周期的EMA趋势方向不一致
        """
        # 获取1H周期数据
        ema_1h = self._get_ema_data(symbol, '1h')
        if not ema_1h:
            return False, ""

        # 1H周期趋势方向
        ema9_1h = ema_1h.get('ema9', 0)
        ema26_1h = ema_1h.get('ema26', 0)

        if ema26_1h == 0:
            return False, ""

        trend_1h = 'long' if ema9_1h > ema26_1h else 'short'

        if direction != trend_1h:
            return True, f"多周期不一致(15M:{direction},1H:{trend_1h})"

        return False, ""

    def validate_before_open(self, symbol: str, direction: str) -> Dict:
        """
        开仓前验证（在开仓前调用，检查是否应该阻止开仓）

        Args:
            symbol: 交易对
            direction: 'long' 或 'short'

        Returns:
            {
                'allow_open': True/False,  # 是否允许开仓
                'issues': [],              # 问题列表
                'reason': ''               # 拒绝原因（如果不允许开仓）
            }
        """
        issues = []

        # 获取15M市场数据
        ema_data = self._get_ema_data(symbol, '15m')
        if not ema_data:
            return {'allow_open': True, 'issues': [], 'reason': ''}  # 无法获取数据时允许开仓

        current_price = ema_data['current_price']

        # ========== 检查1: 震荡市 ==========
        is_ranging, reason = self._check_ranging_market(symbol, ema_data)
        if is_ranging:
            issues.append(reason)

        # ========== 检查2: 趋势末端 ==========
        is_exhausted, reason = self._check_trend_exhaustion(symbol, direction, current_price, ema_data)
        if is_exhausted:
            issues.append(reason)

        # ========== 检查3: 多周期不一致 (不作为检查条件，下行周期无法开多) ==========
        # is_inconsistent, reason = self._check_multi_timeframe_consistency(symbol, direction)
        # if is_inconsistent:
        #     issues.append(reason)

        # 决定是否允许开仓（任意1个问题就阻止）
        allow_open = len(issues) == 0

        result = {
            'allow_open': allow_open,
            'issues': issues,
            'reason': "; ".join(issues) if not allow_open else ''
        }

        if not allow_open:
            logger.warning(f"[开仓前检查] 🚫 {symbol} {direction} 被拦截: {issues}")
        elif issues:
            logger.info(f"[开仓前检查] ⚠️ {symbol} {direction} 存在问题但允许开仓: {issues}")

        return result

    def _get_ema_data(self, symbol: str, timeframe: str, limit: int = 100) -> Optional[Dict]:
        """获取EMA数据"""
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
            ema9_values = self._calculate_ema(close_prices, 9)
            ema26_values = self._calculate_ema(close_prices, 26)
            ma10_values = self._calculate_ma(close_prices, 10)

            if not ema9_values or not ema26_values or not ma10_values:
                return None

            ema9 = ema9_values[-1]
            ema26 = ema26_values[-1]
            ma10 = ma10_values[-1]
            current_price = close_prices[-1]

            ema_diff = ema9 - ema26
            ema_diff_pct = abs(ema_diff) / ema26 * 100 if ema26 != 0 else 0

            return {
                'ema9': ema9,
                'ema26': ema26,
                'ema_diff': ema_diff,
                'ema_diff_pct': ema_diff_pct,
                'ma10': ma10,
                'current_price': current_price,
                'klines': klines
            }

        finally:
            cursor.close()
            conn.close()

    def _calculate_ema(self, prices: List[float], period: int) -> List[float]:
        """计算EMA"""
        if len(prices) < period:
            return []

        multiplier = 2 / (period + 1)
        ema_values = [sum(prices[:period]) / period]

        for price in prices[period:]:
            ema = (price - ema_values[-1]) * multiplier + ema_values[-1]
            ema_values.append(ema)

        return ema_values

    def _calculate_ma(self, prices: List[float], period: int) -> List[float]:
        """计算MA"""
        if len(prices) < period:
            return []

        ma_values = []
        for i in range(period - 1, len(prices)):
            ma = sum(prices[i - period + 1:i + 1]) / period
            ma_values.append(ma)

        return ma_values

    async def close_invalid_position(self, position: Dict, reasons: List[str]):
        """平仓不合理的持仓"""
        position_id = position['id']
        symbol = position['symbol']

        reason_str = "自检平仓: " + "; ".join(reasons)

        logger.warning(f"[自检服务] 🚫 {symbol} 触发自检平仓: {reasons}")

        if self.futures_engine:
            result = self.futures_engine.close_position(
                position_id=position_id,
                reason=reason_str
            )

            if result.get('success'):
                logger.info(f"[自检服务] ✅ {symbol} 自检平仓成功")

                # 发送Telegram通知
                if self.trade_notifier:
                    self.trade_notifier.notify_close_position(
                        symbol=symbol,
                        direction=position['position_side'],
                        quantity=float(position['quantity']),
                        entry_price=float(position['entry_price']),
                        exit_price=result.get('close_price', 0),
                        pnl=result.get('realized_pnl', 0),
                        pnl_pct=result.get('pnl_pct', 0),
                        reason=reason_str,
                        is_paper=True
                    )
            else:
                logger.error(f"[自检服务] ❌ {symbol} 自检平仓失败: {result.get('error')}")


# 全局实例
_position_validator: Optional[PositionValidator] = None


def init_position_validator(db_config: Dict, futures_engine=None, trade_notifier=None) -> PositionValidator:
    """初始化开单自检服务"""
    global _position_validator
    _position_validator = PositionValidator(db_config, futures_engine, trade_notifier)
    return _position_validator


def get_position_validator() -> Optional[PositionValidator]:
    """获取开单自检服务实例"""
    return _position_validator
