#!/usr/bin/env python3
"""
信号评分V2服务
基于数据库预计算的K线评分进行信号过滤
"""
from datetime import datetime
from typing import Dict, Optional, List
import pymysql
from loguru import logger


class SignalScoreV2Service:
    """信号评分V2服务 - 基于数据库预计算评分"""

    def __init__(self, db_config: Dict, score_config: Optional[Dict] = None):
        """初始化服务

        Args:
            db_config: 数据库配置
            score_config: 评分配置（来自config.yaml的resonance_filter部分）
        """
        self.db_config = db_config
        self.connection = None

        # 默认配置
        default_config = {
            'enabled': True,
            'min_symbol_score': 15,  # 代币最低评分（绝对值）
            'min_big4_score': 10,  # Big4最低评分（绝对值）
            'require_same_direction': True,  # 要求方向一致
            'resonance_threshold': 25,  # 共振总分阈值（绝对值之和）
        }

        # 合并用户配置
        self.config = {**default_config, **(score_config or {})}

        logger.info(f"信号评分V2服务已初始化，配置: {self.config}")

    def _get_connection(self):
        """获取数据库连接"""
        if self.connection is None or not self.connection.open:
            self.connection = pymysql.connect(**self.db_config)
        return self.connection

    def get_coin_score(self, symbol: str) -> Optional[Dict]:
        """获取代币评分

        Args:
            symbol: 交易对（如BTC/USDT或BTC/USD）

        Returns:
            评分数据字典，如果没有数据则返回None
        """
        # 币本位转换：BTC/USD -> BTC/USDT (因为币本位没有单独的评分数据)
        query_symbol = symbol
        if symbol.endswith('/USD'):
            query_symbol = symbol.replace('/USD', '/USDT')
            logger.debug(f"币本位交易对 {symbol} 使用 {query_symbol} 的评分数据")

        conn = self._get_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        try:
            cursor.execute("""
                SELECT
                    symbol,
                    total_score,
                    main_score,
                    five_m_bonus,
                    h1_score,
                    h1_bullish_count,
                    h1_bearish_count,
                    h1_level,
                    m15_score,
                    m15_bullish_count,
                    m15_bearish_count,
                    m15_level,
                    m5_bullish_count,
                    m5_bearish_count,
                    direction,
                    strength_level,
                    reason,
                    updated_at
                FROM coin_kline_scores
                WHERE symbol = %s
                AND exchange = 'binance_futures'
                LIMIT 1
            """, (query_symbol,))

            result = cursor.fetchone()
            cursor.close()

            if result:
                logger.debug(f"✅ {symbol} 评分: {result['total_score']}, 方向: {result['direction']}")
                return result
            else:
                logger.warning(f"⚠️ {symbol} 没有评分数据")
                return None

        except Exception as e:
            logger.error(f"❌ 获取 {symbol} 评分失败: {e}")
            cursor.close()
            return None

    def get_big4_score(self) -> Optional[Dict]:
        """获取Big4评分

        Returns:
            Big4评分数据字典，如果没有数据则返回None
        """
        conn = self._get_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        try:
            cursor.execute("""
                SELECT
                    total_score,
                    main_score,
                    five_m_bonus,
                    h1_score,
                    m15_score,
                    direction,
                    strength_level,
                    updated_at
                FROM big4_kline_scores
                WHERE exchange = 'binance_futures'
                ORDER BY updated_at DESC
                LIMIT 1
            """)

            result = cursor.fetchone()
            cursor.close()

            if result:
                logger.debug(f"✅ Big4 评分: {result['total_score']}, 方向: {result['direction']}")
                return result
            else:
                logger.warning(f"⚠️ Big4 没有评分数据")
                return None

        except Exception as e:
            logger.error(f"❌ 获取Big4评分失败: {e}")
            cursor.close()
            return None

    def check_score_filter(self, symbol: str, signal_direction: str) -> Dict:
        """检查代币评分是否通过过滤

        Args:
            symbol: 交易对
            signal_direction: 信号方向 ('LONG' 或 'SHORT')

        Returns:
            {
                'passed': bool,  # 是否通过
                'reason': str,   # 原因
                'coin_score': dict,  # 代币评分数据
                'big4_score': dict,  # Big4评分数据
                'details': dict  # 详细信息
            }
        """
        # 如果未启用，直接通过
        if not self.config.get('enabled', True):
            return {
                'passed': True,
                'reason': '评分过滤未启用',
                'coin_score': None,
                'big4_score': None,
                'details': {}
            }

        # 获取代币评分
        coin_score = self.get_coin_score(symbol)
        if not coin_score:
            return {
                'passed': False,
                'reason': f'{symbol} 没有评分数据',
                'coin_score': None,
                'big4_score': None,
                'details': {}
            }

        # 获取Big4评分
        big4_score = self.get_big4_score()
        if not big4_score:
            # 如果没有Big4数据，只检查代币自身评分
            logger.warning("⚠️ 没有Big4评分数据，仅检查代币评分")
            coin_total = abs(coin_score['total_score'])
            min_score = self.config.get('min_symbol_score', 15)

            if coin_total >= min_score:
                # 检查方向是否匹配
                if coin_score['direction'] == signal_direction:
                    return {
                        'passed': True,
                        'reason': f'{symbol} 评分{coin_score["total_score"]}达标（>={min_score}），方向{coin_score["direction"]}匹配',
                        'coin_score': coin_score,
                        'big4_score': None,
                        'details': {
                            'coin_total': coin_total,
                            'min_score': min_score
                        }
                    }
                else:
                    return {
                        'passed': False,
                        'reason': f'{symbol} 方向不匹配：评分方向{coin_score["direction"]} vs 信号{signal_direction}',
                        'coin_score': coin_score,
                        'big4_score': None,
                        'details': {}
                    }
            else:
                return {
                    'passed': False,
                    'reason': f'{symbol} 评分{coin_score["total_score"]}不达标（需>={min_score}）',
                    'coin_score': coin_score,
                    'big4_score': None,
                    'details': {
                        'coin_total': coin_total,
                        'min_score': min_score
                    }
                }

        # 有Big4数据，进行共振检查
        coin_total = abs(coin_score['total_score'])
        big4_total = abs(big4_score['total_score'])

        min_symbol_score = self.config.get('min_symbol_score', 15)
        min_big4_score = self.config.get('min_big4_score', 10)
        resonance_threshold = self.config.get('resonance_threshold', 25)
        require_same_direction = self.config.get('require_same_direction', True)

        # 检查1: 代币评分是否达标
        if coin_total < min_symbol_score:
            return {
                'passed': False,
                'reason': f'{symbol} 评分{coin_score["total_score"]}不达标（需>={min_symbol_score}）',
                'coin_score': coin_score,
                'big4_score': big4_score,
                'details': {
                    'coin_total': coin_total,
                    'min_symbol_score': min_symbol_score
                }
            }

        # 检查2: Big4评分是否达标
        if big4_total < min_big4_score:
            return {
                'passed': False,
                'reason': f'Big4评分{big4_score["total_score"]}不达标（需>={min_big4_score}）',
                'coin_score': coin_score,
                'big4_score': big4_score,
                'details': {
                    'big4_total': big4_total,
                    'min_big4_score': min_big4_score
                }
            }

        # 检查3: 方向是否一致（如果要求）
        if require_same_direction:
            # 检查代币方向是否与信号方向一致
            if coin_score['direction'] != signal_direction:
                return {
                    'passed': False,
                    'reason': f'{symbol} 方向不匹配：评分{coin_score["direction"]} vs 信号{signal_direction}',
                    'coin_score': coin_score,
                    'big4_score': big4_score,
                    'details': {}
                }

            # 检查Big4方向是否与信号方向一致
            if big4_score['direction'] != signal_direction:
                return {
                    'passed': False,
                    'reason': f'Big4方向不匹配：评分{big4_score["direction"]} vs 信号{signal_direction}',
                    'coin_score': coin_score,
                    'big4_score': big4_score,
                    'details': {}
                }

        # 检查4: 共振总分是否达标
        resonance_score = coin_total + big4_total
        if resonance_score < resonance_threshold:
            return {
                'passed': False,
                'reason': f'共振总分{resonance_score}不达标（需>={resonance_threshold}）',
                'coin_score': coin_score,
                'big4_score': big4_score,
                'details': {
                    'coin_total': coin_total,
                    'big4_total': big4_total,
                    'resonance_score': resonance_score,
                    'resonance_threshold': resonance_threshold
                }
            }

        # 所有检查通过
        return {
            'passed': True,
            'reason': f'✅ 共振通过: {symbol}({coin_score["total_score"]}) + Big4({big4_score["total_score"]}) = {resonance_score} (>={resonance_threshold})',
            'coin_score': coin_score,
            'big4_score': big4_score,
            'details': {
                'coin_total': coin_total,
                'big4_total': big4_total,
                'resonance_score': resonance_score,
                'coin_direction': coin_score['direction'],
                'big4_direction': big4_score['direction'],
                'signal_direction': signal_direction
            }
        }

    def get_top_scored_symbols(
        self,
        direction: Optional[str] = None,
        min_score: Optional[int] = None,
        limit: int = 50
    ) -> List[Dict]:
        """获取评分最高的交易对

        Args:
            direction: 过滤方向 ('LONG', 'SHORT', 'NEUTRAL')，None表示不过滤
            min_score: 最低评分（绝对值），None表示不过滤
            limit: 返回数量

        Returns:
            评分列表
        """
        conn = self._get_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        try:
            where_clauses = ["exchange = 'binance_futures'"]
            params = []

            if direction:
                where_clauses.append("direction = %s")
                params.append(direction)

            if min_score is not None:
                where_clauses.append("ABS(total_score) >= %s")
                params.append(min_score)

            where_sql = " AND ".join(where_clauses)
            params.append(limit)

            query = f"""
                SELECT
                    symbol,
                    total_score,
                    main_score,
                    five_m_bonus,
                    h1_score,
                    m15_score,
                    direction,
                    strength_level,
                    updated_at
                FROM coin_kline_scores
                WHERE {where_sql}
                ORDER BY ABS(total_score) DESC
                LIMIT %s
            """

            cursor.execute(query, params)
            results = cursor.fetchall()
            cursor.close()

            logger.info(f"查询到 {len(results)} 个符合条件的交易对")
            return results

        except Exception as e:
            logger.error(f"❌ 查询评分失败: {e}")
            cursor.close()
            return []

    def get_market_sentiment(self) -> Dict:
        """获取市场整体情绪

        Returns:
            {
                'total': int,  # 总数
                'long_strong': int,  # 强多数量
                'long_medium': int,  # 中多数量
                'short_strong': int,  # 强空数量
                'short_medium': int,  # 中空数量
                'neutral': int,  # 中性数量
                'sentiment': str  # 市场情绪描述
            }
        """
        conn = self._get_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        try:
            cursor.execute("""
                SELECT
                    direction,
                    strength_level,
                    COUNT(*) as count
                FROM coin_kline_scores
                WHERE exchange = 'binance_futures'
                GROUP BY direction, strength_level
            """)

            results = cursor.fetchall()
            cursor.close()

            stats = {
                'total': 0,
                'long_strong': 0,
                'long_medium': 0,
                'short_strong': 0,
                'short_medium': 0,
                'neutral': 0
            }

            for r in results:
                count = r['count']
                stats['total'] += count

                if r['direction'] == 'LONG' and r['strength_level'] == 'strong':
                    stats['long_strong'] = count
                elif r['direction'] == 'LONG' and r['strength_level'] == 'medium':
                    stats['long_medium'] = count
                elif r['direction'] == 'SHORT' and r['strength_level'] == 'strong':
                    stats['short_strong'] = count
                elif r['direction'] == 'SHORT' and r['strength_level'] == 'medium':
                    stats['short_medium'] = count
                elif r['direction'] == 'NEUTRAL' or r['strength_level'] == 'weak':
                    stats['neutral'] += count

            # 计算市场情绪
            bullish = stats['long_strong'] + stats['long_medium']
            bearish = stats['short_strong'] + stats['short_medium']

            if bullish > bearish * 1.5:
                sentiment = '强烈偏多'
            elif bearish > bullish * 1.5:
                sentiment = '强烈偏空'
            elif bullish > bearish:
                sentiment = '偏多'
            elif bearish > bullish:
                sentiment = '偏空'
            else:
                sentiment = '均衡'

            stats['sentiment'] = sentiment
            return stats

        except Exception as e:
            logger.error(f"❌ 获取市场情绪失败: {e}")
            cursor.close()
            return {
                'total': 0,
                'sentiment': '未知'
            }

    def close(self):
        """关闭数据库连接"""
        if self.connection and self.connection.open:
            self.connection.close()
            logger.info("数据库连接已关闭")
