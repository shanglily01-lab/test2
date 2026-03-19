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
from app.database.connection_pool import get_global_pool
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
        self.db_pool = get_global_pool(self.db_config, pool_size=5)

        # 🔥 紧急干预配置
        self.EMERGENCY_DETECTION_HOURS = 4  # 检测最近N小时的剧烈波动
        self.BOTTOM_DROP_THRESHOLD = -5.0   # 底部判断: 跌幅超过5%
        self.TOP_RISE_THRESHOLD = 5.0       # 顶部判断: 涨幅超过5%
        self.BLOCK_DURATION_HOURS = 2       # 触发后阻止交易的时长

        # 🔥 15M深V反转检测配置（方案4：综合放宽）
        self.LOWER_SHADOW_THRESHOLD = 2.0   # 1H长下影线阈值: 2%（从3%降低）
        self.CONSECUTIVE_GREEN_15M = 3      # 15M连续阳线数量: 3根
        self.CHECK_15M_CANDLES = 8          # 检查后续8根15M K线
        self.CHECK_FIRST_BOTTOM = False     # 允许24H内多次触发（不要求首次触底）

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
            'emergency_intervention': {  # 🔥 新增: 紧急干预状态
                'bottom_detected': bool,
                'top_detected': bool,
                'block_long': bool,
                'block_short': bool,
                'details': str
            },
            'timestamp': datetime
        }
        """
        with self.db_pool.get_connection() as conn:
            results = self._detect_market_trend_internal(conn)
        return results

    def _detect_market_trend_internal(self, conn) -> Dict:
        """Internal method with connection passed as parameter"""
        results = {}

        # 🔥 权重系统 (2026-02-12调整)
        # BTC是绝对市场领导者，占据主导地位
        COIN_WEIGHTS = {
            'BTC/USDT': 0.50,  # 50% - 市场绝对领导者
            'ETH/USDT': 0.30,  # 30% - 第二大币
            'BNB/USDT': 0.10,  # 10% - 币安生态
            'SOL/USDT': 0.10   # 10% - 新兴公链
        }

        bullish_count = 0
        bearish_count = 0
        bullish_weight = 0  # 看涨权重总和
        bearish_weight = 0  # 看跌权重总和
        total_strength = 0
        net_weighted_score = 0  # V2: 用于 neutral_bias 计算

        for symbol in BIG4_SYMBOLS:
            analysis = self._analyze_symbol_v2(conn, symbol)
            results[symbol] = analysis

            weight = COIN_WEIGHTS.get(symbol, 0.25)  # 默认25%

            if analysis['signal'] == 'BULLISH':
                bullish_count += 1
                bullish_weight += weight
            elif analysis['signal'] == 'BEARISH':
                bearish_count += 1
                bearish_weight += weight

            # 无论信号是什么，都按权重累加强度
            total_strength += analysis['strength'] * weight
            # V2: 累加原始分（用于 neutral_bias）
            net_weighted_score += analysis.get('raw_score', 0) * weight

        # 🔥 紧急干预检测 (在分析完Big4后执行)
        emergency_intervention = self._detect_emergency_reversal(conn)

        # 🔥 综合判断 - 简化逻辑（2026-02-21）
        # 只看权重，不再要求BTC配合其他币种
        # 权重阈值: >45%

        btc_signal = results.get('BTC/USDT', {}).get('signal', 'NEUTRAL')
        eth_signal = results.get('ETH/USDT', {}).get('signal', 'NEUTRAL')
        bnb_signal = results.get('BNB/USDT', {}).get('signal', 'NEUTRAL')
        sol_signal = results.get('SOL/USDT', {}).get('signal', 'NEUTRAL')

        # 统计支持的币种（用于显示）
        bullish_coins = []
        bearish_coins = []
        if btc_signal == 'BULLISH':
            bullish_coins.append('BTC')
        elif btc_signal == 'BEARISH':
            bearish_coins.append('BTC')
        if eth_signal == 'BULLISH':
            bullish_coins.append('ETH')
        elif eth_signal == 'BEARISH':
            bearish_coins.append('ETH')
        if bnb_signal == 'BULLISH':
            bullish_coins.append('BNB')
        elif bnb_signal == 'BEARISH':
            bearish_coins.append('BNB')
        if sol_signal == 'BULLISH':
            bullish_coins.append('SOL')
        elif sol_signal == 'BEARISH':
            bearish_coins.append('SOL')

        # 已按权重累加，不需要再除以数量（权重总和=1.0）
        avg_strength = total_strength

        # 🔥 趋势判断：要求多币种共振，避免BTC独涨/独跌误判方向
        # STRONG: 权重>70%（BTC+ETH同向，即80%）+ 强度>70
        # BULLISH/BEARISH: 权重>60%（BTC+任一其他，或ETH+BNB+SOL同向）
        # 旧阈值45%允许BTC单独触发（50%>45%），现已修正
        if bullish_weight > 0.70 and avg_strength > 70:
            overall_signal = 'STRONG_BULLISH'
            recommendation = f"{'+'.join(bullish_coins)}看涨(强度{avg_strength:.0f}>70，权重{bullish_weight*100:.0f}%)，🚫禁止做空"
            emergency_intervention['block_short'] = True
            emergency_intervention['details'] = f"Big4强多头趋势(强度{avg_strength:.0f}>70)"
        elif bearish_weight > 0.70 and avg_strength > 70:
            overall_signal = 'STRONG_BEARISH'
            recommendation = f"{'+'.join(bearish_coins)}看跌(强度{avg_strength:.0f}>70，权重{bearish_weight*100:.0f}%)，🚫禁止做多"
            emergency_intervention['block_long'] = True
            emergency_intervention['details'] = f"Big4强空头趋势(强度{avg_strength:.0f}>70)"
        elif bullish_weight > 0.60:
            overall_signal = 'BULLISH'
            recommendation = f"{'+'.join(bullish_coins)}看涨(权重{bullish_weight*100:.0f}%，强度{avg_strength:.0f})，建议优先考虑多单机会"
        elif bearish_weight > 0.60:
            overall_signal = 'BEARISH'
            recommendation = f"{'+'.join(bearish_coins)}看跌(权重{bearish_weight*100:.0f}%，强度{avg_strength:.0f})，建议优先考虑空单机会"
        else:
            overall_signal = 'NEUTRAL'
            recommendation = f"趋势不明(多:{bullish_weight*100:.0f}% 空:{bearish_weight*100:.0f}%，强度{avg_strength:.0f})，建议观望"

        # 🔥 如果紧急干预激活，覆盖recommendation（触顶/触底优先级最高）
        if emergency_intervention.get('block_long') and bullish_count < 3:
            recommendation = f"⚠️ 触顶反转风险 - 禁止做多 | {recommendation}"
        if emergency_intervention.get('block_short') and bearish_count < 3:
            recommendation = f"⚠️ 触底反弹风险 - 禁止做空 | {recommendation}"

        # 已按权重累加，不需要再除以数量（权重总和=1.0）
        avg_strength = total_strength

        result = {
            'overall_signal': overall_signal,
            'signal_strength': avg_strength,
            'bullish_count': bullish_count,
            'bearish_count': bearish_count,
            'bullish_weight': bullish_weight,  # 新增：看涨权重
            'bearish_weight': bearish_weight,  # 新增：看跌权重
            'details': results,
            'recommendation': recommendation,
            'emergency_intervention': emergency_intervention,  # 🔥 新增
            'timestamp': datetime.now()
        }

        # 🔥 震荡市检测
        choppy_market = self._detect_choppy_market(conn)
        result['is_choppy'] = choppy_market['is_choppy']
        result['choppy_market'] = choppy_market

        # V2: neutral_bias（不写DB，仅运行时使用）
        if net_weighted_score >= 15:
            neutral_bias = 'UP'
        elif net_weighted_score <= -15:
            neutral_bias = 'DOWN'
        else:
            neutral_bias = 'FLAT'
        result['neutral_bias'] = neutral_bias
        result['net_weighted_score'] = net_weighted_score

        # 记录到数据库
        self._save_to_database(result)

        return result

    def _detect_choppy_market(self, conn, hours: int = 4) -> Dict:
        """
        检测过去N小时内市场是否处于震荡拉锯状态。

        判断逻辑：
        - 统计过去N小时内 BULLISH(含STRONG) 和 BEARISH(含STRONG) 的记录数
        - 如果少数方向的记录数 > 多数方向的40%，则认为是震荡市
        - 例外：如果当前信号是 STRONG_BULLISH 或 STRONG_BEARISH，不算震荡

        Returns:
            {
                'is_choppy': bool,
                'choppy_ratio': float,  # 少数/多数，越接近1越震荡
                'bullish_count': int,
                'bearish_count': int,
                'reason': str
            }
        """
        try:
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            cursor.execute("""
                SELECT overall_signal, COUNT(*) as cnt
                FROM big4_trend_history
                WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s HOUR)
                AND overall_signal IN ('BULLISH', 'BEARISH', 'STRONG_BULLISH', 'STRONG_BEARISH')
                GROUP BY overall_signal
            """, (hours,))
            rows = cursor.fetchall()

            bullish_count = sum(r['cnt'] for r in rows if r['overall_signal'] in ('BULLISH', 'STRONG_BULLISH'))
            bearish_count = sum(r['cnt'] for r in rows if r['overall_signal'] in ('BEARISH', 'STRONG_BEARISH'))
            total = bullish_count + bearish_count

            if total < 50:
                return {'is_choppy': False, 'choppy_ratio': 0, 'bullish_count': bullish_count,
                        'bearish_count': bearish_count, 'reason': '数据不足，无法判断'}

            if bullish_count == 0 or bearish_count == 0:
                return {'is_choppy': False, 'choppy_ratio': 0, 'bullish_count': bullish_count,
                        'bearish_count': bearish_count, 'reason': '单边趋势'}

            ratio = min(bullish_count, bearish_count) / max(bullish_count, bearish_count)
            dominant = 'BULLISH' if bullish_count >= bearish_count else 'BEARISH'
            is_choppy = ratio > 0.4

            reason = (
                f"震荡拉锯: 多{bullish_count}条/空{bearish_count}条，平衡度{ratio:.2f}"
                if is_choppy else
                f"趋势明确({dominant}): 多{bullish_count}/空{bearish_count}，平衡度{ratio:.2f}"
            )
            return {
                'is_choppy': is_choppy,
                'choppy_ratio': round(ratio, 3),
                'bullish_count': bullish_count,
                'bearish_count': bearish_count,
                'reason': reason
            }
        except Exception as e:
            logger.warning(f"震荡市检测异常: {e}")
            return {'is_choppy': False, 'choppy_ratio': 0, 'bullish_count': 0,
                    'bearish_count': 0, 'reason': f'检测异常: {e}'}

    def _analyze_symbol(self, conn, symbol: str) -> Dict:
        """
        分析单个币种的趋势（基于K线数量评分）

        🔥 2026-02-21 最新优化：添加5M反向评分，用于回调/反弹确认

        步骤:
        1. 1H (30根): 主趋势判断
        2. 15M (16根): 趋势确认
        3. 5M (3根): 反向回调/反弹确认

        评分规则：
        - 1H: 阳线>=18根(强势40分) / >=16根(中等30分)
        - 15M: 阳线>=11根(强势30分) / >=9根(中等20分)
        - 5M反向: 3根反向K线(10分) / 2根反向(5分)
        - 开仓条件: 评分>=50分（中等行情也可做）
        """
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 1. 分析1H K线 (30根) - 从数据库获取
        kline_1h = self._analyze_kline_count(cursor, symbol, '1h', 30)

        # 2. 分析15M K线 (16根) - 从数据库获取
        kline_15m = self._analyze_kline_count(cursor, symbol, '15m', 16)

        # 3. 分析5M K线 (3根) - 从数据库获取
        kline_5m = self._analyze_kline_count(cursor, symbol, '5m', 3)

        cursor.close()

        # 4. 基于数量的评分判断（1H+15M+5M反向）
        signal, strength, reason = self._generate_signal_by_count(
            kline_1h, kline_15m, kline_5m
        )

        return {
            'signal': signal,
            'strength': strength,
            'reason': reason,
            '1h_analysis': kline_1h,
            '15m_analysis': kline_15m,
            '5m_analysis': kline_5m
        }

    def _analyze_kline_power(self, cursor, symbol: str, timeframe: str, count: int) -> Dict:
        """
        分析K线力度（纯价格版本）

        力度 = 价格变化%
        （已移除量能分析，避免滞后性误判）

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
            SELECT open_price, close_price
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

        bullish_count = 0
        bearish_count = 0
        bullish_power = 0  # 阳线力度 = Σ(价格变化%)
        bearish_power = 0  # 阴线力度 = Σ(价格变化%)

        for k in klines:
            open_p = float(k['open_price'])
            close_p = float(k['close_price'])

            if close_p > open_p:
                # 阳线
                bullish_count += 1
                price_change_pct = (close_p - open_p) / open_p * 100
                bullish_power += price_change_pct
            else:
                # 阴线
                bearish_count += 1
                price_change_pct = (open_p - close_p) / open_p * 100
                bearish_power += price_change_pct

        # 判断主导方向 (综合力度和数量，双重验证)
        # 🔥 修改：力度为主（70%）+ 数量为辅（30%）
        # 既看趋势强度，也看趋势一致性

        total_power = bullish_power + bearish_power
        total_count = bullish_count + bearish_count

        if total_power > 0 and total_count > 0:
            # 计算力度占比
            bullish_power_ratio = bullish_power / total_power
            bearish_power_ratio = bearish_power / total_power

            # 计算数量占比
            bullish_count_ratio = bullish_count / total_count
            bearish_count_ratio = bearish_count / total_count

            # 🔥 综合得分 = 力度占比 × 70% + 数量占比 × 30%
            bullish_score = bullish_power_ratio * 0.7 + bullish_count_ratio * 0.3
            bearish_score = bearish_power_ratio * 0.7 + bearish_count_ratio * 0.3

            # 综合得分 >= 0.60 (60%) → 主导趋势
            # 相当于：力度65% + 数量50% → 得分60.5%
            if bullish_score >= 0.60:
                dominant = 'BULL'
            elif bearish_score >= 0.60:
                dominant = 'BEAR'
            else:
                dominant = 'NEUTRAL'
        else:
            # 力度总和为0（数据异常或无变化）
            dominant = 'NEUTRAL'

        return {
            'bullish_count': bullish_count,
            'bearish_count': bearish_count,
            'bullish_power': bullish_power,
            'bearish_power': bearish_power,
            'dominant': dominant
        }

    def _analyze_kline_count(self, cursor, symbol: str, timeframe: str, count: int) -> Dict:
        """
        基于K线数量的趋势分析（简化版）

        🔥 2026-02-13新增：只看阳线/阴线数量，不计算力度

        评分规则：
        - 1H (30根): 阳>=18(强势40分) / >=16(中等30分)
        - 15M (16根): 阳>=11(强势30分) / >=9(中等20分)
        - 5M (3根): 3阳(10分) / 2阳(5分)

        返回:
        {
            'bullish_count': int,  # 阳线数量
            'bearish_count': int,  # 阴线数量
            'score': int,          # 评分
            'level': str           # 'STRONG' / 'MEDIUM' / 'NEUTRAL'
        }
        """
        query = """
            SELECT open_price, close_price
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
                'score': 0,
                'level': 'NEUTRAL'
            }

        bullish_count = 0
        bearish_count = 0

        for k in klines:
            open_p = float(k['open_price'])
            close_p = float(k['close_price'])

            if close_p > open_p:
                bullish_count += 1
            else:
                bearish_count += 1

        # 根据周期和数量评分
        score = 0
        level = 'NEUTRAL'

        if timeframe == '1h' and count == 30:
            # 1H周期评分
            if bullish_count >= 18:  # 60%
                score = 40
                level = 'STRONG'
            elif bullish_count >= 16:  # 53%
                score = 30
                level = 'MEDIUM'
            elif bearish_count >= 18:
                score = -40
                level = 'STRONG'
            elif bearish_count >= 16:
                score = -30
                level = 'MEDIUM'

        elif timeframe == '15m' and count == 16:
            # 15M周期评分
            if bullish_count >= 11:  # 69%
                score = 30
                level = 'STRONG'
            elif bullish_count >= 9:  # 56%
                score = 20
                level = 'MEDIUM'
            elif bearish_count >= 11:
                score = -30
                level = 'STRONG'
            elif bearish_count >= 9:
                score = -20
                level = 'MEDIUM'

        elif timeframe == '5m' and count == 3:
            # 5M周期反向评分（用于回调/反弹确认）
            # 3根连续阴线或阳线
            if bullish_count == 3:
                score = 10
                level = 'STRONG'
            elif bullish_count == 2:
                score = 5
                level = 'MEDIUM'
            elif bearish_count == 3:
                score = -10
                level = 'STRONG'
            elif bearish_count == 2:
                score = -5
                level = 'MEDIUM'

        # 生成主导方向（用于数据库保存）
        if score > 0:
            dominant = 'BULL'
        elif score < 0:
            dominant = 'BEAR'
        else:
            dominant = 'NEUTRAL'

        return {
            'bullish_count': bullish_count,
            'bearish_count': bearish_count,
            'score': score,
            'level': level,
            'dominant': dominant,
            'total': count
        }

    def _generate_signal_by_count(
        self,
        kline_1h: Dict,
        kline_15m: Dict,
        kline_5m: Dict = None
    ) -> Tuple[str, int, str]:
        """
        基于K线数量生成信号（简化版）

        🔥 2026-02-21最新：添加5M反向加分逻辑，用于回调/反弹确认

        权重分配:
        - 1H: 强势40分 / 中等30分
        - 15M: 强势30分 / 中等20分
        - 5M反向: 3根反向(10分) / 2根反向(5分)

        反向评分逻辑:
        - 多头趋势(1H+15M>0) + 5M阴线(回调) → 加分（更好的入场时机）
        - 空头趋势(1H+15M<0) + 5M阳线(反弹) → 加分（更好的入场时机）

        开仓条件:
        - 评分 >= 50分 → BULLISH/BEARISH
        - 评分 < 50分 → NEUTRAL（弱势行情不做）

        可能的开仓组合:
        - 1H强势(40) + 15M强势(30) = 70分 ✅
        - 1H强势(40) + 15M中等(20) + 5M反向(10) = 70分 ✅
        - 1H中等(30) + 15M强势(30) = 60分 ✅
        - 1H中等(30) + 15M中等(20) = 50分 ✅

        返回: (信号方向, 强度0-100, 原因)
        """
        # 计算主趋势得分（1H+15M）
        main_trend_score = kline_1h['score'] + kline_15m['score']
        signal_score = main_trend_score
        reasons = []

        # 1H分析
        if kline_1h['score'] > 0:
            reasons.append(f"1H{kline_1h['level']}多头({kline_1h['bullish_count']}阳:{kline_1h['bearish_count']}阴,{kline_1h['score']}分)")
        elif kline_1h['score'] < 0:
            reasons.append(f"1H{kline_1h['level']}空头({kline_1h['bearish_count']}阴:{kline_1h['bullish_count']}阳,{kline_1h['score']}分)")
        else:
            reasons.append(f"1H中性({kline_1h['bullish_count']}阳:{kline_1h['bearish_count']}阴)")

        # 15M分析
        if kline_15m['score'] > 0:
            reasons.append(f"15M{kline_15m['level']}多头({kline_15m['bullish_count']}阳:{kline_15m['bearish_count']}阴,{kline_15m['score']}分)")
        elif kline_15m['score'] < 0:
            reasons.append(f"15M{kline_15m['level']}空头({kline_15m['bearish_count']}阴:{kline_15m['bullish_count']}阳,{kline_15m['score']}分)")
        else:
            reasons.append(f"15M中性({kline_15m['bullish_count']}阳:{kline_15m['bearish_count']}阴)")

        # 🔥 5M反向评分（回调/反弹确认）
        if kline_5m:
            kline_5m_score = kline_5m['score']

            # 反向加分逻辑
            if main_trend_score > 0 and kline_5m_score < 0:
                # 多头趋势 + 5M阴线回调 → 加分
                reverse_bonus = abs(kline_5m_score)
                signal_score += reverse_bonus
                reasons.append(f"5M回调确认({kline_5m['bearish_count']}阴:{kline_5m['bullish_count']}阳,+{reverse_bonus}分)")
            elif main_trend_score < 0 and kline_5m_score > 0:
                # 空头趋势 + 5M阳线反弹 → 加分
                reverse_bonus = abs(kline_5m_score)
                signal_score -= reverse_bonus
                reasons.append(f"5M反弹确认({kline_5m['bullish_count']}阳:{kline_5m['bearish_count']}阴,+{reverse_bonus}分)")
            elif kline_5m_score != 0:
                # 5M与主趋势同向，不加分（不是反向确认）
                if kline_5m_score > 0:
                    reasons.append(f"5M多头({kline_5m['bullish_count']}阳:{kline_5m['bearish_count']}阴,无加分)")
                else:
                    reasons.append(f"5M空头({kline_5m['bearish_count']}阴:{kline_5m['bullish_count']}阳,无加分)")

        # 判断信号（门槛50分）
        if signal_score >= 50:
            signal = 'BULLISH'
            reasons.append(f'✅总分{signal_score}>=50，看涨')
        elif signal_score <= -50:
            signal = 'BEARISH'
            reasons.append(f'✅总分{signal_score}<=-50，看跌')
        else:
            signal = 'NEUTRAL'
            reasons.append(f'⚠️总分{signal_score}，趋势不明确')

        strength = min(abs(signal_score), 100)
        reason = ' | '.join(reasons)

        return signal, strength, reason

    def _detect_5m_signal(self, cursor, symbol: str) -> Dict:
        """
        检测5M破位（需要量能配合）

        破位检测需要量能确认:
        - 力度 = 价格变化% × 0.8 + 成交量归一化 × 0.2
        - 价格突破 + 量能配合 = 真突破
        - 价格突破 + 量能不足 = 假突破
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
                # 阳线力度（价格80% + 量能20%）
                price_change_pct = (close_p - open_p) / open_p * 100
                power = price_change_pct * 0.8 + volume_normalized * 0.2
                total_bull_power += power
            else:
                # 阴线力度（价格80% + 量能20%）
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
        kline_1h: Dict,
        kline_15m: Dict,
        kline_5m: Dict
    ) -> Tuple[str, int, str]:
        """
        综合生成信号 (简化版)

        权重分配:
        - 1H主导方向: 60分
        - 15M趋势确认: 30分
        - 5M买卖时机: 10分

        返回: (信号方向, 强度0-100, 原因)
        """
        signal_score = 0  # -100 to +100
        reasons = []

        # 1. 1H主导方向 (权重: 60)
        if kline_1h['dominant'] == 'BULL':
            signal_score += 60
            reasons.append(f"1H多头主导({kline_1h['bullish_count']}阳:{kline_1h['bearish_count']}阴)")
        elif kline_1h['dominant'] == 'BEAR':
            signal_score -= 60
            reasons.append(f"1H空头主导({kline_1h['bearish_count']}阴:{kline_1h['bullish_count']}阳)")
        else:
            reasons.append("1H方向中性")

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

        # 生成最终信号
        # 🔥 调整 (2026-02-12): 降低阈值到35，保留1H验证
        # 阈值35: 昨晚42.5分可触发，兼顾灵敏度和稳定性
        # 保留1H验证: 避免震荡市中15M+5M短周期假突破
        if signal_score > 35 and kline_1h['dominant'] == 'BULL':
            signal = 'BULLISH'
        elif signal_score < -35 and kline_1h['dominant'] == 'BEAR':
            signal = 'BEARISH'
        else:
            signal = 'NEUTRAL'
            # 添加原因说明（如果是因为1H不达标）
            if signal_score > 35 and kline_1h['dominant'] != 'BULL':
                reasons.append("⚠️ 评分达标但1H非多头，判定为中性")
            elif signal_score < -35 and kline_1h['dominant'] != 'BEAR':
                reasons.append("⚠️ 评分达标但1H非空头，判定为中性")

        strength = min(abs(signal_score), 100)
        reason = ' | '.join(reasons) if reasons else '无明显信号'

        return signal, strength, reason

    def _generate_signal_with_resonance(
        self,
        kline_4h: Dict,
        kline_1h: Dict,
        kline_15m: Dict,
        kline_5m: Dict
    ) -> Tuple[str, int, str]:
        """
        多周期共振信号生成（确保1小时有效性）

        🔥 2026-02-13新增：增加4H周期，提升信号预测准确性

        权重分配:
        - 4H主趋势: 40分（最重要，决定未来1小时方向）
        - 1H中期趋势: 30分
        - 15M短期确认: 20分
        - 5M入场时机: 10分

        共振规则:
        1. 4H主趋势必须明确（BULL或BEAR），否则返回NEUTRAL
        2. 1H、15M必须与4H同向，才算有效信号
        3. 5M可以不同向（用于精确入场时机）
        4. 只有多周期共振（4H+1H+15M同向），信号才有效

        目标：确保信号发出后，未来1小时方向正确率>=70%

        返回: (信号方向, 强度0-100, 原因)
        """
        signal_score = 0  # -100 to +100
        reasons = []

        # 📊 第一步：检查4H主趋势（必须明确）
        trend_4h = kline_4h['dominant']

        if trend_4h == 'NEUTRAL':
            # 4H都看不清方向，直接返回NEUTRAL
            return 'NEUTRAL', 0, '4H主趋势不明确，市场方向不清晰'

        # 📊 第二步：多周期共振检查
        trend_1h = kline_1h['dominant']
        trend_15m = kline_15m['dominant']

        # 4H主趋势（权重40）
        if trend_4h == 'BULL':
            signal_score += 40
            reasons.append(f"4H多头主导({kline_4h['bullish_count']}阳:{kline_4h['bearish_count']}阴)")
        else:  # BEAR
            signal_score -= 40
            reasons.append(f"4H空头主导({kline_4h['bearish_count']}阴:{kline_4h['bullish_count']}阳)")

        # 1H中期趋势（权重30，必须与4H同向）
        if trend_1h == trend_4h:
            # 同向，加分
            if trend_1h == 'BULL':
                signal_score += 30
                reasons.append(f"1H多头确认({kline_1h['bullish_count']}阳:{kline_1h['bearish_count']}阴)")
            else:  # BEAR
                signal_score -= 30
                reasons.append(f"1H空头确认({kline_1h['bearish_count']}阴:{kline_1h['bullish_count']}阳)")
        else:
            # 1H与4H不同向，扣分并警告
            reasons.append(f"⚠️ 1H({trend_1h})与4H({trend_4h})分歧")

        # 15M短期确认（权重20，必须与4H同向）
        if trend_15m == trend_4h:
            # 同向，加分
            if trend_15m == 'BULL':
                signal_score += 20
                reasons.append(f"15M多头确认({kline_15m['bullish_count']}阳:{kline_15m['bearish_count']}阴)")
            else:  # BEAR
                signal_score -= 20
                reasons.append(f"15M空头确认({kline_15m['bearish_count']}阴:{kline_15m['bullish_count']}阳)")
        else:
            # 15M与4H不同向，警告
            reasons.append(f"⚠️ 15M({trend_15m})与4H({trend_4h})分歧")

        # 5M入场时机（权重10，可以不同向）
        if kline_5m['detected']:
            direction_5m = kline_5m['direction']
            # 只有当5M与4H同向时才加分
            if (direction_5m == 'BULLISH' and trend_4h == 'BULL') or \
               (direction_5m == 'BEARISH' and trend_4h == 'BEAR'):
                if direction_5m == 'BULLISH':
                    signal_score += 10
                else:
                    signal_score -= 10
                reasons.append(kline_5m['reason'])

        # 📊 第三步：判断信号（要求多周期共振）
        # 至少需要70分（4H+1H+15M全部同向）才发出信号
        if signal_score >= 70 and trend_4h == 'BULL':
            signal = 'BULLISH'
            reasons.append('✅ 多周期共振，看涨信号确认')
        elif signal_score <= -70 and trend_4h == 'BEAR':
            signal = 'BEARISH'
            reasons.append('✅ 多周期共振，看跌信号确认')
        else:
            signal = 'NEUTRAL'
            if abs(signal_score) >= 40:
                reasons.append(f'⚠️ 周期分歧，评分{signal_score}分但未达到共振标准(±70分)')
            else:
                reasons.append('市场方向不明确')

        strength = min(abs(signal_score), 100)
        reason = ' | '.join(reasons) if reasons else '无明显信号'

        return signal, strength, reason

    # ─────────────────────────────────────────────────────────────────
    # V2 算法：MA8/MA20偏差 + 4H动量（响应时间 4-6H，替代30H计数法）
    # ─────────────────────────────────────────────────────────────────

    def _calc_ma_trend(self, cursor, symbol: str) -> Dict:
        """计算 MA8/MA20 偏差，返回评分（±40/±25/0）"""
        cursor.execute("""
            SELECT close_price FROM kline_data
            WHERE symbol=%s AND timeframe='1h' AND exchange='binance_futures'
            ORDER BY open_time DESC LIMIT 24
        """, (symbol,))
        rows = cursor.fetchall()
        if len(rows) < 20:
            return {'score': 0, 'level': 'FLAT', 'ma8': 0, 'ma20': 0, 'deviation': 0,
                    'reason': 'MA数据不足'}

        closes = [float(r['close_price']) for r in rows]  # 最新在前
        ma8  = sum(closes[:8])  / 8
        ma20 = sum(closes[:20]) / 20
        deviation = (ma8 - ma20) / ma20 * 100  # 正=多头排列

        if deviation > 2.0:
            score, level = 40, 'STRONG_BULL'
        elif deviation > 0.5:
            score, level = 25, 'MILD_BULL'
        elif deviation < -2.0:
            score, level = -40, 'STRONG_BEAR'
        elif deviation < -0.5:
            score, level = -25, 'MILD_BEAR'
        else:
            score, level = 0, 'FLAT'

        return {'score': score, 'level': level,
                'ma8': ma8, 'ma20': ma20, 'deviation': deviation,
                'reason': f'MA偏差{deviation:+.2f}%({level})'}

    def _calc_momentum_4h(self, cursor, symbol: str) -> Dict:
        """计算近3根4H K线的价格动量，返回评分（±30/±20/0）"""
        cursor.execute("""
            SELECT close_price FROM kline_data
            WHERE symbol=%s AND timeframe='4h' AND exchange='binance_futures'
            ORDER BY open_time DESC LIMIT 3
        """, (symbol,))
        rows = cursor.fetchall()
        if len(rows) < 2:
            return {'score': 0, 'level': 'FLAT', 'change': 0, 'reason': '4H动量数据不足'}

        latest = float(rows[0]['close_price'])
        prev   = float(rows[-1]['close_price'])   # 2或3根前
        change = (latest - prev) / prev * 100

        if change > 1.5:
            score, level = 30, 'STRONG_UP'
        elif change > 0.5:
            score, level = 20, 'MILD_UP'
        elif change < -1.5:
            score, level = -30, 'STRONG_DOWN'
        elif change < -0.5:
            score, level = -20, 'MILD_DOWN'
        else:
            score, level = 0, 'FLAT'

        return {'score': score, 'level': level, 'change': change,
                'reason': f'4H动量{change:+.2f}%({level})'}

    def _analyze_symbol_v2(self, conn, symbol: str) -> Dict:
        """
        V2单币分析：MA8/MA20偏差 + 4H动量
        最大分值: MA±40 + 动量±30 = ±70
        BULLISH/BEARISH 门槛: ±50
        保留 1h_analysis/15m_analysis 字段以兼容 _save_to_database
        """
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        ma    = self._calc_ma_trend(cursor, symbol)
        mom4h = self._calc_momentum_4h(cursor, symbol)
        cursor.close()

        raw_score = ma['score'] + mom4h['score']

        if raw_score >= 50:
            signal = 'BULLISH'
        elif raw_score <= -50:
            signal = 'BEARISH'
        else:
            signal = 'NEUTRAL'

        strength = min(abs(raw_score), 100)
        reason   = f"{ma['reason']} | {mom4h['reason']}"

        ma_dominant = 'BULL' if ma['score'] > 0 else ('BEAR' if ma['score'] < 0 else 'NEUTRAL')

        return {
            'signal': signal,
            'strength': strength,
            'raw_score': raw_score,
            'reason': reason,
            '1h_analysis': {'dominant': ma_dominant},
            '15m_analysis': {'dominant': 'NEUTRAL'},
            'ma': ma,
            'momentum_4h': mom4h,
        }

    # ─────────────────────────────────────────────────────────────────

    def _detect_emergency_reversal(self, conn) -> Dict:
        """
        🔥 检测紧急底部/顶部反转 - 避免死猫跳陷阱

        双重检测逻辑:
        【方法1】1H级别检测 (长周期):
        - 检测最近4小时的剧烈波动 (跌幅>5% 或 涨幅>5%)
        - 适合捕捉较大级别的反转

        【方法2】15M级别检测 (短周期深V反转):
        - 检测1H K线的长下影线 (>3%)
        - 检测后续15M连续3根阳线
        - 适合捕捉快速触底反弹

        返回:
        {
            'bottom_detected': bool,      # 是否检测到触底
            'top_detected': bool,         # 是否检测到触顶
            'block_long': bool,           # 是否阻止做多
            'block_short': bool,          # 是否阻止做空
            'details': str,               # 详细原因
            'expires_at': datetime | None # 干预失效时间
            'bounce_opportunity': bool,   # 🔥 是否有反弹交易机会
            'bounce_symbols': list,       # 🔥 反弹交易的币种列表
            'bounce_window_end': datetime | None  # 🔥 反弹窗口结束时间 (45分钟)
        }
        """
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 1. 检查数据库中是否有未过期的紧急干预记录 (可能有多条)
        cursor.execute("""
            SELECT intervention_type, expires_at, trigger_reason, block_long, block_short
            FROM emergency_intervention
            WHERE account_id = 2
            AND trading_type = 'usdt_futures'
            AND expires_at > NOW()
            ORDER BY created_at DESC
        """)

        existing_records = cursor.fetchall()

        if existing_records:
            # 🔥 智能干预: 合并多条记录，但动态检查是否应该提前解除
            bottom_detected = False
            top_detected = False
            block_long = False
            block_short = False
            reasons = []
            latest_expires = None
            oldest_created = None

            for record in existing_records:
                if record['intervention_type'] == 'BOTTOM_BOUNCE':
                    bottom_detected = True
                    block_short = block_short or record['block_short']
                elif record['intervention_type'] == 'TOP_REVERSAL':
                    top_detected = True
                    block_long = block_long or record['block_long']

                reasons.append(f"{record['trigger_reason']}")
                if latest_expires is None or record['expires_at'] > latest_expires:
                    latest_expires = record['expires_at']

            # 🔥 新增: 动态检查是否应该提前解除干预
            # 检查反弹窗口是否已结束 (45分钟)
            cursor.execute("""
                SELECT window_end
                FROM bounce_window
                WHERE account_id = 2
                AND trading_type = 'usdt_futures'
                AND window_end > NOW()
                ORDER BY created_at DESC
                LIMIT 1
            """)
            active_bounce_window = cursor.fetchone()

            # 如果反弹窗口已结束，检查市场是否已经恢复
            if not active_bounce_window and bottom_detected:
                # 检查Big4是否已经反弹完成（反弹超过2%）
                market_recovered = True
                for symbol in BIG4_SYMBOLS:
                    cursor.execute("""
                        SELECT open_price, close_price, low_price, high_price
                        FROM kline_data
                        WHERE symbol = %s
                        AND timeframe = '1h'
                        AND exchange = 'binance_futures'
                        ORDER BY open_time DESC
                        LIMIT 4
                    """, (symbol,))

                    recent_klines = cursor.fetchall()
                    if recent_klines:
                        period_low = min([float(k['low_price']) for k in recent_klines])
                        latest_close = float(recent_klines[0]['close_price'])
                        recovery_pct = (latest_close - period_low) / period_low * 100

                        # 如果任一币种未完成2%反弹，认为市场尚未恢复
                        if recovery_pct < 2.0:
                            market_recovered = False
                            break

                # 如果市场已恢复，解除禁止做空限制
                if market_recovered:
                    block_short = False
                    reasons.append("✅ 市场已反弹2%+，解除做空限制")

            # 同理检查触顶是否已回调完成
            if not active_bounce_window and top_detected:
                market_cooled = True
                for symbol in BIG4_SYMBOLS:
                    cursor.execute("""
                        SELECT open_price, close_price, low_price, high_price
                        FROM kline_data
                        WHERE symbol = %s
                        AND timeframe = '1h'
                        AND exchange = 'binance_futures'
                        ORDER BY open_time DESC
                        LIMIT 4
                    """, (symbol,))

                    recent_klines = cursor.fetchall()
                    if recent_klines:
                        period_high = max([float(k['high_price']) for k in recent_klines])
                        latest_close = float(recent_klines[0]['close_price'])
                        cooldown_pct = (latest_close - period_high) / period_high * 100

                        # 如果任一币种未完成3%回调，认为市场尚未冷却
                        if cooldown_pct > -3.0:
                            market_cooled = False
                            break

                # 如果市场已冷却，解除禁止做多限制
                if market_cooled:
                    block_long = False
                    reasons.append("✅ 市场已回调3%+，解除做多限制")

            cursor.close()

            combined_reason = ', '.join(reasons)

            # 🔥 矛盾冲突检测: 若触底(block_short)和触顶(block_long)同时激活，
            # 说明市场在极端震荡中先暴跌后暴涨，两个方向都封锁会导致系统完全停止交易。
            # 逻辑上两者互相矛盾，自动解除双重封锁，恢复正常交易。
            if block_long and block_short:
                block_long = False
                block_short = False
                logger.warning(
                    f"[EMERGENCY-CONFLICT] 触底反弹 + 触顶回调同时激活（逻辑矛盾），"
                    f"双重封锁自动解除，恢复正常交易 | 原始原因: {combined_reason}"
                )
                combined_reason = f"触底+触顶冲突自动解除 | {combined_reason}"

            # 如果所有限制都已解除，返回空结果
            if not block_long and not block_short:
                return {
                    'bottom_detected': False,
                    'top_detected': False,
                    'block_long': False,
                    'block_short': False,
                    'details': f"✅ 紧急干预已自动解除: {combined_reason}",
                    'expires_at': None,
                    'bounce_opportunity': False,
                    'bounce_symbols': [],
                    'bounce_window_end': None
                }

            return {
                'bottom_detected': bottom_detected,
                'top_detected': top_detected,
                'block_long': block_long,
                'block_short': block_short,
                'details': f"⚠️ 紧急干预中: {combined_reason} (失效于 {latest_expires.strftime('%H:%M')})",
                'expires_at': latest_expires,
                'bounce_opportunity': False,  # 已在干预期，不触发新反弹
                'bounce_symbols': [],
                'bounce_window_end': None
            }

        # 2. 双重检测: 1H级别 + 15M深V反转
        hours_ago_dt = datetime.now() - timedelta(hours=self.EMERGENCY_DETECTION_HOURS)
        hours_ago_timestamp = int(hours_ago_dt.timestamp() * 1000)  # 🔥 修复: 转换为毫秒时间戳

        bottom_detected = False
        top_detected = False
        trigger_symbols = []
        max_drop = 0
        max_rise = 0

        # 🔥 反弹交易机会追踪
        bounce_opportunity = False
        bounce_symbols = []
        bounce_window_end = None

        for symbol in BIG4_SYMBOLS:
            # ========== 方法1: 1H级别长周期检测 ==========
            # 获取N小时前和当前的价格
            cursor.execute("""
                SELECT open_price, close_price, low_price, high_price, open_time
                FROM kline_data
                WHERE symbol = %s
                AND timeframe = '1h'
                AND exchange = 'binance_futures'
                AND open_time >= %s
                ORDER BY open_time ASC
            """, (symbol, hours_ago_timestamp))

            klines = cursor.fetchall()

            if not klines or len(klines) < 2:
                continue

            # 计算期间的最高价和最低价
            period_high = max([float(k['high_price']) for k in klines])
            period_low = min([float(k['low_price']) for k in klines])
            latest_close = float(klines[-1]['close_price'])

            # 从最高点到最低点的跌幅
            drop_pct = (period_low - period_high) / period_high * 100
            # 从最低点到当前的涨幅
            rise_from_low = (latest_close - period_low) / period_low * 100
            # 从最高点的总跌幅
            drop_from_high = (latest_close - period_high) / period_high * 100

            # 判断触底 (剧烈下跌后可能反弹)
            if drop_pct <= self.BOTTOM_DROP_THRESHOLD and rise_from_low > 0:
                bottom_detected = True
                trigger_symbols.append(f"{symbol.split('/')[0]}触底({drop_pct:.1f}%→+{rise_from_low:.1f}%)")
                max_drop = min(max_drop, drop_pct)

            # 判断触顶 (剧烈上涨后可能回调)
            rise_pct = (period_high - period_low) / period_low * 100
            if rise_pct >= self.TOP_RISE_THRESHOLD and drop_from_high < 0:
                top_detected = True
                trigger_symbols.append(f"{symbol.split('/')[0]}触顶(+{rise_pct:.1f}%→{drop_from_high:.1f}%)")
                max_rise = max(max_rise, rise_pct)

            # ========== 方法2: 15M深V反转检测 ==========
            # 检测最近2根1H K线的长下影线 + 后续15M连续阳线
            cursor.execute("""
                SELECT open_price, close_price, low_price, high_price, open_time
                FROM kline_data
                WHERE symbol = %s
                AND timeframe = '1h'
                AND exchange = 'binance_futures'
                ORDER BY open_time DESC
                LIMIT 2
            """, (symbol,))

            recent_1h = cursor.fetchall()

            for h1_candle in recent_1h:
                open_p = float(h1_candle['open_price'])
                close_p = float(h1_candle['close_price'])
                high_p = float(h1_candle['high_price'])
                low_p = float(h1_candle['low_price'])

                # 计算下影线长度
                body_low = min(open_p, close_p)
                lower_shadow_pct = (body_low - low_p) / low_p * 100 if low_p > 0 else 0

                # 🔥 检测长下影线 = 潜在反弹交易机会
                if lower_shadow_pct >= self.LOWER_SHADOW_THRESHOLD:
                    # 计算1H K线的时间
                    h1_ts = int(h1_candle['open_time']) / 1000 if int(h1_candle['open_time']) > 9999999999 else int(h1_candle['open_time'])
                    h1_time = datetime.fromtimestamp(h1_ts)
                    time_since_candle = (datetime.now() - h1_time).total_seconds() / 60  # 分钟

                    # 🎯 大周期过滤: 检查前72H是否持续下跌 (避免震荡市假信号)
                    cursor_check = conn.cursor(pymysql.cursors.DictCursor)
                    cursor_check.execute("""
                        SELECT high_price, low_price, open_time
                        FROM kline_data
                        WHERE symbol = %s
                        AND timeframe = '1h'
                        AND exchange = 'binance_futures'
                        AND open_time <= %s
                        ORDER BY open_time DESC
                        LIMIT 72
                    """, (symbol, h1_candle['open_time']))

                    history_72h = cursor_check.fetchall()
                    cursor_check.close()

                    is_true_deep_v = False

                    if len(history_72h) >= 24:  # 至少需要24H数据
                        # 计算72H和24H的最高点
                        high_72h = max([float(k['high_price']) for k in history_72h])
                        high_24h = max([float(k['high_price']) for k in history_72h[:24]])

                        # 从高点到当前低点的跌幅
                        drop_from_high_72h = (low_p - high_72h) / high_72h * 100
                        drop_from_high_24h = (low_p - high_24h) / high_24h * 100

                        # 判断条件（方案4：综合放宽）:
                        # 1. 72H持续下跌 >= 3% (从5%进一步降低)
                        # 2. 24H加速下跌 >= 1.5% (从2.5%进一步降低)
                        # 3. 允许多次触底 (不要求首次触底)
                        is_sustained_drop = drop_from_high_72h <= -3.0
                        is_accelerating = drop_from_high_24h <= -1.5

                        # 检查24H内是否首次出现长下影线（如果启用检查）
                        is_first_bottom = True
                        if self.CHECK_FIRST_BOTTOM:
                            for prev_k in history_72h[:24]:
                                if prev_k['open_time'] == h1_candle['open_time']:
                                    continue  # 跳过当前K线
                                prev_open = float(prev_k.get('open_price', 0)) if 'open_price' in prev_k else 0
                                prev_close = float(prev_k.get('close_price', 0)) if 'close_price' in prev_k else 0
                                prev_low = float(prev_k['low_price'])
                                if prev_open > 0 and prev_close > 0:
                                    prev_body_low = min(prev_open, prev_close)
                                    prev_shadow = (prev_body_low - prev_low) / prev_low * 100 if prev_low > 0 else 0
                                    if prev_shadow >= self.LOWER_SHADOW_THRESHOLD:
                                        is_first_bottom = False
                                        break

                        # 判断条件：持续下跌 + 加速下跌 + (首次触底 or 允许多次)
                        is_true_deep_v = is_sustained_drop and is_accelerating and is_first_bottom

                        if not is_true_deep_v:
                            logger.info(f"⚠️ {symbol} 下影{lower_shadow_pct:.1f}% 不满足大周期条件: "
                                      f"72H跌幅{drop_from_high_72h:.1f}% (需<-3%), "
                                      f"24H跌幅{drop_from_high_24h:.1f}% (需<-1.5%), "
                                      f"{'首次触底' + ('✅' if is_first_bottom else '❌') if self.CHECK_FIRST_BOTTOM else '允许多次✅'}")
                    else:
                        logger.info(f"⚠️ {symbol} 下影{lower_shadow_pct:.1f}% 数据不足，无法判断大周期")

                    # 🔥 只有真深V反转才创建反弹窗口
                    if is_true_deep_v and time_since_candle <= 45:
                        bounce_opportunity = True
                        bounce_symbols.append(symbol)
                        bounce_window_end = h1_time + timedelta(minutes=45)

                        # 🔥 同时标记为bottom_detected，立即触发紧急干预（不等15M确认）
                        bottom_detected = True
                        trigger_symbols.append(
                            f"{symbol.split('/')[0]}深V反转(1H下影{lower_shadow_pct:.1f}%)"
                        )

                        logger.warning(f"🚀🚀🚀 真深V反转! {symbol} 下影{lower_shadow_pct:.1f}%, "
                                     f"72H跌幅{drop_from_high_72h:.1f}%, 24H跌幅{drop_from_high_24h:.1f}%, "
                                     f"首次触底, 窗口剩余{45-time_since_candle:.0f}分钟 | "
                                     f"立即禁止做空2小时")

                    # 🔥 已优化：深V反转检测到长下影线即立即触发紧急干预
                    # 不再等待15M阳线确认，避免抢反弹过程中被做空信号干扰
                    #
                    # 原逻辑（已废弃）：
                    # - 检测15M连续阳线
                    # - 连续3根阳线才触发emergency intervention
                    # 问题：
                    # - bounce_window已创建允许抢反弹，但未禁止做空
                    # - 逻辑不一致，时间延迟
                    #
                    # 新逻辑（已在上面实现）：
                    # - 检测到is_true_deep_v即同时设置bottom_detected
                    # - bounce_window和emergency_intervention同步创建
                    # - 立即保护反弹仓位

                    # 如果已经通过长下影线触发了bottom_detected，跳出循环
                    if is_true_deep_v and time_since_candle <= 45:
                        break  # 不再检查这个币种的其他1H K线

        # 3. 保存反弹窗口到数据库 (独立于emergency intervention)
        if bounce_opportunity and bounce_symbols:
            try:
                with self.db_pool.get_connection() as conn_write:
                    cursor_write = conn_write.cursor()

                    for symbol in bounce_symbols:
                        # 获取该币种的1H K线信息
                        cursor_write.execute("""
                        SELECT open_price, close_price, low_price, high_price, open_time
                        FROM kline_data
                        WHERE symbol = %s
                        AND timeframe = '1h'
                        AND exchange = 'binance_futures'
                        ORDER BY open_time DESC
                        LIMIT 1
                        """, (symbol,))

                        h1_data = cursor_write.fetchone()
                        if not h1_data:
                            continue

                        open_p = float(h1_data[0])
                        close_p = float(h1_data[1])
                        low_p = float(h1_data[2])
                        h1_open_time = h1_data[4]

                        body_low = min(open_p, close_p)
                        lower_shadow_pct = (body_low - low_p) / low_p * 100 if low_p > 0 else 0

                        # 计算触发时间
                        h1_ts = int(h1_open_time) / 1000 if int(h1_open_time) > 9999999999 else int(h1_open_time)
                        trigger_time = datetime.fromtimestamp(h1_ts)
                        window_start = trigger_time
                        window_end = trigger_time + timedelta(minutes=45)

                        # 检查是否已存在未过期的bounce_window
                        cursor_write.execute("""
                            SELECT id FROM bounce_window
                            WHERE account_id = 2
                            AND trading_type = 'usdt_futures'
                            AND symbol = %s
                            AND window_end > NOW()
                            AND bounce_entered = FALSE
                            ORDER BY created_at DESC
                            LIMIT 1
                        """, (symbol,))

                        existing_window = cursor_write.fetchone()

                        if not existing_window:
                            # 创建新的bounce window
                            cursor_write.execute("""
                                INSERT INTO bounce_window
                                (account_id, trading_type, symbol, trigger_time, window_start, window_end,
                                 lower_shadow_pct, trigger_price, bounce_entered, notes, created_at)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FALSE, %s, NOW())
                            """, (
                                2, 'usdt_futures', symbol, trigger_time, window_start, window_end,
                                lower_shadow_pct, close_p,
                                f"1H下影线{lower_shadow_pct:.2f}%, 45分钟反弹窗口"
                            ))

                            logger.info(f"💾 反弹窗口已保存: {symbol} 下影{lower_shadow_pct:.1f}% 窗口至{window_end.strftime('%H:%M')}")

                    conn_write.commit()
                    cursor_write.close()

            except Exception as e:
                logger.error(f"❌ 保存反弹窗口失败: {e}")

        cursor.close()

        # 4. 如果检测到新的反转，保存到数据库
        # 🔥 修复: 触底和触顶分开处理，分别插入记录
        if bottom_detected or top_detected:
            expires_at = datetime.now() + timedelta(hours=self.BLOCK_DURATION_HOURS)

            try:
                with self.db_pool.get_connection() as conn_write:
                    cursor_write = conn_write.cursor()

                    # 处理触底反弹 (优先级更高，先插入)
                if bottom_detected:
                    bottom_symbols = [s for s in trigger_symbols if '触底' in s]
                    bottom_details = f"触底反弹: {', '.join(bottom_symbols)}"

                    cursor_write.execute("""
                        INSERT INTO emergency_intervention
                        (account_id, trading_type, intervention_type, block_long, block_short,
                         trigger_reason, expires_at, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    """, (
                        2, 'usdt_futures', 'BOTTOM_BOUNCE', False, True,  # 只禁止做空
                        bottom_details, expires_at
                    ))

                    logger.warning(f"🚨 紧急干预已激活: {bottom_details} (禁止做空{self.BLOCK_DURATION_HOURS}小时)")

                # 处理触顶回调
                if top_detected:
                    top_symbols = [s for s in trigger_symbols if '触顶' in s]
                    top_details = f"触顶回调: {', '.join(top_symbols)}"

                    cursor_write.execute("""
                        INSERT INTO emergency_intervention
                        (account_id, trading_type, intervention_type, block_long, block_short,
                         trigger_reason, expires_at, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    """, (
                        2, 'usdt_futures', 'TOP_REVERSAL', True, False,  # 只禁止做多
                        top_details, expires_at
                    ))

                    logger.warning(f"🚨 紧急干预已激活: {top_details} (禁止做多{self.BLOCK_DURATION_HOURS}小时)")

                    conn_write.commit()
                    cursor_write.close()

            except Exception as e:
                logger.error(f"❌ 保存紧急干预失败: {e}")

            # 🔥 修复: 返回正确的block状态
            details_list = []
            if bottom_detected:
                bottom_symbols = [s for s in trigger_symbols if '触底' in s]
                details_list.append(f"触底反弹: {', '.join(bottom_symbols)}")
            if top_detected:
                top_symbols = [s for s in trigger_symbols if '触顶' in s]
                details_list.append(f"触顶回调: {', '.join(top_symbols)}")

            combined_details = ' | '.join(details_list)

            return {
                'bottom_detected': bottom_detected,
                'top_detected': top_detected,
                'block_long': top_detected,      # 触顶时禁止做多
                'block_short': bottom_detected,  # 触底时禁止做空
                'details': f"⚠️ {combined_details} (阻止{self.BLOCK_DURATION_HOURS}小时)",
                'expires_at': expires_at,
                'bounce_opportunity': bounce_opportunity,
                'bounce_symbols': bounce_symbols,
                'bounce_window_end': bounce_window_end
            }

        # 无紧急情况 (但可能有反弹机会)
        return {
            'bottom_detected': False,
            'top_detected': False,
            'block_long': False,
            'block_short': False,
            'details': '无紧急干预',
            'expires_at': None,
            'bounce_opportunity': bounce_opportunity,
            'bounce_symbols': bounce_symbols,
            'bounce_window_end': bounce_window_end
        }

    def _save_to_database(self, result: Dict):
        """保存检测结果到数据库"""
        try:
            with self.db_pool.get_connection() as conn:
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
                details['BTC/USDT']['1h_analysis']['dominant'],
                details['BTC/USDT']['15m_analysis']['dominant'],
                # ETH
                details['ETH/USDT']['signal'],
                details['ETH/USDT']['strength'],
                details['ETH/USDT']['reason'],
                details['ETH/USDT']['1h_analysis']['dominant'],
                details['ETH/USDT']['15m_analysis']['dominant'],
                # BNB
                details['BNB/USDT']['signal'],
                details['BNB/USDT']['strength'],
                details['BNB/USDT']['reason'],
                details['BNB/USDT']['1h_analysis']['dominant'],
                details['BNB/USDT']['15m_analysis']['dominant'],
                # SOL
                details['SOL/USDT']['signal'],
                details['SOL/USDT']['strength'],
                details['SOL/USDT']['reason'],
                    details['SOL/USDT']['1h_analysis']['dominant'],
                    details['SOL/USDT']['15m_analysis']['dominant']
                ))

                conn.commit()
                cursor.close()

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
