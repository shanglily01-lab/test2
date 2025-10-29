#!/usr/bin/env python3
"""
测试各个模块初始化 - 找出导致Windows崩溃的模块
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

import yaml

print("=" * 70)
print("模块初始化诊断工具")
print("=" * 70)

# 加载配置
config_path = project_root / 'config.yaml'
with open(config_path, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

print(f"\n配置文件加载成功")

# 测试1: DatabaseService
print("\n" + "=" * 70)
print("测试 1/6: DatabaseService")
print("=" * 70)
try:
    from app.database.db_service import DatabaseService
    db_service = DatabaseService(config.get('database', {}))
    print("✅ DatabaseService 初始化成功")
except Exception as e:
    print(f"❌ DatabaseService 初始化失败: {e}")
    import traceback
    traceback.print_exc()

# 测试2: PriceCollector
print("\n" + "=" * 70)
print("测试 2/6: MultiExchangeCollector (价格采集器)")
print("=" * 70)
try:
    from app.collectors.price_collector import MultiExchangeCollector
    price_collector = MultiExchangeCollector(config)
    print("✅ MultiExchangeCollector 初始化成功")
except Exception as e:
    print(f"❌ MultiExchangeCollector 初始化失败: {e}")
    import traceback
    traceback.print_exc()

# 测试3: NewsAggregator
print("\n" + "=" * 70)
print("测试 3/6: NewsAggregator (新闻采集器)")
print("=" * 70)
try:
    from app.collectors.news_collector import NewsAggregator
    news_aggregator = NewsAggregator(config)
    print("✅ NewsAggregator 初始化成功")
except Exception as e:
    print(f"❌ NewsAggregator 初始化失败: {e}")
    import traceback
    traceback.print_exc()

# 测试4: TechnicalIndicators
print("\n" + "=" * 70)
print("测试 4/6: TechnicalIndicators (技术分析器)")
print("=" * 70)
try:
    from app.analyzers.technical_indicators import TechnicalIndicators
    technical_analyzer = TechnicalIndicators(config.get('indicators', {}))
    print("✅ TechnicalIndicators 初始化成功")
except Exception as e:
    print(f"❌ TechnicalIndicators 初始化失败: {e}")
    import traceback
    traceback.print_exc()

# 测试5: SentimentAnalyzer
print("\n" + "=" * 70)
print("测试 5/6: SentimentAnalyzer (情绪分析器)")
print("=" * 70)
try:
    from app.analyzers.sentiment_analyzer import SentimentAnalyzer
    sentiment_analyzer = SentimentAnalyzer(config)
    print("✅ SentimentAnalyzer 初始化成功")
except Exception as e:
    print(f"❌ SentimentAnalyzer 初始化失败: {e}")
    import traceback
    traceback.print_exc()

# 测试6: EnhancedDashboard
print("\n" + "=" * 70)
print("测试 6/6: EnhancedDashboard (增强仪表盘)")
print("=" * 70)
try:
    from app.api.enhanced_dashboard import EnhancedDashboard
    enhanced_dashboard = EnhancedDashboard(config)
    print("✅ EnhancedDashboard 初始化成功")
except Exception as e:
    print(f"❌ EnhancedDashboard 初始化失败: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 70)
print("诊断完成")
print("=" * 70)
print("\n如果所有模块都初始化成功，说明问题不在模块初始化本身。")
print("可能的原因：")
print("  1. 后台线程与FastAPI事件循环冲突")
print("  2. 模块初始化的时机问题（启动时 vs 后台线程）")
print("  3. 某些模块的异步操作与Windows不兼容")
