#!/usr/bin/env python3
"""
信号评分V2服务
基于数据库预计算的K线评分进行信号过滤
"""
from datetime import datetime, timedelta, timezone
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
        # 修复：移除单一持久连接（会因MySQL wait_timeout静默断开）
        # 改为每次查询创建新连接并在finally中关闭

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

    def _new_connection(self):
        """创建新数据库连接（每次调用返回新连接，调用方负责关闭）"""
        return pymysql.connect(**self.db_config)

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

        conn = None
        try:
            conn = self._new_connection()
            cursor = conn.cursor(pymysql.cursors.DictCursor)
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
            return None
        finally:
            if conn:
                conn.close()

    def get_big4_score(self) -> Optional[Dict]:
        """获取Big4评分

        Returns:
            Big4评分数据字典，如果没有数据则返回None
        """
        conn = None
        try:
            conn = self._new_connection()
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            # 🔥 使用big4_trend_history表获取最新Big4趋势数据
            cursor.execute("""
                SELECT
                    signal_strength,
                    overall_signal,
                    bullish_count,
                    bearish_count,
                    recommendation,
                    created_at
                FROM big4_trend_history
                ORDER BY created_at DESC
                LIMIT 1
            """)

            result = cursor.fetchone()
            cursor.close()

            if result:
                # 转换字段名以保持兼容性 (转为整数保持与V1一致)
                signal_strength = int(round(float(result['signal_strength'])))
                overall_signal = result['overall_signal']

                # 映射overall_signal到direction
                direction_map = {
                    'STRONG_BULLISH': 'LONG',
                    'BULLISH': 'LONG',
                    'STRONG_BEARISH': 'SHORT',
                    'BEARISH': 'SHORT',
                    'NEUTRAL': 'NEUTRAL'
                }
                direction = direction_map.get(overall_signal, 'NEUTRAL')

                # 根据signal判断正负
                if overall_signal in ('BEARISH', 'STRONG_BEARISH'):
                    total_score = -signal_strength
                else:
                    total_score = signal_strength

                # 计算strength_level
                if signal_strength >= 50:
                    strength_level = 'strong'
                elif signal_strength >= 20:
                    strength_level = 'medium'
                else:
                    strength_level = 'weak'

                # 构造返回数据结构
                big4_data = {
                    'total_score': total_score,
                    'signal_strength': signal_strength,  # 绝对值
                    'direction': direction,
                    'strength_level': strength_level,
                    'overall_signal': overall_signal,
                    'bullish_count': result['bullish_count'],
                    'bearish_count': result['bearish_count'],
                    'recommendation': result['recommendation'],
                    'updated_at': result['created_at']
                }

                logger.debug(f"✅ Big4 评分: {total_score:+d}, 方向: {direction} (来自big4_trend_history)")
                return big4_data
            else:
                logger.warning(f"⚠️ Big4 没有评分数据")
                return None

        except Exception as e:
            logger.error(f"❌ 获取Big4评分失败: {e}")
            return None
        finally:
            if conn:
                conn.close()

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
            # 无V2数据不等于反对，V1信号已足够，直接放行
            return {
                'passed': True,
                'reason': f'{symbol} 无V2评分数据，依赖V1信号',
                'coin_score': None,
                'big4_score': None,
                'details': {'no_v2_data': True}
            }

        # 🔥 检查数据是否过时（超过15分钟），如果过时则忽略V2过滤
        updated_at = coin_score.get('updated_at')
        if updated_at:
            if isinstance(updated_at, str):
                updated_at = datetime.fromisoformat(updated_at)
            # 数据库时间是UTC，使用UTC时间进行比较
            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            # 如果updated_at没有时区信息，假定为UTC
            if updated_at.tzinfo is None:
                age_minutes = (now_utc - updated_at).total_seconds() / 60
            else:
                age_minutes = (now_utc - updated_at.replace(tzinfo=None)).total_seconds() / 60

            if age_minutes > 15:
                logger.warning(f"⚠️ {symbol} 评分数据过时（{age_minutes:.1f}分钟前更新），忽略V2过滤")
                return {
                    'passed': True,
                    'reason': f'{symbol} 评分数据过时（{age_minutes:.1f}min），自动通过',
                    'coin_score': coin_score,
                    'big4_score': None,
                    'details': {'data_age_minutes': age_minutes}
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
                        'details': {'direction_mismatch': True}
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

        # 🔥 新逻辑：V1占绝对主力，V2只做协同确认
        # V2检查：1）方向必须与V1一致 2）单币强度>=15
        # 不做Big4共振检查，Big4过滤在后续流程中处理
        coin_raw_score = coin_score['total_score']  # 带符号：正数=LONG，负数=SHORT

        # 检查1: V2评分方向是否与V1方向强力相反（只有强度>=20才算真实冲突，弱反向视为NEUTRAL）
        # 例：V2=SHORT -10/-15 只是弱噪音，不足以拦截V1信号；V2=SHORT -20+ 才算真实反向
        opposite_dir = {'LONG': 'SHORT', 'SHORT': 'LONG'}
        _min_conflict_strength = 20  # 触发方向冲突拦截的最低反向强度
        if coin_score['direction'] == opposite_dir.get(signal_direction) and abs(coin_raw_score) >= _min_conflict_strength:
            return {
                'passed': False,
                'reason': f'❌ V1/V2方向冲突: V1信号{signal_direction} vs V2评分{coin_score["direction"]}({coin_raw_score:+d})',
                'coin_score': coin_score,
                'big4_score': big4_score,
                'details': {
                    'v1_signal_direction': signal_direction,
                    'v2_coin_direction': coin_score['direction'],
                    'direction_mismatch': True
                }
            }

        # 检查2: 若V2与V1方向一致但强度不足 → 视为NEUTRAL放行（弱确认≠反对）
        # 只有V2方向与V1相反时才拦截；同向弱信号和NEUTRAL都放行
        min_symbol_score = self.config.get('min_symbol_score', 15)  # V2强力确认阈值
        coin_strength = abs(coin_raw_score)  # 使用绝对值判断强度

        # 所有检查通过 - V1主导，V2协同确认（或V2中性/弱同向不反对）
        if coin_score['direction'] == signal_direction and coin_strength >= min_symbol_score:
            v2_desc = f'V2评分{coin_raw_score:+d}（强度{coin_strength}），方向{coin_score["direction"]}与V1一致'
        elif coin_score['direction'] == signal_direction and coin_strength < min_symbol_score:
            v2_desc = f'V2评分{coin_raw_score:+d}（强度{coin_strength}<{min_symbol_score}，弱确认），视为中性放行'
        else:
            v2_desc = f'V2评分NEUTRAL，不反对V1方向'
        reason = f'✅ V2协同确认: {symbol} {v2_desc}'

        return {
            'passed': True,
            'reason': reason,
            'coin_score': coin_score,
            'big4_score': big4_score,
            'details': {
                'coin_raw_score': coin_raw_score,
                'coin_strength': coin_strength,
                'coin_direction': coin_score['direction'],
                'signal_direction': signal_direction,
                'v2_filter_mode': 'simple'  # 简化模式：只检查V2单币强度和方向
            }
        }

    def get_top_scored_symbols(
        self,
        direction: Optional[str] = None,
        min_score: Optional[int] = None,
        limit: int = 50
    ) -> List[Dict]:
        """获取评分最高的交易对"""
        conn = None
        try:
            conn = self._new_connection()
            cursor = conn.cursor(pymysql.cursors.DictCursor)

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
            return []
        finally:
            if conn:
                conn.close()

    def get_market_sentiment(self) -> Dict:
        """获取市场整体情绪"""
        conn = None
        try:
            conn = self._new_connection()
            cursor = conn.cursor(pymysql.cursors.DictCursor)

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
            return {
                'total': 0,
                'sentiment': '未知'
            }
        finally:
            if conn:
                conn.close()

    def close(self):
        """兼容性方法（连接已改为按需创建，无需显式关闭）"""
        pass
