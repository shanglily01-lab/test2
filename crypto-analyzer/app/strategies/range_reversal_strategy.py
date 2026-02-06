"""
震荡市反转策略（基于实战经验）

核心逻辑:
- 做空: 价格接近4H高点(90%) + 量能萎缩 + 上引线拒绝
- 做多: 价格接近4H低点(110%) + 量能萎缩 + 下引线支撑
"""

import pymysql
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from loguru import logger


class RangeReversalStrategy:
    """震荡市反转策略"""

    def __init__(self, db_config: dict):
        self.db_config = db_config

        # 策略参数
        self.lookback_4h_bars = 16  # 4小时=16根15M K线
        self.price_near_high_threshold = 0.90  # 价格到达4H高点的90%
        self.price_near_low_threshold = 1.10   # 价格到达4H低点的110%

        # 成交量萎缩判断
        self.volume_shrink_threshold = 0.8  # 当前量<均量的80% = 萎缩

        # 引线判断参数
        self.min_wick_body_ratio = 2.0  # 引线至少是实体的2倍
        self.min_wick_pct = 0.3  # 引线至少占总高度的30%

        # 做空/做多的引线价格阈值
        self.short_entry_from_wick_high = 0.80  # 上引线最高价的80%开空
        self.long_entry_from_wick_low = 1.20    # 下引线最低价的120%开多

    def _get_connection(self):
        """获取数据库连接"""
        return pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)

    def load_klines(self, symbol: str, timeframe: str, limit: int) -> List[Dict]:
        """加载K线数据"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT open_time, open_price, high_price, low_price, close_price, volume
                FROM kline_data
                WHERE symbol = %s
                AND timeframe = %s
                AND exchange = 'binance_futures'
                ORDER BY open_time DESC
                LIMIT %s
            """, (symbol, timeframe, limit))

            klines = cursor.fetchall()
            cursor.close()
            conn.close()

            if not klines:
                return []

            # 反转（从旧到新）
            klines = klines[::-1]

            # 转换数据类型
            for k in klines:
                k['open'] = float(k['open_price'])
                k['high'] = float(k['high_price'])
                k['low'] = float(k['low_price'])
                k['close'] = float(k['close_price'])
                k['volume'] = float(k['volume'])

            return klines

        except Exception as e:
            logger.error(f"加载K线失败 {symbol}: {e}")
            return []

    def detect_volume_shrink(self, klines: List[Dict], recent_bars: int = 3) -> Dict:
        """
        检测成交量萎缩

        Args:
            klines: K线数据
            recent_bars: 检查最近N根K线

        Returns:
            {
                'is_shrinking': True/False,
                'current_volume': 当前成交量,
                'avg_volume': 平均成交量,
                'shrink_ratio': 萎缩比例,
                'shrink_bars': 连续萎缩的K线数
            }
        """
        if len(klines) < 20:
            return {'is_shrinking': False}

        # 计算平均成交量（最近20根）
        volumes = [k['volume'] for k in klines[-20:]]
        avg_volume = np.mean(volumes)

        # 检查最近N根K线是否萎缩
        recent_volumes = volumes[-recent_bars:]
        current_volume = recent_volumes[-1]

        shrink_count = sum(1 for v in recent_volumes if v < avg_volume * self.volume_shrink_threshold)
        is_shrinking = shrink_count >= (recent_bars - 1)  # 至少N-1根萎缩

        shrink_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0

        return {
            'is_shrinking': is_shrinking,
            'current_volume': current_volume,
            'avg_volume': avg_volume,
            'shrink_ratio': shrink_ratio,
            'shrink_bars': shrink_count
        }

    def detect_rejection_wick(
        self,
        klines: List[Dict],
        direction: str,
        lookback: int = 3
    ) -> Dict:
        """
        检测拒绝引线（上引线或下引线）

        Args:
            klines: K线数据
            direction: 'SHORT'检测上引线, 'LONG'检测下引线
            lookback: 检查最近N根K线

        Returns:
            {
                'has_wick': True/False,
                'wick_high': 引线最高价（做空用）,
                'wick_low': 引线最低价（做多用）,
                'wick_length_pct': 引线长度占比%,
                'body_length_pct': 实体长度占比%,
                'wick_body_ratio': 引线/实体比例,
                'wick_bar_index': 引线所在K线位置（-1=最新）
            }
        """
        if len(klines) < lookback:
            return {'has_wick': False}

        recent_klines = klines[-lookback:]
        best_wick = None
        best_ratio = 0

        for i, k in enumerate(recent_klines):
            open_price = k['open']
            close_price = k['close']
            high_price = k['high']
            low_price = k['low']

            # 计算实体和影线长度
            body_length = abs(close_price - open_price)
            total_height = high_price - low_price

            if total_height == 0:
                continue

            if direction == 'SHORT':
                # 检测上引线：高点被拒绝
                # 上引线 = 最高价 - max(开盘价, 收盘价)
                upper_wick = high_price - max(open_price, close_price)
                wick_pct = upper_wick / total_height

                # 判断条件
                if wick_pct >= self.min_wick_pct:  # 引线占比足够
                    if body_length > 0:
                        wick_body_ratio = upper_wick / body_length
                    else:
                        wick_body_ratio = 999  # 十字星

                    if wick_body_ratio >= self.min_wick_body_ratio or wick_body_ratio > 10:
                        if wick_body_ratio > best_ratio:
                            best_ratio = wick_body_ratio
                            best_wick = {
                                'has_wick': True,
                                'wick_high': high_price,
                                'wick_low': None,
                                'wick_length_pct': wick_pct * 100,
                                'body_length_pct': (body_length / total_height) * 100,
                                'wick_body_ratio': wick_body_ratio,
                                'wick_bar_index': i - lookback  # 相对于最新K线的位置
                            }

            else:  # LONG
                # 检测下引线：低点被支撑
                # 下引线 = min(开盘价, 收盘价) - 最低价
                lower_wick = min(open_price, close_price) - low_price
                wick_pct = lower_wick / total_height

                # 判断条件
                if wick_pct >= self.min_wick_pct:
                    if body_length > 0:
                        wick_body_ratio = lower_wick / body_length
                    else:
                        wick_body_ratio = 999

                    if wick_body_ratio >= self.min_wick_body_ratio or wick_body_ratio > 10:
                        if wick_body_ratio > best_ratio:
                            best_ratio = wick_body_ratio
                            best_wick = {
                                'has_wick': True,
                                'wick_high': None,
                                'wick_low': low_price,
                                'wick_length_pct': wick_pct * 100,
                                'body_length_pct': (body_length / total_height) * 100,
                                'wick_body_ratio': wick_body_ratio,
                                'wick_bar_index': i - lookback
                            }

        return best_wick if best_wick else {'has_wick': False}

    def generate_short_signal(self, symbol: str, big4_signal: str) -> Optional[Dict]:
        """
        生成做空信号

        条件:
        1. 价格接近4H最高价的90%
        2. 成交量萎缩（上涨无力）
        3. 15M/5M有上引线（拒绝反弹）

        Returns:
            信号字典或None
        """
        # 只在震荡市(NEUTRAL)环境下运行
        if big4_signal != 'NEUTRAL':
            return None

        # 加载K线数据
        klines_15m = self.load_klines(symbol, '15m', 50)
        klines_5m = self.load_klines(symbol, '5m', 100)

        if len(klines_15m) < self.lookback_4h_bars + 10:
            return None

        # 1️⃣ 检查价格位置：是否接近4H高点
        recent_4h = klines_15m[-self.lookback_4h_bars:]  # 最近4小时
        high_4h = max([k['high'] for k in recent_4h])
        current_price = klines_15m[-1]['close']

        price_ratio = current_price / high_4h

        if price_ratio < self.price_near_high_threshold:
            logger.debug(f"[RANGE_SHORT] {symbol} 价格不够接近4H高点: {price_ratio:.2%} < 90%")
            return None

        # 2️⃣ 检查成交量萎缩
        volume_info = self.detect_volume_shrink(klines_15m, recent_bars=3)

        if not volume_info['is_shrinking']:
            logger.debug(
                f"[RANGE_SHORT] {symbol} 成交量未萎缩: "
                f"{volume_info['shrink_ratio']:.2%} (需要<80%)"
            )
            return None

        # 3️⃣ 检查上引线（15M或5M）
        wick_15m = self.detect_rejection_wick(klines_15m, 'SHORT', lookback=3)
        wick_5m = self.detect_rejection_wick(klines_5m, 'SHORT', lookback=5)

        # 至少有一个周期有明显上引线
        if not wick_15m['has_wick'] and not wick_5m['has_wick']:
            logger.debug(f"[RANGE_SHORT] {symbol} 未检测到拒绝上引线")
            return None

        # 选择最明显的引线
        best_wick = wick_15m if wick_15m['has_wick'] else wick_5m
        if wick_5m['has_wick'] and wick_15m['has_wick']:
            best_wick = wick_15m if wick_15m['wick_body_ratio'] > wick_5m['wick_body_ratio'] else wick_5m

        # 计算开仓价格：上引线最高价的80%
        entry_price = best_wick['wick_high'] * self.short_entry_from_wick_high
        stop_loss_price = best_wick['wick_high'] * 1.005  # 止损在引线高点上方0.5%

        # 计算评分
        score = 60  # 基础分
        reasons = []

        reasons.append(f'接近4H高点({price_ratio:.1%})')
        reasons.append(f'量能萎缩({volume_info["shrink_ratio"]:.1%})')
        reasons.append(f'上引线拒绝(引线/实体={best_wick["wick_body_ratio"]:.1f})')

        # 加分项
        if price_ratio >= 0.95:
            score += 15
            reasons.append('紧贴4H高点')

        if volume_info['shrink_ratio'] < 0.6:
            score += 10
            reasons.append('量能严重萎缩')

        if best_wick['wick_body_ratio'] >= 3.0:
            score += 15
            reasons.append('强力拒绝')

        logger.info(
            f"✅ [RANGE_SHORT] {symbol} 震荡做空信号 | "
            f"价格:{price_ratio:.1%} | 量萎:{volume_info['shrink_ratio']:.1%} | "
            f"引线比:{best_wick['wick_body_ratio']:.1f} | 评分:{score}"
        )

        return {
            'symbol': symbol,
            'signal': 'SHORT',
            'score': min(score, 100),
            'strategy': 'range_reversal',
            'entry_price': entry_price,
            'current_price': current_price,
            'stop_loss_price': stop_loss_price,
            'take_profit_price': high_4h * 0.85,  # 目标：回落到4H区间中部
            'high_4h': high_4h,
            'price_ratio': price_ratio,
            'volume_shrink_ratio': volume_info['shrink_ratio'],
            'wick_body_ratio': best_wick['wick_body_ratio'],
            'wick_high': best_wick['wick_high'],
            'reason': ' + '.join(reasons),
            'timeframe': '15m'
        }

    def generate_long_signal(self, symbol: str, big4_signal: str) -> Optional[Dict]:
        """
        生成做多信号

        条件:
        1. 价格接近4H最低价的110%
        2. 成交量萎缩（下跌无力）
        3. 15M/5M有下引线（拒绝下探）

        Returns:
            信号字典或None
        """
        # 只在震荡市(NEUTRAL)环境下运行
        if big4_signal != 'NEUTRAL':
            return None

        # 加载K线数据
        klines_15m = self.load_klines(symbol, '15m', 50)
        klines_5m = self.load_klines(symbol, '5m', 100)

        if len(klines_15m) < self.lookback_4h_bars + 10:
            return None

        # 1️⃣ 检查价格位置：是否接近4H低点
        recent_4h = klines_15m[-self.lookback_4h_bars:]
        low_4h = min([k['low'] for k in recent_4h])
        current_price = klines_15m[-1]['close']

        price_ratio = current_price / low_4h

        if price_ratio > self.price_near_low_threshold:
            logger.debug(f"[RANGE_LONG] {symbol} 价格不够接近4H低点: {price_ratio:.2%} > 110%")
            return None

        # 2️⃣ 检查成交量萎缩
        volume_info = self.detect_volume_shrink(klines_15m, recent_bars=3)

        if not volume_info['is_shrinking']:
            logger.debug(
                f"[RANGE_LONG] {symbol} 成交量未萎缩: "
                f"{volume_info['shrink_ratio']:.2%}"
            )
            return None

        # 3️⃣ 检查下引线
        wick_15m = self.detect_rejection_wick(klines_15m, 'LONG', lookback=3)
        wick_5m = self.detect_rejection_wick(klines_5m, 'LONG', lookback=5)

        if not wick_15m['has_wick'] and not wick_5m['has_wick']:
            logger.debug(f"[RANGE_LONG] {symbol} 未检测到支撑下引线")
            return None

        # 选择最明显的引线
        best_wick = wick_15m if wick_15m['has_wick'] else wick_5m
        if wick_5m['has_wick'] and wick_15m['has_wick']:
            best_wick = wick_15m if wick_15m['wick_body_ratio'] > wick_5m['wick_body_ratio'] else wick_5m

        # 计算开仓价格：下引线最低价的120%
        entry_price = best_wick['wick_low'] * self.long_entry_from_wick_low
        stop_loss_price = best_wick['wick_low'] * 0.995  # 止损在引线低点下方0.5%

        # 计算评分
        score = 60
        reasons = []

        reasons.append(f'接近4H低点({price_ratio:.1%})')
        reasons.append(f'量能萎缩({volume_info["shrink_ratio"]:.1%})')
        reasons.append(f'下引线支撑(引线/实体={best_wick["wick_body_ratio"]:.1f})')

        # 加分项
        if price_ratio <= 1.05:
            score += 15
            reasons.append('紧贴4H低点')

        if volume_info['shrink_ratio'] < 0.6:
            score += 10
            reasons.append('量能严重萎缩')

        if best_wick['wick_body_ratio'] >= 3.0:
            score += 15
            reasons.append('强力支撑')

        logger.info(
            f"✅ [RANGE_LONG] {symbol} 震荡做多信号 | "
            f"价格:{price_ratio:.1%} | 量萎:{volume_info['shrink_ratio']:.1%} | "
            f"引线比:{best_wick['wick_body_ratio']:.1f} | 评分:{score}"
        )

        return {
            'symbol': symbol,
            'signal': 'LONG',
            'score': min(score, 100),
            'strategy': 'range_reversal',
            'entry_price': entry_price,
            'current_price': current_price,
            'stop_loss_price': stop_loss_price,
            'take_profit_price': low_4h * 1.15,  # 目标：反弹到4H区间中部
            'low_4h': low_4h,
            'price_ratio': price_ratio,
            'volume_shrink_ratio': volume_info['shrink_ratio'],
            'wick_body_ratio': best_wick['wick_body_ratio'],
            'wick_low': best_wick['wick_low'],
            'reason': ' + '.join(reasons),
            'timeframe': '15m'
        }

    def generate_signal(self, symbol: str, big4_signal: str) -> Optional[Dict]:
        """
        生成交易信号（做多或做空）

        Args:
            symbol: 交易对
            big4_signal: Big4市场信号

        Returns:
            信号字典或None
        """
        try:
            # 尝试做空信号
            short_signal = self.generate_short_signal(symbol, big4_signal)
            if short_signal:
                return short_signal

            # 尝试做多信号
            long_signal = self.generate_long_signal(symbol, big4_signal)
            if long_signal:
                return long_signal

            return None

        except Exception as e:
            logger.error(f"[RANGE_STRATEGY] {symbol} 信号生成失败: {e}")
            return None
