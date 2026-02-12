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

        # 🔥 15M深V反转检测配置
        self.LOWER_SHADOW_THRESHOLD = 3.0   # 1H长下影线阈值: 3%
        self.CONSECUTIVE_GREEN_15M = 3      # 15M连续阳线数量: 3根
        self.CHECK_15M_CANDLES = 8          # 检查后续8根15M K线

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

        for symbol in BIG4_SYMBOLS:
            analysis = self._analyze_symbol(conn, symbol)
            results[symbol] = analysis

            weight = COIN_WEIGHTS.get(symbol, 0.25)  # 默认25%

            if analysis['signal'] == 'BULLISH':
                bullish_count += 1
                bullish_weight += weight
                total_strength += analysis['strength']
            elif analysis['signal'] == 'BEARISH':
                bearish_count += 1
                bearish_weight += weight
                total_strength += analysis['strength']

        # 🔥 紧急干预检测 (在分析完Big4后执行)
        emergency_intervention = self._detect_emergency_reversal(conn)

        conn.close()

        # 综合判断 - 使用权重而非简单计数
        # 权重≥60%视为趋势明确（例如BTC+ETH=70%，或BTC+BNB+SOL=70%）
        if bullish_weight >= 0.60:
            overall_signal = 'BULLISH'
            recommendation = f"市场整体看涨(权重{bullish_weight*100:.0f}%)，建议优先考虑多单机会"
        elif bearish_weight >= 0.60:
            overall_signal = 'BEARISH'
            recommendation = f"市场整体看跌(权重{bearish_weight*100:.0f}%)，建议优先考虑空单机会"
        else:
            overall_signal = 'NEUTRAL'
            recommendation = f"市场方向不明确(多:{bullish_weight*100:.0f}% 空:{bearish_weight*100:.0f}%)，建议观望或减少仓位"

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
            'bullish_weight': bullish_weight,  # 新增：看涨权重
            'bearish_weight': bearish_weight,  # 新增：看跌权重
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
                # 检查Big4是否已经反弹完成（反弹超过3%）
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

                        # 如果任一币种未完成3%反弹，认为市场尚未恢复
                        if recovery_pct < 3.0:
                            market_recovered = False
                            break

                # 如果市场已恢复，解除禁止做空限制
                if market_recovered:
                    block_short = False
                    reasons.append("✅ 市场已反弹3%+，解除做空限制")

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

                        # 判断条件:
                        # 1. 72H持续下跌 >= 8%
                        # 2. 24H加速下跌 >= 4%
                        # 3. 首次触底 (24H内没有其他长下影线)
                        is_sustained_drop = drop_from_high_72h <= -8.0
                        is_accelerating = drop_from_high_24h <= -4.0

                        # 检查24H内是否首次出现长下影线
                        is_first_bottom = True
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

                        # 三个条件都满足 = 真深V反转
                        is_true_deep_v = is_sustained_drop and is_accelerating and is_first_bottom

                        if not is_true_deep_v:
                            logger.info(f"⚠️ {symbol} 下影{lower_shadow_pct:.1f}% 不满足大周期条件: "
                                      f"72H跌幅{drop_from_high_72h:.1f}% (需<-8%), "
                                      f"24H跌幅{drop_from_high_24h:.1f}% (需<-4%), "
                                      f"首次触底{'✅' if is_first_bottom else '❌'}")
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
                conn_write = pymysql.connect(**self.db_config)
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
                conn_write.close()

            except Exception as e:
                logger.error(f"❌ 保存反弹窗口失败: {e}")

        cursor.close()

        # 4. 如果检测到新的反转，保存到数据库
        # 🔥 修复: 触底和触顶分开处理，分别插入记录
        if bottom_detected or top_detected:
            expires_at = datetime.now() + timedelta(hours=self.BLOCK_DURATION_HOURS)

            try:
                conn_write = pymysql.connect(**self.db_config)
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
                conn_write.close()

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
