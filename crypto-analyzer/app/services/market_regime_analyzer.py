"""
市场状态分析器 - 趋势/震荡判断 + 连续亏损熔断

功能:
1. 趋势/震荡判断：使用 ADX 指标判断市场是趋势还是震荡
2. 连续亏损熔断：连续 N 单亏损后暂停同方向开仓

Author: Claude
Date: 2025-12-20
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple, List
from decimal import Decimal

logger = logging.getLogger(__name__)


class MarketRegimeAnalyzer:
    """市场状态分析器"""

    # 默认参数
    DEFAULT_ADX_PERIOD = 14  # ADX 计算周期
    DEFAULT_ADX_TREND_THRESHOLD = 25  # ADX >= 25 认为是趋势行情
    DEFAULT_ADX_STRONG_TREND_THRESHOLD = 40  # ADX >= 40 认为是强趋势
    DEFAULT_CONSECUTIVE_LOSS_LIMIT = 4  # 连续亏损次数限制
    DEFAULT_COOLDOWN_HOURS = 1  # 熔断冷却时间（小时）

    def __init__(self, db_config: Dict):
        """
        初始化市场状态分析器

        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config
        self.connection = None

        # 熔断状态缓存: {'long': datetime, 'short': datetime}
        # 记录每个方向的熔断解除时间
        self._circuit_breaker_until: Dict[str, datetime] = {}

        # ADX 缓存: {symbol: {'adx': float, 'updated_at': datetime}}
        self._adx_cache: Dict[str, Dict] = {}
        self._adx_cache_ttl = 60  # 缓存有效期（秒）

    def get_db_connection(self):
        """获取数据库连接"""
        import pymysql
        if self.connection is None or not self.connection.open:
            self.connection = pymysql.connect(
                host=self.db_config['host'],
                port=self.db_config.get('port', 3306),
                user=self.db_config['user'],
                password=self.db_config['password'],
                database=self.db_config['database'],
                cursorclass=pymysql.cursors.DictCursor,
                charset='utf8mb4'
            )
        return self.connection

    def get_local_time(self) -> datetime:
        """获取本地时间（新加坡时区 UTC+8）"""
        local_tz = timezone(timedelta(hours=8))
        return datetime.now(local_tz).replace(tzinfo=None)

    # ==================== ADX 趋势判断 ====================

    def calculate_adx(self, symbol: str, period: int = None, timeframe: str = '15m') -> Optional[float]:
        """
        计算 ADX 指标

        ADX (Average Directional Index) 用于判断趋势强度:
        - ADX < 20: 弱趋势/震荡
        - 20 <= ADX < 25: 趋势形成中
        - 25 <= ADX < 40: 趋势行情
        - ADX >= 40: 强趋势

        Args:
            symbol: 交易对
            period: ADX 周期（默认 14）
            timeframe: K线周期（默认 15m）

        Returns:
            ADX 值，计算失败返回 None
        """
        if period is None:
            period = self.DEFAULT_ADX_PERIOD

        # 检查缓存
        cache_key = f"{symbol}_{timeframe}"
        now = self.get_local_time()
        if cache_key in self._adx_cache:
            cached = self._adx_cache[cache_key]
            if (now - cached['updated_at']).total_seconds() < self._adx_cache_ttl:
                return cached['adx']

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            # 需要足够多的数据来计算 ADX（至少 2 * period + 1）
            limit = period * 3 + 10

            # 根据 timeframe 选择表
            if timeframe == '5m':
                table = 'klines_5m'
            elif timeframe == '1h':
                table = 'klines_1h'
            else:
                table = 'klines_15m'

            cursor.execute(f"""
                SELECT high, low, close
                FROM {table}
                WHERE symbol = %s
                ORDER BY open_time DESC
                LIMIT %s
            """, (symbol, limit))

            rows = cursor.fetchall()
            cursor.close()

            if len(rows) < period * 2 + 1:
                logger.warning(f"[ADX] {symbol} 数据不足: {len(rows)} < {period * 2 + 1}")
                return None

            # 转换为列表（注意：数据是倒序的，需要反转）
            rows = list(reversed(rows))
            high = [float(r['high']) for r in rows]
            low = [float(r['low']) for r in rows]
            close = [float(r['close']) for r in rows]

            # 计算 True Range (TR)
            tr = []
            for i in range(1, len(rows)):
                tr1 = high[i] - low[i]
                tr2 = abs(high[i] - close[i - 1])
                tr3 = abs(low[i] - close[i - 1])
                tr.append(max(tr1, tr2, tr3))

            # 计算 +DM 和 -DM
            plus_dm = []
            minus_dm = []
            for i in range(1, len(rows)):
                up_move = high[i] - high[i - 1]
                down_move = low[i - 1] - low[i]

                if up_move > down_move and up_move > 0:
                    plus_dm.append(up_move)
                else:
                    plus_dm.append(0)

                if down_move > up_move and down_move > 0:
                    minus_dm.append(down_move)
                else:
                    minus_dm.append(0)

            # 使用 Wilder's Smoothing（指数平滑）
            def wilder_smooth(data, period):
                result = [0.0] * len(data)
                if len(data) < period:
                    return result
                result[period - 1] = sum(data[:period])
                for i in range(period, len(data)):
                    result[i] = result[i - 1] - (result[i - 1] / period) + data[i]
                return result

            # 平滑 TR, +DM, -DM
            smoothed_tr = wilder_smooth(tr, period)
            smoothed_plus_dm = wilder_smooth(plus_dm, period)
            smoothed_minus_dm = wilder_smooth(minus_dm, period)

            # 计算 +DI 和 -DI
            plus_di = []
            minus_di = []
            for i in range(len(smoothed_tr)):
                if smoothed_tr[i] != 0:
                    plus_di.append(100 * smoothed_plus_dm[i] / smoothed_tr[i])
                    minus_di.append(100 * smoothed_minus_dm[i] / smoothed_tr[i])
                else:
                    plus_di.append(0)
                    minus_di.append(0)

            # 计算 DX
            dx = []
            for i in range(len(plus_di)):
                di_sum = plus_di[i] + minus_di[i]
                di_diff = abs(plus_di[i] - minus_di[i])
                if di_sum != 0:
                    dx.append(100 * di_diff / di_sum)
                else:
                    dx.append(0)

            # 计算 ADX（DX 的平滑）
            dx_for_adx = dx[period - 1:] if len(dx) >= period else dx
            adx = wilder_smooth(dx_for_adx, period)

            # 取最新的 ADX 值
            if len(adx) > 0 and adx[-1] > 0:
                adx_value = float(adx[-1])

                # 更新缓存
                self._adx_cache[cache_key] = {
                    'adx': adx_value,
                    'updated_at': now
                }

                return adx_value

            return None

        except Exception as e:
            logger.error(f"[ADX] {symbol} 计算失败: {e}")
            return None

    def is_trending_market(self, symbol: str, threshold: float = None, timeframe: str = '15m') -> Tuple[bool, str]:
        """
        判断是否是趋势行情

        Args:
            symbol: 交易对
            threshold: ADX 阈值（默认 25）
            timeframe: K线周期

        Returns:
            (是否趋势行情, 描述)
        """
        if threshold is None:
            threshold = self.DEFAULT_ADX_TREND_THRESHOLD

        adx = self.calculate_adx(symbol, timeframe=timeframe)

        if adx is None:
            return True, "ADX计算失败，默认允许开仓"

        if adx >= self.DEFAULT_ADX_STRONG_TREND_THRESHOLD:
            return True, f"强趋势(ADX={adx:.1f} >= {self.DEFAULT_ADX_STRONG_TREND_THRESHOLD})"
        elif adx >= threshold:
            return True, f"趋势行情(ADX={adx:.1f} >= {threshold})"
        else:
            return False, f"震荡行情(ADX={adx:.1f} < {threshold})"

    # ==================== 连续亏损熔断 ====================

    def check_consecutive_losses(self, direction: str, limit: int = None) -> Tuple[bool, int, str]:
        """
        检查是否触发连续亏损熔断

        Args:
            direction: 交易方向 'long' 或 'short'
            limit: 连续亏损次数限制（默认 4）

        Returns:
            (是否触发熔断, 连续亏损次数, 描述)
        """
        if limit is None:
            limit = self.DEFAULT_CONSECUTIVE_LOSS_LIMIT

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            # 查询最近的平仓记录（所有币种），按时间倒序
            position_side = direction.upper()
            cursor.execute("""
                SELECT id, symbol, position_side, realized_pnl, close_time
                FROM futures_positions
                WHERE status = 'closed' AND position_side = %s
                ORDER BY close_time DESC
                LIMIT %s
            """, (position_side, limit + 5))  # 多取几条以防万一

            rows = cursor.fetchall()
            cursor.close()

            if not rows:
                return False, 0, "无历史交易记录"

            # 统计连续亏损次数（从最近开始）
            consecutive_losses = 0
            for row in rows:
                pnl = float(row['realized_pnl'] or 0)
                if pnl < 0:
                    consecutive_losses += 1
                else:
                    break  # 遇到盈利就停止

            if consecutive_losses >= limit:
                return True, consecutive_losses, f"连续{consecutive_losses}单亏损(>={limit})"
            else:
                return False, consecutive_losses, f"连续亏损{consecutive_losses}次(<{limit})"

        except Exception as e:
            logger.error(f"[熔断检查] 查询失败: {e}")
            return False, 0, f"查询失败: {e}"

    def is_circuit_breaker_active(self, direction: str, cooldown_hours: float = None) -> Tuple[bool, str]:
        """
        检查熔断是否生效中

        Args:
            direction: 交易方向 'long' 或 'short'
            cooldown_hours: 冷却时间（小时）

        Returns:
            (是否熔断中, 描述)
        """
        if cooldown_hours is None:
            cooldown_hours = self.DEFAULT_COOLDOWN_HOURS

        now = self.get_local_time()

        # 检查是否在冷却期内
        if direction in self._circuit_breaker_until:
            until = self._circuit_breaker_until[direction]
            if now < until:
                remaining = (until - now).total_seconds() / 60
                return True, f"熔断冷却中，剩余{remaining:.0f}分钟"

        # 检查是否需要触发熔断
        triggered, losses, desc = self.check_consecutive_losses(direction)

        if triggered:
            # 设置熔断解除时间
            self._circuit_breaker_until[direction] = now + timedelta(hours=cooldown_hours)
            logger.warning(f"🚨 [熔断] {direction.upper()} 方向触发熔断: {desc}，冷却{cooldown_hours}小时")
            return True, f"触发熔断: {desc}，冷却{cooldown_hours}小时"

        return False, desc

    def clear_circuit_breaker(self, direction: str = None):
        """
        清除熔断状态

        Args:
            direction: 交易方向，None 表示清除所有
        """
        if direction:
            if direction in self._circuit_breaker_until:
                del self._circuit_breaker_until[direction]
                logger.info(f"[熔断] 已清除 {direction.upper()} 方向熔断状态")
        else:
            self._circuit_breaker_until.clear()
            logger.info("[熔断] 已清除所有熔断状态")

    # ==================== 综合检查 ====================

    def can_open_position(self, symbol: str, direction: str, strategy: Dict = None) -> Tuple[bool, str]:
        """
        综合检查是否可以开仓

        检查项目:
        1. ADX 趋势判断（震荡行情不开仓）
        2. 连续亏损熔断

        Args:
            symbol: 交易对
            direction: 交易方向 'long' 或 'short'
            strategy: 策略配置（可选，用于读取自定义参数）

        Returns:
            (是否可以开仓, 描述)
        """
        # 从策略配置读取参数
        adx_enabled = True
        adx_threshold = self.DEFAULT_ADX_TREND_THRESHOLD
        circuit_breaker_enabled = True
        consecutive_loss_limit = self.DEFAULT_CONSECUTIVE_LOSS_LIMIT
        cooldown_hours = self.DEFAULT_COOLDOWN_HOURS

        if strategy:
            # 市场状态分析配置
            market_regime = strategy.get('marketRegime', {})
            adx_enabled = market_regime.get('adxEnabled', True)
            adx_threshold = market_regime.get('adxThreshold', self.DEFAULT_ADX_TREND_THRESHOLD)

            # 熔断配置
            circuit_breaker = strategy.get('circuitBreaker', {})
            circuit_breaker_enabled = circuit_breaker.get('enabled', True)
            consecutive_loss_limit = circuit_breaker.get('consecutiveLossLimit', self.DEFAULT_CONSECUTIVE_LOSS_LIMIT)
            cooldown_hours = circuit_breaker.get('cooldownHours', self.DEFAULT_COOLDOWN_HOURS)

        # 1. 检查连续亏损熔断（优先级最高）
        if circuit_breaker_enabled:
            is_breaker_active, breaker_desc = self.is_circuit_breaker_active(
                direction, cooldown_hours
            )
            if is_breaker_active:
                return False, f"🚨 熔断: {breaker_desc}"

        # 2. 检查 ADX 趋势判断
        if adx_enabled:
            is_trending, trend_desc = self.is_trending_market(symbol, adx_threshold)
            if not is_trending:
                return False, f"📊 {trend_desc}，不开仓"

        return True, "✅ 通过市场状态检查"

    def get_market_status(self, symbol: str) -> Dict:
        """
        获取市场状态摘要

        Args:
            symbol: 交易对

        Returns:
            市场状态信息
        """
        adx = self.calculate_adx(symbol)
        is_trending, trend_desc = self.is_trending_market(symbol)

        long_breaker, long_desc = self.is_circuit_breaker_active('long')
        short_breaker, short_desc = self.is_circuit_breaker_active('short')

        return {
            'symbol': symbol,
            'adx': adx,
            'is_trending': is_trending,
            'trend_desc': trend_desc,
            'long_circuit_breaker': long_breaker,
            'long_circuit_breaker_desc': long_desc,
            'short_circuit_breaker': short_breaker,
            'short_circuit_breaker_desc': short_desc,
            'timestamp': self.get_local_time().isoformat()
        }
