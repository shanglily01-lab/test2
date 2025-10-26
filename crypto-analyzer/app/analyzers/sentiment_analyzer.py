"""
新闻情绪分析器
基于关键词和NLP技术分析新闻对币价的影响
"""

import re
from typing import Dict, List
from datetime import datetime


class SentimentAnalyzer:
    """情绪分析器"""

    # 利好关键词（权重）
    POSITIVE_KEYWORDS = {
        # 英文
        'partnership': 3, 'adoption': 4, 'upgrade': 3, 'etf approved': 10,
        'institutional': 5, 'bullish': 3, 'rally': 3, 'surge': 3,
        'milestone': 4, 'breakthrough': 5, 'integration': 3, 'launch': 3,
        'collaboration': 3, 'investment': 4, 'fund': 4, 'acquisition': 5,
        'approval': 5, 'success': 3, 'growth': 3, 'expansion': 3,
        'positive': 2, 'optimistic': 3, 'moon': 2, 'ath': 5,  # all-time high
        'halving': 6, 'burn': 4, 'buyback': 4, 'staking': 2,

        # 中文
        '合作': 3, '采用': 4, '升级': 3, '批准': 5, '通过': 4,
        '利好': 4, '看涨': 3, '暴涨': 3, '突破': 5, '里程碑': 4,
        '整合': 3, '发布': 3, '投资': 4, '收购': 5, '成功': 3,
        '增长': 3, '扩张': 3, '积极': 2, '乐观': 3, '减半': 6,
        '销毁': 4, '回购': 4, '质押': 2
    }

    # 利空关键词（权重为负）
    NEGATIVE_KEYWORDS = {
        # 英文
        'hack': -8, 'hacked': -8, 'exploit': -7, 'scam': -9, 'fraud': -9,
        'ban': -7, 'banned': -7, 'regulation': -4, 'crackdown': -6,
        'crash': -7, 'plunge': -6, 'dump': -6, 'bearish': -3,
        'lawsuit': -6, 'sue': -6, 'investigation': -5, 'probe': -5,
        'collapse': -9, 'bankrupt': -10, 'insolvent': -9, 'rug pull': -10,
        'vulnerability': -5, 'bug': -4, 'breach': -7, 'theft': -8,
        'panic': -5, 'fear': -4, 'warning': -3, 'risk': -3,
        'delay': -3, 'postpone': -3, 'reject': -6, 'denied': -6,

        # 中文
        '黑客': -8, '被黑': -8, '漏洞': -5, '骗局': -9, '欺诈': -9,
        '禁止': -7, '禁令': -7, '监管': -4, '打压': -6, '暴跌': -7,
        '崩盘': -9, '跳水': -6, '砸盘': -6, '利空': -4, '看跌': -3,
        '诉讼': -6, '起诉': -6, '调查': -5, '破产': -10, '倒闭': -9,
        '跑路': -10, '攻击': -7, '盗窃': -8, '恐慌': -5, '风险': -3,
        '延迟': -3, '推迟': -3, '拒绝': -6, '否决': -6
    }

    # 重大事件关键词（影响系数放大）
    MAJOR_EVENT_KEYWORDS = [
        'etf', 'sec', 'fed', 'federal reserve', 'halving', 'merge',
        'regulation', 'institutional', 'government', 'central bank',
        'etf', '美联储', '监管', '减半', '合并', '机构', '央行'
    ]

    def __init__(self):
        pass

    def analyze_text(self, text: str) -> Dict:
        """
        分析文本情绪

        Args:
            text: 新闻标题或内容

        Returns:
            {
                'score': 情绪分数 (-100 到 +100),
                'sentiment': 情绪类别 (positive/negative/neutral),
                'keywords_found': 检测到的关键词,
                'is_major_event': 是否重大事件
            }
        """
        text_lower = text.lower()
        score = 0
        keywords_found = []

        # 检测利好关键词
        for keyword, weight in self.POSITIVE_KEYWORDS.items():
            if keyword in text_lower:
                score += weight
                keywords_found.append((keyword, weight))

        # 检测利空关键词
        for keyword, weight in self.NEGATIVE_KEYWORDS.items():
            if keyword in text_lower:
                score += weight  # weight是负数
                keywords_found.append((keyword, weight))

        # 检测重大事件
        is_major_event = any(keyword in text_lower for keyword in self.MAJOR_EVENT_KEYWORDS)
        if is_major_event:
            score *= 1.5  # 重大事件影响放大1.5倍

        # 归一化到 -100 到 +100
        score = max(min(score, 100), -100)

        # 判断情绪类别
        if score > 3:
            sentiment = 'positive'
        elif score < -3:
            sentiment = 'negative'
        else:
            sentiment = 'neutral'

        return {
            'score': round(score, 2),
            'sentiment': sentiment,
            'keywords_found': keywords_found,
            'is_major_event': is_major_event
        }

    def analyze_news_batch(self, news_list: List[Dict]) -> List[Dict]:
        """
        批量分析新闻列表

        Args:
            news_list: 新闻列表，每条新闻需包含 'title' 和 'description'

        Returns:
            添加了情绪分析结果的新闻列表
        """
        for news in news_list:
            title = news.get('title', '')
            description = news.get('description', '')
            full_text = f"{title} {description}"

            analysis = self.analyze_text(full_text)

            # 更新新闻的情绪信息
            news['sentiment'] = analysis['sentiment']
            news['sentiment_score'] = analysis['score']
            news['keywords'] = [kw[0] for kw in analysis['keywords_found']]
            news['is_major_event'] = analysis['is_major_event']

        return news_list

    def calculate_aggregate_sentiment(self, news_list: List[Dict]) -> Dict:
        """
        计算聚合情绪指数

        Args:
            news_list: 新闻列表（需要已经过情绪分析）

        Returns:
            聚合情绪统计
        """
        if not news_list:
            return {
                'total_news': 0,
                'sentiment_index': 0,
                'positive_count': 0,
                'negative_count': 0,
                'neutral_count': 0,
                'major_events_count': 0,
                'average_score': 0
            }

        total = len(news_list)
        positive_count = sum(1 for n in news_list if n.get('sentiment') == 'positive')
        negative_count = sum(1 for n in news_list if n.get('sentiment') == 'negative')
        neutral_count = sum(1 for n in news_list if n.get('sentiment') == 'neutral')
        major_events = sum(1 for n in news_list if n.get('is_major_event', False))

        # 计算平均分数
        scores = [n.get('sentiment_score', 0) for n in news_list]
        average_score = sum(scores) / total if total > 0 else 0

        # 计算情绪指数 (-100 到 +100)
        # 考虑数量占比和平均分数
        sentiment_index = ((positive_count - negative_count) / total) * 100 * 0.5 + average_score * 0.5

        return {
            'total_news': total,
            'sentiment_index': round(sentiment_index, 2),
            'positive_count': positive_count,
            'negative_count': negative_count,
            'neutral_count': neutral_count,
            'major_events_count': major_events,
            'average_score': round(average_score, 2),
            'distribution': {
                'positive_pct': round(positive_count / total * 100, 1),
                'negative_pct': round(negative_count / total * 100, 1),
                'neutral_pct': round(neutral_count / total * 100, 1)
            }
        }

    def get_impact_level(self, sentiment_index: float) -> Dict:
        """
        根据情绪指数判断影响程度

        Args:
            sentiment_index: 情绪指数 (-100 到 +100)

        Returns:
            影响等级和建议
        """
        if sentiment_index >= 50:
            return {
                'level': 'VERY_POSITIVE',
                'description': '极度利好',
                'emoji': '🚀🚀🚀',
                'suggestion': '强烈看涨信号，考虑做多'
            }
        elif sentiment_index >= 20:
            return {
                'level': 'POSITIVE',
                'description': '利好',
                'emoji': '📈',
                'suggestion': '偏多信号，可考虑建仓'
            }
        elif sentiment_index >= -20:
            return {
                'level': 'NEUTRAL',
                'description': '中性',
                'emoji': '➡️',
                'suggestion': '观望为主，等待明确信号'
            }
        elif sentiment_index >= -50:
            return {
                'level': 'NEGATIVE',
                'description': '利空',
                'emoji': '📉',
                'suggestion': '偏空信号，谨慎操作'
            }
        else:
            return {
                'level': 'VERY_NEGATIVE',
                'description': '极度利空',
                'emoji': '💥💥💥',
                'suggestion': '强烈看跌信号，考虑做空或离场'
            }

    def detect_sudden_change(self, current_index: float, previous_index: float) -> Dict:
        """
        检测情绪突变

        Args:
            current_index: 当前情绪指数
            previous_index: 之前的情绪指数

        Returns:
            突变信息
        """
        change = current_index - previous_index
        change_pct = abs(change)

        if change_pct >= 30:
            severity = 'CRITICAL'
            alert = True
        elif change_pct >= 15:
            severity = 'HIGH'
            alert = True
        elif change_pct >= 5:
            severity = 'MEDIUM'
            alert = False
        else:
            severity = 'LOW'
            alert = False

        direction = 'POSITIVE' if change > 0 else 'NEGATIVE' if change < 0 else 'STABLE'

        return {
            'change': round(change, 2),
            'change_pct': round(change_pct, 2),
            'direction': direction,
            'severity': severity,
            'alert': alert,
            'message': self._get_change_message(change, change_pct)
        }

    def _get_change_message(self, change: float, change_pct: float) -> str:
        """生成情绪变化消息"""
        if change_pct < 5:
            return "市场情绪平稳"
        elif change > 0:
            if change_pct >= 30:
                return f"⚠️ 市场情绪急剧转好！指数上涨{change_pct:.1f}点"
            elif change_pct >= 15:
                return f"📈 市场情绪明显改善，指数上涨{change_pct:.1f}点"
            else:
                return f"市场情绪略有好转，指数上涨{change_pct:.1f}点"
        else:
            if change_pct >= 30:
                return f"⚠️ 市场情绪急剧恶化！指数下跌{change_pct:.1f}点"
            elif change_pct >= 15:
                return f"📉 市场情绪明显转差，指数下跌{change_pct:.1f}点"
            else:
                return f"市场情绪略有转差，指数下跌{change_pct:.1f}点"


# 使用示例
def main():
    """测试情绪分析器"""

    analyzer = SentimentAnalyzer()

    # 测试单条新闻
    test_news = [
        "SEC Approves Bitcoin Spot ETF - Major Milestone for Crypto",
        "Major Exchange Hacked - $500M Stolen in Security Breach",
        "Ethereum Successfully Completes Network Upgrade",
        "美国SEC批准比特币现货ETF，加密货币迎来重大利好",
        "某交易所遭黑客攻击，损失5亿美元",
        "以太坊成功完成网络升级"
    ]

    print("=== 单条新闻情绪分析 ===\n")
    for news_text in test_news:
        result = analyzer.analyze_text(news_text)
        print(f"新闻: {news_text}")
        print(f"情绪: {result['sentiment']} (分数: {result['score']})")
        print(f"关键词: {[kw[0] for kw in result['keywords_found']]}")
        print(f"重大事件: {'是' if result['is_major_event'] else '否'}")
        print()

    # 测试批量分析
    print("\n=== 批量新闻分析 ===\n")
    news_batch = [
        {'title': 'Bitcoin ETF Approved', 'description': 'SEC gives green light'},
        {'title': 'Exchange Hacked', 'description': 'Security breach reported'},
        {'title': 'New Partnership Announced', 'description': 'Major adoption milestone'},
    ]

    analyzed = analyzer.analyze_news_batch(news_batch)
    aggregate = analyzer.calculate_aggregate_sentiment(analyzed)

    print(f"总新闻数: {aggregate['total_news']}")
    print(f"情绪指数: {aggregate['sentiment_index']}/100")
    print(f"利好: {aggregate['positive_count']} ({aggregate['distribution']['positive_pct']}%)")
    print(f"利空: {aggregate['negative_count']} ({aggregate['distribution']['negative_pct']}%)")
    print(f"中性: {aggregate['neutral_count']} ({aggregate['distribution']['neutral_pct']}%)")
    print(f"重大事件: {aggregate['major_events_count']}")

    # 测试影响等级
    print("\n=== 影响等级判断 ===\n")
    impact = analyzer.get_impact_level(aggregate['sentiment_index'])
    print(f"{impact['emoji']} {impact['description']}")
    print(f"建议: {impact['suggestion']}")

    # 测试情绪突变检测
    print("\n=== 情绪突变检测 ===\n")
    change_info = analyzer.detect_sudden_change(current_index=45, previous_index=10)
    print(f"变化: {change_info['change']} 点")
    print(f"方向: {change_info['direction']}")
    print(f"严重程度: {change_info['severity']}")
    print(f"是否预警: {'是' if change_info['alert'] else '否'}")
    print(f"消息: {change_info['message']}")


if __name__ == '__main__':
    main()
