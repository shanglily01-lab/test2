#!/usr/bin/env python3
"""
四大天王趋势判断系统
监控 BTC, ETH, BNB, SOL 的关键方向性变化，为整体市场提供先导信号

核心逻辑:
1. 盘整期判断 (6小时内涨跌幅<0.5%)
2. 方向性K线力度分析 (阴阳线数量+成交量)
3. 拐点捕获 (5M/15M突然出现强力度K线)
4. 提前预警信号生成
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import mysql.connector
from collections import defaultdict

logger = logging.getLogger(__name__)

# 数据库配置
DB_CONFIG = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

# 四大天王
BIG4_SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']


class Big4TrendDetector:
    """四大天王趋势检测器"""

    def __init__(self):
        self.consolidation_threshold = 0.5  # 盘整阈值 0.5%
        self.consolidation_hours = 6        # 盘整观察期 6小时
        self.breakout_volume_multiplier = 1.5  # 突破成交量倍数

    def detect_market_trend(self) -> Dict:
        """
        检测四大天王的市场趋势

        返回:
        {
            'overall_signal': 'BULLISH' | 'BEARISH' | 'NEUTRAL',
            'signal_strength': 0-100,
            'details': {
                'BTC/USDT': {...},
                'ETH/USDT': {...},
                ...
            },
            'recommendation': str
        }
        """
        conn = mysql.connector.connect(**DB_CONFIG)
        results = {}

        bullish_count = 0
        bearish_count = 0
        total_strength = 0

        for symbol in BIG4_SYMBOLS:
            analysis = self._analyze_symbol(conn, symbol)
            results[symbol] = analysis

            if analysis['signal'] == 'BULLISH':
                bullish_count += 1
                total_strength += analysis['strength']
            elif analysis['signal'] == 'BEARISH':
                bearish_count += 1
                total_strength += analysis['strength']

        conn.close()

        # 综合判断
        if bullish_count >= 3:
            overall_signal = 'BULLISH'
            recommendation = "市场整体看涨，建议优先考虑多单机会"
        elif bearish_count >= 3:
            overall_signal = 'BEARISH'
            recommendation = "市场整体看跌，建议优先考虑空单机会"
        else:
            overall_signal = 'NEUTRAL'
            recommendation = "市场方向不明确，建议观望或减少仓位"

        avg_strength = total_strength / len(BIG4_SYMBOLS) if BIG4_SYMBOLS else 0

        return {
            'overall_signal': overall_signal,
            'signal_strength': avg_strength,
            'bullish_count': bullish_count,
            'bearish_count': bearish_count,
            'details': results,
            'recommendation': recommendation,
            'timestamp': datetime.now()
        }

    def _analyze_symbol(self, conn, symbol: str) -> Dict:
        """
        分析单个天王的趋势

        步骤:
        1. 检查6小时内是否在盘整 (涨跌幅<0.5%)
        2. 分析1H/15M K线的阴阳线力度
        3. 检测5M/15M的突破信号
        4. 生成预警信号
        """
        cursor = conn.cursor(dictionary=True)

        # 1. 检查6小时盘整
        is_consolidating, price_change_pct = self._check_consolidation(cursor, symbol)

        # 2. 分析K线力度 (1H, 15M)
        kline_analysis_1h = self._analyze_kline_strength(cursor, symbol, '1h', hours=6)
        kline_analysis_15m = self._analyze_kline_strength(cursor, symbol, '15m', hours=2)

        # 3. 检测突破信号 (5M, 15M)
        breakout_5m = self._detect_breakout(cursor, symbol, '5m', candles=10)
        breakout_15m = self._detect_breakout(cursor, symbol, '15m', candles=5)

        cursor.close()

        # 4. 综合判断
        signal, strength, reason = self._generate_signal(
            is_consolidating, price_change_pct,
            kline_analysis_1h, kline_analysis_15m,
            breakout_5m, breakout_15m
        )

        return {
            'signal': signal,
            'strength': strength,
            'reason': reason,
            'is_consolidating': is_consolidating,
            'price_change_6h': price_change_pct,
            '1h_analysis': kline_analysis_1h,
            '15m_analysis': kline_analysis_15m,
            '5m_breakout': breakout_5m,
            '15m_breakout': breakout_15m
        }

    def _check_consolidation(self, cursor, symbol: str) -> Tuple[bool, float]:
        """
        检查6小时内是否在盘整

        返回: (是否盘整, 涨跌幅百分比)
        """
        cursor.execute("""
            SELECT
                open_price as first_open,
                close_price as last_close
            FROM kline_data
            WHERE symbol = %s
            AND timeframe = '1h'
            AND timestamp >= DATE_SUB(NOW(), INTERVAL 6 HOUR)
            ORDER BY timestamp ASC
            LIMIT 1
        """, (symbol,))

        first = cursor.fetchone()

        if not first:
            return False, 0.0

        cursor.execute("""
            SELECT close_price as last_close
            FROM kline_data
            WHERE symbol = %s
            AND timeframe = '1h'
            AND timestamp >= DATE_SUB(NOW(), INTERVAL 6 HOUR)
            ORDER BY timestamp DESC
            LIMIT 1
        """, (symbol,))

        last = cursor.fetchone()

        if not last:
            return False, 0.0

        first_price = float(first['first_open'])
        last_price = float(last['last_close'])

        price_change_pct = ((last_price - first_price) / first_price) * 100
        is_consolidating = abs(price_change_pct) < self.consolidation_threshold

        return is_consolidating, price_change_pct

    def _analyze_kline_strength(self, cursor, symbol: str, timeframe: str, hours: int) -> Dict:
        """
        分析K线的阴阳线力度

        返回:
        {
            'bullish_count': int,    # 阳线数量
            'bearish_count': int,    # 阴线数量
            'bullish_volume': float, # 阳线总成交量
            'bearish_volume': float, # 阴线总成交量
            'avg_bullish_size': float,  # 阳线平均实体大小
            'avg_bearish_size': float,  # 阴线平均实体大小
            'dominant': 'BULL' | 'BEAR' | 'NEUTRAL'  # 主导方向
        }
        """
        cursor.execute("""
            SELECT
                open_price, close_price, high_price, low_price, volume
            FROM kline_data
            WHERE symbol = %s
            AND timeframe = %s
            AND timestamp >= DATE_SUB(NOW(), INTERVAL %s HOUR)
            ORDER BY timestamp ASC
        """, (symbol, timeframe, hours))

        klines = cursor.fetchall()

        if not klines:
            return {
                'bullish_count': 0,
                'bearish_count': 0,
                'bullish_volume': 0,
                'bearish_volume': 0,
                'avg_bullish_size': 0,
                'avg_bearish_size': 0,
                'dominant': 'NEUTRAL'
            }

        bullish_count = 0
        bearish_count = 0
        bullish_volume = 0
        bearish_volume = 0
        bullish_sizes = []
        bearish_sizes = []

        for k in klines:
            open_p = float(k['open_price'])
            close_p = float(k['close_price'])
            volume = float(k['volume']) if k['volume'] else 0

            # 实体大小 (%)
            body_size = abs(close_p - open_p) / open_p * 100

            if close_p > open_p:
                # 阳线
                bullish_count += 1
                bullish_volume += volume
                bullish_sizes.append(body_size)
            else:
                # 阴线
                bearish_count += 1
                bearish_volume += volume
                bearish_sizes.append(body_size)

        avg_bullish_size = sum(bullish_sizes) / len(bullish_sizes) if bullish_sizes else 0
        avg_bearish_size = sum(bearish_sizes) / len(bearish_sizes) if bearish_sizes else 0

        # 判断主导方向
        # 条件: 数量优势 + 成交量优势 + 实体大小优势
        count_score = (bullish_count - bearish_count) / len(klines) if klines else 0
        volume_score = (bullish_volume - bearish_volume) / (bullish_volume + bearish_volume) if (bullish_volume + bearish_volume) > 0 else 0
        size_score = (avg_bullish_size - avg_bearish_size) / max(avg_bullish_size, avg_bearish_size, 0.01)

        overall_score = (count_score + volume_score + size_score) / 3

        if overall_score > 0.2:
            dominant = 'BULL'
        elif overall_score < -0.2:
            dominant = 'BEAR'
        else:
            dominant = 'NEUTRAL'

        return {
            'bullish_count': bullish_count,
            'bearish_count': bearish_count,
            'bullish_volume': bullish_volume,
            'bearish_volume': bearish_volume,
            'avg_bullish_size': avg_bullish_size,
            'avg_bearish_size': avg_bearish_size,
            'dominant': dominant,
            'score': overall_score
        }

    def _detect_breakout(self, cursor, symbol: str, timeframe: str, candles: int) -> Dict:
        """
        检测突破信号

        检查最近的K线是否出现强力度突破:
        - 成交量 > 平均成交量 * 1.5
        - 实体大小 > 平均实体大小 * 1.5
        - 方向明确 (上涨或下跌)
        """
        cursor.execute("""
            SELECT
                open_price, close_price, high_price, low_price, volume, timestamp
            FROM kline_data
            WHERE symbol = %s
            AND timeframe = %s
            ORDER BY timestamp DESC
            LIMIT %s
        """, (symbol, timeframe, candles))

        klines = cursor.fetchall()

        if not klines or len(klines) < 3:
            return {
                'detected': False,
                'direction': 'NEUTRAL',
                'strength': 0,
                'reason': '数据不足'
            }

        # 最新K线
        latest = klines[0]
        # 之前的K线 (用于计算平均)
        previous = klines[1:]

        # 计算平均成交量和实体大小
        avg_volume = sum(float(k['volume'] or 0) for k in previous) / len(previous)

        avg_body_sizes = []
        for k in previous:
            open_p = float(k['open_price'])
            close_p = float(k['close_price'])
            body_size = abs(close_p - open_p) / open_p * 100
            avg_body_sizes.append(body_size)

        avg_body_size = sum(avg_body_sizes) / len(avg_body_sizes) if avg_body_sizes else 0

        # 分析最新K线
        latest_open = float(latest['open_price'])
        latest_close = float(latest['close_price'])
        latest_volume = float(latest['volume']) if latest['volume'] else 0
        latest_body_size = abs(latest_close - latest_open) / latest_open * 100

        # 判断是否为突破
        volume_breakout = latest_volume > (avg_volume * self.breakout_volume_multiplier)
        size_breakout = latest_body_size > (avg_body_size * self.breakout_volume_multiplier)

        if volume_breakout and size_breakout:
            detected = True
            direction = 'BULLISH' if latest_close > latest_open else 'BEARISH'

            # 计算强度 (0-100)
            volume_ratio = latest_volume / avg_volume if avg_volume > 0 else 1
            size_ratio = latest_body_size / avg_body_size if avg_body_size > 0 else 1
            strength = min(((volume_ratio + size_ratio) / 2 - 1) * 50, 100)

            reason = f"{timeframe}突破: 成交量{volume_ratio:.1f}x, 实体{size_ratio:.1f}x"
        else:
            detected = False
            direction = 'NEUTRAL'
            strength = 0
            reason = '未检测到突破'

        return {
            'detected': detected,
            'direction': direction,
            'strength': strength,
            'reason': reason,
            'latest_volume': latest_volume,
            'avg_volume': avg_volume,
            'latest_body_size': latest_body_size,
            'avg_body_size': avg_body_size
        }

    def _generate_signal(
        self,
        is_consolidating: bool,
        price_change_pct: float,
        kline_1h: Dict,
        kline_15m: Dict,
        breakout_5m: Dict,
        breakout_15m: Dict
    ) -> Tuple[str, int, str]:
        """
        综合生成信号

        返回: (信号方向, 强度0-100, 原因)
        """
        reasons = []
        signal_score = 0  # -100 to +100

        # 1. 盘整期判断 (权重: 20)
        if is_consolidating:
            reasons.append(f"6H盘整({price_change_pct:+.2f}%)")

            # 盘整中，重点看突破
            if breakout_5m['detected']:
                if breakout_5m['direction'] == 'BEARISH':
                    signal_score -= 30
                    reasons.append(f"5M向下突破({breakout_5m['strength']:.0f}分)")
                else:
                    signal_score += 30
                    reasons.append(f"5M向上突破({breakout_5m['strength']:.0f}分)")

            if breakout_15m['detected']:
                if breakout_15m['direction'] == 'BEARISH':
                    signal_score -= 25
                    reasons.append(f"15M向下突破({breakout_15m['strength']:.0f}分)")
                else:
                    signal_score += 25
                    reasons.append(f"15M向上突破({breakout_15m['strength']:.0f}分)")

        # 2. K线力度分析 (权重: 40)
        if kline_1h['dominant'] == 'BEAR':
            signal_score -= 20
            reasons.append(f"1H阴线主导(阴{kline_1h['bearish_count']}:阳{kline_1h['bullish_count']})")
        elif kline_1h['dominant'] == 'BULL':
            signal_score += 20
            reasons.append(f"1H阳线主导(阳{kline_1h['bullish_count']}:阴{kline_1h['bearish_count']})")

        if kline_15m['dominant'] == 'BEAR':
            signal_score -= 20
            reasons.append(f"15M阴线主导")
        elif kline_15m['dominant'] == 'BULL':
            signal_score += 20
            reasons.append(f"15M阳线主导")

        # 3. 非盘整期的突破 (权重: 40)
        if not is_consolidating:
            if breakout_5m['detected']:
                weight = 20
                if breakout_5m['direction'] == 'BEARISH':
                    signal_score -= weight
                else:
                    signal_score += weight

            if breakout_15m['detected']:
                weight = 20
                if breakout_15m['direction'] == 'BEARISH':
                    signal_score -= weight
                else:
                    signal_score += weight

        # 生成最终信号
        if signal_score > 30:
            signal = 'BULLISH'
        elif signal_score < -30:
            signal = 'BEARISH'
        else:
            signal = 'NEUTRAL'

        strength = min(abs(signal_score), 100)
        reason = ' | '.join(reasons) if reasons else '无明显信号'

        return signal, strength, reason


def get_big4_detector() -> Big4TrendDetector:
    """获取四大天王检测器单例"""
    global _detector_instance
    if '_detector_instance' not in globals():
        _detector_instance = Big4TrendDetector()
    return _detector_instance


if __name__ == '__main__':
    # 测试
    detector = Big4TrendDetector()
    result = detector.detect_market_trend()

    print("=" * 100)
    print("四大天王趋势分析")
    print("=" * 100)
    print()
    print(f"整体信号: {result['overall_signal']}")
    print(f"信号强度: {result['signal_strength']:.0f}/100")
    print(f"看涨数量: {result['bullish_count']}/4")
    print(f"看跌数量: {result['bearish_count']}/4")
    print(f"建议: {result['recommendation']}")
    print()

    for symbol, detail in result['details'].items():
        print(f"\n{'='*100}")
        print(f"{symbol}: {detail['signal']} (强度: {detail['strength']}/100)")
        print(f"原因: {detail['reason']}")
        print(f"6H盘整: {'是' if detail['is_consolidating'] else '否'} ({detail['price_change_6h']:+.2f}%)")
        print(f"1H主导: {detail['1h_analysis']['dominant']}")
        print(f"15M主导: {detail['15m_analysis']['dominant']}")
        if detail['5m_breakout']['detected']:
            print(f"5M突破: {detail['5m_breakout']['direction']} - {detail['5m_breakout']['reason']}")
        if detail['15m_breakout']['detected']:
            print(f"15M突破: {detail['15m_breakout']['direction']} - {detail['15m_breakout']['reason']}")
