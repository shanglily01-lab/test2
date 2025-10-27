#!/usr/bin/env python3
"""
诊断ETF信息在缓存更新后消失的问题
"""

import yaml
from datetime import datetime
from app.services.cache_update_service import CacheUpdateService
from app.database.db_service import DatabaseService
from sqlalchemy import text

# 加载配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

cache_service = CacheUpdateService(config)
db_service = DatabaseService(config.get('database', {}))

print('=' * 80)
print('诊断ETF信息消失问题')
print('=' * 80)
print()

symbols = ['BTC/USDT', 'ETH/USDT']

for symbol in symbols:
    print(f"\n{'=' * 80}")
    print(f"币种: {symbol}")
    print('=' * 80)

    # 1. 检查ETF汇总数据
    print("\n1️⃣  检查crypto_etf_daily_summary表")
    print("-" * 80)

    asset_type = symbol.split('/')[0]
    session = db_service.get_session()
    try:
        sql = text("""
            SELECT trade_date, total_net_inflow, total_aum, etf_count
            FROM crypto_etf_daily_summary
            WHERE asset_type = :asset_type
            ORDER BY trade_date DESC
            LIMIT 7
        """)
        results = session.execute(sql, {"asset_type": asset_type}).fetchall()

        if results:
            print(f"✅ 找到 {len(results)} 条ETF汇总数据:")
            for row in results:
                result_dict = dict(row._mapping) if hasattr(row, '_mapping') else dict(row)
                print(f"  {result_dict['trade_date']}: 净流入=${float(result_dict['total_net_inflow'] or 0):,.0f}, "
                      f"AUM=${float(result_dict['total_aum'] or 0):,.0f}, ETF数量={result_dict['etf_count']}")
        else:
            print(f"❌ 没有找到{asset_type}的ETF汇总数据")

    finally:
        session.close()

    # 2. 测试_get_cached_etf_data方法
    print(f"\n2️⃣  测试_get_cached_etf_data('{symbol}')")
    print("-" * 80)

    etf_data = cache_service._get_cached_etf_data(symbol)
    if etf_data:
        print(f"✅ ETF数据获取成功:")
        print(f"  评分: {etf_data.get('score', 0):.1f}")
        print(f"  信号: {etf_data.get('signal')}")
        print(f"  置信度: {etf_data.get('confidence', 0):.1%}")
        details = etf_data.get('details', {})
        print(f"  最新净流入: ${details.get('total_net_inflow', 0):,.0f}")
        print(f"  3日均流入: ${details.get('avg_3day_inflow', 0):,.0f}")
        print(f"  7日总流入: ${details.get('weekly_total_inflow', 0):,.0f}")
    else:
        print(f"❌ ETF数据获取失败 - 返回None")
        print(f"  可能原因:")
        print(f"    1. crypto_etf_daily_summary表中没有{asset_type}的数据")
        print(f"    2. 查询出错（检查日志）")

    # 3. 检查其他维度数据
    print(f"\n3️⃣  检查其他维度数据")
    print("-" * 80)

    technical = cache_service._get_cached_technical_data(symbol)
    news = cache_service._get_cached_news_data(symbol)
    funding = cache_service._get_cached_funding_data(symbol)
    hyperliquid = cache_service._get_cached_hyperliquid_data(symbol)
    price_stats = cache_service._get_cached_price_stats(symbol)

    print(f"  技术指标: {'✅ 有数据' if technical else '❌ 无数据'}")
    print(f"  新闻情绪: {'✅ 有数据' if news else '❌ 无数据'}")
    print(f"  资金费率: {'✅ 有数据' if funding else '❌ 无数据'}")
    print(f"  Hyperliquid: {'✅ 有数据' if hyperliquid else '❌ 无数据'}")
    print(f"  价格统计: {'✅ 有数据' if price_stats else '❌ 无数据'}")
    print(f"  ETF数据: {'✅ 有数据' if etf_data else '❌ 无数据'}")

    # 4. 模拟投资分析器调用
    print(f"\n4️⃣  模拟投资分析器")
    print("-" * 80)

    if price_stats and price_stats.get('current_price', 0) > 0:
        from app.analyzers.enhanced_investment_analyzer import EnhancedInvestmentAnalyzer
        analyzer = EnhancedInvestmentAnalyzer(config)

        analysis = analyzer.analyze(
            symbol=symbol,
            technical_data=technical,
            news_data=news,
            funding_data=funding,
            hyperliquid_data=hyperliquid,
            ethereum_data=None,
            etf_data=etf_data,  # 传入ETF数据
            current_price=price_stats.get('current_price')
        )

        print(f"  信号: {analysis.get('signal')}")
        print(f"  置信度: {analysis.get('confidence', 0):.1f}%")
        print(f"  综合评分: {analysis.get('score', 0):.1f}")

        reasons = analysis.get('reasons', [])
        print(f"  建议理由 ({len(reasons)}条):")
        for reason in reasons[:15]:
            print(f"    {reason}")

        # 检查是否包含ETF信息
        reasons_text = '\n'.join(reasons)
        if 'ETF' in reasons_text or '🏦' in reasons_text:
            print(f"\n  ✅ 建议理由中包含ETF信息")
        else:
            print(f"\n  ❌ 建议理由中不包含ETF信息")
            if etf_data:
                print(f"     问题: ETF数据存在但未出现在理由中")
                print(f"     ETF评分: {etf_data.get('score', 50)}")
                print(f"     是否等于50: {etf_data.get('score', 50) == 50}")
    else:
        print(f"  ❌ 无法进行分析 - 没有价格数据")

    # 5. 检查投资建议缓存表
    print(f"\n5️⃣  检查investment_recommendations_cache表")
    print("-" * 80)

    session = db_service.get_session()
    try:
        sql = text("SELECT * FROM investment_recommendations_cache WHERE symbol = :symbol")
        result = session.execute(sql, {"symbol": symbol}).fetchone()

        if result:
            result_dict = dict(result._mapping) if hasattr(result, '_mapping') else dict(result)
            print(f"  信号: {result_dict.get('signal')}")
            print(f"  置信度: {result_dict.get('confidence', 0):.1f}%")
            print(f"  更新时间: {result_dict.get('updated_at')}")

            reasons = result_dict.get('reasons', '')
            if 'ETF' in reasons or '🏦' in reasons:
                print(f"  ✅ 缓存中包含ETF信息")
            else:
                print(f"  ❌ 缓存中不包含ETF信息")
        else:
            print(f"  ❌ 缓存中没有{symbol}的投资建议")
    finally:
        session.close()

print()
print('=' * 80)
print('诊断完成')
print('=' * 80)
