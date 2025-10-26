#!/usr/bin/env python3
"""
ETF 数据分析模块
分析加密货币 ETF 的资金流向，生成市场情绪评分
"""

from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple
import pymysql
from decimal import Decimal


class ETFAnalyzer:
    """ETF 数据分析器"""

    def __init__(self, db_connection):
        """
        初始化 ETF 分析器

        Args:
            db_connection: 数据库连接
        """
        self.conn = db_connection
        self.cursor = db_connection.cursor()

    def get_etf_score(self, symbol: str, analysis_date: date = None) -> Dict:
        """
        获取 ETF 综合评分

        Args:
            symbol: 交易对符号 (如 'BTC/USDT')
            analysis_date: 分析日期 (默认最新)

        Returns:
            ETF 评分结果字典
        """
        # 提取资产类型 (BTC 或 ETH)
        asset_type = self._extract_asset_type(symbol)

        if not asset_type:
            return {
                'score': 50,  # 中性评分
                'signal': 'neutral',
                'confidence': 0.0,
                'details': {'error': f'不支持的币种: {symbol}'}
            }

        # 获取 ETF 数据
        etf_data = self._get_etf_flows(asset_type, analysis_date)

        if not etf_data:
            return {
                'score': 50,  # 中性评分
                'signal': 'neutral',
                'confidence': 0.0,
                'details': {'error': f'无 {asset_type} ETF 数据'}
            }

        # 计算评分
        score_result = self._calculate_etf_score(etf_data, asset_type)

        return score_result

    def _extract_asset_type(self, symbol: str) -> Optional[str]:
        """
        从交易对提取资产类型

        Args:
            symbol: 交易对符号 (如 'BTC/USDT', 'ETH/USDT')

        Returns:
            'BTC' 或 'ETH' 或 None
        """
        symbol_upper = symbol.upper()
        if 'BTC' in symbol_upper:
            return 'BTC'
        elif 'ETH' in symbol_upper:
            return 'ETH'
        return None

    def _get_etf_flows(self, asset_type: str, analysis_date: date = None) -> Dict:
        """
        获取 ETF 资金流向数据

        Args:
            asset_type: 资产类型 ('BTC' 或 'ETH')
            analysis_date: 分析日期

        Returns:
            ETF 数据字典
        """
        if analysis_date is None:
            # 获取最新日期
            self.cursor.execute("""
                SELECT MAX(trade_date)
                FROM crypto_etf_flows f
                JOIN crypto_etf_products p ON f.etf_id = p.id
                WHERE p.asset_type = %s
            """, (asset_type,))
            result = self.cursor.fetchone()
            if not result or not result[0]:
                return {}
            analysis_date = result[0]

        # 获取当日数据
        self.cursor.execute("""
            SELECT
                f.trade_date,
                f.ticker,
                f.net_inflow,
                f.gross_inflow,
                f.gross_outflow,
                p.full_name
            FROM crypto_etf_flows f
            JOIN crypto_etf_products p ON f.etf_id = p.id
            WHERE p.asset_type = %s
                AND f.trade_date = %s
            ORDER BY f.net_inflow DESC
        """, (asset_type, analysis_date))

        flows = []
        total_inflow = 0
        total_outflow = 0

        for row in self.cursor.fetchall():
            trade_date, ticker, net_inflow, gross_inflow, gross_outflow, name = row

            net_inflow = float(net_inflow) if net_inflow else 0
            gross_inflow = float(gross_inflow) if gross_inflow else 0
            gross_outflow = float(gross_outflow) if gross_outflow else 0

            flows.append({
                'ticker': ticker,
                'name': name,
                'net_inflow': net_inflow,
                'gross_inflow': gross_inflow,
                'gross_outflow': gross_outflow
            })

            total_inflow += net_inflow

        # 获取历史趋势 (最近 5 天)
        self.cursor.execute("""
            SELECT
                f.trade_date,
                SUM(f.net_inflow) as daily_total
            FROM crypto_etf_flows f
            JOIN crypto_etf_products p ON f.etf_id = p.id
            WHERE p.asset_type = %s
                AND f.trade_date <= %s
                AND f.trade_date >= DATE_SUB(%s, INTERVAL 5 DAY)
            GROUP BY f.trade_date
            ORDER BY f.trade_date DESC
            LIMIT 5
        """, (asset_type, analysis_date, analysis_date))

        history = []
        for row in self.cursor.fetchall():
            trade_date, daily_total = row
            history.append({
                'date': trade_date,
                'total_inflow': float(daily_total) if daily_total else 0
            })

        return {
            'asset_type': asset_type,
            'analysis_date': analysis_date,
            'total_net_inflow': total_inflow,
            'etf_count': len(flows),
            'flows': flows,
            'history': history
        }

    def _calculate_etf_score(self, etf_data: Dict, asset_type: str) -> Dict:
        """
        计算 ETF 综合评分

        评分逻辑:
        1. 当日净流入/流出 (40%权重)
        2. 流入强度 (30%权重)
        3. 趋势连续性 (30%权重)

        Args:
            etf_data: ETF 数据
            asset_type: 资产类型

        Returns:
            评分结果
        """
        total_inflow = etf_data['total_net_inflow']
        flows = etf_data['flows']
        history = etf_data['history']

        # 1. 当日净流入评分 (40%权重)
        # BTC: 超过 $500M 极度看涨, $200M 看涨, -$200M 看跌, -$500M 极度看跌
        # ETH: 超过 $100M 极度看涨, $50M 看涨, -$50M 看跌, -$100M 极度看跌

        if asset_type == 'BTC':
            thresholds = {
                'strong_bullish': 500_000_000,
                'bullish': 200_000_000,
                'bearish': -200_000_000,
                'strong_bearish': -500_000_000
            }
        else:  # ETH
            thresholds = {
                'strong_bullish': 100_000_000,
                'bullish': 50_000_000,
                'bearish': -50_000_000,
                'strong_bearish': -100_000_000
            }

        if total_inflow >= thresholds['strong_bullish']:
            daily_score = 90
        elif total_inflow >= thresholds['bullish']:
            # 线性插值
            ratio = total_inflow / thresholds['strong_bullish']
            daily_score = 70 + (ratio * 20)
        elif total_inflow >= 0:
            # 正流入但不强
            ratio = total_inflow / thresholds['bullish']
            daily_score = 50 + (ratio * 20)
        elif total_inflow >= thresholds['bearish']:
            # 轻微负流入
            ratio = abs(total_inflow) / abs(thresholds['bearish'])
            daily_score = 50 - (ratio * 20)
        elif total_inflow >= thresholds['strong_bearish']:
            # 严重负流入
            ratio = abs(total_inflow) / abs(thresholds['strong_bearish'])
            daily_score = 30 - (ratio * 20)
        else:
            # 极度负流入
            daily_score = 10

        # 2. 流入强度评分 (30%权重)
        # 计算有多少个 ETF 是正流入
        positive_count = sum(1 for f in flows if f['net_inflow'] > 0)
        negative_count = sum(1 for f in flows if f['net_inflow'] < 0)
        total_count = len(flows)

        if total_count > 0:
            positive_ratio = positive_count / total_count
            intensity_score = 50 + (positive_ratio - 0.5) * 100
        else:
            intensity_score = 50

        # 3. 趋势连续性评分 (30%权重)
        if len(history) >= 3:
            # 检查是否连续流入/流出
            recent_inflows = [h['total_inflow'] for h in history[:3]]

            consecutive_positive = all(x > 0 for x in recent_inflows)
            consecutive_negative = all(x < 0 for x in recent_inflows)

            if consecutive_positive:
                # 计算增长趋势
                if recent_inflows[0] > recent_inflows[1]:
                    trend_score = 80  # 流入加速
                else:
                    trend_score = 70  # 持续流入
            elif consecutive_negative:
                # 计算下降趋势
                if recent_inflows[0] < recent_inflows[1]:
                    trend_score = 20  # 流出加速
                else:
                    trend_score = 30  # 持续流出
            else:
                # 趋势不明确
                trend_score = 50
        else:
            trend_score = 50

        # 综合评分
        final_score = (
            daily_score * 0.4 +
            intensity_score * 0.3 +
            trend_score * 0.3
        )

        # 生成信号
        if final_score >= 70:
            signal = 'strong_bullish'
            signal_text = '强烈看涨'
        elif final_score >= 60:
            signal = 'bullish'
            signal_text = '看涨'
        elif final_score >= 40:
            signal = 'neutral'
            signal_text = '中性'
        elif final_score >= 30:
            signal = 'bearish'
            signal_text = '看跌'
        else:
            signal = 'strong_bearish'
            signal_text = '强烈看跌'

        # 置信度: 基于数据完整性和一致性
        confidence = self._calculate_confidence(etf_data, daily_score, intensity_score, trend_score)

        # 构建详细信息
        details = {
            'total_net_inflow': total_inflow,
            'etf_count': total_count,
            'positive_count': positive_count,
            'negative_count': negative_count,
            'daily_score': round(daily_score, 2),
            'intensity_score': round(intensity_score, 2),
            'trend_score': round(trend_score, 2),
            'top_inflows': sorted(flows, key=lambda x: x['net_inflow'], reverse=True)[:3],
            'top_outflows': sorted(flows, key=lambda x: x['net_inflow'])[:3],
            'history_days': len(history),
            'signal_text': signal_text
        }

        return {
            'score': round(final_score, 2),
            'signal': signal,
            'confidence': round(confidence, 2),
            'details': details
        }

    def _calculate_confidence(self, etf_data: Dict, daily_score: float,
                             intensity_score: float, trend_score: float) -> float:
        """
        计算评分置信度

        Args:
            etf_data: ETF 数据
            daily_score, intensity_score, trend_score: 各项评分

        Returns:
            置信度 (0-1)
        """
        confidence = 0.5  # 基础置信度

        # 1. 数据完整性 (+0.2)
        if etf_data['etf_count'] >= 8:  # 至少8个ETF有数据
            confidence += 0.2
        elif etf_data['etf_count'] >= 5:
            confidence += 0.1

        # 2. 历史数据充足性 (+0.15)
        if len(etf_data['history']) >= 5:
            confidence += 0.15
        elif len(etf_data['history']) >= 3:
            confidence += 0.1

        # 3. 各项评分一致性 (+0.15)
        scores = [daily_score, intensity_score, trend_score]
        avg_score = sum(scores) / len(scores)
        variance = sum((s - avg_score) ** 2 for s in scores) / len(scores)

        # 方差小说明一致性高
        if variance < 100:  # 非常一致
            confidence += 0.15
        elif variance < 300:  # 较一致
            confidence += 0.1
        elif variance < 500:  # 一般
            confidence += 0.05

        return min(confidence, 1.0)  # 最大1.0

    def get_etf_summary(self, asset_type: str = None, days: int = 5) -> Dict:
        """
        获取 ETF 数据摘要

        Args:
            asset_type: 资产类型 (None=所有)
            days: 查询天数

        Returns:
            摘要数据
        """
        where_clause = ""
        params = []

        if asset_type:
            where_clause = "WHERE p.asset_type = %s"
            params.append(asset_type)

        self.cursor.execute(f"""
            SELECT
                p.asset_type,
                f.trade_date,
                COUNT(DISTINCT f.etf_id) as etf_count,
                SUM(f.net_inflow) as total_inflow,
                AVG(f.net_inflow) as avg_inflow,
                MAX(f.net_inflow) as max_inflow,
                MIN(f.net_inflow) as min_inflow
            FROM crypto_etf_flows f
            JOIN crypto_etf_products p ON f.etf_id = p.id
            {where_clause}
            GROUP BY p.asset_type, f.trade_date
            ORDER BY f.trade_date DESC
            LIMIT {days}
        """, params)

        summary = []
        for row in self.cursor.fetchall():
            asset, trade_date, count, total, avg, max_flow, min_flow = row
            summary.append({
                'asset_type': asset,
                'date': trade_date,
                'etf_count': count,
                'total_inflow': float(total) if total else 0,
                'avg_inflow': float(avg) if avg else 0,
                'max_inflow': float(max_flow) if max_flow else 0,
                'min_inflow': float(min_flow) if min_flow else 0
            })

        return summary


if __name__ == '__main__':
    """测试 ETF 分析器"""
    import yaml

    # 加载配置
    with open('../../config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
        db_config = config['database']['mysql']

    # 连接数据库
    conn = pymysql.connect(
        host=db_config['host'],
        user=db_config['user'],
        password=db_config['password'],
        database=db_config['database']
    )

    # 创建分析器
    analyzer = ETFAnalyzer(conn)

    # 测试 BTC
    print("=" * 80)
    print("BTC ETF 分析")
    print("=" * 80)
    btc_score = analyzer.get_etf_score('BTC/USDT')
    print(f"\n评分: {btc_score['score']}")
    print(f"信号: {btc_score['signal']} ({btc_score['details']['signal_text']})")
    print(f"置信度: {btc_score['confidence']}")
    print(f"\n总净流入: ${btc_score['details']['total_net_inflow']:,.0f}")
    print(f"ETF 数量: {btc_score['details']['etf_count']}")
    print(f"  流入: {btc_score['details']['positive_count']}")
    print(f"  流出: {btc_score['details']['negative_count']}")

    print("\n流入前3名:")
    for etf in btc_score['details']['top_inflows']:
        print(f"  {etf['ticker']:6s}: ${etf['net_inflow']:>15,.0f}")

    print("\n流出前3名:")
    for etf in btc_score['details']['top_outflows']:
        print(f"  {etf['ticker']:6s}: ${etf['net_inflow']:>15,.0f}")

    # 测试 ETH
    print("\n" + "=" * 80)
    print("ETH ETF 分析")
    print("=" * 80)
    eth_score = analyzer.get_etf_score('ETH/USDT')
    print(f"\n评分: {eth_score['score']}")
    print(f"信号: {eth_score['signal']} ({eth_score['details']['signal_text']})")
    print(f"置信度: {eth_score['confidence']}")

    conn.close()
