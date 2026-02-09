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
        # 🔥 紧急干预配置
        self.EMERGENCY_DETECTION_HOURS = 4  # 检测最近N小时的剧烈波动
        self.BOTTOM_DROP_THRESHOLD = -5.0   # 底部判断: 跌幅超过5%
        self.TOP_RISE_THRESHOLD = 5.0       # 顶部判断: 涨幅超过5%
        self.BLOCK_DURATION_HOURS = 2       # 触发后阻止交易的时长

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

        # 🔥 紧急干预检测 (在分析完Big4后执行)
        emergency_intervention = self._detect_emergency_reversal(conn)

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

        # 🔥 如果紧急干预激活，覆盖recommendation
        if emergency_intervention['block_long']:
            recommendation = f"⚠️ 触顶反转风险 - 禁止做多 | {recommendation}"
        if emergency_intervention['block_short']:
            recommendation = f"⚠️ 触底反弹风险 - 禁止做空 | {recommendation}"

        avg_strength = total_strength / len(BIG4_SYMBOLS) if BIG4_SYMBOLS else 0

        result = {
            'overall_signal': overall_signal,
            'signal_strength': avg_strength,
            'bullish_count': bullish_count,
            'bearish_count': bearish_count,
            'details': results,
            'recommendation': recommendation,
            'emergency_intervention': emergency_intervention,  # 🔥 新增
            'timestamp': datetime.now()
        }

        # 记录到数据库
        self._save_to_database(result)

        return result

    def _analyze_symbol(self, conn, symbol: str) -> Dict:
        """
        分析单个币种的趋势 (简化版)

        步骤:
        1. 1H (30根): 主导方向判断
        2. 15M (30根): 趋势确认
        3. 5M (3根): 买卖时机
        """
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 1. 分析1H K线 (30根) - 主导方向
        kline_1h = self._analyze_kline_power(cursor, symbol, '1h', 30)

        # 2. 分析15M K线 (30根) - 趋势确认
        kline_15m = self._analyze_kline_power(cursor, symbol, '15m', 30)

        # 3. 分析5M K线 (3根) - 买卖时机
        kline_5m = self._detect_5m_signal(cursor, symbol)

        cursor.close()

        # 4. 综合判断
        signal, strength, reason = self._generate_signal(kline_1h, kline_15m, kline_5m)

        return {
            'signal': signal,
            'strength': strength,
            'reason': reason,
            '1h_analysis': kline_1h,
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
            SELECT open_price, close_price, volume
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
            else:
                # 阴线
                bearish_count += 1
                price_change_pct = (open_p - close_p) / open_p * 100
                # 力度 = 价格变化%(80%) + 成交量归一化(20%)
                power = price_change_pct * 0.8 + volume_normalized * 0.2
                bearish_power += power

        # 判断主导方向 (更严格的标准)
        # 上涨趋势: 阳线 >= 17根 (30根K线中占57%)
        # 下跌趋势: 阴线 >= 17根 (30根K线中占57%)
        # 其他情况: 震荡行情
        if bullish_count >= 17:
            dominant = 'BULL'
        elif bearish_count >= 17:
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
        if signal_score > 30:
            signal = 'BULLISH'
        elif signal_score < -30:
            signal = 'BEARISH'
        else:
            signal = 'NEUTRAL'

        strength = min(abs(signal_score), 100)
        reason = ' | '.join(reasons) if reasons else '无明显信号'

        return signal, strength, reason

    def _detect_emergency_reversal(self, conn) -> Dict:
        """
        🔥 检测紧急底部/顶部反转 - 避免死猫跳陷阱

        逻辑:
        1. 检测最近N小时Big4的剧烈波动
        2. 如果检测到触底 (跌幅>5%): 禁止做空2小时
        3. 如果检测到触顶 (涨幅>5%): 禁止做多2小时

        返回:
        {
            'bottom_detected': bool,      # 是否检测到触底
            'top_detected': bool,         # 是否检测到触顶
            'block_long': bool,           # 是否阻止做多
            'block_short': bool,          # 是否阻止做空
            'details': str,               # 详细原因
            'expires_at': datetime | None # 干预失效时间
        }
        """
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 1. 检查数据库中是否有未过期的紧急干预记录
        cursor.execute("""
            SELECT intervention_type, expires_at, trigger_reason
            FROM emergency_intervention
            WHERE account_id = 2
            AND trading_type = 'usdt_futures'
            AND expires_at > NOW()
            ORDER BY created_at DESC
            LIMIT 1
        """)

        existing = cursor.fetchone()

        if existing:
            # 已有未过期的干预记录
            intervention_type = existing['intervention_type']
            expires_at = existing['expires_at']
            reason = existing['trigger_reason']

            cursor.close()

            return {
                'bottom_detected': intervention_type == 'BOTTOM_BOUNCE',
                'top_detected': intervention_type == 'TOP_REVERSAL',
                'block_long': intervention_type == 'TOP_REVERSAL',
                'block_short': intervention_type == 'BOTTOM_BOUNCE',
                'details': f"⚠️ 紧急干预中: {reason} (失效于 {expires_at.strftime('%H:%M')})",
                'expires_at': expires_at
            }

        # 2. 分析最近N小时的Big4价格变化
        hours_ago = datetime.now() - timedelta(hours=self.EMERGENCY_DETECTION_HOURS)

        bottom_detected = False
        top_detected = False
        trigger_symbols = []
        max_drop = 0
        max_rise = 0

        for symbol in BIG4_SYMBOLS:
            # 获取N小时前和当前的价格
            cursor.execute("""
                SELECT open_price, close_price, low_price, high_price, open_time
                FROM kline_data
                WHERE symbol = %s
                AND timeframe = '1h'
                AND exchange = 'binance_futures'
                AND open_time >= %s
                ORDER BY open_time ASC
            """, (symbol, hours_ago))

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

        cursor.close()

        # 3. 如果检测到新的反转，保存到数据库
        if bottom_detected or top_detected:
            intervention_type = 'BOTTOM_BOUNCE' if bottom_detected else 'TOP_REVERSAL'
            block_long = top_detected
            block_short = bottom_detected
            details = f"{'触底反弹' if bottom_detected else '触顶回调'}: {', '.join(trigger_symbols)}"
            expires_at = datetime.now() + timedelta(hours=self.BLOCK_DURATION_HOURS)

            # 保存到数据库
            try:
                conn_write = pymysql.connect(**self.db_config)
                cursor_write = conn_write.cursor()

                cursor_write.execute("""
                    INSERT INTO emergency_intervention
                    (account_id, trading_type, intervention_type, block_long, block_short,
                     trigger_reason, expires_at, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                """, (
                    2, 'usdt_futures', intervention_type, block_long, block_short,
                    details, expires_at
                ))

                conn_write.commit()
                cursor_write.close()
                conn_write.close()

                logger.warning(f"🚨 紧急干预已激活: {details} (持续{self.BLOCK_DURATION_HOURS}小时)")

            except Exception as e:
                logger.error(f"❌ 保存紧急干预失败: {e}")

            return {
                'bottom_detected': bottom_detected,
                'top_detected': top_detected,
                'block_long': block_long,
                'block_short': block_short,
                'details': f"⚠️ {details} (阻止{self.BLOCK_DURATION_HOURS}小时)",
                'expires_at': expires_at
            }

        # 无紧急情况
        return {
            'bottom_detected': False,
            'top_detected': False,
            'block_long': False,
            'block_short': False,
            'details': '无紧急干预',
            'expires_at': None
        }

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
