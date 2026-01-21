#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场观察器 - 监控主流币种走势,提供市场状态分析和预警

监控币种: BTC, ETH, SOL, BNB, DOGE
功能:
1. 实时趋势分析 (上涨/下跌/震荡)
2. 市场情绪判断 (牛市/熊市/震荡市)
3. 异常波动预警
4. 关键支撑/阻力位监控
"""

import ccxt
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from loguru import logger
import pymysql


class MarketObserver:
    """市场观察器"""

    def __init__(self, db_config: dict = None):
        self.db_config = db_config
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })

        # 主流币种
        self.major_symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'DOGE/USDT']

        # 市场状态阈值
        self.thresholds = {
            'strong_trend': 0.03,      # 强趋势: 3%以上
            'moderate_trend': 0.015,   # 中等趋势: 1.5%以上
            'consolidation': 0.01,     # 震荡: 1%以内
            'extreme_volatility': 0.05, # 极端波动: 5%以上
            'volume_surge': 2.0,       # 成交量激增: 2倍以上
        }

    def analyze_market_state(self) -> Dict:
        """
        分析当前市场状态

        返回:
        - overall_trend: 整体趋势 (bullish/bearish/neutral)
        - market_strength: 市场强度 (0-100)
        - symbols_analysis: 各币种详细分析
        - warnings: 预警信息
        """
        logger.info("🔍 开始分析市场状态...")

        symbols_analysis = {}
        bullish_count = 0
        bearish_count = 0
        warnings = []

        for symbol in self.major_symbols:
            try:
                analysis = self._analyze_symbol(symbol)
                symbols_analysis[symbol] = analysis

                # 统计多空趋势
                if analysis['trend'] == 'bullish':
                    bullish_count += 1
                elif analysis['trend'] == 'bearish':
                    bearish_count += 1

                # 收集预警
                if analysis['warnings']:
                    warnings.extend([f"{symbol}: {w}" for w in analysis['warnings']])

            except Exception as e:
                logger.error(f"分析 {symbol} 失败: {e}")
                continue

        # 判断整体市场趋势
        total_analyzed = len(symbols_analysis)
        if bullish_count >= total_analyzed * 0.6:
            overall_trend = 'bullish'
            market_strength = 70 + (bullish_count / total_analyzed) * 30
        elif bearish_count >= total_analyzed * 0.6:
            overall_trend = 'bearish'
            market_strength = 30 - (bearish_count / total_analyzed) * 30
        else:
            overall_trend = 'neutral'
            market_strength = 50

        result = {
            'timestamp': datetime.now(),
            'overall_trend': overall_trend,
            'market_strength': round(market_strength, 2),
            'bullish_count': bullish_count,
            'bearish_count': bearish_count,
            'neutral_count': total_analyzed - bullish_count - bearish_count,
            'symbols_analysis': symbols_analysis,
            'warnings': warnings
        }

        # 保存到数据库
        if self.db_config:
            self._save_market_state(result)

        return result

    def _analyze_symbol(self, symbol: str) -> Dict:
        """
        分析单个币种

        分析内容:
        1. 多时间框架趋势 (15m, 1h, 4h, 1d)
        2. 价格变化百分比
        3. 成交量变化
        4. 关键价格位
        5. RSI指标
        """

        # 获取多时间框架K线数据
        klines_15m = self.exchange.fetch_ohlcv(symbol, '15m', limit=100)
        klines_1h = self.exchange.fetch_ohlcv(symbol, '1h', limit=100)
        klines_4h = self.exchange.fetch_ohlcv(symbol, '4h', limit=50)
        klines_1d = self.exchange.fetch_ohlcv(symbol, '1d', limit=30)

        current_price = klines_15m[-1][4]  # 当前收盘价

        # 计算各时间框架趋势
        trend_15m = self._calculate_trend(klines_15m, 20)
        trend_1h = self._calculate_trend(klines_1h, 20)
        trend_4h = self._calculate_trend(klines_4h, 14)
        trend_1d = self._calculate_trend(klines_1d, 10)

        # 价格变化
        price_change_1h = (klines_1h[-1][4] - klines_1h[-2][4]) / klines_1h[-2][4]
        price_change_4h = (klines_4h[-1][4] - klines_4h[-5][4]) / klines_4h[-5][4]
        price_change_1d = (klines_1d[-1][4] - klines_1d[-2][4]) / klines_1d[-2][4]

        # 成交量分析
        volume_avg = np.mean([k[5] for k in klines_1h[-20:]])
        volume_current = klines_1h[-1][5]
        volume_ratio = volume_current / volume_avg if volume_avg > 0 else 1

        # RSI计算
        rsi = self._calculate_rsi([k[4] for k in klines_1h], period=14)

        # 波动率
        volatility = np.std([k[4] for k in klines_1h[-20:]]) / np.mean([k[4] for k in klines_1h[-20:]])

        # 综合趋势判断
        trend_score = (
            trend_15m['strength'] * 0.2 +
            trend_1h['strength'] * 0.3 +
            trend_4h['strength'] * 0.3 +
            trend_1d['strength'] * 0.2
        )

        if trend_score > 0.02:
            overall_trend = 'bullish'
        elif trend_score < -0.02:
            overall_trend = 'bearish'
        else:
            overall_trend = 'neutral'

        # 生成预警
        warnings = []

        # 极端波动预警
        if abs(price_change_1h) > self.thresholds['extreme_volatility']:
            direction = "暴涨" if price_change_1h > 0 else "暴跌"
            warnings.append(f"1小时{direction} {abs(price_change_1h)*100:.2f}%")

        # 成交量异常预警
        if volume_ratio > self.thresholds['volume_surge']:
            warnings.append(f"成交量激增 {volume_ratio:.1f}x")

        # RSI超买超卖预警
        if rsi > 75:
            warnings.append(f"RSI超买 ({rsi:.1f})")
        elif rsi < 25:
            warnings.append(f"RSI超卖 ({rsi:.1f})")

        # 趋势反转预警
        if trend_1h['direction'] != trend_4h['direction']:
            warnings.append("短期趋势与中期趋势背离")

        return {
            'symbol': symbol,
            'current_price': current_price,
            'trend': overall_trend,
            'trend_score': round(trend_score, 4),
            'price_changes': {
                '1h': round(price_change_1h * 100, 2),
                '4h': round(price_change_4h * 100, 2),
                '1d': round(price_change_1d * 100, 2)
            },
            'volume_ratio': round(volume_ratio, 2),
            'rsi': round(rsi, 2),
            'volatility': round(volatility * 100, 2),
            'timeframe_trends': {
                '15m': trend_15m['direction'],
                '1h': trend_1h['direction'],
                '4h': trend_4h['direction'],
                '1d': trend_1d['direction']
            },
            'warnings': warnings
        }

    def _calculate_trend(self, klines: List, period: int) -> Dict:
        """
        计算趋势方向和强度

        使用EMA和价格斜率
        """
        closes = [k[4] for k in klines[-period:]]

        # 计算EMA
        ema = self._calculate_ema(closes, period)
        current_price = closes[-1]

        # 价格相对EMA的位置
        price_ema_diff = (current_price - ema[-1]) / ema[-1]

        # EMA斜率
        ema_slope = (ema[-1] - ema[-5]) / ema[-5] if len(ema) >= 5 else 0

        # 趋势方向
        if price_ema_diff > 0.01 and ema_slope > 0.005:
            direction = 'up'
            strength = min(abs(price_ema_diff) + abs(ema_slope), 0.1)
        elif price_ema_diff < -0.01 and ema_slope < -0.005:
            direction = 'down'
            strength = -min(abs(price_ema_diff) + abs(ema_slope), 0.1)
        else:
            direction = 'sideways'
            strength = 0

        return {
            'direction': direction,
            'strength': strength,
            'price_ema_diff': price_ema_diff,
            'ema_slope': ema_slope
        }

    def _calculate_ema(self, prices: List[float], period: int) -> List[float]:
        """计算指数移动平均线"""
        ema = []
        multiplier = 2 / (period + 1)

        # 初始EMA使用SMA
        ema.append(np.mean(prices[:period]))

        for price in prices[period:]:
            ema.append((price - ema[-1]) * multiplier + ema[-1])

        return ema

    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """计算RSI指标"""
        if len(prices) < period + 1:
            return 50

        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])

        if avg_loss == 0:
            return 100

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    def _save_market_state(self, state: Dict):
        """保存市场状态到数据库"""
        if not self.db_config:
            return

        try:
            conn = pymysql.connect(**self.db_config, charset='utf8mb4')
            cursor = conn.cursor()

            # 创建表(如果不存在)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_observations (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    timestamp TIMESTAMP NOT NULL,
                    overall_trend VARCHAR(20),
                    market_strength DECIMAL(5,2),
                    bullish_count INT,
                    bearish_count INT,
                    neutral_count INT,
                    btc_price DECIMAL(12,2),
                    btc_trend VARCHAR(20),
                    eth_price DECIMAL(12,2),
                    eth_trend VARCHAR(20),
                    warnings TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_timestamp (timestamp)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            # 插入数据
            btc_data = state['symbols_analysis'].get('BTC/USDT', {})
            eth_data = state['symbols_analysis'].get('ETH/USDT', {})

            cursor.execute("""
                INSERT INTO market_observations
                (timestamp, overall_trend, market_strength, bullish_count, bearish_count, neutral_count,
                 btc_price, btc_trend, eth_price, eth_trend, warnings)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                state['timestamp'],
                state['overall_trend'],
                state['market_strength'],
                state['bullish_count'],
                state['bearish_count'],
                state['neutral_count'],
                btc_data.get('current_price'),
                btc_data.get('trend'),
                eth_data.get('current_price'),
                eth_data.get('trend'),
                '\n'.join(state['warnings']) if state['warnings'] else None
            ))

            conn.commit()
            cursor.close()
            conn.close()

        except Exception as e:
            logger.error(f"保存市场状态失败: {e}")

    def get_trading_recommendation(self, market_state: Dict) -> str:
        """
        基于市场状态给出交易建议

        返回: aggressive / moderate / conservative / pause
        """
        overall_trend = market_state['overall_trend']
        market_strength = market_state['market_strength']
        warnings_count = len(market_state['warnings'])

        # 极端市场条件 - 暂停交易
        if warnings_count >= 3:
            return 'pause'

        # 强势牛市 - 激进做多
        if overall_trend == 'bullish' and market_strength > 75:
            return 'aggressive_long'

        # 强势熊市 - 激进做空
        if overall_trend == 'bearish' and market_strength < 25:
            return 'aggressive_short'

        # 温和牛市 - 适度做多
        if overall_trend == 'bullish' and 60 <= market_strength <= 75:
            return 'moderate_long'

        # 温和熊市 - 适度做空
        if overall_trend == 'bearish' and 25 <= market_strength <= 40:
            return 'moderate_short'

        # 震荡市 - 保守交易
        if overall_trend == 'neutral':
            return 'conservative'

        return 'moderate'

    def print_market_report(self, state: Dict):
        """打印市场观察报告"""
        print('\n' + '=' * 100)
        print('📊 市场观察报告')
        print('=' * 100)
        print(f"\n时间: {state['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"整体趋势: {state['overall_trend'].upper()}")
        print(f"市场强度: {state['market_strength']:.1f}/100")
        print(f"多头币种: {state['bullish_count']} | 空头币种: {state['bearish_count']} | 震荡: {state['neutral_count']}")

        # 各币种详情
        print(f"\n{'-' * 100}")
        print(f"{'币种':<12} {'价格':<12} {'趋势':<10} {'1H变化':<10} {'4H变化':<10} {'1D变化':<10} {'RSI':<8} {'预警':<30}")
        print('-' * 100)

        for symbol, data in state['symbols_analysis'].items():
            warnings_str = ', '.join(data['warnings'][:2]) if data['warnings'] else '-'
            if len(warnings_str) > 28:
                warnings_str = warnings_str[:25] + '...'

            print(f"{symbol:<12} ${data['current_price']:<11.2f} {data['trend']:<10} "
                  f"{data['price_changes']['1h']:>8.2f}% {data['price_changes']['4h']:>8.2f}% "
                  f"{data['price_changes']['1d']:>8.2f}% {data['rsi']:>6.1f} {warnings_str:<30}")

        # 预警信息
        if state['warnings']:
            print(f"\n{'⚠️ 市场预警':^100}")
            print('-' * 100)
            for i, warning in enumerate(state['warnings'], 1):
                print(f"  {i}. {warning}")

        # 交易建议
        recommendation = self.get_trading_recommendation(state)
        print(f"\n{'💡 交易建议':^100}")
        print('-' * 100)
        rec_map = {
            'pause': '⛔ 暂停交易 - 市场异常波动',
            'aggressive_long': '🚀 激进做多 - 强势牛市',
            'aggressive_short': '📉 激进做空 - 强势熊市',
            'moderate_long': '📈 适度做多 - 温和上涨',
            'moderate_short': '📊 适度做空 - 温和下跌',
            'conservative': '⚖️ 保守交易 - 震荡市场',
            'moderate': '🎯 正常交易 - 平稳市场'
        }
        print(f"  {rec_map.get(recommendation, recommendation)}")

        print('=' * 100 + '\n')
