"""
震荡市检测器
识别市场是否处于震荡状态,并检测关键支撑阻力区间
"""

import pymysql
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class RangeMarketDetector:
    """震荡市检测器"""

    def __init__(self, db_config: dict):
        self.db_config = db_config

    def is_ranging_market(self, big4_signal: str, big4_strength: float) -> bool:
        """
        判断是否为震荡市

        Args:
            big4_signal: Big4信号 (BULLISH/BEARISH/NEUTRAL)
            big4_strength: Big4强度 (0-100)

        Returns:
            bool: True表示震荡市
        """
        # 条件1: Big4信号为NEUTRAL
        if big4_signal != 'NEUTRAL':
            return False

        # 条件2: 强度较弱 (< 50)
        if big4_strength >= 50:
            return False

        return True

    def detect_support_resistance(
        self,
        symbol: str,
        timeframe: str = '1h',
        lookback_hours: int = 24
    ) -> Optional[Dict]:
        """
        检测支撑阻力区间

        Args:
            symbol: 交易对
            timeframe: K线周期
            lookback_hours: 回溯小时数

        Returns:
            区间信息字典或None
        """
        try:
            conn = pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)
            cursor = conn.cursor()

            # 获取历史K线数据
            cursor.execute("""
                SELECT
                    high_price, low_price, close_price, volume,
                    open_time
                FROM kline_data
                WHERE symbol = %s
                AND timeframe = %s
                AND exchange = 'binance_futures'
                AND open_time >= %s
                ORDER BY open_time ASC
            """, (
                symbol,
                timeframe,
                int((datetime.now() - timedelta(hours=lookback_hours)).timestamp() * 1000)
            ))

            klines = cursor.fetchall()
            cursor.close()
            conn.close()

            if len(klines) < 10:
                return None

            # 提取高低点
            highs = np.array([float(k['high_price']) for k in klines])
            lows = np.array([float(k['low_price']) for k in klines])
            closes = np.array([float(k['close_price']) for k in klines])

            # 计算关键价位
            recent_high = np.max(highs[-20:])  # 最近20根K线最高价
            recent_low = np.min(lows[-20:])    # 最近20根K线最低价
            current_price = closes[-1]

            # 计算区间幅度
            range_pct = ((recent_high - recent_low) / recent_low) * 100

            # 只有区间幅度在3-15%之间才认为是有效震荡区间
            if range_pct < 3 or range_pct > 15:
                return None

            # 计算支撑阻力位触及次数
            support_touches = self._count_touches(lows, recent_low, tolerance=0.02)
            resistance_touches = self._count_touches(highs, recent_high, tolerance=0.02)

            # 至少要有2次触及才算有效
            if support_touches < 2 or resistance_touches < 2:
                return None

            # 计算可信度分数
            confidence = self._calculate_confidence(
                support_touches,
                resistance_touches,
                range_pct,
                current_price,
                recent_low,
                recent_high
            )

            return {
                'symbol': symbol,
                'timeframe': timeframe,
                'support_price': recent_low,
                'resistance_price': recent_high,
                'range_pct': round(range_pct, 2),
                'current_price': current_price,
                'support_touches': support_touches,
                'resistance_touches': resistance_touches,
                'confidence_score': round(confidence, 2),
                'is_valid': confidence >= 60  # 可信度>=60才算有效
            }

        except Exception as e:
            logger.error(f"检测支撑阻力区间失败 {symbol}: {e}")
            return None

    def _count_touches(self, prices: np.ndarray, level: float, tolerance: float = 0.02) -> int:
        """
        计算价格触及某个水平的次数

        Args:
            prices: 价格数组
            level: 目标价位
            tolerance: 容差百分比

        Returns:
            触及次数
        """
        upper_bound = level * (1 + tolerance)
        lower_bound = level * (1 - tolerance)

        touches = 0
        in_zone = False

        for price in prices:
            if lower_bound <= price <= upper_bound:
                if not in_zone:
                    touches += 1
                    in_zone = True
            else:
                in_zone = False

        return touches

    def _calculate_confidence(
        self,
        support_touches: int,
        resistance_touches: int,
        range_pct: float,
        current_price: float,
        support: float,
        resistance: float
    ) -> float:
        """
        计算区间可信度分数

        Returns:
            0-100的分数
        """
        score = 0

        # 触及次数越多,可信度越高 (最高40分)
        touch_score = min((support_touches + resistance_touches) * 5, 40)
        score += touch_score

        # 区间幅度适中 (3-8%最佳, 最高30分)
        if 3 <= range_pct <= 8:
            range_score = 30
        elif 8 < range_pct <= 12:
            range_score = 20
        else:
            range_score = 10
        score += range_score

        # 当前价格在区间中部,可信度更高 (最高30分)
        price_position = (current_price - support) / (resistance - support)
        if 0.3 <= price_position <= 0.7:
            position_score = 30
        elif 0.2 <= price_position <= 0.8:
            position_score = 20
        else:
            position_score = 10
        score += position_score

        return min(score, 100)

    def save_zone_to_db(self, zone: Dict) -> Optional[int]:
        """
        保存区间到数据库

        Returns:
            zone_id或None
        """
        if not zone or not zone.get('is_valid'):
            return None

        try:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor()

            # 先停用该币种的旧区间
            cursor.execute("""
                UPDATE range_market_zones
                SET is_active = FALSE
                WHERE symbol = %s
                AND timeframe = %s
                AND is_active = TRUE
            """, (zone['symbol'], zone['timeframe']))

            # 插入新区间
            cursor.execute("""
                INSERT INTO range_market_zones (
                    symbol, timeframe,
                    support_price, resistance_price, range_pct,
                    touch_count, confidence_score,
                    is_active, detected_at, expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, TRUE, NOW(),
                    DATE_ADD(NOW(), INTERVAL 24 HOUR)
                )
            """, (
                zone['symbol'],
                zone['timeframe'],
                zone['support_price'],
                zone['resistance_price'],
                zone['range_pct'],
                zone['support_touches'] + zone['resistance_touches'],
                zone['confidence_score']
            ))

            zone_id = cursor.lastrowid
            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"✅ 保存震荡区间: {zone['symbol']} [{zone['support_price']:.4f} - {zone['resistance_price']:.4f}] 可信度:{zone['confidence_score']}")
            return zone_id

        except Exception as e:
            logger.error(f"保存区间失败: {e}")
            return None

    def get_active_zone(self, symbol: str) -> Optional[Dict]:
        """获取该币种当前有效的震荡区间"""
        try:
            conn = pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT *
                FROM range_market_zones
                WHERE symbol = %s
                AND is_active = TRUE
                AND expires_at > NOW()
                ORDER BY confidence_score DESC, detected_at DESC
                LIMIT 1
            """, (symbol,))

            zone = cursor.fetchone()
            cursor.close()
            conn.close()

            return zone

        except Exception as e:
            logger.error(f"获取震荡区间失败: {e}")
            return None
