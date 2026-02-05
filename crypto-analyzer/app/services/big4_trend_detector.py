#!/usr/bin/env python3
"""
四大天王趋势判断系统 (简化版)
监控 BTC, ETH, BNB, SOL 的关键方向性变化

优化逻辑:
1. 1H (30根K线): 主导方向判断 (阳阴线数量 + 力度)
2. 15M (30根K线): 趋势确认 (阳阴线数量 + 力度)
3. 5M (3根K线): 买卖时机判断 (突破检测)
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import pymysql
from dotenv import load_dotenv
import os

# 加载环境变量
load_dotenv()

logger = logging.getLogger(__name__)

# 四大天王
BIG4_SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']


class Big4TrendDetector:
    """四大天王趋势检测器 (简化版)"""

    def __init__(self):
        self.db_config = {
            'host': os.getenv('DB_HOST', '13.212.252.171'),
            'port': int(os.getenv('DB_PORT', 3306)),
            'user': os.getenv('DB_USER', 'admin'),
            'password': os.getenv('DB_PASSWORD', 'Tonny@1000'),
            'database': os.getenv('DB_NAME', 'binance-data'),
            'charset': 'utf8mb4'
        }

    def detect_market_trend(self) -> Dict:
        """
        检测四大天王的市场趋势 (简化版)

        返回:
        {
            'overall_signal': 'BULLISH' | 'BEARISH' | 'NEUTRAL',
            'signal_strength': 0-100,
            'bullish_count': int,
            'bearish_count': int,
            'details': {
                'BTC/USDT': {...},
                'ETH/USDT': {...},
                ...
            },
            'recommendation': str,
            'timestamp': datetime
        }
        """
        conn = pymysql.connect(**self.db_config)
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

        result = {
            'overall_signal': overall_signal,
            'signal_strength': avg_strength,
            'bullish_count': bullish_count,
            'bearish_count': bearish_count,
            'details': results,
            'recommendation': recommendation,
            'timestamp': datetime.now()
        }

        # 记录到数据库
        self._save_to_database(result)

        return result

    def _analyze_symbol(self, conn, symbol: str) -> Dict:
        """
        分析单个币种的趋势 (简化版)

        步骤:
        1. 1H (30根): 大趋势判断
        2. 1H (5根): 小趋势判断 (用于修正大趋势)
        3. 15M (30根): 趋势确认
        4. 5M (3根): 买卖时机
        """
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 1. 分析1H K线 (30根) - 大趋势
        kline_1h_30 = self._analyze_kline_power(cursor, symbol, '1h', 30)

        # 2. 分析1H K线 (5根) - 小趋势 (用于修正)
        kline_1h_5 = self._analyze_kline_power(cursor, symbol, '1h', 5)

        # 3. 分析15M K线 (30根) - 趋势确认
        kline_15m = self._analyze_kline_power(cursor, symbol, '15m', 30)

        # 4. 分析5M K线 (3根) - 买卖时机
        kline_5m = self._detect_5m_signal(cursor, symbol)

        cursor.close()

        # 5. 综合判断
        signal, strength, reason = self._generate_signal(kline_1h_30, kline_1h_5, kline_15m, kline_5m)

        return {
            'signal': signal,
            'strength': strength,
            'reason': reason,
            '1h_30_analysis': kline_1h_30,
            '1h_5_analysis': kline_1h_5,
            '15m_analysis': kline_15m,
            '5m_signal': kline_5m
        }

    def _analyze_kline_power(self, cursor, symbol: str, timeframe: str, count: int) -> Dict:
        """
        分析K线力度 (简化版)

        力度 = 价格变化% × 0.8 + 成交量归一化 × 0.2
        (价格权重80%, 成交量权重20%)

        返回:
        {
            'bullish_count': int,       # 阳线数量
            'bearish_count': int,       # 阴线数量
            'bullish_power': float,     # 阳线力度总和
            'bearish_power': float,     # 阴线力度总和
            'dominant': 'BULL'|'BEAR'|'NEUTRAL'  # 主导方向
        }
        """
        query = """
            SELECT open_price, close_price, high_price, low_price, volume
            FROM kline_data
            WHERE symbol = %s
            AND timeframe = %s
            AND exchange = 'binance_futures'
            ORDER BY open_time DESC
            LIMIT %s
        """

        cursor.execute(query, (symbol, timeframe, count))
        klines = cursor.fetchall()

        if not klines or len(klines) < count:
            return {
                'bullish_count': 0,
                'bearish_count': 0,
                'bullish_power': 0,
                'bearish_power': 0,
                'dominant': 'NEUTRAL'
            }

        # 先收集所有数据,用于计算成交量归一化
        volumes = [float(k['volume']) if k['volume'] else 0 for k in klines]
        max_volume = max(volumes) if volumes else 1
        min_volume = min(volumes) if volumes else 0
        volume_range = max_volume - min_volume if max_volume != min_volume else 1

        bullish_count = 0
        bearish_count = 0
        bullish_power = 0  # 阳线力度 = Σ(价格变化% × 0.8 + 成交量归一化 × 0.2)
        bearish_power = 0  # 阴线力度 = Σ(价格变化% × 0.8 + 成交量归一化 × 0.2)

        # 记录首尾价格,用于计算总体波动
        first_open = float(klines[-1]['open_price'])  # 最早的K线开盘价
        last_close = float(klines[0]['close_price'])   # 最新的K线收盘价

        # 记录最高最低价,用于计算波动幅度
        all_highs = [float(k['high_price']) for k in klines]
        all_lows = [float(k['low_price']) for k in klines]
        period_high = max(all_highs)
        period_low = min(all_lows)

        # 记录大波动K线数量(单根涨跌>3%)
        big_bullish_candles = 0
        big_bearish_candles = 0

        for k in klines:
            open_p = float(k['open_price'])
            close_p = float(k['close_price'])
            volume = float(k['volume']) if k['volume'] else 0

            # 成交量归一化到 0-100
            volume_normalized = ((volume - min_volume) / volume_range * 100) if volume_range > 0 else 0

            if close_p > open_p:
                # 阳线
                bullish_count += 1
                price_change_pct = (close_p - open_p) / open_p * 100
                # 力度 = 价格变化%(80%) + 成交量归一化(20%)
                power = price_change_pct * 0.8 + volume_normalized * 0.2
                bullish_power += power

                # 检测大阳线(单根涨幅>3%)
                if price_change_pct > 3:
                    big_bullish_candles += 1
            else:
                # 阴线
                bearish_count += 1
                price_change_pct = (open_p - close_p) / open_p * 100
                # 力度 = 价格变化%(80%) + 成交量归一化(20%)
                power = price_change_pct * 0.8 + volume_normalized * 0.2
                bearish_power += power

                # 检测大阴线(单根跌幅>3%)
                if price_change_pct > 3:
                    big_bearish_candles += 1

        # 计算总体价格变化
        total_change_pct = (last_close - first_open) / first_open * 100

        # 计算波动幅度 (high-low)/low
        volatility_pct = (period_high - period_low) / period_low * 100 if period_low > 0 else 0

        # 判断主导方向 (综合多个因素)
        # 动态阈值: 根据K线数量自适应
        # - 30根K线: 17/30 = 56.7% → 阳线/阴线>=17
        # - 5根K线: 3/5 = 60% → 阳线/阴线>=3
        threshold_ratio = 0.6  # 60%占比
        count_threshold = int(count * threshold_ratio)  # 30根→18, 5根→3

        # 1. 如果波动率<3%, 无论如何都是震荡市
        if volatility_pct < 3:
            dominant = 'NEUTRAL'
        # 2. 如果总体涨跌幅很小(<2%), 也是震荡市
        elif abs(total_change_pct) < 2:
            dominant = 'NEUTRAL'
        # 3. 否则综合判断: 阴阳比例 + 力度对比
        else:
            # 基础判断: 阳线/阴线数量>=阈值
            count_bullish = bullish_count >= count_threshold
            count_bearish = bearish_count >= count_threshold

            # 力度判断: bullish_power明显大于bearish_power (差距>20%)
            power_bullish = bullish_power > bearish_power * 1.2 if bearish_power > 0 else bullish_power > 0
            power_bearish = bearish_power > bullish_power * 1.2 if bullish_power > 0 else bearish_power > 0

            # 大波动K线数量 (根据总数量调整阈值)
            # 30根K线→2根大K线, 5根K线→1根大K线
            big_candle_threshold = max(1, int(count * 0.067))  # 30根→2, 5根→1
            big_candle_bullish = big_bullish_candles >= big_candle_threshold
            big_candle_bearish = big_bearish_candles >= big_candle_threshold

            # 综合判断 (3个条件满足2个即可)
            bullish_signals = sum([count_bullish, power_bullish, big_candle_bullish])
            bearish_signals = sum([count_bearish, power_bearish, big_candle_bearish])

            if bullish_signals >= 2:
                dominant = 'BULL'
            elif bearish_signals >= 2:
                dominant = 'BEAR'
            else:
                dominant = 'NEUTRAL'

        return {
            'bullish_count': bullish_count,
            'bearish_count': bearish_count,
            'bullish_power': bullish_power,
            'bearish_power': bearish_power,
            'dominant': dominant
        }

    def _detect_5m_signal(self, cursor, symbol: str) -> Dict:
        """
        检测5M买卖时机 (最近3根K线)

        检测突破:
        - 力度 = 价格变化% × 0.8 + 成交量归一化 × 0.2
        """
        query = """
            SELECT open_price, close_price, high_price, low_price, volume
            FROM kline_data
            WHERE symbol = %s
            AND timeframe = '5m'
            AND exchange = 'binance_futures'
            ORDER BY open_time DESC
            LIMIT 3
        """

        cursor.execute(query, (symbol,))
        klines = cursor.fetchall()

        if not klines or len(klines) < 3:
            return {
                'detected': False,
                'direction': 'NEUTRAL',
                'strength': 0,
                'reason': '数据不足'
            }

        # 先收集所有成交量,用于归一化
        volumes = [float(k['volume']) if k['volume'] else 0 for k in klines]
        max_volume = max(volumes) if volumes else 1
        min_volume = min(volumes) if volumes else 0
        volume_range = max_volume - min_volume if max_volume != min_volume else 1

        # 分析最近3根K线
        total_bull_power = 0
        total_bear_power = 0

        for k in klines:
            open_p = float(k['open_price'])
            close_p = float(k['close_price'])
            volume = float(k['volume']) if k['volume'] else 0

            # 成交量归一化到 0-100
            volume_normalized = ((volume - min_volume) / volume_range * 100) if volume_range > 0 else 0

            if close_p > open_p:
                # 阳线力度
                price_change_pct = (close_p - open_p) / open_p * 100
                power = price_change_pct * 0.8 + volume_normalized * 0.2
                total_bull_power += power
            else:
                # 阴线力度
                price_change_pct = (open_p - close_p) / open_p * 100
                power = price_change_pct * 0.8 + volume_normalized * 0.2
                total_bear_power += power

        # 判断突破方向
        if total_bull_power > total_bear_power * 1.5:  # 多头力度明显强于空头
            detected = True
            direction = 'BULLISH'
            strength = min(total_bull_power / max(total_bear_power, 1), 100)
            reason = f"5M多头突破(力度比{total_bull_power/max(total_bear_power, 1):.1f}:1)"
        elif total_bear_power > total_bull_power * 1.5:  # 空头力度明显强于多头
            detected = True
            direction = 'BEARISH'
            strength = min(total_bear_power / max(total_bull_power, 1), 100)
            reason = f"5M空头突破(力度比{total_bear_power/max(total_bull_power, 1):.1f}:1)"
        else:
            detected = False
            direction = 'NEUTRAL'
            strength = 0
            reason = '5M无明显突破'

        return {
            'detected': detected,
            'direction': direction,
            'strength': strength,
            'reason': reason
        }

    def _generate_signal(
        self,
        kline_1h_30: Dict,
        kline_1h_5: Dict,
        kline_15m: Dict,
        kline_5m: Dict
    ) -> Tuple[str, int, str]:
        """
        综合生成信号 (简化版 + 趋势修正)

        趋势修正规则:
        1. 如果30H大趋势BEAR + 5H小趋势BULL → 修正为NEUTRAL (不做空)
        2. 如果30H大趋势BULL + 5H小趋势BEAR → 修正为NEUTRAL (不做多)
        3. 如果30H大趋势和5H小趋势一致 → 保持大趋势方向
        4. 如果30H大趋势NEUTRAL → 跟随5H小趋势

        权重分配:
        - 修正后的1H方向: 60分
        - 15M趋势确认: 30分
        - 5M买卖时机: 10分

        返回: (信号方向, 强度0-100, 原因)
        """
        signal_score = 0  # -100 to +100
        reasons = []

        # === 步骤1: 趋势修正逻辑 ===
        big_trend = kline_1h_30['dominant']  # 30H大趋势
        small_trend = kline_1h_5['dominant']  # 5H小趋势

        # 趋势修正
        if big_trend == 'BEAR' and small_trend == 'BULL':
            # 大趋势下跌但小趋势上涨 → 修正为震荡
            corrected_trend = 'NEUTRAL'
            reasons.append(f"⚠️趋势修正: 30H下跌({kline_1h_30['bearish_count']}阴)但5H反弹({kline_1h_5['bullish_count']}阳) → 震荡")
        elif big_trend == 'BULL' and small_trend == 'BEAR':
            # 大趋势上涨但小趋势下跌 → 修正为震荡
            corrected_trend = 'NEUTRAL'
            reasons.append(f"⚠️趋势修正: 30H上涨({kline_1h_30['bullish_count']}阳)但5H回调({kline_1h_5['bearish_count']}阴) → 震荡")
        elif big_trend == 'NEUTRAL':
            # 大趋势震荡 → 跟随小趋势
            corrected_trend = small_trend
            if small_trend == 'BULL':
                reasons.append(f"30H震荡,跟随5H上涨({kline_1h_5['bullish_count']}阳:{kline_1h_5['bearish_count']}阴)")
            elif small_trend == 'BEAR':
                reasons.append(f"30H震荡,跟随5H下跌({kline_1h_5['bearish_count']}阴:{kline_1h_5['bullish_count']}阳)")
            else:
                reasons.append("30H震荡,5H无明确方向")
        else:
            # 大小趋势一致 → 保持大趋势
            corrected_trend = big_trend
            if big_trend == 'BULL':
                reasons.append(f"1H多头({kline_1h_30['bullish_count']}阳,5H确认{kline_1h_5['bullish_count']}阳)")
            elif big_trend == 'BEAR':
                reasons.append(f"1H空头({kline_1h_30['bearish_count']}阴,5H确认{kline_1h_5['bearish_count']}阴)")

        # === 步骤2: 基于修正后的趋势打分 ===
        # 1. 修正后的1H方向 (权重: 60)
        if corrected_trend == 'BULL':
            signal_score += 60
        elif corrected_trend == 'BEAR':
            signal_score -= 60

        # 2. 15M趋势确认 (权重: 30)
        if kline_15m['dominant'] == 'BULL':
            signal_score += 30
            reasons.append(f"15M多头确认({kline_15m['bullish_count']}阳:{kline_15m['bearish_count']}阴)")
        elif kline_15m['dominant'] == 'BEAR':
            signal_score -= 30
            reasons.append(f"15M空头确认({kline_15m['bearish_count']}阴:{kline_15m['bullish_count']}阳)")

        # 3. 5M买卖时机 (权重: 10)
        if kline_5m['detected']:
            if kline_5m['direction'] == 'BULLISH':
                signal_score += 10
                reasons.append(kline_5m['reason'])
            elif kline_5m['direction'] == 'BEARISH':
                signal_score -= 10
                reasons.append(kline_5m['reason'])

        # === 步骤3: 生成最终信号 ===
        if signal_score > 30:
            signal = 'BULLISH'
        elif signal_score < -30:
            signal = 'BEARISH'
        else:
            signal = 'NEUTRAL'

        strength = min(abs(signal_score), 100)
        reason = ' | '.join(reasons) if reasons else '无明显信号'

        return signal, strength, reason

    def _save_to_database(self, result: Dict):
        """保存检测结果到数据库"""
        try:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor()

            details = result['details']

            cursor.execute("""
                INSERT INTO big4_trend_history (
                    overall_signal, signal_strength, bullish_count, bearish_count, recommendation,
                    btc_signal, btc_strength, btc_reason, btc_1h_dominant, btc_15m_dominant,
                    eth_signal, eth_strength, eth_reason, eth_1h_dominant, eth_15m_dominant,
                    bnb_signal, bnb_strength, bnb_reason, bnb_1h_dominant, bnb_15m_dominant,
                    sol_signal, sol_strength, sol_reason, sol_1h_dominant, sol_15m_dominant
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
            """, (
                result['overall_signal'],
                result['signal_strength'],
                result['bullish_count'],
                result['bearish_count'],
                result['recommendation'],
                # BTC
                details['BTC/USDT']['signal'],
                details['BTC/USDT']['strength'],
                details['BTC/USDT']['reason'],
                details['BTC/USDT']['1h_30_analysis']['dominant'],
                details['BTC/USDT']['15m_analysis']['dominant'],
                # ETH
                details['ETH/USDT']['signal'],
                details['ETH/USDT']['strength'],
                details['ETH/USDT']['reason'],
                details['ETH/USDT']['1h_30_analysis']['dominant'],
                details['ETH/USDT']['15m_analysis']['dominant'],
                # BNB
                details['BNB/USDT']['signal'],
                details['BNB/USDT']['strength'],
                details['BNB/USDT']['reason'],
                details['BNB/USDT']['1h_30_analysis']['dominant'],
                details['BNB/USDT']['15m_analysis']['dominant'],
                # SOL
                details['SOL/USDT']['signal'],
                details['SOL/USDT']['strength'],
                details['SOL/USDT']['reason'],
                details['SOL/USDT']['1h_30_analysis']['dominant'],
                details['SOL/USDT']['15m_analysis']['dominant']
            ))

            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"✅ Big4趋势已保存: {result['overall_signal']} (强度: {result['signal_strength']:.0f})")

        except Exception as e:
            logger.error(f"❌ 保存Big4趋势失败: {e}")


if __name__ == '__main__':
    # 测试代码
    logging.basicConfig(level=logging.INFO)
    detector = Big4TrendDetector()
    result = detector.detect_market_trend()

    print("\n" + "=" * 80)
    print(f"Big4市场趋势: {result['overall_signal']} (强度: {result['signal_strength']:.0f})")
    print(f"建议: {result['recommendation']}")
    print("=" * 80)

    for symbol, detail in result['details'].items():
        print(f"\n{symbol}:")
        print(f"  信号: {detail['signal']} (强度: {detail['strength']:.0f})")
        print(f"  原因: {detail['reason']}")
