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
    TRAILING_CALLBACK = 1.0  # 移动止盈回撤 (%)
    MAX_TAKE_PROFIT = 8.0  # 最大止盈 (%)

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
        entry_price = float(position.get('entry_price', 0))
        position_side = position.get('position_side', 'LONG')
        max_profit_pct = float(position.get('max_profit_pct', 0))
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
        entry_price = float(position.get('entry_price', 0))
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

    # ==================== 开仓执行 ====================

    async def execute_open_position(self, symbol: str, direction: str, signal_type: str,
                                     strategy: Dict, account_id: int = 2) -> Dict:
        """
        执行开仓

        Args:
            symbol: 交易对
            direction: 'long' 或 'short'
            signal_type: 信号类型
            strategy: 策略配置
            account_id: 账户ID

        Returns:
            执行结果
        """
        try:
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
                result = self.futures_engine.open_position(
                    symbol=symbol,
                    direction=direction,
                    quantity=quantity,
                    leverage=leverage,
                    account_id=account_id,
                    stop_loss_pct=self.HARD_STOP_LOSS,
                    take_profit_pct=self.MAX_TAKE_PROFIT,
                    signal_type=signal_type
                )

                if result.get('success'):
                    position_id = result.get('position_id')

                    # 更新开仓时的EMA差值
                    conn = self.get_db_connection()
                    cursor = conn.cursor()
                    try:
                        cursor.execute("""
                            UPDATE futures_positions
                            SET entry_signal_type = %s, entry_ema_diff = %s
                            WHERE id = %s
                        """, (signal_type, ema_diff_pct, position_id))
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

        # 2. 检查现有持仓，处理平仓
        positions = self._get_open_positions(symbol, account_id)
        close_results = []

        for position in positions:
            close_needed, close_reason, updates = False, "", {}

            # 2.1 检查金叉/死叉反转
            close_needed, close_reason = self.check_cross_reversal(position, ema_data)

            # 2.2 检查移动止盈
            if not close_needed:
                close_needed, close_reason, updates = self.check_trailing_stop(position, current_price)

            # 2.3 检查硬止损
            if not close_needed:
                close_needed, close_reason = self.check_hard_stop_loss(position, current_price)

            # 更新持仓信息（如最高盈利）
            if updates:
                self._update_position(position['id'], updates)

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
                    open_result = await self.execute_open_position(
                        symbol, signal, 'golden_cross' if signal == 'long' else 'death_cross',
                        strategy, account_id
                    )

            # 3.2 检查连续趋势信号
            if not open_result or not open_result.get('success'):
                signal, signal_desc = self.check_sustained_trend(symbol)
                debug_info.append(f"连续趋势: {signal_desc}")

                if signal and signal in buy_directions:
                    open_result = await self.execute_open_position(
                        symbol, signal, 'sustained_trend', strategy, account_id
                    )

            # 3.3 检查震荡反向信号
            if not open_result or not open_result.get('success'):
                signal, signal_desc = self.check_oscillation_reversal(symbol)
                debug_info.append(f"震荡反向: {signal_desc}")

                if signal and signal in buy_directions:
                    open_result = await self.execute_open_position(
                        symbol, signal, 'oscillation_reversal', strategy, account_id
                    )

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
                SELECT id, name, strategy_config, account_id, enabled, market_type
                FROM trading_strategies
                WHERE enabled = 1
                ORDER BY id
            """)

            strategies = []
            for row in cursor.fetchall():
                try:
                    import json
                    config = json.loads(row['strategy_config']) if row['strategy_config'] else {}
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
