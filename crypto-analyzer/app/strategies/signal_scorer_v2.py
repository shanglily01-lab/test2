#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信号评分器 V2版本
基于1小时K线的多维度评分系统

核心逻辑:
- 主要使用1小时K线分析(48根,2天数据)
- 1天K线作为辅助确认大趋势
- 多维度评分: 位置/动量/趋势/波动率/连续性
"""

from typing import Dict, List, Optional
from loguru import logger


class SignalScorerV2:
    """V2版本信号评分器 - 基于1H K线"""

    def __init__(self, db_config: dict):
        self.db_config = db_config
        self.connection = None

        # V2评分阈值 (总分90分)
        self.min_score_to_trade = 35  # 38.9%开仓阈值

        # V2评分权重配置
        self.scoring_weights = {
            'position_low': {'long': 20, 'short': 0},       # 72H低位
            'position_mid': {'long': 5, 'short': 5},        # 72H中位
            'position_high': {'long': 0, 'short': 20},      # 72H高位
            'momentum_down_3pct': {'long': 15, 'short': 0}, # 24H跌3%
            'momentum_up_3pct': {'long': 0, 'short': 15},   # 24H涨3%
            'trend_1h_bull': {'long': 20, 'short': 0},      # 48H趋势看多
            'trend_1h_bear': {'long': 0, 'short': 20},      # 48H趋势看空
            'volatility_high': {'long': 10, 'short': 10},   # 波动率>5%
            'consecutive_bull': {'long': 15, 'short': 0},   # 10H连续阳线
            'consecutive_bear': {'long': 0, 'short': 15},   # 10H连续阴线
            'trend_1d_bull': {'long': 10, 'short': 0},      # 30D趋势确认
            'trend_1d_bear': {'long': 0, 'short': 10}       # 30D趋势确认
        }

        logger.info("✅ V2信号评分器已初始化")
        logger.info(f"   评分阈值: {self.min_score_to_trade}分/{sum([max(w.values()) for w in self.scoring_weights.values()])}分 ({self.min_score_to_trade/90*100:.1f}%)")

    def _get_connection(self):
        """获取数据库连接"""
        import pymysql
        if self.connection is None or not self.connection.open:
            self.connection = pymysql.connect(
                **self.db_config,
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
        else:
            try:
                self.connection.ping(reconnect=True)
            except:
                self.connection = pymysql.connect(
                    **self.db_config,
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor
                )
        return self.connection

    def load_klines(self, symbol: str, timeframe: str, limit: int) -> List[Dict]:
        """从数据库加载K线数据"""
        conn = self._get_connection()
        cursor = conn.cursor()

        query = """
            SELECT open_price as open, high_price as high,
                   low_price as low, close_price as close
            FROM kline_data
            WHERE symbol = %s AND timeframe = %s
            AND open_time >= UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 60 DAY)) * 1000
            ORDER BY open_time DESC LIMIT %s
        """
        cursor.execute(query, (symbol, timeframe, limit))
        klines = list(cursor.fetchall())
        cursor.close()

        # 反转顺序(从旧到新)
        klines.reverse()

        # 转换数据类型
        for k in klines:
            k['open'] = float(k['open'])
            k['high'] = float(k['high'])
            k['low'] = float(k['low'])
            k['close'] = float(k['close'])

        return klines

    def calculate_score(self, symbol: str) -> Optional[Dict]:
        """
        计算V2评分

        Returns:
            {
                'symbol': str,
                'side': 'LONG'/'SHORT',
                'score': int,
                'details': dict,  # 评分明细
                'current_price': float
            }
        """
        try:
            # 加载K线数据
            klines_1d = self.load_klines(symbol, '1d', 50)
            klines_1h = self.load_klines(symbol, '1h', 100)

            # 数据验证
            if len(klines_1d) < 30 or len(klines_1h) < 72:
                logger.debug(f"{symbol} K线数据不足 (1d:{len(klines_1d)}, 1h:{len(klines_1h)})")
                return None

            current_price = klines_1h[-1]['close']

            # 初始化评分
            long_score = 0
            short_score = 0
            signal_components = {}  # 记录评分明细

            # ========== 1. 位置评分 - 使用72小时(3天)高低点 ==========
            high_72h = max(k['high'] for k in klines_1h[-72:])
            low_72h = min(k['low'] for k in klines_1h[-72:])

            if high_72h == low_72h:
                position_pct = 50
            else:
                position_pct = (current_price - low_72h) / (high_72h - low_72h) * 100

            # 低位做多，高位做空
            if position_pct < 30:
                weight = self.scoring_weights['position_low']
                long_score += weight['long']
                if weight['long'] > 0:
                    signal_components['position_low'] = weight['long']
            elif position_pct > 70:
                weight = self.scoring_weights['position_high']
                short_score += weight['short']
                if weight['short'] > 0:
                    signal_components['position_high'] = weight['short']
            else:
                weight = self.scoring_weights['position_mid']
                long_score += weight['long']
                short_score += weight['short']
                if weight['long'] > 0:
                    signal_components['position_mid'] = weight['long']

            # ========== 2. 短期动量 - 最近24小时涨幅 ==========
            gain_24h = (current_price - klines_1h[-24]['close']) / klines_1h[-24]['close'] * 100

            if gain_24h < -3:  # 24小时跌超过3%
                weight = self.scoring_weights['momentum_down_3pct']
                long_score += weight['long']
                if weight['long'] > 0:
                    signal_components['momentum_down_3pct'] = weight['long']
            elif gain_24h > 3:  # 24小时涨超过3%
                weight = self.scoring_weights['momentum_up_3pct']
                short_score += weight['short']
                if weight['short'] > 0:
                    signal_components['momentum_up_3pct'] = weight['short']

            # ========== 3. 1小时趋势评分 - 最近48根K线(2天) ==========
            bullish_1h = sum(1 for k in klines_1h[-48:] if k['close'] > k['open'])
            bearish_1h = 48 - bullish_1h

            if bullish_1h > 30:  # 超过62.5%是阳线
                weight = self.scoring_weights['trend_1h_bull']
                long_score += weight['long']
                if weight['long'] > 0:
                    signal_components['trend_1h_bull'] = weight['long']
            elif bearish_1h > 30:  # 超过62.5%是阴线
                weight = self.scoring_weights['trend_1h_bear']
                short_score += weight['short']
                if weight['short'] > 0:
                    signal_components['trend_1h_bear'] = weight['short']

            # ========== 4. 波动率评分 - 最近24小时 ==========
            recent_24h = klines_1h[-24:]
            volatility = (max(k['high'] for k in recent_24h) - min(k['low'] for k in recent_24h)) / current_price * 100

            # 高波动率更适合交易
            if volatility > 5:  # 波动超过5%
                weight = self.scoring_weights['volatility_high']
                if long_score > short_score:
                    long_score += weight['long']
                    if weight['long'] > 0:
                        signal_components['volatility_high'] = weight['long']
                else:
                    short_score += weight['short']
                    if weight['short'] > 0:
                        signal_components['volatility_high'] = weight['short']

            # ========== 5. 连续趋势强化信号 - 最近10根1小时K线 ==========
            recent_10h = klines_1h[-10:]
            bullish_10h = sum(1 for k in recent_10h if k['close'] > k['open'])
            bearish_10h = 10 - bullish_10h

            # 计算最近10小时涨跌幅
            gain_10h = (current_price - recent_10h[0]['close']) / recent_10h[0]['close'] * 100

            # 连续阳线且上涨幅度适中(不在顶部) - 强做多信号
            if bullish_10h >= 7 and gain_10h < 5 and position_pct < 70:
                weight = self.scoring_weights['consecutive_bull']
                long_score += weight['long']
                if weight['long'] > 0:
                    signal_components['consecutive_bull'] = weight['long']

            # 连续阴线且下跌幅度适中(不在底部) - 强做空信号
            elif bearish_10h >= 7 and gain_10h > -5 and position_pct > 30:
                weight = self.scoring_weights['consecutive_bear']
                short_score += weight['short']
                if weight['short'] > 0:
                    signal_components['consecutive_bear'] = weight['short']

            # ========== 6. 1天K线确认 (辅助) ==========
            # 大趋势确认: 如果30天趋势与1小时趋势一致，加分
            bullish_1d = sum(1 for k in klines_1d[-30:] if k['close'] > k['open'])
            bearish_1d = 30 - bullish_1d

            if bullish_1d > 18 and long_score > short_score:  # 大趋势上涨且1小时也看多
                weight = self.scoring_weights['trend_1d_bull']
                long_score += weight['long']
                if weight['long'] > 0:
                    signal_components['trend_1d_bull'] = weight['long']
            elif bearish_1d > 18 and short_score > long_score:  # 大趋势下跌且1小时也看空
                weight = self.scoring_weights['trend_1d_bear']
                short_score += weight['short']
                if weight['short'] > 0:
                    signal_components['trend_1d_bear'] = weight['short']

            # ========== 7. 选择得分更高的方向 ==========
            if long_score >= self.min_score_to_trade or short_score >= self.min_score_to_trade:
                if long_score >= short_score:
                    side = 'LONG'
                    score = long_score
                else:
                    side = 'SHORT'
                    score = short_score

                return {
                    'symbol': symbol,
                    'side': side,
                    'score': score,
                    'details': {
                        'position_pct': position_pct,
                        'gain_24h': gain_24h,
                        'gain_10h': gain_10h,
                        'volatility': volatility,
                        'bullish_1h_pct': bullish_1h / 48 * 100,
                        'bullish_10h': bullish_10h,
                        'components': signal_components
                    },
                    'current_price': current_price
                }

            return None

        except Exception as e:
            logger.error(f"{symbol} V2评分计算失败: {e}")
            return None

    def scan_all_symbols(self, symbols: List[str]) -> List[Dict]:
        """
        扫描所有交易对

        Args:
            symbols: 交易对列表

        Returns:
            评分结果列表
        """
        results = []
        for symbol in symbols:
            result = self.calculate_score(symbol)
            if result:
                results.append(result)
        return results
