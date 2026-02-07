#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信号评分系统 V3.0
多时间周期权重评分: Big4(5分) + 5H(8分) + 15M(10分) + 量价(7分) + 技术指标(10分)
"""

import pymysql
import os
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()


class SignalScorerV3:
    """信号评分系统 V3.0"""

    def __init__(self, db_config: dict):
        self.db_config = db_config

        # 评分权重配置
        self.score_weights = {
            'big4': 5,          # Big4 (30H宏观趋势)
            '5h_trend': 8,      # 5H趋势 (3根K线)
            '15m_signal': 10,   # 2H内15M信号 (8根K线)
            'volume_price': 7,  # 量价配合
            'technical': 10     # 技术指标
        }

        # 总分和阈值
        self.max_score = 40
        self.min_score_to_trade = 25  # 62.5%以上才开仓

    def get_db_connection(self):
        """获取数据库连接"""
        return pymysql.connect(
            host=self.db_config['host'],
            user=self.db_config['user'],
            password=self.db_config['password'],
            database=self.db_config['database'],
            cursorclass=pymysql.cursors.DictCursor
        )

    def calculate_total_score(
        self,
        symbol: str,
        position_side: str,
        klines_5h: List[Dict],
        klines_15m: List[Dict],
        big4_signal: Optional[str] = None,
        big4_strength: Optional[int] = None
    ) -> Dict:
        """
        计算总分

        Args:
            symbol: 交易对
            position_side: LONG/SHORT
            klines_5h: 5H K线列表 (至少3根)
            klines_15m: 15M K线列表 (至少8根)
            big4_signal: Big4信号 (BULL/BEAR/NEUTRAL)
            big4_strength: Big4强度 (0-100)

        Returns:
            评分结果字典
        """
        print(f"\n{'='*80}")
        print(f"[信号评分V3] {symbol} {position_side}")
        print(f"{'='*80}\n")

        scores = {}

        # 1. Big4评分
        scores['big4'] = self.score_big4(position_side, big4_signal, big4_strength)
        print(f"[Big4评分] {scores['big4']:.1f}/{self.score_weights['big4']} - "
              f"信号:{big4_signal}, 强度:{big4_strength}")

        # 2. 5H趋势评分
        scores['5h_trend'] = self.score_5h_trend(position_side, klines_5h)
        print(f"[5H趋势] {scores['5h_trend']:.1f}/{self.score_weights['5h_trend']}")

        # 3. 15M信号评分
        scores['15m_signal'] = self.score_15m_signal(position_side, klines_15m)
        print(f"[15M信号] {scores['15m_signal']:.1f}/{self.score_weights['15m_signal']}")

        # 4. 量价配合评分
        scores['volume_price'] = self.score_volume_price(position_side, klines_15m)
        print(f"[量价配合] {scores['volume_price']:.1f}/{self.score_weights['volume_price']}")

        # 5. 技术指标评分
        scores['technical'] = self.score_technical_indicators(position_side, klines_15m)
        print(f"[技术指标] {scores['technical']:.1f}/{self.score_weights['technical']}")

        # 计算总分
        total_score = sum(scores.values())
        score_pct = (total_score / self.max_score) * 100

        print(f"\n{'='*80}")
        print(f"[总分] {total_score:.1f}/{self.max_score} ({score_pct:.1f}%)")
        print(f"[阈值] {self.min_score_to_trade}/{self.max_score} ({self.min_score_to_trade/self.max_score*100:.1f}%)")
        print(f"[结果] {'✅ 可开仓' if total_score >= self.min_score_to_trade else '❌ 不可开仓'}")
        print(f"{'='*80}\n")

        return {
            'total_score': total_score,
            'max_score': self.max_score,
            'score_pct': score_pct,
            'can_trade': total_score >= self.min_score_to_trade,
            'breakdown': scores
        }

    def score_big4(
        self,
        position_side: str,
        big4_signal: Optional[str],
        big4_strength: Optional[int]
    ) -> float:
        """
        Big4评分 (max 5分)

        逻辑:
        - 信号方向一致 + 强度>=80: 5分
        - 信号方向一致 + 强度>=70: 4分
        - 信号方向一致 + 强度>=60: 3分
        - 其他: 0分
        """
        if not big4_signal or not big4_strength:
            return 0.0

        # 检查方向是否一致
        if (position_side == 'LONG' and big4_signal == 'BULL') or \
           (position_side == 'SHORT' and big4_signal == 'BEAR'):

            if big4_strength >= 80:
                return 5.0
            elif big4_strength >= 70:
                return 4.0
            elif big4_strength >= 60:
                return 3.0

        return 0.0

    def score_5h_trend(self, position_side: str, klines_5h: List[Dict]) -> float:
        """
        5H趋势评分 (max 8分)

        逻辑:
        - 连续3根同向K线: 8分
        - 2根同向K线: 5分
        - 其他: 0分
        """
        if len(klines_5h) < 3:
            return 0.0

        # 统计阳线和阴线数量
        bull_count = sum(1 for k in klines_5h[:3] if k['close'] > k['open'])
        bear_count = sum(1 for k in klines_5h[:3] if k['close'] < k['open'])

        if position_side == 'LONG':
            if bull_count == 3:
                return 8.0
            elif bull_count == 2:
                return 5.0
        elif position_side == 'SHORT':
            if bear_count == 3:
                return 8.0
            elif bear_count == 2:
                return 5.0

        return 0.0

    def score_15m_signal(self, position_side: str, klines_15m: List[Dict]) -> float:
        """
        15M信号评分 (max 10分)

        逻辑:
        - 最近2小时(8根)中,同向K线>=6根: 10分
        - 同向K线=5根: 7分
        - 同向K线=4根: 3分
        - 其他: 0分
        """
        if len(klines_15m) < 8:
            return 0.0

        # 统计最近8根K线
        bull_count = sum(1 for k in klines_15m[:8] if k['close'] > k['open'])
        bear_count = sum(1 for k in klines_15m[:8] if k['close'] < k['open'])

        if position_side == 'LONG':
            if bull_count >= 6:
                return 10.0
            elif bull_count >= 5:
                return 7.0
            elif bull_count >= 4:
                return 3.0
        elif position_side == 'SHORT':
            if bear_count >= 6:
                return 10.0
            elif bear_count >= 5:
                return 7.0
            elif bear_count >= 4:
                return 3.0

        return 0.0

    def score_volume_price(self, position_side: str, klines_15m: List[Dict]) -> float:
        """
        量价配合评分 (max 7分)

        逻辑:
        - 最新K线量能>平均量1.5倍 + 方向一致: 7分
        - 最新K线量能>平均量1.2倍 + 方向一致: 4分
        - 量能正常: 1分
        """
        if len(klines_15m) < 6:
            return 0.0

        # 计算最近5根的平均量
        avg_volume = sum(k['volume'] for k in klines_15m[1:6]) / 5

        # 最新K线
        latest = klines_15m[0]
        latest_volume = latest['volume']
        latest_price_change = latest['close'] - latest['open']

        # 检查方向是否一致
        if position_side == 'LONG' and latest_price_change > 0:
            if latest_volume > avg_volume * 1.5:
                return 7.0  # 量价齐升
            elif latest_volume > avg_volume * 1.2:
                return 4.0  # 量能温和放大
            else:
                return 1.0  # 量能一般

        elif position_side == 'SHORT' and latest_price_change < 0:
            if latest_volume > avg_volume * 1.5:
                return 7.0  # 量价齐跌
            elif latest_volume > avg_volume * 1.2:
                return 4.0
            else:
                return 1.0

        return 0.0

    def score_technical_indicators(
        self,
        position_side: str,
        klines_15m: List[Dict]
    ) -> float:
        """
        技术指标评分 (max 10分)

        逻辑:
        - RSI: max 3分
        - MACD: max 4分
        - 布林带: max 3分
        """
        if len(klines_15m) < 20:
            return 0.0

        score = 0.0

        # 1. RSI指标 (max 3分)
        rsi = self.calculate_rsi(klines_15m, period=14)
        if rsi:
            if position_side == 'LONG':
                if 30 <= rsi <= 50:
                    score += 3.0  # 超卖区回升
                elif 50 < rsi <= 60:
                    score += 2.0
            elif position_side == 'SHORT':
                if 50 <= rsi <= 70:
                    score += 3.0  # 超买区回落
                elif 40 < rsi < 50:
                    score += 2.0

        # 2. MACD指标 (max 4分)
        macd_signal = self.calculate_macd_signal(klines_15m)
        if macd_signal == position_side:
            score += 4.0

        # 3. 布林带位置 (max 3分)
        bb_position = self.calculate_bollinger_position(klines_15m)
        if position_side == 'LONG' and bb_position == 'lower':
            score += 3.0  # 价格在下轨附近
        elif position_side == 'LONG' and bb_position == 'middle':
            score += 2.0
        elif position_side == 'SHORT' and bb_position == 'upper':
            score += 3.0
        elif position_side == 'SHORT' and bb_position == 'middle':
            score += 2.0

        return score

    def calculate_rsi(self, klines: List[Dict], period: int = 14) -> Optional[float]:
        """计算RSI"""
        if len(klines) < period + 1:
            return None

        closes = [k['close'] for k in klines[:period+1]]

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

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    def calculate_macd_signal(self, klines: List[Dict]) -> Optional[str]:
        """计算MACD信号"""
        if len(klines) < 26:
            return None

        # 简化版: 只判断最新价格相对于MA的位置
        closes = [k['close'] for k in klines[:26]]
        ma12 = sum(closes[:12]) / 12
        ma26 = sum(closes) / 26

        if ma12 > ma26:
            return 'LONG'
        else:
            return 'SHORT'

    def calculate_bollinger_position(self, klines: List[Dict]) -> Optional[str]:
        """计算布林带位置"""
        if len(klines) < 20:
            return None

        closes = [k['close'] for k in klines[:20]]
        ma = sum(closes) / 20

        # 计算标准差
        variance = sum((x - ma) ** 2 for x in closes) / 20
        std = variance ** 0.5

        upper = ma + 2 * std
        lower = ma - 2 * std

        current_price = klines[0]['close']

        if current_price < lower:
            return 'lower'
        elif current_price > upper:
            return 'upper'
        else:
            return 'middle'


# 测试代码
def test_signal_scorer():
    """测试评分系统"""
    db_config = {
        'host': os.getenv('DB_HOST'),
        'user': os.getenv('DB_USER'),
        'password': os.getenv('DB_PASSWORD'),
        'database': os.getenv('DB_NAME')
    }

    scorer = SignalScorerV3(db_config)

    # 模拟K线数据
    import random

    def generate_klines(count, trend='bull'):
        klines = []
        base_price = 100.0
        for i in range(count):
            if trend == 'bull':
                open_price = base_price + random.uniform(0, 0.5)
                close_price = open_price + random.uniform(0, 1)
            else:
                open_price = base_price + random.uniform(0, 0.5)
                close_price = open_price - random.uniform(0, 1)

            klines.append({
                'open': open_price,
                'close': close_price,
                'high': max(open_price, close_price) + random.uniform(0, 0.2),
                'low': min(open_price, close_price) - random.uniform(0, 0.2),
                'volume': random.uniform(1000, 5000)
            })
            base_price = close_price

        return klines

    klines_5h = generate_klines(3, 'bull')
    klines_15m = generate_klines(20, 'bull')

    result = scorer.calculate_total_score(
        symbol='BTC/USDT',
        position_side='LONG',
        klines_5h=klines_5h,
        klines_15m=klines_15m,
        big4_signal='BULL',
        big4_strength=75
    )

    print(f"\n评分结果:")
    print(f"  总分: {result['total_score']:.1f}/{result['max_score']}")
    print(f"  百分比: {result['score_pct']:.1f}%")
    print(f"  可交易: {result['can_trade']}")


if __name__ == '__main__':
    test_signal_scorer()
