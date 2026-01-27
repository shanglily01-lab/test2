#!/usr/bin/env python3
"""
信号分析服务 - 24H K线强度 + 信号捕捉分析
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pymysql
from loguru import logger


class SignalAnalysisService:
    """信号分析服务"""

    def __init__(self, db_config: Dict):
        """初始化服务

        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config
        self.connection = None

    def _get_connection(self):
        """获取数据库连接"""
        if self.connection is None or not self.connection.open:
            self.connection = pymysql.connect(**self.db_config)
        return self.connection

    def analyze_kline_strength(self, symbol: str, timeframe: str, hours: int = 24) -> Optional[Dict]:
        """分析K线强度

        Args:
            symbol: 交易对
            timeframe: 时间周期 (5m/15m/1h)
            hours: 分析的小时数

        Returns:
            K线强度分析结果，包含总数、多空比例、强力K线等
        """
        conn = self._get_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        cursor.execute('''
            SELECT
                timestamp,
                open_price,
                close_price,
                volume,
                high_price,
                low_price
            FROM kline_data
            WHERE symbol = %s
            AND timeframe = %s
            AND timestamp >= DATE_SUB(NOW(), INTERVAL %s HOUR)
            ORDER BY timestamp DESC
        ''', (symbol, timeframe, hours))

        klines = cursor.fetchall()
        cursor.close()

        if not klines:
            return None

        total_klines = len(klines)
        bull_klines = sum(1 for k in klines if float(k['close_price']) > float(k['open_price']))
        bear_klines = total_klines - bull_klines

        # 计算平均成交量
        volumes = [float(k['volume']) for k in klines]
        avg_volume = sum(volumes) / len(volumes) if volumes else 0

        # 强力K线（成交量>1.2倍均量）
        strong_bull = 0
        strong_bear = 0

        for k in klines:
            is_bull = float(k['close_price']) > float(k['open_price'])
            is_high_volume = float(k['volume']) > avg_volume * 1.2

            if is_bull and is_high_volume:
                strong_bull += 1
            elif not is_bull and is_high_volume:
                strong_bear += 1

        return {
            'total': total_klines,
            'bull': bull_klines,
            'bear': bear_klines,
            'bull_pct': (bull_klines / total_klines * 100) if total_klines > 0 else 0,
            'strong_bull': strong_bull,
            'strong_bear': strong_bear,
            'net_power': strong_bull - strong_bear,
            'avg_volume': avg_volume
        }

    def check_signal_status(self, symbol: str, hours: int = 24) -> Dict:
        """检查信号状态

        Args:
            symbol: 交易对
            hours: 检查的小时数

        Returns:
            信号状态，包含是否有持仓、持仓详情等
        """
        conn = self._get_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        past_hours = datetime.now() - timedelta(hours=hours)

        cursor.execute('''
            SELECT
                id,
                position_side,
                entry_signal_type,
                open_time,
                status
            FROM futures_positions
            WHERE symbol = %s
            AND open_time >= %s
            ORDER BY open_time DESC
            LIMIT 1
        ''', (symbol, past_hours))

        position = cursor.fetchone()
        cursor.close()

        return {
            'has_position': position is not None,
            'position': position
        }

    def analyze_all_symbols(self, symbols: List[str], hours: int = 24) -> Dict:
        """分析所有交易对

        Args:
            symbols: 交易对列表
            hours: 分析的小时数

        Returns:
            完整的分析报告
        """
        logger.info(f"开始分析 {len(symbols)} 个交易对的{hours}H信号情况...")

        results = []

        for symbol in symbols:
            # K线强度分析
            strength_5m = self.analyze_kline_strength(symbol, '5m', hours)
            strength_15m = self.analyze_kline_strength(symbol, '15m', hours)
            strength_1h = self.analyze_kline_strength(symbol, '1h', hours)

            if not all([strength_5m, strength_15m, strength_1h]):
                continue

            # 信号状态
            signal_status = self.check_signal_status(symbol, hours)

            results.append({
                'symbol': symbol,
                'strength_5m': strength_5m,
                'strength_15m': strength_15m,
                'strength_1h': strength_1h,
                'signal_status': signal_status
            })

        # 按净力量排序
        results.sort(key=lambda x: abs(x['strength_1h']['net_power']), reverse=True)

        # 生成统计信息
        stats = self._generate_statistics(results)

        # 分析错过的机会
        missed_opportunities = self._analyze_missed_opportunities(results)

        logger.info(f"分析完成: {stats['total_analyzed']}个交易对, "
                   f"{stats['has_position']}个已开仓, "
                   f"{stats['missed']}个错过机会, "
                   f"捕获率{stats['capture_rate']:.1f}%")

        return {
            'results': results,
            'statistics': stats,
            'missed_opportunities': missed_opportunities,
            'analysis_time': datetime.now()
        }

    def _generate_statistics(self, results: List[Dict]) -> Dict:
        """生成统计信息"""
        total_analyzed = len(results)
        has_position = sum(1 for r in results if r['signal_status']['has_position'])

        should_trade = sum(1 for r in results
                          if abs(r['strength_1h']['net_power']) >= 3
                          or r['strength_1h']['bull_pct'] > 60
                          or r['strength_1h']['bull_pct'] < 40)

        missed = sum(1 for r in results
                    if not r['signal_status']['has_position']
                    and (abs(r['strength_1h']['net_power']) >= 3
                         or r['strength_1h']['bull_pct'] > 60
                         or r['strength_1h']['bull_pct'] < 40))

        # 方向错误统计
        wrong_direction = 0
        for r in results:
            s1h = r['strength_1h']
            sig = r['signal_status']
            if not sig['has_position']:
                continue
            pos_side = sig['position']['position_side']

            # 判断建议方向
            if s1h['net_power'] >= 3 or s1h['bull_pct'] > 55:
                suggest_side = 'LONG'
            elif s1h['net_power'] <= -3 or s1h['bull_pct'] < 45:
                suggest_side = 'SHORT'
            else:
                suggest_side = None

            if suggest_side and pos_side != suggest_side:
                wrong_direction += 1

        correct_captures = has_position - wrong_direction
        capture_rate = (correct_captures / should_trade * 100) if should_trade > 0 else 0

        return {
            'total_analyzed': total_analyzed,
            'has_position': has_position,
            'should_trade': should_trade,
            'missed': missed,
            'wrong_direction': wrong_direction,
            'correct_captures': correct_captures,
            'capture_rate': capture_rate
        }

    def _analyze_missed_opportunities(self, results: List[Dict]) -> List[Dict]:
        """分析错过的机会"""
        missed_opportunities = []

        for r in results:
            s = r['symbol']
            s1h = r['strength_1h']
            s15m = r['strength_15m']
            s5m = r['strength_5m']
            sig = r['signal_status']

            # 判断是否应该交易
            should_trade = abs(s1h['net_power']) >= 3 or s1h['bull_pct'] > 60 or s1h['bull_pct'] < 40

            if should_trade and not sig['has_position']:
                # 判断多空倾向
                if s1h['net_power'] >= 3:
                    suggest_side = 'LONG'
                    reason = f'1H净力量{s1h["net_power"]:+d}'
                elif s1h['net_power'] <= -3:
                    suggest_side = 'SHORT'
                    reason = f'1H净力量{s1h["net_power"]:+d}'
                elif s1h['bull_pct'] > 60:
                    suggest_side = 'LONG'
                    reason = f'1H阳线占比{s1h["bull_pct"]:.0f}%'
                elif s1h['bull_pct'] < 40:
                    suggest_side = 'SHORT'
                    reason = f'1H阳线占比{s1h["bull_pct"]:.0f}%'
                else:
                    suggest_side = None
                    reason = ''

                # 分析可能的原因
                possible_reasons = []

                # 检查5M是否有冲突信号
                if suggest_side == 'LONG' and s5m['net_power'] < -3:
                    possible_reasons.append(f'5M净力量为{s5m["net_power"]}(空头)，与1H多头冲突')
                elif suggest_side == 'SHORT' and s5m['net_power'] > 3:
                    possible_reasons.append(f'5M净力量为+{s5m["net_power"]}(多头)，与1H空头冲突')

                # 检查是否信号评分不够
                if not possible_reasons:
                    possible_reasons.append('可能信号评分未达到开仓阈值(45分)')

                # 检查是否在黑名单
                possible_reasons.append('或交易对在黑名单/评级过低')

                # 检查是否已有相同方向持仓
                possible_reasons.append('或已有同向持仓未平')

                missed_opportunities.append({
                    'symbol': s,
                    'side': suggest_side,
                    'reason': reason,
                    'possible_reasons': possible_reasons,
                    'net_power_1h': s1h['net_power'],
                    'net_power_15m': s15m['net_power'],
                    'net_power_5m': s5m['net_power']
                })

        return missed_opportunities

    def close(self):
        """关闭数据库连接"""
        if self.connection and self.connection.open:
            self.connection.close()
