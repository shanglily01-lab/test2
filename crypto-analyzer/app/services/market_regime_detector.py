"""
行情类型检测器
自动识别趋势/震荡行情，并为策略执行提供参数建议

行情类型：
- strong_uptrend: 强趋势上涨 (EMA差值>1%, ADX>40)
- weak_uptrend: 弱趋势上涨 (EMA差值0.3-1%, ADX 25-40)
- strong_downtrend: 强趋势下跌 (EMA差值<-1%, ADX>40)
- weak_downtrend: 弱趋势下跌 (EMA差值-0.3 ~ -1%, ADX 25-40)
- ranging: 震荡行情 (EMA差值<0.3%, ADX<25)
"""

import logging
import pymysql
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import json

logger = logging.getLogger(__name__)


class MarketRegimeDetector:
    """行情类型检测器"""

    # 行情类型常量
    STRONG_UPTREND = 'strong_uptrend'
    WEAK_UPTREND = 'weak_uptrend'
    STRONG_DOWNTREND = 'strong_downtrend'
    WEAK_DOWNTREND = 'weak_downtrend'
    RANGING = 'ranging'

    # 阈值配置
    STRONG_TREND_EMA_DIFF = 1.0  # 强趋势EMA差值阈值(%)
    WEAK_TREND_EMA_DIFF = 0.3    # 弱趋势EMA差值阈值(%)
    STRONG_ADX_THRESHOLD = 40    # 强趋势ADX阈值
    WEAK_ADX_THRESHOLD = 25      # 弱趋势ADX阈值
    MIN_TREND_BARS = 3           # 趋势确认最小K线数

    # 滞后机制配置 - 防止频繁切换
    HYSTERESIS_SCORE = 5.0       # 切换需要超过的得分差距
    MIN_REGIME_DURATION = 3      # 新状态需要持续的检测次数

    def __init__(self, db_config: Dict):
        """
        初始化检测器

        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config
        # 状态缓存：记录每个交易对的上一次状态
        self._regime_cache = {}  # {symbol_timeframe: {'type': str, 'score': float, 'count': int}}

    def detect_regime(self, symbol: str, timeframe: str = '15m',
                      kline_data: List[Dict] = None) -> Dict:
        """
        检测单个交易对的行情类型

        Args:
            symbol: 交易对符号
            timeframe: 时间周期
            kline_data: K线数据（可选，如果不提供则从数据库获取）

        Returns:
            行情检测结果
        """
        try:
            # 如果没有提供K线数据，从数据库获取
            if kline_data is None:
                kline_data = self._get_kline_data(symbol, timeframe)

            if not kline_data or len(kline_data) < 30:
                return {
                    'symbol': symbol,
                    'timeframe': timeframe,
                    'regime_type': self.RANGING,
                    'regime_score': 0,
                    'error': 'Insufficient data'
                }

            # 计算技术指标
            indicators = self._calculate_indicators(kline_data)

            # 判断行情类型（原始判断）
            raw_regime_type, regime_score = self._classify_regime(indicators)

            # 应用滞后机制防止频繁切换
            regime_type = self._apply_hysteresis(symbol, timeframe, raw_regime_type, regime_score)

            # 构建结果
            result = {
                'symbol': symbol,
                'timeframe': timeframe,
                'regime_type': regime_type,
                'regime_score': regime_score,
                'ema_diff_pct': indicators.get('ema_diff_pct', 0),
                'adx_value': indicators.get('adx', 0),
                'trend_bars': indicators.get('trend_bars', 0),
                'volatility': indicators.get('volatility', 0),
                'detected_at': datetime.now(),
                'details': {
                    'ema9': indicators.get('ema9'),
                    'ema26': indicators.get('ema26'),
                    'ma10': indicators.get('ma10'),
                    'ema10': indicators.get('ema10'),
                    'rsi': indicators.get('rsi'),
                    'price': indicators.get('current_price'),
                    'trend_direction': indicators.get('trend_direction'),
                    'price_position': indicators.get('price_position')
                }
            }

            # 保存到数据库
            self._save_regime(result)

            return result

        except Exception as e:
            logger.error(f"检测 {symbol} [{timeframe}] 行情类型失败: {e}")
            return {
                'symbol': symbol,
                'timeframe': timeframe,
                'regime_type': self.RANGING,
                'regime_score': 0,
                'error': str(e)
            }

    def _calculate_indicators(self, kline_data: List[Dict]) -> Dict:
        """计算技术指标"""
        closes = [float(k.get('close', k.get('close_price', 0))) for k in kline_data]
        highs = [float(k.get('high', k.get('high_price', 0))) for k in kline_data]
        lows = [float(k.get('low', k.get('low_price', 0))) for k in kline_data]

        if not closes or len(closes) < 26:
            return {}

        # 计算EMA
        ema9 = self._calculate_ema(closes, 9)
        ema26 = self._calculate_ema(closes, 26)
        ema10 = self._calculate_ema(closes, 10)

        # 计算MA10
        ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else closes[-1]

        # EMA差值百分比
        ema_diff_pct = (ema9 - ema26) / ema26 * 100 if ema26 != 0 else 0

        # 计算ADX (简化版)
        adx = self._calculate_adx(highs, lows, closes, 14)

        # 计算RSI
        rsi = self._calculate_rsi(closes, 14)

        # 计算波动率 (ATR百分比)
        atr = self._calculate_atr(highs, lows, closes, 14)
        volatility = (atr / closes[-1] * 100) if closes[-1] != 0 else 0

        # 判断趋势持续K线数
        trend_bars = self._count_trend_bars(closes, ema9, ema26)

        # 趋势方向
        if ema9 > ema26:
            trend_direction = 'bullish'
        elif ema9 < ema26:
            trend_direction = 'bearish'
        else:
            trend_direction = 'neutral'

        # 价格相对EMA的位置
        current_price = closes[-1]
        if current_price > ema9:
            price_position = 'above_ema'
        elif current_price < ema9:
            price_position = 'below_ema'
        else:
            price_position = 'at_ema'

        return {
            'ema9': ema9,
            'ema26': ema26,
            'ema10': ema10,
            'ma10': ma10,
            'ema_diff_pct': ema_diff_pct,
            'adx': adx,
            'rsi': rsi,
            'volatility': volatility,
            'trend_bars': trend_bars,
            'trend_direction': trend_direction,
            'price_position': price_position,
            'current_price': current_price
        }

    def _classify_regime(self, indicators: Dict) -> Tuple[str, float]:
        """
        根据指标判断行情类型

        Returns:
            (regime_type, regime_score)
            regime_score: -100 到 100，正为多头倾向，负为空头倾向
        """
        ema_diff_pct = indicators.get('ema_diff_pct', 0)
        adx = indicators.get('adx', 0)
        trend_bars = indicators.get('trend_bars', 0)
        rsi = indicators.get('rsi', 50)

        # 计算行情得分
        # 基础得分来自EMA差值
        base_score = ema_diff_pct * 20  # 放大EMA差值的影响

        # ADX调整（趋势强度）
        if adx > self.STRONG_ADX_THRESHOLD:
            adx_multiplier = 1.5
        elif adx > self.WEAK_ADX_THRESHOLD:
            adx_multiplier = 1.2
        else:
            adx_multiplier = 0.8  # 震荡时降低得分

        # RSI调整
        rsi_adjustment = 0
        if rsi > 70:
            rsi_adjustment = -10  # 超买，可能回调
        elif rsi < 30:
            rsi_adjustment = 10   # 超卖，可能反弹

        # 趋势持续性调整
        if trend_bars >= self.MIN_TREND_BARS:
            trend_adjustment = 10 if ema_diff_pct > 0 else -10
        else:
            trend_adjustment = 0

        # 最终得分
        regime_score = (base_score * adx_multiplier) + rsi_adjustment + trend_adjustment
        regime_score = max(-100, min(100, regime_score))  # 限制范围

        # 判断行情类型
        abs_ema_diff = abs(ema_diff_pct)

        if ema_diff_pct > 0:
            # 多头方向
            if abs_ema_diff >= self.STRONG_TREND_EMA_DIFF and adx >= self.STRONG_ADX_THRESHOLD:
                regime_type = self.STRONG_UPTREND
            elif abs_ema_diff >= self.WEAK_TREND_EMA_DIFF or adx >= self.WEAK_ADX_THRESHOLD:
                regime_type = self.WEAK_UPTREND
            else:
                regime_type = self.RANGING
        elif ema_diff_pct < 0:
            # 空头方向
            if abs_ema_diff >= self.STRONG_TREND_EMA_DIFF and adx >= self.STRONG_ADX_THRESHOLD:
                regime_type = self.STRONG_DOWNTREND
            elif abs_ema_diff >= self.WEAK_TREND_EMA_DIFF or adx >= self.WEAK_ADX_THRESHOLD:
                regime_type = self.WEAK_DOWNTREND
            else:
                regime_type = self.RANGING
        else:
            regime_type = self.RANGING

        return regime_type, round(regime_score, 2)

    def _apply_hysteresis(self, symbol: str, timeframe: str,
                          new_regime: str, new_score: float) -> str:
        """
        应用滞后机制防止行情类型频繁切换

        规则：
        1. 从震荡切换到趋势：得分绝对值需要 > 15 (原来是自动切换)
        2. 从趋势切换到震荡：得分绝对值需要 < 10 (原来是自动切换)
        3. 新状态需要连续出现 MIN_REGIME_DURATION 次才确认切换
        """
        cache_key = f"{symbol}_{timeframe}"
        cached = self._regime_cache.get(cache_key)

        # 行情类型分组
        trend_types = {self.STRONG_UPTREND, self.WEAK_UPTREND,
                       self.STRONG_DOWNTREND, self.WEAK_DOWNTREND}

        # 如果没有缓存，直接使用新状态
        if cached is None:
            self._regime_cache[cache_key] = {
                'type': new_regime,
                'score': new_score,
                'count': 1,
                'pending_type': None,
                'pending_count': 0
            }
            return new_regime

        old_regime = cached['type']
        old_score = cached['score']

        # 判断是否需要切换
        should_switch = False
        abs_new_score = abs(new_score)

        # 情况1: 类型相同，更新缓存
        if new_regime == old_regime:
            cached['score'] = new_score
            cached['count'] += 1
            cached['pending_type'] = None
            cached['pending_count'] = 0
            return new_regime

        # 情况2: 从震荡切换到趋势 - 需要得分足够强
        if old_regime == self.RANGING and new_regime in trend_types:
            # 只有当得分绝对值 > 15 才允许切换
            if abs_new_score > 15:
                should_switch = True
            else:
                # 得分不够，维持震荡
                logger.debug(f"[行情滞后] {symbol} 趋势信号不够强 (得分:{new_score:.1f})，维持震荡")
                return old_regime

        # 情况3: 从趋势切换到震荡 - 需要得分足够弱
        elif old_regime in trend_types and new_regime == self.RANGING:
            # 只有当得分绝对值 < 10 才允许切换到震荡
            if abs_new_score < 10:
                should_switch = True
            else:
                # 得分还不够弱，维持趋势
                logger.debug(f"[行情滞后] {symbol} 趋势未完全消失 (得分:{new_score:.1f})，维持{old_regime}")
                return old_regime

        # 情况4: 趋势方向切换（如从看多变看空）- 需要得分差距足够大
        elif old_regime in trend_types and new_regime in trend_types:
            # 检查方向是否改变
            old_is_bull = old_regime in {self.STRONG_UPTREND, self.WEAK_UPTREND}
            new_is_bull = new_regime in {self.STRONG_UPTREND, self.WEAK_UPTREND}

            if old_is_bull != new_is_bull:
                # 方向改变，需要更大的得分差距
                score_diff = abs(new_score - old_score)
                if score_diff > self.HYSTERESIS_SCORE * 2:
                    should_switch = True
                else:
                    logger.debug(f"[行情滞后] {symbol} 方向切换信号不够强 (差距:{score_diff:.1f})，维持{old_regime}")
                    return old_regime
            else:
                # 同方向强度变化，可以直接切换
                should_switch = True

        # 情况5: 其他切换
        else:
            score_diff = abs(new_score - old_score)
            if score_diff > self.HYSTERESIS_SCORE:
                should_switch = True

        # 应用状态持续要求
        if should_switch:
            if cached.get('pending_type') == new_regime:
                cached['pending_count'] += 1
                if cached['pending_count'] >= self.MIN_REGIME_DURATION:
                    # 确认切换
                    logger.info(f"[行情切换] {symbol} {old_regime} → {new_regime} (得分:{new_score:.1f})")
                    cached['type'] = new_regime
                    cached['score'] = new_score
                    cached['count'] = 1
                    cached['pending_type'] = None
                    cached['pending_count'] = 0
                    return new_regime
                else:
                    # 还需要更多确认
                    logger.debug(f"[行情滞后] {symbol} 等待切换确认 ({cached['pending_count']}/{self.MIN_REGIME_DURATION})")
                    return old_regime
            else:
                # 新的待定状态
                cached['pending_type'] = new_regime
                cached['pending_count'] = 1
                return old_regime

        # 不需要切换
        cached['score'] = new_score
        return old_regime

    def _calculate_ema(self, data: List[float], period: int) -> float:
        """计算EMA"""
        if len(data) < period:
            return data[-1] if data else 0

        multiplier = 2 / (period + 1)
        ema = sum(data[:period]) / period  # 初始SMA

        for price in data[period:]:
            ema = (price - ema) * multiplier + ema

        return ema

    def _calculate_adx(self, highs: List[float], lows: List[float],
                       closes: List[float], period: int = 14) -> float:
        """计算ADX (简化版)"""
        if len(closes) < period + 1:
            return 25  # 默认中性值

        # 计算真实波幅和方向移动
        tr_list = []
        plus_dm_list = []
        minus_dm_list = []

        for i in range(1, len(closes)):
            high = highs[i]
            low = lows[i]
            prev_close = closes[i-1]
            prev_high = highs[i-1]
            prev_low = lows[i-1]

            # 真实波幅
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)

            # 方向移动
            plus_dm = max(0, high - prev_high) if high - prev_high > prev_low - low else 0
            minus_dm = max(0, prev_low - low) if prev_low - low > high - prev_high else 0
            plus_dm_list.append(plus_dm)
            minus_dm_list.append(minus_dm)

        if len(tr_list) < period:
            return 25

        # 平滑计算
        atr = sum(tr_list[-period:]) / period
        plus_di = (sum(plus_dm_list[-period:]) / period) / atr * 100 if atr > 0 else 0
        minus_di = (sum(minus_dm_list[-period:]) / period) / atr * 100 if atr > 0 else 0

        # DX
        di_sum = plus_di + minus_di
        dx = abs(plus_di - minus_di) / di_sum * 100 if di_sum > 0 else 0

        return dx

    def _calculate_rsi(self, closes: List[float], period: int = 14) -> float:
        """计算RSI"""
        if len(closes) < period + 1:
            return 50

        gains = []
        losses = []

        for i in range(1, len(closes)):
            change = closes[i] - closes[i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    def _calculate_atr(self, highs: List[float], lows: List[float],
                       closes: List[float], period: int = 14) -> float:
        """计算ATR"""
        if len(closes) < period + 1:
            return 0

        tr_list = []
        for i in range(1, len(closes)):
            high = highs[i]
            low = lows[i]
            prev_close = closes[i-1]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)

        return sum(tr_list[-period:]) / period if tr_list else 0

    def _count_trend_bars(self, closes: List[float], ema9: float, ema26: float) -> int:
        """计算趋势持续的K线数"""
        if len(closes) < 26:
            return 0

        # 重新计算每根K线的EMA值来判断趋势持续时间
        count = 0
        is_bullish = ema9 > ema26

        # 从最近的K线往前数
        ema9_values = []
        ema26_values = []

        # 计算历史EMA
        for i in range(len(closes)):
            if i < 9:
                ema9_val = sum(closes[:i+1]) / (i+1)
            else:
                if not ema9_values:
                    ema9_val = sum(closes[:9]) / 9
                else:
                    multiplier = 2 / 10
                    ema9_val = (closes[i] - ema9_values[-1]) * multiplier + ema9_values[-1]
            ema9_values.append(ema9_val)

            if i < 26:
                ema26_val = sum(closes[:i+1]) / (i+1)
            else:
                if len(ema26_values) < 26:
                    ema26_val = sum(closes[:26]) / 26
                else:
                    multiplier = 2 / 27
                    ema26_val = (closes[i] - ema26_values[-1]) * multiplier + ema26_values[-1]
            ema26_values.append(ema26_val)

        # 从最新往前数连续趋势K线数
        for i in range(len(ema9_values) - 1, 25, -1):
            if is_bullish:
                if ema9_values[i] > ema26_values[i]:
                    count += 1
                else:
                    break
            else:
                if ema9_values[i] < ema26_values[i]:
                    count += 1
                else:
                    break

        return count

    def _get_kline_data(self, symbol: str, timeframe: str) -> List[Dict]:
        """从数据库获取K线数据"""
        try:
            connection = pymysql.connect(
                host=self.db_config.get('host', 'localhost'),
                port=self.db_config.get('port', 3306),
                user=self.db_config.get('user', 'root'),
                password=self.db_config.get('password', ''),
                database=self.db_config.get('database', 'binance-data'),
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )

            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT open_price, high_price, low_price, close_price, volume, timestamp
                    FROM kline_data
                    WHERE symbol = %s AND timeframe = %s AND exchange = 'binance_futures'
                    ORDER BY timestamp DESC
                    LIMIT 100
                """, (symbol, timeframe))
                rows = cursor.fetchall()

            connection.close()

            # 反转为时间正序
            return list(reversed(rows)) if rows else []

        except Exception as e:
            logger.error(f"获取K线数据失败: {e}")
            return []

    def _save_regime(self, result: Dict) -> bool:
        """保存行情检测结果到数据库"""
        try:
            connection = pymysql.connect(
                host=self.db_config.get('host', 'localhost'),
                port=self.db_config.get('port', 3306),
                user=self.db_config.get('user', 'root'),
                password=self.db_config.get('password', ''),
                database=self.db_config.get('database', 'binance-data'),
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )

            with connection.cursor() as cursor:
                # 检查是否需要记录行情切换
                cursor.execute("""
                    SELECT regime_type, regime_score FROM market_regime
                    WHERE symbol = %s AND timeframe = %s
                    ORDER BY detected_at DESC LIMIT 1
                """, (result['symbol'], result['timeframe']))
                last_regime = cursor.fetchone()

                # 如果行情类型发生变化，记录切换日志
                if last_regime and last_regime['regime_type'] != result['regime_type']:
                    cursor.execute("""
                        INSERT INTO market_regime_changes
                        (symbol, timeframe, old_regime, new_regime, old_score, new_score, changed_at)
                        VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    """, (
                        result['symbol'],
                        result['timeframe'],
                        last_regime['regime_type'],
                        result['regime_type'],
                        last_regime['regime_score'],
                        result['regime_score']
                    ))
                    logger.info(f"📊 {result['symbol']} [{result['timeframe']}] 行情切换: "
                               f"{last_regime['regime_type']} → {result['regime_type']}")

                # 插入新的行情记录
                cursor.execute("""
                    INSERT INTO market_regime
                    (symbol, timeframe, regime_type, regime_score, ema_diff_pct,
                     adx_value, trend_bars, volatility, details, detected_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """, (
                    result['symbol'],
                    result['timeframe'],
                    result['regime_type'],
                    result['regime_score'],
                    result.get('ema_diff_pct'),
                    result.get('adx_value'),
                    result.get('trend_bars'),
                    result.get('volatility'),
                    json.dumps(result.get('details', {}), ensure_ascii=False)
                ))

                connection.commit()

            connection.close()
            return True

        except Exception as e:
            logger.error(f"保存行情检测结果失败: {e}")
            return False

    def get_regime_params(self, strategy_id: int, regime_type: str) -> Optional[Dict]:
        """
        获取指定策略在特定行情类型下的参数配置

        Args:
            strategy_id: 策略ID
            regime_type: 行情类型

        Returns:
            参数配置字典，如果不存在则返回None
        """
        try:
            connection = pymysql.connect(
                host=self.db_config.get('host', 'localhost'),
                port=self.db_config.get('port', 3306),
                user=self.db_config.get('user', 'root'),
                password=self.db_config.get('password', ''),
                database=self.db_config.get('database', 'binance-data'),
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )

            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT enabled, params, description
                    FROM strategy_regime_params
                    WHERE strategy_id = %s AND regime_type = %s
                """, (strategy_id, regime_type))
                row = cursor.fetchone()

            connection.close()

            if row:
                params = row['params']
                if isinstance(params, str):
                    params = json.loads(params)
                return {
                    'enabled': row['enabled'],
                    'params': params,
                    'description': row['description']
                }

            return None

        except Exception as e:
            logger.error(f"获取行情参数配置失败: {e}")
            return None

    def get_latest_regime(self, symbol: str, timeframe: str = '15m') -> Optional[Dict]:
        """获取最新的行情类型"""
        try:
            connection = pymysql.connect(
                host=self.db_config.get('host', 'localhost'),
                port=self.db_config.get('port', 3306),
                user=self.db_config.get('user', 'root'),
                password=self.db_config.get('password', ''),
                database=self.db_config.get('database', 'binance-data'),
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )

            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT * FROM market_regime
                    WHERE symbol = %s AND timeframe = %s
                    ORDER BY detected_at DESC LIMIT 1
                """, (symbol, timeframe))
                row = cursor.fetchone()

            connection.close()

            if row and row.get('details'):
                if isinstance(row['details'], str):
                    row['details'] = json.loads(row['details'])

            return row

        except Exception as e:
            logger.error(f"获取最新行情类型失败: {e}")
            return None

    def detect_all_symbols(self, symbols: List[str], timeframe: str = '15m') -> Dict[str, Dict]:
        """
        批量检测多个交易对的行情类型

        Args:
            symbols: 交易对列表
            timeframe: 时间周期

        Returns:
            {symbol: regime_result} 字典
        """
        results = {}
        for symbol in symbols:
            results[symbol] = self.detect_regime(symbol, timeframe)
        return results


def get_regime_display_name(regime_type: str) -> str:
    """获取行情类型的中文显示名称"""
    names = {
        'strong_uptrend': '强趋势上涨 📈',
        'weak_uptrend': '弱趋势上涨 ↗️',
        'strong_downtrend': '强趋势下跌 📉',
        'weak_downtrend': '弱趋势下跌 ↘️',
        'ranging': '震荡行情 ↔️'
    }
    return names.get(regime_type, regime_type)


def get_regime_trading_suggestion(regime_type: str) -> str:
    """获取行情类型对应的交易建议"""
    suggestions = {
        'strong_uptrend': '趋势明确，可积极做多，使用持续趋势信号',
        'weak_uptrend': '趋势较弱，谨慎做多，只在金叉信号时开仓',
        'strong_downtrend': '趋势明确，可积极做空，使用持续趋势信号',
        'weak_downtrend': '趋势较弱，谨慎做空，只在死叉信号时开仓',
        'ranging': '震荡行情，建议观望或降低仓位，等待趋势明确'
    }
    return suggestions.get(regime_type, '未知行情类型')
