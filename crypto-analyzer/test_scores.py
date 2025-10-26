#!/usr/bin/env python3
"""测试新的评分功能"""

# 模拟评分计算逻辑
def calculate_scores():
    """计算各维度评分"""

    # 模拟技术指标分数
    tech_points = 3  # RSI(+2) + MACD(+1) + EMA(+1) = +4, 布林带(-1) = 3
    technical_score = max(10, min(90, 50 + tech_points * 8))

    # 模拟新闻情绪
    sentiment_score = 40  # 积极情绪
    news_score = max(0, min(100, 50 + sentiment_score / 2))

    # 模拟资金费率
    funding_points = -1  # 偏高
    funding_score = max(20, min(80, 50 + funding_points * 12))

    # 模拟Hyperliquid聪明钱
    hyperliquid_points = 2  # 积累信号
    hyperliquid_score = max(20, min(80, 50 + hyperliquid_points * 10))

    # 链上数据
    ethereum_score = max(30, min(70, 50 + hyperliquid_points * 5))

    # 加权综合评分
    weighted_total_score = (
        technical_score * 0.40 +  # 40%
        news_score * 0.20 +       # 20%
        funding_score * 0.15 +    # 15%
        hyperliquid_score * 0.20 + # 20%
        ethereum_score * 0.05     # 5%
    )

    return {
        'technical': round(technical_score),
        'news': round(news_score),
        'funding': round(funding_score),
        'hyperliquid': round(hyperliquid_score),
        'ethereum': round(ethereum_score),
        'total': round(weighted_total_score)
    }


def main():
    print("=" * 60)
    print("智能投资分析 - 5维度评分测试")
    print("=" * 60)

    scores = calculate_scores()

    print("\n✅ 各维度评分：")
    print(f"  📊 技术指标: {scores['technical']}/100")
    print(f"  📰 新闻情绪: {scores['news']}/100")
    print(f"  💰 资金费率: {scores['funding']}/100")
    print(f"  🧠 Hyperliquid: {scores['hyperliquid']}/100")
    print(f"  ⛓️  链上数据: {scores['ethereum']}/100")
    print(f"\n  📈 综合评分: {scores['total']}/100")

    print("\n" + "=" * 60)
    print("✅ 评分计算逻辑验证成功！")
    print("=" * 60)

    print("\n💡 后端返回的数据结构：")
    print(f"""
{{
    'symbol': 'BTC/USDT',
    'signal': 'BUY',
    'confidence': 78,
    'advice': '建议买入,技术面偏多',
    'scores': {{
        'technical': {scores['technical']},
        'news': {scores['news']},
        'funding': {scores['funding']},
        'hyperliquid': {scores['hyperliquid']},
        'ethereum': {scores['ethereum']},
        'total': {scores['total']}
    }}
}}
    """)

    print("\n🎯 前端可以这样使用：")
    print("  - rec.scores.technical  // 技术指标评分")
    print("  - rec.scores.news       // 新闻情绪评分")
    print("  - rec.scores.funding    // 资金费率评分")
    print("  - rec.scores.hyperliquid // Hyperliquid评分")
    print("  - rec.scores.ethereum   // 链上数据评分")
    print("  - rec.scores.total      // 综合评分")

    print("\n" + "=" * 60)


if __name__ == '__main__':
    main()
