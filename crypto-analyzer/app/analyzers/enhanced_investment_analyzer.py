"""
增强版投资分析器
整合多维度数据源生成综合投资建议:
1. 技术指标 (RSI, MACD, 布林带等)
2. 新闻情绪分析
3. 资金费率 (期货市场情绪)
4. Hyperliquid 聪明钱活动
5. 以太坊链上聪明钱交易
6. ETF 资金流向 (机构资金情绪)
"""

from typing import Dict, List, Optional
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class EnhancedInvestmentAnalyzer:
    """增强版投资分析器"""

    def __init__(self, config: dict = None):
        """
        初始化

        Args:
            config: 配置字典
        """
        self.config = config or {}

        # 权重配置 (总和 = 1.0)
        weights = self.config.get('analysis', {}).get('weights', {})
        self.technical_weight = weights.get('technical', 0.35)      # 技术指标 35%
        self.news_weight = weights.get('news', 0.10)                # 新闻情绪 10%
        self.funding_weight = weights.get('funding', 0.15)          # 资金费率 15%
        self.hyperliquid_weight = weights.get('hyperliquid', 0.15)  # Hyperliquid 15%
        self.ethereum_weight = weights.get('ethereum', 0.10)        # 以太坊链上 10%
        self.etf_weight = weights.get('etf', 0.15)                  # ETF 流向 15%

        # 置信度阈值
        self.strong_buy_threshold = 75
        self.buy_threshold = 60
        self.sell_threshold = 40
        self.strong_sell_threshold = 25

    def analyze(
        self,
        symbol: str,
        technical_data: Optional[Dict] = None,
        news_data: Optional[Dict] = None,
        funding_data: Optional[Dict] = None,
        hyperliquid_data: Optional[Dict] = None,
        ethereum_data: Optional[Dict] = None,
        etf_data: Optional[Dict] = None,
        current_price: Optional[float] = None
    ) -> Dict:
        """
        综合分析并生成投资建议

        Args:
            symbol: 交易对 (如 BTC/USDT)
            technical_data: 技术指标数据
            news_data: 新闻情绪数据
            funding_data: 资金费率数据
            hyperliquid_data: Hyperliquid 聪明钱数据
            ethereum_data: 以太坊链上数据
            etf_data: ETF 资金流向数据
            current_price: 当前价格

        Returns:
            投资建议字典
        """
        # 1. 计算各维度评分 (0-100)
        scores = {
            'technical': self._analyze_technical(technical_data) if technical_data else 50,
            'news': self._analyze_news(news_data) if news_data else 50,
            'funding': self._analyze_funding(funding_data) if funding_data else 50,
            'hyperliquid': self._analyze_hyperliquid(hyperliquid_data) if hyperliquid_data else 50,
            'ethereum': self._analyze_ethereum(ethereum_data) if ethereum_data else 50,
            'etf': self._analyze_etf(etf_data) if etf_data else 50
        }

        # 2. 计算加权综合评分
        weighted_score = (
            scores['technical'] * self.technical_weight +
            scores['news'] * self.news_weight +
            scores['funding'] * self.funding_weight +
            scores['hyperliquid'] * self.hyperliquid_weight +
            scores['ethereum'] * self.ethereum_weight +
            scores['etf'] * self.etf_weight
        )

        # 3. 确定信号和置信度
        signal, confidence = self._determine_signal(weighted_score, scores)

        # 4. 生成建议理由
        reasons = self._generate_reasons(scores, technical_data, news_data,
                                        funding_data, hyperliquid_data, ethereum_data, etf_data)

        # 5. 计算价格目标
        entry, stop_loss, take_profit = self._calculate_targets(
            current_price or (technical_data.get('price', 0) if technical_data else 0),
            signal
        )

        # 6. 风险评估
        risk_level, risk_factors = self._assess_risk(scores, signal)

        return {
            'symbol': symbol,
            'timestamp': datetime.now().isoformat(),
            'signal': signal,
            'confidence': round(confidence, 1),
            'score': {
                'total': round(weighted_score, 1),
                'technical': round(scores['technical'], 1),
                'news': round(scores['news'], 1),
                'funding': round(scores['funding'], 1),
                'hyperliquid': round(scores['hyperliquid'], 1),
                'ethereum': round(scores['ethereum'], 1),
                'etf': round(scores['etf'], 1)
            },
            'price': {
                'current': current_price,
                'entry': entry,
                'stop_loss': stop_loss,
                'take_profit': take_profit
            },
            'reasons': reasons,
            'risk': {
                'level': risk_level,
                'factors': risk_factors
            },
            'data_sources': {
                'technical': technical_data is not None,
                'news': news_data is not None,
                'funding': funding_data is not None,
                'hyperliquid': hyperliquid_data is not None,
                'ethereum': ethereum_data is not None,
                'etf': etf_data is not None
            }
        }

    def _analyze_technical(self, data: Dict) -> float:
        """
        分析技术指标

        Returns:
            评分 0-100 (50=中性, >50=看涨, <50=看跌)
        """
        score = 50  # 起始中性分

        # RSI 分析 (权重: 20%)
        rsi = data.get('rsi', {})
        rsi_value = rsi.get('value', 50)
        if rsi_value < 30:  # 超卖
            score += 15
        elif rsi_value < 40:  # 偏低
            score += 8
        elif rsi_value > 70:  # 超买
            score -= 12
        elif rsi_value > 60:  # 偏高
            score -= 6

        # MACD 分析 (权重: 25%)
        macd = data.get('macd', {})
        if macd.get('bullish_cross'):
            score += 18
        elif macd.get('bearish_cross'):
            score -= 18
        elif macd.get('histogram', 0) > 0:
            score += 8
        else:
            score -= 8

        # 布林带分析 (权重: 20%)
        bb = data.get('bollinger', {})
        price_pos = bb.get('price_position', 'middle')
        if price_pos == 'below_lower':
            score += 12
        elif price_pos == 'above_upper':
            score -= 10

        # EMA 趋势 (权重: 20%)
        ema = data.get('ema', {})
        trend = ema.get('trend', 'neutral')
        if trend == 'up':
            score += 12
        elif trend == 'down':
            score -= 12

        # 成交量确认 (权重: 15%)
        volume = data.get('volume', {})
        if volume.get('above_average'):
            # 成交量放大,增强信号
            if score > 50:
                score += 8
            elif score < 50:
                score -= 8

        return max(0, min(100, score))

    def _analyze_news(self, data: Dict) -> float:
        """
        分析新闻情绪

        Returns:
            评分 0-100
        """
        sentiment_index = data.get('sentiment_index', 0)  # -100 到 100

        # 转换为 0-100
        score = 50 + (sentiment_index / 2)

        # 新闻数量影响置信度
        news_count = data.get('total_news', 0)
        if news_count < 3:
            # 新闻太少,向中性回归
            score = 50 + (score - 50) * 0.5

        # 重大事件加权
        if data.get('major_events_count', 0) > 0:
            if score > 50:
                score += 10
            else:
                score -= 10

        return max(0, min(100, score))

    def _analyze_funding(self, data: Dict) -> float:
        """
        分析资金费率 (期货市场情绪指标)

        资金费率含义:
        - 正值且高: 多头过热,可能回调 (看跌信号)
        - 负值且低: 空头过度,可能反弹 (看涨信号)
        - 接近0: 市场平衡 (中性)

        Returns:
            评分 0-100
        """
        funding_rate = data.get('funding_rate', 0)  # 例如 0.0001 = 0.01%

        score = 50  # 中性起点

        # 资金费率阈值
        if funding_rate > 0.001:  # >0.1% (极度多头过热)
            score = 25  # 强烈看跌
        elif funding_rate > 0.0005:  # >0.05% (多头过热)
            score = 35  # 看跌
        elif funding_rate > 0.0001:  # >0.01% (轻微多头)
            score = 45  # 略偏空
        elif funding_rate < -0.001:  # <-0.1% (极度空头过度)
            score = 75  # 强烈看涨
        elif funding_rate < -0.0005:  # <-0.05% (空头过度)
            score = 65  # 看涨
        elif funding_rate < -0.0001:  # <-0.01% (轻微空头)
            score = 55  # 略偏多

        return score

    def _analyze_hyperliquid(self, data: Dict) -> float:
        """
        分析 Hyperliquid 聪明钱活动

        Args:
            data: {
                'smart_money_trades': [...],  # 聪明钱最近交易
                'net_flow': float,             # 净流入 (USD)
                'long_trades': int,            # 做多笔数
                'short_trades': int,           # 做空笔数
                'avg_pnl': float,              # 平均盈亏
                'active_wallets': int          # 活跃钱包数
            }

        Returns:
            评分 0-100
        """
        score = 50

        # 净流入分析 (权重: 40%)
        net_flow = data.get('net_flow', 0)
        if abs(net_flow) > 1000000:  # >$1M
            if net_flow > 0:
                score += 20  # 大额流入
            else:
                score -= 20  # 大额流出
        elif abs(net_flow) > 500000:  # >$500K
            if net_flow > 0:
                score += 12
            else:
                score -= 12
        elif abs(net_flow) > 100000:  # >$100K
            if net_flow > 0:
                score += 6
            else:
                score -= 6

        # 交易方向分析 (权重: 30%)
        long_trades = data.get('long_trades', 0)
        short_trades = data.get('short_trades', 0)
        total_trades = long_trades + short_trades

        if total_trades > 0:
            long_ratio = long_trades / total_trades
            if long_ratio > 0.7:  # >70% 做多
                score += 15
            elif long_ratio > 0.6:
                score += 8
            elif long_ratio < 0.3:  # <30% 做多 (70% 做空)
                score -= 15
            elif long_ratio < 0.4:
                score -= 8

        # 盈亏分析 (权重: 20%)
        avg_pnl = data.get('avg_pnl', 0)
        if avg_pnl > 10000:  # 高盈利
            score += 10
        elif avg_pnl > 0:
            score += 5
        elif avg_pnl < -10000:  # 高亏损
            score -= 10
        elif avg_pnl < 0:
            score -= 5

        # 活跃度分析 (权重: 10%)
        active_wallets = data.get('active_wallets', 0)
        if active_wallets > 5:
            # 多个聪明钱包同时活跃,信号更强
            if score > 50:
                score += 5
            else:
                score -= 5

        return max(0, min(100, score))

    def _analyze_ethereum(self, data: Dict) -> float:
        """
        分析以太坊链上聪明钱活动

        Args:
            data: {
                'recent_transactions': [...],  # 最近交易
                'buy_volume': float,           # 买入量
                'sell_volume': float,          # 卖出量
                'unique_wallets': int,         # 唯一钱包数
                'avg_transaction_size': float  # 平均交易额
            }

        Returns:
            评分 0-100
        """
        score = 50

        buy_volume = data.get('buy_volume', 0)
        sell_volume = data.get('sell_volume', 0)
        total_volume = buy_volume + sell_volume

        if total_volume > 0:
            buy_ratio = buy_volume / total_volume

            if buy_ratio > 0.7:
                score += 20
            elif buy_ratio > 0.6:
                score += 10
            elif buy_ratio < 0.3:
                score -= 20
            elif buy_ratio < 0.4:
                score -= 10

        # 钱包数量
        unique_wallets = data.get('unique_wallets', 0)
        if unique_wallets > 10:
            if score > 50:
                score += 10
            else:
                score -= 10

        # 交易规模
        avg_size = data.get('avg_transaction_size', 0)
        if avg_size > 100000:  # >$100K 大额交易
            if score > 50:
                score += 10
            else:
                score -= 10

        return max(0, min(100, score))

    def _analyze_etf(self, data: Dict) -> float:
        """
        分析 ETF 资金流向 (机构资金情绪)

        Args:
            data: ETF 分析数据 (来自 ETFAnalyzer)
                {
                    'score': float,           # ETF 评分 0-100
                    'signal': str,            # 信号类型
                    'confidence': float,      # 置信度
                    'details': {
                        'total_net_inflow': float,
                        'positive_count': int,
                        'negative_count': int,
                        ...
                    }
                }

        Returns:
            评分 0-100
        """
        # 直接使用 ETF 分析器的评分
        etf_score = data.get('score', 50)

        # 根据置信度调整评分
        confidence = data.get('confidence', 0.5)

        # 置信度低时向中性回归
        if confidence < 0.6:
            etf_score = 50 + (etf_score - 50) * (confidence / 0.6)

        return etf_score

    def _determine_signal(self, weighted_score: float, scores: Dict) -> tuple:
        """
        根据综合评分确定信号和置信度

        Returns:
            (signal, confidence)
        """
        # 基础信号
        if weighted_score >= self.strong_buy_threshold:
            signal = 'STRONG_BUY'
            confidence = weighted_score
        elif weighted_score >= self.buy_threshold:
            signal = 'BUY'
            confidence = weighted_score
        elif weighted_score <= self.strong_sell_threshold:
            signal = 'STRONG_SELL'
            confidence = 100 - weighted_score
        elif weighted_score <= self.sell_threshold:
            signal = 'SELL'
            confidence = 100 - weighted_score
        else:
            signal = 'HOLD'
            confidence = 50

        # 一致性检查 - 所有维度方向一致则提升置信度
        bullish_count = sum(1 for v in scores.values() if v > 55)
        bearish_count = sum(1 for v in scores.values() if v < 45)

        if bullish_count >= 4:  # 4个以上维度看涨
            confidence = min(confidence + 10, 100)
        elif bearish_count >= 4:  # 4个以上维度看跌
            confidence = min(confidence + 10, 100)
        elif bullish_count + bearish_count < 3:  # 信号分歧
            confidence = max(confidence - 10, 0)

        return signal, confidence

    def _calculate_targets(self, current_price: float, signal: str) -> tuple:
        """
        计算价格目标

        Returns:
            (entry, stop_loss, take_profit)
        """
        if current_price == 0:
            return (0, 0, 0)

        if signal in ['BUY', 'STRONG_BUY']:
            entry = current_price
            stop_loss = current_price * 0.97  # -3%
            if signal == 'STRONG_BUY':
                take_profit = current_price * 1.10  # +10%
            else:
                take_profit = current_price * 1.06  # +6%
        elif signal in ['SELL', 'STRONG_SELL']:
            entry = current_price
            stop_loss = current_price * 1.03  # +3%
            if signal == 'STRONG_SELL':
                take_profit = current_price * 0.90  # -10%
            else:
                take_profit = current_price * 0.94  # -6%
        else:  # HOLD
            entry = current_price
            stop_loss = current_price * 0.98
            take_profit = current_price * 1.02

        return (
            round(entry, 2),
            round(stop_loss, 2),
            round(take_profit, 2)
        )

    def _generate_reasons(
        self,
        scores: Dict,
        technical: Optional[Dict],
        news: Optional[Dict],
        funding: Optional[Dict],
        hyperliquid: Optional[Dict],
        ethereum: Optional[Dict],
        etf: Optional[Dict]
    ) -> List[str]:
        """生成投资建议理由"""
        reasons = []

        # 技术面
        if technical and scores['technical'] != 50:
            if scores['technical'] > 60:
                reasons.append(f"📊 技术指标看涨 (评分: {scores['technical']:.0f}/100)")
                # 具体指标
                if technical.get('rsi', {}).get('value', 50) < 40:
                    reasons.append("  • RSI处于低位,超卖反弹")
                if technical.get('macd', {}).get('bullish_cross'):
                    reasons.append("  • MACD金叉形成")
            elif scores['technical'] < 40:
                reasons.append(f"📊 技术指标看跌 (评分: {scores['technical']:.0f}/100)")
                if technical.get('rsi', {}).get('value', 50) > 60:
                    reasons.append("  • RSI处于高位,超买回调")
                if technical.get('macd', {}).get('bearish_cross'):
                    reasons.append("  • MACD死叉形成")

        # 新闻面
        if news and scores['news'] != 50:
            if scores['news'] > 60:
                reasons.append(f"📰 新闻面利好 (评分: {scores['news']:.0f}/100, {news.get('total_news', 0)}条新闻)")
            elif scores['news'] < 40:
                reasons.append(f"📰 新闻面利空 (评分: {scores['news']:.0f}/100, {news.get('total_news', 0)}条新闻)")

        # 资金费率
        if funding and scores['funding'] != 50:
            funding_rate_pct = funding.get('funding_rate', 0) * 100
            if scores['funding'] > 60:
                reasons.append(f"💰 资金费率看涨 ({funding_rate_pct:+.3f}% - 空头过度)")
            elif scores['funding'] < 40:
                reasons.append(f"💰 资金费率看跌 ({funding_rate_pct:+.3f}% - 多头过热)")

        # Hyperliquid 聪明钱
        if hyperliquid and scores['hyperliquid'] != 50:
            net_flow = hyperliquid.get('net_flow', 0)
            active_wallets = hyperliquid.get('active_wallets', 0)
            if scores['hyperliquid'] > 60:
                reasons.append(f"🧠 Hyperliquid聪明钱看涨 (净流入: ${abs(net_flow):,.0f}, {active_wallets}个活跃钱包)")
            elif scores['hyperliquid'] < 40:
                reasons.append(f"🧠 Hyperliquid聪明钱看跌 (净流出: ${abs(net_flow):,.0f}, {active_wallets}个活跃钱包)")

        # 以太坊链上
        if ethereum and scores['ethereum'] != 50:
            unique_wallets = ethereum.get('unique_wallets', 0)
            if scores['ethereum'] > 60:
                reasons.append(f"⛓️  链上聪明钱看涨 ({unique_wallets}个钱包活跃)")
            elif scores['ethereum'] < 40:
                reasons.append(f"⛓️  链上聪明钱看跌 ({unique_wallets}个钱包活跃)")

        # ETF 资金流向
        if etf and scores['etf'] != 50:
            details = etf.get('details', {})
            total_inflow = details.get('total_net_inflow', 0)
            etf_count = details.get('etf_count', 0)
            signal_text = details.get('signal_text', '')

            if scores['etf'] > 60:
                reasons.append(f"🏦 ETF 机构资金看涨 ({signal_text}, 净流入: ${abs(total_inflow):,.0f}, {etf_count}个ETF)")
                # 显示流入最多的 ETF
                top_inflows = details.get('top_inflows', [])
                if top_inflows:
                    top_etf = top_inflows[0]
                    reasons.append(f"  • {top_etf['ticker']} 流入最多: ${top_etf['net_inflow']:,.0f}")
            elif scores['etf'] < 40:
                reasons.append(f"🏦 ETF 机构资金看跌 ({signal_text}, 净流出: ${abs(total_inflow):,.0f}, {etf_count}个ETF)")
                # 显示流出最多的 ETF
                top_outflows = details.get('top_outflows', [])
                if top_outflows:
                    top_etf = top_outflows[0]
                    reasons.append(f"  • {top_etf['ticker']} 流出最多: ${abs(top_etf['net_inflow']):,.0f}")

        return reasons

    def _assess_risk(self, scores: Dict, signal: str) -> tuple:
        """
        评估风险等级

        Returns:
            (risk_level, risk_factors)
        """
        risk_factors = []

        # 计算分歧度
        score_values = list(scores.values())
        score_range = max(score_values) - min(score_values)

        if score_range > 40:
            risk_factors.append("⚠️ 各维度信号分歧较大")

        # 检查极端信号
        if signal in ['STRONG_BUY', 'STRONG_SELL']:
            risk_factors.append("⚠️ 强信号可能伴随高波动")

        # 数据完整性检查
        missing_data = sum(1 for v in scores.values() if v == 50)
        if missing_data >= 2:
            risk_factors.append(f"⚠️ 缺少{missing_data}个维度数据,分析可能不全面")

        # 确定风险等级
        if len(risk_factors) >= 2 or score_range > 50:
            risk_level = 'HIGH'
        elif len(risk_factors) == 1 or score_range > 30:
            risk_level = 'MEDIUM'
        else:
            risk_level = 'LOW'

        # 通用风险提示
        risk_factors.append("💡 加密货币市场波动大,请做好风险管理")
        risk_factors.append("💡 本分析仅供参考,不构成投资建议")

        return risk_level, risk_factors


# 使用示例
if __name__ == '__main__':
    analyzer = EnhancedInvestmentAnalyzer()

    # 模拟数据
    result = analyzer.analyze(
        symbol='BTC/USDT',
        technical_data={
            'price': 95000,
            'rsi': {'value': 35},
            'macd': {'bullish_cross': True, 'histogram': 50},
            'ema': {'trend': 'up'},
            'volume': {'above_average': True}
        },
        news_data={
            'sentiment_index': 45,
            'total_news': 12,
            'major_events_count': 1
        },
        funding_data={
            'funding_rate': -0.0008  # 空头过度
        },
        hyperliquid_data={
            'net_flow': 1500000,
            'long_trades': 15,
            'short_trades': 3,
            'avg_pnl': 25000,
            'active_wallets': 8
        },
        ethereum_data={
            'buy_volume': 8000000,
            'sell_volume': 2000000,
            'unique_wallets': 15,
            'avg_transaction_size': 150000
        },
        current_price=95000
    )

    import json
    print(json.dumps(result, indent=2, ensure_ascii=False))
