#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场状态管理器
每6小时分析一次大盘方向,影响超级大脑的交易决策

核心功能:
1. 判断6小时级别的市场趋势 (牛市/熊市/震荡)
2. 调整超级大脑的开仓倾向性
3. 动态调整仓位大小
4. 控制风险暴露
"""

import pymysql
from pymysql.cursors import DictCursor
from datetime import datetime, timedelta
from typing import Dict, Optional
from loguru import logger
import json


class MarketRegimeManager:
    """市场状态管理器 - 6小时级别决策"""

    def __init__(self, db_config: dict):
        self.db_config = db_config
        self.current_regime = None
        self.regime_strength = 0
        self.last_update = None

    def analyze_market_regime(self) -> Dict:
        """
        分析6小时级别的市场状态

        返回:
        {
            'regime': 'bull_market' / 'bear_market' / 'neutral',
            'strength': 0-100,
            'bias': 'long' / 'short' / 'balanced',
            'position_adjustment': 0.7-1.3,
            'score_threshold_adjustment': -5 to +5,
            'recommendations': []
        }
        """
        logger.info("🔍 分析6小时市场状态...")

        conn = self._get_connection()
        cursor = conn.cursor()

        # 获取最近6小时的市场观察数据
        cursor.execute("""
            SELECT
                overall_trend,
                market_strength,
                btc_price,
                btc_trend,
                eth_price,
                eth_trend,
                bullish_count,
                bearish_count,
                warnings,
                timestamp
            FROM market_observations
            WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 6 HOUR)
            ORDER BY timestamp DESC
        """)

        observations = cursor.fetchall()

        if len(observations) < 3:
            logger.warning("市场观察数据不足,使用保守策略")
            return self._get_conservative_regime()

        # 分析趋势一致性
        regime_analysis = self._analyze_regime_consistency(observations)

        # 分析价格动量
        price_momentum = self._analyze_price_momentum(observations)

        # 综合判断市场状态
        regime = self._determine_regime(regime_analysis, price_momentum)

        # 保存市场状态
        self._save_market_regime(regime)

        # 更新内部状态
        self.current_regime = regime['regime']
        self.regime_strength = regime['strength']
        self.last_update = datetime.now()

        cursor.close()
        conn.close()

        logger.info(f"📊 市场状态: {regime['regime'].upper()} | 强度: {regime['strength']:.1f} | 建议倾向: {regime['bias']}")

        return regime

    def _analyze_regime_consistency(self, observations: list) -> Dict:
        """分析趋势一致性"""
        bull_count = 0
        bear_count = 0
        neutral_count = 0

        avg_strength = 0

        for obs in observations:
            trend = obs['overall_trend']
            if trend == 'bullish':
                bull_count += 1
            elif trend == 'bearish':
                bear_count += 1
            else:
                neutral_count += 1

            avg_strength += float(obs['market_strength'] or 50)

        total = len(observations)
        avg_strength = avg_strength / total if total > 0 else 50

        bull_pct = bull_count / total
        bear_pct = bear_count / total

        return {
            'bull_percentage': bull_pct,
            'bear_percentage': bear_pct,
            'neutral_percentage': neutral_count / total,
            'avg_strength': avg_strength,
            'consistency': max(bull_pct, bear_pct)  # 趋势一致性
        }

    def _analyze_price_momentum(self, observations: list) -> Dict:
        """分析价格动量"""
        if len(observations) < 2:
            return {'btc_change': 0, 'eth_change': 0, 'momentum': 0}

        # 获取6小时前后的价格
        latest = observations[0]
        earliest = observations[-1]

        btc_change = 0
        eth_change = 0

        if latest['btc_price'] and earliest['btc_price']:
            btc_change = (float(latest['btc_price']) - float(earliest['btc_price'])) / float(earliest['btc_price'])

        if latest['eth_price'] and earliest['eth_price']:
            eth_change = (float(latest['eth_price']) - float(earliest['eth_price'])) / float(earliest['eth_price'])

        # 综合动量 (BTC权重70%, ETH权重30%)
        momentum = btc_change * 0.7 + eth_change * 0.3

        return {
            'btc_change': btc_change,
            'eth_change': eth_change,
            'momentum': momentum
        }

    def _determine_regime(self, consistency: Dict, momentum: Dict) -> Dict:
        """
        综合判断市场状态

        牛市条件: 趋势一致性>60% + 6小时涨幅>1%
        熊市条件: 趋势一致性>60% + 6小时跌幅>1%
        震荡: 其他情况
        """
        bull_pct = consistency['bull_percentage']
        bear_pct = consistency['bear_percentage']
        avg_strength = consistency['avg_strength']
        price_momentum = momentum['momentum']

        # 判断市场状态
        if bull_pct >= 0.6 and price_momentum > 0.01:
            # 牛市
            regime_type = 'bull_market'
            strength = min(avg_strength + price_momentum * 100, 100)
            bias = 'long'

            # 牛市策略调整
            if strength > 75:
                # 强势牛市
                position_adj = 1.3  # 增加30%仓位
                score_threshold_adj = -5  # 降低5分开仓门槛
                recommendations = [
                    "强势牛市,积极做多",
                    "优先开LONG仓",
                    "适当增加仓位",
                    "提高止盈目标"
                ]
            else:
                # 温和牛市
                position_adj = 1.15  # 增加15%仓位
                score_threshold_adj = -3  # 降低3分开仓门槛
                recommendations = [
                    "温和牛市,倾向做多",
                    "LONG仓为主,SHORT仓为辅",
                    "适度增加仓位"
                ]

        elif bear_pct >= 0.6 and price_momentum < -0.01:
            # 熊市
            regime_type = 'bear_market'
            strength = max(avg_strength + price_momentum * 100, 0)
            bias = 'short'

            # 熊市策略调整
            if strength < 25:
                # 强势熊市
                position_adj = 1.3  # 增加30%仓位
                score_threshold_adj = -5  # 降低5分开仓门槛
                recommendations = [
                    "强势熊市,积极做空",
                    "优先开SHORT仓",
                    "适当增加仓位",
                    "及时止盈"
                ]
            else:
                # 温和熊市
                position_adj = 1.15  # 增加15%仓位
                score_threshold_adj = -3  # 降低3分开仓门槛
                recommendations = [
                    "温和熊市,倾向做空",
                    "SHORT仓为主,LONG仓为辅",
                    "适度增加仓位"
                ]

        else:
            # 震荡市
            regime_type = 'neutral'
            strength = avg_strength
            bias = 'balanced'
            position_adj = 0.85  # 减少15%仓位
            score_threshold_adj = 3  # 提高3分开仓门槛
            recommendations = [
                "震荡市场,保守交易",
                "多空均衡",
                "减少仓位",
                "提高开仓标准",
                "快速止盈止损"
            ]

        return {
            'regime': regime_type,
            'strength': round(strength, 2),
            'bias': bias,
            'position_adjustment': position_adj,
            'score_threshold_adjustment': score_threshold_adj,
            'btc_6h_change': round(momentum['btc_change'] * 100, 2),
            'eth_6h_change': round(momentum['eth_change'] * 100, 2),
            'trend_consistency': round(consistency['consistency'] * 100, 1),
            'recommendations': recommendations,
            'timestamp': datetime.now()
        }

    def _get_conservative_regime(self) -> Dict:
        """数据不足时返回保守策略"""
        return {
            'regime': 'neutral',
            'strength': 50,
            'bias': 'balanced',
            'position_adjustment': 0.8,
            'score_threshold_adjustment': 5,
            'btc_6h_change': 0,
            'eth_6h_change': 0,
            'trend_consistency': 0,
            'recommendations': ["数据不足,采用保守策略"],
            'timestamp': datetime.now()
        }

    def _save_market_regime(self, regime: Dict):
        """保存市场状态到数据库"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 创建表(如果不存在)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_regime_states (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    timestamp TIMESTAMP NOT NULL,
                    regime VARCHAR(20) NOT NULL,
                    strength DECIMAL(5,2),
                    bias VARCHAR(20),
                    position_adjustment DECIMAL(4,2),
                    score_threshold_adjustment INT,
                    btc_6h_change DECIMAL(6,2),
                    eth_6h_change DECIMAL(6,2),
                    trend_consistency DECIMAL(5,2),
                    recommendations TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_timestamp (timestamp)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            # 插入数据
            cursor.execute("""
                INSERT INTO market_regime_states
                (timestamp, regime, strength, bias, position_adjustment,
                 score_threshold_adjustment, btc_6h_change, eth_6h_change,
                 trend_consistency, recommendations)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                regime['timestamp'],
                regime['regime'],
                regime['strength'],
                regime['bias'],
                regime['position_adjustment'],
                regime['score_threshold_adjustment'],
                regime['btc_6h_change'],
                regime['eth_6h_change'],
                regime['trend_consistency'],
                '\n'.join(regime['recommendations'])
            ))

            conn.commit()
            cursor.close()
            conn.close()

        except Exception as e:
            logger.error(f"保存市场状态失败: {e}")

    def get_current_regime(self) -> Optional[Dict]:
        """获取当前市场状态"""
        # 如果最近更新过(6小时内),直接返回缓存
        if (self.last_update and
            (datetime.now() - self.last_update).seconds < 21600):
            return {
                'regime': self.current_regime,
                'strength': self.regime_strength
            }

        # 否则从数据库获取最新状态
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT regime, strength, bias, position_adjustment,
                       score_threshold_adjustment, recommendations, timestamp
                FROM market_regime_states
                WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 6 HOUR)
                ORDER BY timestamp DESC
                LIMIT 1
            """)

            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if result:
                self.current_regime = result['regime']
                self.regime_strength = float(result['strength'])
                self.last_update = result['timestamp']
                return dict(result)

        except Exception as e:
            logger.error(f"获取市场状态失败: {e}")

        return None

    def should_favor_long(self) -> bool:
        """是否应该倾向做多"""
        regime = self.get_current_regime()
        if not regime:
            return False
        return regime.get('bias') == 'long'

    def should_favor_short(self) -> bool:
        """是否应该倾向做空"""
        regime = self.get_current_regime()
        if not regime:
            return False
        return regime.get('bias') == 'short'

    def get_position_multiplier(self) -> float:
        """获取仓位调整倍数"""
        regime = self.get_current_regime()
        if not regime:
            return 1.0
        return float(regime.get('position_adjustment', 1.0))

    def get_score_threshold_adjustment(self) -> int:
        """获取开仓分数阈值调整"""
        regime = self.get_current_regime()
        if not regime:
            return 0
        return int(regime.get('score_threshold_adjustment', 0))

    def _get_connection(self):
        """获取数据库连接"""
        return pymysql.connect(
            **self.db_config,
            charset='utf8mb4',
            cursorclass=DictCursor
        )

    def print_regime_report(self, regime: Dict):
        """打印市场状态报告"""
        print('\n' + '=' * 100)
        print('🌍 6小时市场状态报告')
        print('=' * 100)

        print(f"\n时间: {regime['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"市场状态: {regime['regime'].upper()}")
        print(f"市场强度: {regime['strength']:.1f}/100")
        print(f"交易倾向: {regime['bias'].upper()}")

        print(f"\n大盘走势 (6小时):")
        print(f"  BTC变化: {regime['btc_6h_change']:+.2f}%")
        print(f"  ETH变化: {regime['eth_6h_change']:+.2f}%")
        print(f"  趋势一致性: {regime['trend_consistency']:.1f}%")

        print(f"\n策略调整:")
        print(f"  仓位倍数: {regime['position_adjustment']:.2f}x")
        adj = regime['score_threshold_adjustment']
        print(f"  分数阈值: {adj:+d} ({'降低开仓门槛' if adj < 0 else '提高开仓门槛' if adj > 0 else '保持不变'})")

        print(f"\n交易建议:")
        for i, rec in enumerate(regime['recommendations'], 1):
            print(f"  {i}. {rec}")

        print('=' * 100 + '\n')
