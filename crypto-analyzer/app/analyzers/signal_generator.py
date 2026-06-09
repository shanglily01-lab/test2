"""
综合交易信号生成器
整合技术指标、新闻情绪、社交媒体等多维度数据
生成最终的做多/做空建议
"""

from typing import Dict, Optional, List
from datetime import datetime
from loguru import logger


class SignalGenerator:
    """综合信号生成器"""

    def __init__(self, config: dict = None):
        """
        初始化

        Args:
            config: 配置字典，包含权重等参数
        """
        self.config = config or {}

        # 权重配置
        weights = self.config.get('signals', {}).get('weights', {})
        self.technical_weight = weights.get('technical', 0.60)
        self.news_weight = weights.get('news', 0.30)
        self.social_weight = weights.get('social', 0.10)

        # 置信度阈值
        confidence_config = self.config.get('signals', {}).get('confidence', {})
        self.strong_long_threshold = confidence_config.get('strong_long', 0.75)
        self.long_threshold = confidence_config.get('long', 0.60)
        self.short_threshold = confidence_config.get('short', 0.60)
        self.strong_short_threshold = confidence_config.get('strong_short', 0.75)

        # 风险管理
        risk_config = self.config.get('signals', {}).get('risk', {})
        self.stop_loss_pct = risk_config.get('stop_loss_pct', 0.03)
        self.take_profit_pct = risk_config.get('take_profit_pct', 0.05)
        self.max_position = risk_config.get('max_position', 0.5)

    def generate_signal(
        self,
        symbol: str,
        technical_data: Dict,
        news_data: Optional[Dict] = None,
        social_data: Optional[Dict] = None,
        current_price: Optional[float] = None
    ) -> Dict:
        """
        生成综合交易信号

        Args:
            symbol: 交易对
            technical_data: 技术指标数据
            news_data: 新闻情绪数据（可选）
            social_data: 社交媒体数据（可选）
            current_price: 当前价格

        Returns:
            信号字典
        """
        # 1. 获取技术分析评分
        technical_score = self._get_technical_score(technical_data)

        # 2. 获取新闻情绪评分
        news_score = self._get_news_score(news_data) if news_data else 0

        # 3. 获取社交媒体评分
        social_score = self._get_social_score(social_data) if social_data else 0

        # 4. 计算综合评分
        final_score = (
            technical_score * self.technical_weight +
            news_score * self.news_weight +
            social_score * self.social_weight
        )

        # 归一化到 -100 ~ 100
        final_score = max(min(final_score, 100), -100)

        # 5. 生成信号
        action, confidence = self._determine_action(final_score)

        # 6. 计算入场价、止损、止盈
        if current_price is None:
            current_price = technical_data.get('price', 0)

        entry_price, stop_loss, take_profit = self._calculate_levels(
            current_price,
            action
        )

        # 7. 生成原因列表
        reasons = self._generate_reasons(
            technical_data,
            news_data,
            social_data,
            action
        )

        # 8. 风险提示
        risks = self._generate_risk_warnings(action, confidence, news_data)

        return {
            'symbol': symbol,
            'timestamp': datetime.now().isoformat(),
            'action': action,
            'confidence': round(confidence, 2),
            'score': {
                'total': round(final_score, 2),
                'technical': round(technical_score, 2),
                'news': round(news_score, 2),
                'social': round(social_score, 2)
            },
            'price': {
                'current': current_price,
                'entry': entry_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit
            },
            'position': {
                'recommended_size': self._calculate_position_size(confidence),
                'max_loss_pct': self.stop_loss_pct * 100,
                'max_profit_pct': self.take_profit_pct * 100
            },
            'reasons': reasons,
            'risks': risks,
            'timeframe': '1h-4h',  # 建议持仓时间
            'details': {
                'technical': technical_data,
                'news': news_data,
                'social': social_data
            }
        }

    def _get_technical_score(self, data: Dict) -> float:
        """从技术指标数据中提取评分"""
        if not data:
            return 0

        # 如果technical_data已经包含score，直接使用
        if 'score' in data:
            return data['score']

        # 否则，基于indicators计算
        score = 0

        # RSI
        rsi = data.get('rsi', {})
        if rsi.get('oversold'):
            score += 20
        elif rsi.get('overbought'):
            score -= 15

        # MACD
        macd = data.get('macd', {})
        if macd.get('bullish_cross'):
            score += 25
        elif macd.get('bearish_cross'):
            score -= 25

        # 布林带
        bb = data.get('bollinger', {})
        if bb.get('price_position') == 'below_lower':
            score += 15
        elif bb.get('price_position') == 'above_upper':
            score -= 10

        # EMA趋势
        ema = data.get('ema', {})
        if ema.get('trend') == 'up':
            score += 10
        elif ema.get('trend') == 'down':
            score -= 10

        # 成交量
        volume = data.get('volume', {})
        if volume.get('above_average'):
            # 成交量放大，增强信号
            score *= 1.2

        return max(min(score, 100), -100)

    def _get_news_score(self, data: Dict) -> float:
        """从新闻数据中提取评分"""
        if not data:
            return 0

        sentiment_index = data.get('sentiment_index', 0)

        # 检查是否有重大事件
        if data.get('major_events_count', 0) > 0:
            # 重大事件，情绪影响加倍
            sentiment_index *= 1.5

        return max(min(sentiment_index, 100), -100)

    def _get_social_score(self, data: Dict) -> float:
        """从社交媒体数据中提取评分"""
        if not data:
            return 0

        # 社交媒体情绪指数
        return data.get('sentiment_score', 0)

    def _determine_action(self, score: float) -> tuple:
        """
        根据综合评分确定操作

        Returns:
            (action, confidence)
        """
        if score >= 70:
            return 'STRONG_LONG', min(score / 100, 1.0)
        elif score >= 40:
            return 'LONG', score / 100
        elif score <= -70:
            return 'STRONG_SHORT', min(abs(score) / 100, 1.0)
        elif score <= -40:
            return 'SHORT', abs(score) / 100
        else:
            return 'HOLD', 0.5

    def _calculate_levels(
        self,
        current_price: float,
        action: str
    ) -> tuple:
        """
        计算入场价、止损、止盈

        Returns:
            (entry_price, stop_loss, take_profit)
        """
        if action in ['LONG', 'STRONG_LONG']:
            entry = current_price
            stop_loss = entry * (1 - self.stop_loss_pct)
            take_profit = entry * (1 + self.take_profit_pct)
        elif action in ['SHORT', 'STRONG_SHORT']:
            entry = current_price
            stop_loss = entry * (1 + self.stop_loss_pct)
            take_profit = entry * (1 - self.take_profit_pct)
        else:
            # HOLD
            entry = current_price
            stop_loss = current_price * 0.98
            take_profit = current_price * 1.02

        return (
            round(entry, 2),
            round(stop_loss, 2),
            round(take_profit, 2)
        )

    def _calculate_position_size(self, confidence: float) -> float:
        """
        根据置信度计算建议仓位

        Returns:
            仓位比例 (0-1)
        """
        # 置信度越高，仓位越大，但不超过max_position
        position = confidence * self.max_position
        return round(position, 2)

    def _generate_reasons(
        self,
        technical: Dict,
        news: Optional[Dict],
        social: Optional[Dict],
        action: str
    ) -> List[str]:
        """生成信号原因列表"""
        reasons = []

        # 技术面原因
        if technical:
            if 'signals' in technical:
                reasons.extend(technical['signals'])
            else:
                # 基于指标生成原因
                rsi = technical.get('rsi', {})
                if rsi.get('oversold'):
                    reasons.append("RSI超卖，可能反弹")
                elif rsi.get('overbought'):
                    reasons.append("RSI超买，可能回调")

                macd = technical.get('macd', {})
                if macd.get('bullish_cross'):
                    reasons.append("MACD金叉形成")
                elif macd.get('bearish_cross'):
                    reasons.append("MACD死叉形成")

                ema = technical.get('ema', {})
                if ema.get('trend') == 'up':
                    reasons.append("短期均线呈上升趋势")
                elif ema.get('trend') == 'down':
                    reasons.append("短期均线呈下降趋势")

        # 新闻面原因
        if news:
            sentiment_index = news.get('sentiment_index', 0)
            if sentiment_index > 50:
                reasons.append(f"新闻面极度利好（指数: {sentiment_index:.0f}）")
            elif sentiment_index > 20:
                reasons.append(f"新闻面偏向利好（指数: {sentiment_index:.0f}）")
            elif sentiment_index < -50:
                reasons.append(f"新闻面极度利空（指数: {sentiment_index:.0f}）")
            elif sentiment_index < -20:
                reasons.append(f"新闻面偏向利空（指数: {sentiment_index:.0f}）")

            # 添加重要新闻
            recent_news = news.get('recent_news', [])
            for item in recent_news[:2]:  # 最多2条
                reasons.append(f"📰 {item.get('title', '')[:50]}...")

        # 社交媒体原因
        if social:
            sentiment = social.get('sentiment', 'neutral')
            if sentiment == 'bullish':
                reasons.append("社交媒体讨论偏向看涨")
            elif sentiment == 'bearish':
                reasons.append("社交媒体讨论偏向看跌")

        return reasons

    def _generate_risk_warnings(
        self,
        action: str,
        confidence: float,
        news: Optional[Dict]
    ) -> List[str]:
        """生成风险提示"""
        warnings = []

        # 基于置信度的风险
        if confidence < 0.6:
            warnings.append("⚠️ 信号置信度较低，建议谨慎操作")

        # 基于操作类型的风险
        if action in ['STRONG_LONG', 'STRONG_SHORT']:
            warnings.append("⚠️ 强信号可能伴随高波动，注意风险控制")

        # 基于新闻的风险
        if news:
            if news.get('major_events_count', 0) > 0:
                warnings.append("⚠️ 检测到重大事件，市场可能剧烈波动")

        # 通用风险提示
        warnings.append("💡 建议分批建仓，严格止损")
        warnings.append("💡 本系统不构成投资建议，仅供参考")

        return warnings

    def batch_generate_signals(
        self,
        symbols_data: List[Dict]
    ) -> List[Dict]:
        """
        批量生成多个交易对的信号

        Args:
            symbols_data: 包含每个交易对数据的列表

        Returns:
            信号列表
        """
        signals = []

        for data in symbols_data:
            symbol = data.get('symbol')
            technical = data.get('technical')
            news = data.get('news')
            social = data.get('social')
            price = data.get('price')

            signal = self.generate_signal(
                symbol,
                technical,
                news,
                social,
                price
            )

            signals.append(signal)

        # 按置信度排序
        signals.sort(key=lambda x: x['confidence'], reverse=True)

        return signals

    def format_signal_text(self, signal: Dict) -> str:
        """
        格式化信号为文本（用于通知）

        Args:
            signal: 信号字典

        Returns:
            格式化的文本
        """
        action_emoji = {
            'STRONG_LONG': '🚀🚀🚀',
            'LONG': '📈',
            'HOLD': '➡️',
            'SHORT': '📉',
            'STRONG_SHORT': '💥💥💥'
        }

        text = f"""
{action_emoji.get(signal['action'], '')} {signal['action']} 信号

交易对: {signal['symbol']}
置信度: {signal['confidence']:.0%}
综合评分: {signal['score']['total']:.0f}/100

💰 价格信息:
当前价: ${signal['price']['current']:,.2f}
建议入场: ${signal['price']['entry']:,.2f}
止损位: ${signal['price']['stop_loss']:,.2f}
止盈位: ${signal['price']['take_profit']:,.2f}

📊 建议仓位: {signal['position']['recommended_size']:.0%}

📝 信号原因:
"""
        for reason in signal['reasons'][:5]:  # 最多5条
            text += f"• {reason}\n"

        text += "\n⚠️ 风险提示:\n"
        for warning in signal['risks'][:3]:  # 最多3条
            text += f"{warning}\n"

        return text


# 使用示例
def main():
    """测试信号生成器"""

    config = {
        'signals': {
            'weights': {
                'technical': 0.60,
                'news': 0.30,
                'social': 0.10
            },
            'confidence': {
                'strong_long': 0.75,
                'long': 0.60,
                'short': 0.60,
                'strong_short': 0.75
            },
            'risk': {
                'stop_loss_pct': 0.03,
                'take_profit_pct': 0.05,
                'max_position': 0.5
            }
        }
    }

    generator = SignalGenerator(config)

    # 模拟数据
    technical_data = {
        'price': 45000,
        'score': 75,
        'signals': [
            'RSI从超卖区域回升',
            'MACD金叉形成',
            '价格突破布林带中轨',
            '成交量放大'
        ],
        'rsi': {'value': 55, 'oversold': False, 'overbought': False},
        'macd': {'bullish_cross': True, 'bearish_cross': False},
        'ema': {'trend': 'up'}
    }

    news_data = {
        'sentiment_index': 65,
        'total_news': 15,
        'positive': 10,
        'negative': 2,
        'major_events_count': 1,
        'recent_news': [
            {'title': '美国SEC批准比特币现货ETF'},
            {'title': '某大型机构宣布增持BTC'}
        ]
    }

    # 生成信号
    print("=== 生成交易信号 ===\n")
    signal = generator.generate_signal(
        'BTC/USDT',
        technical_data,
        news_data,
        current_price=45000
    )

    # 打印格式化文本
    print(generator.format_signal_text(signal))

    # 打印JSON
    print("\n=== 信号JSON ===")
    import json
    print(json.dumps(signal, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
