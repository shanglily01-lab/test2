"""
模拟盘开单自检服务
1. 待开仓自检：信号触发后进入待开仓状态，自检通过后才真正开仓
2. 持仓自检：开仓后进行二次验证，如果发现开单不合理，自动平仓避免损失
"""

import asyncio
import logging
import pymysql
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from decimal import Decimal
import pytz

from app.utils.indicators import calculate_ema, calculate_ma
from app.utils.db import create_connection

logger = logging.getLogger(__name__)


class PositionValidator:
    """模拟盘开单自检服务"""

    # 默认自检配置
    DEFAULT_VALIDATION_CONFIG = {
        # ===== 待开仓自检配置 =====
        'pending_enabled': True,           # 是否启用待开仓自检
        'pending_expire_seconds': 300,     # 待开仓过期时间（5分钟）
        'pending_check_interval': 10,      # 待开仓检查间隔（10秒）
        'pending_max_price_diff': 0.5,     # 最大价格偏差（%）
        'pending_require_ema_confirm': True,  # 是否需要EMA确认
        'pending_require_ma_confirm': True,   # 是否需要MA确认
        'pending_check_ranging': True,     # 是否检查震荡市
        'pending_check_trend_end': True,   # 是否检查趋势末端
        'pending_min_ema_diff_pct': 0.10,  # 最小EMA差值（%），低于此值说明趋势弱，拒绝开仓
        'pending_check_ema_converging': True,  # 是否检查EMA收敛（即将交叉）
        'pending_close_cooldown': 300,     # 平仓后冷却时间（秒），同方向不再开仓
        'pending_max_check_count': 15,     # 最大检查次数，超过直接标记为失败

        # ===== 持仓自检配置 =====
        'enabled': True,
        'first_check_delay': 30,           # 首次检查延迟（秒）
        'check_interval': 30,              # 检查间隔（秒）
        'validation_window': 900,          # 验证窗口（15分钟）
        # quick_loss 已移除，使用固定止损和移动止盈代替
        'ranging_volatility': 1.0,         # 震荡市波动阈值（%）
        'trend_exhaustion_threshold': 0.3, # 趋势末端阈值（%）
        'signal_decay_threshold': 30,      # 信号衰减阈值（%）
        'immediate_reversal_threshold': 0.3,  # 逆势阈值（%）
        'min_issues_to_close': 2,          # 触发平仓的最小问题数
    }

    LOCAL_TZ = pytz.timezone('Asia/Shanghai')

    def __init__(self, db_config: Dict, futures_engine=None, trade_notifier=None, strategy_executor=None):
        """
        初始化自检服务

        Args:
            db_config: 数据库配置
            futures_engine: 模拟盘交易引擎
            trade_notifier: Telegram 通知服务
            strategy_executor: 策略执行器（用于执行真正的开仓）
        """
        self.db_config = db_config
        self.futures_engine = futures_engine
        self.trade_notifier = trade_notifier
        self.strategy_executor = strategy_executor
        self.running = False

        # 检查 live_engine 绑定状态
        if futures_engine:
            live_engine_bound = hasattr(futures_engine, 'live_engine') and futures_engine.live_engine is not None
            logger.info(f"[自检服务] 初始化完成, futures_engine={futures_engine is not None}, live_engine绑定={live_engine_bound}")
        self.task = None
        self.pending_task = None  # 待开仓自检任务
        # 记录已验证过的持仓（避免重复平仓）
        self.validated_positions = set()
        # 自检配置（可从策略配置中覆盖）
        self.validation_config = self.DEFAULT_VALIDATION_CONFIG.copy()

    def get_local_time(self):
        """获取UTC时间"""
        return datetime.utcnow()

    def get_db_connection(self):
        """获取数据库连接"""
        return create_connection(self.db_config)

    def set_strategy_executor(self, executor):
        """设置策略执行器"""
        self.strategy_executor = executor

    def update_config(self, config: Dict):
        """更新自检配置"""
        if config:
            self.validation_config.update(config)
            logger.info(f"[自检服务] 配置已更新: {config}")

    async def start(self):
        """启动自检服务"""
        if self.running:
            logger.warning("[自检服务] 服务已在运行中")
            return

        self.running = True
        # 启动持仓自检循环
        self.task = asyncio.create_task(self._validation_loop())
        # 启动待开仓自检循环
        self.pending_task = asyncio.create_task(self._pending_validation_loop())
        logger.info("[自检服务] ✅ 开单自检服务已启动（含待开仓自检）")

    async def stop(self):
        """停止自检服务"""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.pending_task:
            self.pending_task.cancel()
            try:
                await self.pending_task
            except asyncio.CancelledError:
                pass
        logger.info("[自检服务] 🛑 开单自检服务已停止")

    async def _validation_loop(self):
        """持仓自检主循环"""
        while self.running:
            try:
                await self._check_new_positions()
            except Exception as e:
                logger.error(f"[持仓自检] 检查循环出错: {e}")

            await asyncio.sleep(self.validation_config.get('check_interval', 30))

    async def _pending_validation_loop(self):
        """待开仓自检主循环"""
        while self.running:
            try:
                if self.validation_config.get('pending_enabled', True):
                    await self._check_pending_positions()
            except Exception as e:
                logger.error(f"[待开仓自检] 检查循环出错: {e}")

            await asyncio.sleep(self.validation_config.get('pending_check_interval', 10))

    async def _check_new_positions(self):
        """检查新开的持仓"""
        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            # 获取验证窗口内的新持仓
            validation_window = self.validation_config['validation_window']
            min_delay = self.validation_config['first_check_delay']

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
        now = self.datetime.utcnow()
        hold_seconds = (now - created_at).total_seconds()

        # ========== 检查1: 震荡市追单 (已移至开仓前检查) ==========
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
        min_issues = self.validation_config['min_issues_to_close']
        should_close = len(issues) >= min_issues

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

        ranging_threshold = self.validation_config['ranging_volatility']

        if volatility < ranging_threshold and ema_diff_pct < 0.15:
            return True, f"ranging(vol{volatility:.2f}%,ema{ema_diff_pct:.2f}%)"

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

        threshold = self.validation_config['trend_exhaustion_threshold']

        if direction == 'long':
            # Near high when going long
            distance_to_high = (max_high - entry_price) / entry_price * 100
            if distance_to_high < threshold:
                return True, f"near_high({distance_to_high:.2f}%)"
        else:
            # Near low when going short
            distance_to_low = (entry_price - min_low) / entry_price * 100
            if distance_to_low < threshold:
                return True, f"near_low({distance_to_low:.2f}%)"

        return False, ""

    def _check_immediate_reversal(self, position: Dict, current_price: float,
                                   hold_seconds: float) -> Tuple[bool, str]:
        """
        检测开仓后立即反向

        条件：
        - 开仓后2分钟内
        - 价格反向移动超过0.3%
        """
        # 固定2分钟窗口
        if hold_seconds > 120:
            return False, ""

        direction = position['position_side'].lower()
        entry_price = float(position['entry_price'])

        threshold = self.validation_config['immediate_reversal_threshold']

        if direction == 'long':
            # 做多后价格下跌
            change_pct = (current_price - entry_price) / entry_price * 100
            if change_pct < -threshold:
                return True, f"immediate_drop({change_pct:.2f}%)"
        else:
            # 做空后价格上涨
            change_pct = (entry_price - current_price) / entry_price * 100
            if change_pct < -threshold:
                return True, f"immediate_rise({-change_pct:.2f}%)"

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
        decay_threshold = self.validation_config['signal_decay_threshold']

        decay_pct = (entry_ema_diff - current_ema_diff) / entry_ema_diff * 100

        if decay_pct > decay_threshold:
            return True, f"signal_decay({decay_pct:.0f}%,{entry_ema_diff:.2f}%->{current_ema_diff:.2f}%)"

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
            return True, f"mtf_mismatch(15M:{direction},1H:{trend_1h})"

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
        """计算EMA - 委托给公共模块"""
        return calculate_ema(prices, period)

    def _calculate_ma(self, prices: List[float], period: int) -> List[float]:
        """计算MA - 委托给公共模块"""
        return calculate_ma(prices, period)

    async def close_invalid_position(self, position: Dict, reasons: List[str]):
        """平仓不合理的持仓"""
        position_id = position['id']
        symbol = position['symbol']

        reason_str = "validation_close: " + "; ".join(reasons)

        # 检查 live_engine 绑定状态
        live_engine_bound = hasattr(self.futures_engine, 'live_engine') and self.futures_engine.live_engine is not None if self.futures_engine else False
        logger.warning(f"[自检服务] 🚫 {symbol} 触发自检平仓: {reasons}, live_engine绑定={live_engine_bound}")

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

    def _check_close_cooldown(self, symbol: str, direction: str, strategy_id: int,
                               cooldown_seconds: int) -> Tuple[bool, str]:
        """
        检查是否在平仓冷却期内

        如果同交易对同方向在冷却时间内有平仓订单，返回True拒绝开仓
        避免"平仓-开仓-平仓"的循环

        Args:
            symbol: 交易对
            direction: 方向 'long' 或 'short'
            strategy_id: 策略ID
            cooldown_seconds: 冷却时间（秒）

        Returns:
            (是否在冷却期, 原因)
        """
        # 将 direction 转换为订单表的 side 格式
        close_side = f"CLOSE_{direction.upper()}"

        # 计算时间阈值（使用Python计算，避免MySQL函数）
        now = self.datetime.utcnow()
        time_threshold = now - timedelta(seconds=cooldown_seconds)

        # 带重试的查询
        max_retries = 2
        for attempt in range(max_retries):
            conn = None
            cursor = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()

                # 使用参数化时间阈值，避免MySQL NOW()函数
                cursor.execute("""
                    SELECT id, created_at, price
                    FROM futures_orders
                    WHERE account_id = 2
                    AND symbol = %s
                    AND side = %s
                    AND status = 'FILLED'
                    AND created_at > %s
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (symbol, close_side, time_threshold))

                recent_close = cursor.fetchone()

                if recent_close:
                    close_time = recent_close['created_at']
                    elapsed = (now - close_time).total_seconds()
                    remaining = cooldown_seconds - elapsed
                    return True, f"cooldown({remaining:.0f}s)"

                return False, ""

            except pymysql.err.OperationalError as e:
                # 连接超时或丢失，重试
                if attempt < max_retries - 1:
                    logger.warning(f"[待开仓自检] 检查平仓冷却连接异常，重试中({attempt+1}/{max_retries}): {e}")
                    continue
                else:
                    logger.error(f"[待开仓自检] 检查平仓冷却重试失败: {e}")
                    return False, ""
            except Exception as e:
                logger.error(f"[待开仓自检] 检查平仓冷却出错: {e}")
                return False, ""
            finally:
                if cursor:
                    cursor.close()
                if conn:
                    conn.close()

        return False, ""

    # ==================== 待开仓自检相关方法 ====================

    async def _check_pending_positions(self):
        """检查待开仓的信号"""
        expire_seconds = self.validation_config.get('pending_expire_seconds', 300)

        # 计算时间阈值（使用Python计算，避免MySQL函数）
        now = self.datetime.utcnow()
        expire_threshold = now - timedelta(seconds=expire_seconds)

        # 带重试的数据库操作
        max_retries = 2
        pending_list = []

        for attempt in range(max_retries):
            conn = None
            cursor = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()

                # 1. 先处理过期的待开仓（超过5分钟）
                cursor.execute("""
                    UPDATE pending_positions
                    SET status = 'expired', rejection_reason = 'expired_without_passing'
                    WHERE status = 'pending'
                    AND created_at < %s
                """, (expire_threshold,))
                expired_count = cursor.rowcount
                if expired_count > 0:
                    conn.commit()
                    logger.info(f"[待开仓自检] {expired_count} 个待开仓信号已过期")

                # 2. 获取待自检的信号
                cursor.execute("""
                    SELECT
                        id, symbol, direction, signal_type, signal_price,
                        signal_ema9, signal_ema26, signal_ema_diff_pct,
                        signal_reason, strategy_id, account_id, leverage, margin_pct,
                        validation_count, created_at
                    FROM pending_positions
                    WHERE status = 'pending'
                    AND created_at > %s
                    ORDER BY created_at ASC
                """, (expire_threshold,))

                pending_list = cursor.fetchall()
                break  # 成功，退出重试循环

            except pymysql.err.OperationalError as e:
                # 连接超时或丢失，重试
                if attempt < max_retries - 1:
                    logger.warning(f"[待开仓自检] 数据库连接异常，重试中({attempt+1}/{max_retries}): {e}")
                    continue
                else:
                    logger.error(f"[待开仓自检] 数据库连接重试失败: {e}")
                    return
            except Exception as e:
                logger.error(f"[待开仓自检] 检查出错: {e}")
                return
            finally:
                if cursor:
                    cursor.close()
                if conn:
                    conn.close()

        # 验证每个待开仓信号
        for pending in pending_list:
            await self._validate_pending_position(pending)

    async def _validate_pending_position(self, pending: Dict):
        """验证单个待开仓信号"""
        pending_id = pending['id']
        symbol = pending['symbol']
        direction = pending['direction']
        signal_type = pending.get('signal_type', '')  # 信号类型：golden_cross/death_cross/sustained_trend
        signal_price = float(pending['signal_price'])
        signal_ema_diff_pct = float(pending['signal_ema_diff_pct']) if pending['signal_ema_diff_pct'] else 0

        # 判断是否是金叉/死叉信号（突破信号跳过趋势末端检查）
        is_crossover_signal = signal_type in ('golden_cross', 'death_cross', 'ema_crossover')

        # 获取当前市场数据
        ema_data = self._get_ema_data(symbol, '15m')
        if not ema_data:
            logger.warning(f"[待开仓自检] {symbol} 无法获取市场数据，跳过本次检查")
            return

        current_price = ema_data['current_price']
        current_ema9 = ema_data['ema9']
        current_ema26 = ema_data['ema26']
        current_ema_diff = ema_data['ema_diff']
        current_ema_diff_pct = ema_data['ema_diff_pct']
        ma10 = ema_data['ma10']
        strategy_id = pending['strategy_id']

        issues = []
        checks_passed = True

        # ========== 检查0: 平仓冷却检查 ==========
        # 如果同交易对同方向在冷却时间内有平仓，拒绝开仓
        cooldown_seconds = self.validation_config.get('pending_close_cooldown', 300)
        if cooldown_seconds > 0:
            is_cooling, cooldown_reason = self._check_close_cooldown(symbol, direction, strategy_id, cooldown_seconds)
            if is_cooling:
                issues.append(cooldown_reason)
                checks_passed = False

        # ========== Check 1: Price deviation ==========
        max_price_diff = self.validation_config.get('pending_max_price_diff', 0.5)
        price_diff_pct = abs(current_price - signal_price) / signal_price * 100
        if price_diff_pct > max_price_diff:
            issues.append(f"price_diff({price_diff_pct:.2f}%>{max_price_diff}%)")
            checks_passed = False

        # ========== Check 2: EMA direction ==========
        if self.validation_config.get('pending_require_ema_confirm', True):
            if direction == 'long':
                if current_ema_diff <= 0:  # EMA9 < EMA26
                    issues.append(f"ema_direction(EMA9<EMA26)")
                    checks_passed = False
            else:  # short
                if current_ema_diff >= 0:  # EMA9 > EMA26
                    issues.append(f"ema_direction(EMA9>EMA26)")
                    checks_passed = False

        # ========== Check 3: MA direction ==========
        if self.validation_config.get('pending_require_ma_confirm', True):
            if direction == 'long':
                if current_price < ma10:
                    issues.append(f"price<MA10({current_price:.4f}<{ma10:.4f})")
                    checks_passed = False
            else:  # short
                if current_price > ma10:
                    issues.append(f"price>MA10({current_price:.4f}>{ma10:.4f})")
                    checks_passed = False

        # ========== Check 4: Ranging market ==========
        if self.validation_config.get('pending_check_ranging', True):
            is_ranging, reason = self._check_ranging_market(symbol, ema_data)
            if is_ranging:
                issues.append(reason)
                checks_passed = False

        # ========== Check 5: Trend exhaustion (只对持续趋势信号检查，金叉/死叉跳过) ==========
        if self.validation_config.get('pending_check_trend_end', True) and not is_crossover_signal:
            is_exhausted, reason = self._check_trend_exhaustion(symbol, direction, current_price, ema_data)
            if is_exhausted:
                issues.append(reason)
                checks_passed = False

        # ========== Check 6: EMA trend strength ==========
        min_ema_diff = self.validation_config.get('pending_min_ema_diff_pct', 0.15)
        if abs(current_ema_diff_pct) < min_ema_diff:
            issues.append(f"weak_trend({current_ema_diff_pct:.2f}%<{min_ema_diff}%)")
            checks_passed = False

        # ========== Check 7: EMA converging ==========
        if self.validation_config.get('pending_check_ema_converging', True):
            if signal_ema_diff_pct != 0 and abs(current_ema_diff_pct) < abs(signal_ema_diff_pct) * 0.7:
                decay_pct = (1 - abs(current_ema_diff_pct) / abs(signal_ema_diff_pct)) * 100
                issues.append(f"ema_converging({decay_pct:.0f}%)")
                checks_passed = False

        # 更新自检次数
        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            validation_count = pending['validation_count'] + 1

            if checks_passed:
                # 自检通过，执行真正的开仓
                logger.info(f"[待开仓自检] ✅ {symbol} {direction} 自检通过（第{validation_count}次），准备开仓")
                cursor.execute("""
                    UPDATE pending_positions
                    SET status = 'validated', validation_count = %s, last_validation_time = NOW()
                    WHERE id = %s
                """, (validation_count, pending_id))
                conn.commit()

                # 执行真正的开仓
                await self._execute_validated_open(pending, current_price, ema_data)

            else:
                # 自检未通过
                max_check_count = self.validation_config.get('pending_max_check_count', 15)

                if validation_count >= max_check_count:
                    # 超过最大检查次数，直接标记为失败
                    logger.warning(f"[待开仓自检] ❌ {symbol} {direction} 检查{validation_count}次仍未通过，放弃开仓: {issues}")
                    cursor.execute("""
                        UPDATE pending_positions
                        SET status = 'cancelled', validation_count = %s, last_validation_time = NOW(),
                            rejection_reason = %s
                        WHERE id = %s
                    """, (validation_count, f"failed_after_{validation_count}_checks: " + "; ".join(issues), pending_id))
                else:
                    # 继续等待
                    logger.info(f"[待开仓自检] ⏳ {symbol} {direction} 第{validation_count}次自检未通过: {issues}")
                    cursor.execute("""
                        UPDATE pending_positions
                        SET validation_count = %s, last_validation_time = NOW(), rejection_reason = %s
                        WHERE id = %s
                    """, (validation_count, "; ".join(issues), pending_id))
                conn.commit()

        finally:
            cursor.close()
            conn.close()

    async def _execute_validated_open(self, pending: Dict, current_price: float, ema_data: Dict):
        """执行已验证通过的开仓（调用 strategy_executor 的开仓逻辑）"""
        symbol = pending['symbol']
        direction = pending['direction']
        signal_type = pending['signal_type']
        strategy_id = pending['strategy_id']
        account_id = pending['account_id']
        signal_reason = pending['signal_reason']

        if not self.strategy_executor:
            logger.error(f"[待开仓自检] {symbol} 无法开仓：strategy_executor 未初始化")
            return

        try:
            # 获取策略配置
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT config, sync_live FROM trading_strategies WHERE id = %s
            """, (strategy_id,))
            strategy_row = cursor.fetchone()
            cursor.close()
            conn.close()

            if not strategy_row:
                logger.error(f"[待开仓自检] {symbol} 无法开仓：策略不存在")
                return

            import json
            strategy = json.loads(strategy_row['config']) if strategy_row.get('config') else {}
            strategy['id'] = strategy_id
            # 数据库列 sync_live 优先级高于 JSON config 中的 syncLive
            db_sync_live = strategy_row.get('sync_live')
            if db_sync_live is not None:
                strategy['syncLive'] = bool(db_sync_live)
            # 使用待开仓记录中的杠杆和保证金比例
            strategy['leverage'] = pending['leverage']
            strategy['positionSizePct'] = float(pending['margin_pct']) if pending['margin_pct'] else strategy.get('positionSizePct', 1)

            # 使用待开仓记录中的 account_id（通常是2=实盘）
            account_id = pending.get('account_id', 2)

            # 正向开仓
            result = await self.strategy_executor._do_open_position(
                symbol=symbol,
                direction=direction,
                signal_type=signal_type,
                strategy=strategy,
                account_id=account_id,
                signal_reason=signal_reason or "",
                current_price=current_price,
                ema_data=ema_data
            )

            if result.get('success'):
                logger.info(f"[待开仓自检] ✅ {symbol} {direction} 开仓成功, ID={result.get('position_id')}")
            else:
                logger.error(f"[待开仓自检] ❌ {symbol} {direction} 开仓失败: {result.get('error')}")

        except Exception as e:
            logger.error(f"[待开仓自检] {symbol} 开仓异常: {e}")

    def create_pending_position(self, symbol: str, direction: str, signal_type: str,
                                 signal_price: float, ema_data: Dict, strategy: Dict,
                                 account_id: int = 2, signal_reason: str = "") -> Dict:
        """
        创建待开仓记录

        Args:
            symbol: 交易对
            direction: 方向 'long' 或 'short'
            signal_type: 信号类型
            signal_price: 信号触发时的价格
            ema_data: EMA数据
            strategy: 策略配置
            account_id: 账户ID
            signal_reason: 开仓原因

        Returns:
            {'success': True/False, 'pending_id': xxx, 'error': xxx}
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            strategy_id = strategy.get('id')
            leverage = strategy.get('leverage', 10)
            margin_pct = strategy.get('marginPct', 1)

            # 检查是否已有相同的待开仓信号
            cursor.execute("""
                SELECT id, signal_type FROM pending_positions
                WHERE symbol = %s AND direction = %s AND strategy_id = %s
                AND status = 'pending'
            """, (symbol, direction, strategy_id))

            existing = cursor.fetchone()
            if existing:
                logger.info(f"[待开仓] {symbol} {direction} 已有待开仓信号(ID={existing['id']}, type={existing['signal_type']})，跳过")
                return {'success': False, 'error': '已有相同的待开仓信号'}

            # 插入待开仓记录
            expire_seconds = self.validation_config.get('pending_expire_seconds', 300)
            cursor.execute("""
                INSERT INTO pending_positions
                (symbol, direction, signal_type, signal_price, signal_ema9, signal_ema26,
                 signal_ema_diff_pct, signal_reason, strategy_id, account_id, leverage,
                 margin_pct, status, expired_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending',
                        NOW() + INTERVAL %s SECOND)
            """, (
                symbol, direction, signal_type, signal_price,
                ema_data.get('ema9'), ema_data.get('ema26'), ema_data.get('ema_diff_pct'),
                signal_reason, strategy_id, account_id, leverage, margin_pct, expire_seconds
            ))

            conn.commit()
            pending_id = cursor.lastrowid

            logger.info(f"[待开仓] ✅ {symbol} {direction} 创建待开仓信号, ID={pending_id}, 将在{expire_seconds}秒内完成自检")

            return {'success': True, 'pending_id': pending_id}

        except Exception as e:
            logger.error(f"[待开仓] 创建失败: {e}")
            return {'success': False, 'error': str(e)}
        finally:
            cursor.close()
            conn.close()


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
