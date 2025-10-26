#!/usr/bin/env python3
"""
检查所有6个缓存表的数据状态
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database.db_service import DatabaseService
from sqlalchemy import text
import yaml

def check_all_caches():
    # 加载配置
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')

    if not os.path.exists(config_path):
        print(f"❌ 配置文件不存在: {config_path}")
        return

    print(f"✅ 读取配置文件: {config_path}\n")
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    db_config = config.get('database', {})
    db_service = DatabaseService(db_config)
    session = None

    # 定义6个缓存表
    cache_tables = [
        ('price_stats_24h', '价格统计'),
        ('technical_indicators_cache', '技术指标'),
        ('funding_rate_stats', '资金费率'),
        ('news_sentiment_aggregation', '新闻情绪'),
        ('hyperliquid_symbol_aggregation', 'Hyperliquid'),
        ('investment_recommendations_cache', '投资建议'),
    ]

    try:
        session = db_service.get_session()

        print("=" * 80)
        print("检查所有6个缓存表的状态")
        print("=" * 80)
        print()

        all_empty = True

        for table_name, table_desc in cache_tables:
            # 检查表是否存在
            result = session.execute(text(f"""
                SELECT COUNT(*) as count
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                AND table_name = '{table_name}'
            """))
            row = result.fetchone()
            exists = row[0] > 0

            if not exists:
                print(f"❌ {table_desc} ({table_name}): 表不存在")
                continue

            # 检查记录数
            result = session.execute(text(f"SELECT COUNT(*) as count FROM {table_name}"))
            row = result.fetchone()
            count = row[0]

            if count > 0:
                all_empty = False
                print(f"✅ {table_desc} ({table_name}): {count} 条记录")

                # 显示最新一条记录的时间
                try:
                    result = session.execute(text(f"""
                        SELECT updated_at
                        FROM {table_name}
                        ORDER BY updated_at DESC
                        LIMIT 1
                    """))
                    row = result.fetchone()
                    if row and row[0]:
                        print(f"   最后更新: {row[0]}")
                except:
                    pass
            else:
                print(f"⚠️  {table_desc} ({table_name}): 0 条记录（空表）")

            print()

        print("=" * 80)

        if all_empty:
            print("❌ 所有缓存表都是空的！")
            print()
            print("可能的原因：")
            print("1. 从未运行过缓存更新脚本")
            print("2. 缓存更新脚本运行时出错（但没有显示错误）")
            print("3. 数据采集失败（网络问题、API问题等）")
            print()
            print("解决方案：")
            print("1. 检查是否能访问交易所API（Binance）")
            print("2. 检查 main.py 的日志输出")
            print("3. 尝试手动采集一次价格数据测试")
        else:
            print("✅ 至少有一些缓存表有数据")
            print()
            print("如果 investment_recommendations_cache 是空的：")
            print("- 确保 price_stats_24h 有数据（这是生成建议的前提）")
            print("- 重新运行: python scripts/管理/update_cache_manual.py")

        print("=" * 80)

        # 额外检查：是否有原始的价格数据
        print()
        print("=" * 80)
        print("检查原始数据表（非缓存表）")
        print("=" * 80)
        print()

        # 检查 kline_data 表
        try:
            result = session.execute(text("""
                SELECT COUNT(*) as count
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                AND table_name = 'kline_data'
            """))
            row = result.fetchone()
            if row[0] > 0:
                result = session.execute(text("SELECT COUNT(*) FROM kline_data"))
                row = result.fetchone()
                print(f"K线数据 (kline_data): {row[0]} 条记录")

                if row[0] > 0:
                    result = session.execute(text("""
                        SELECT DISTINCT symbol FROM kline_data LIMIT 10
                    """))
                    symbols = [r[0] for r in result.fetchall()]
                    print(f"  包含币种: {', '.join(symbols)}")
            else:
                print("⚠️  K线数据表 (kline_data) 不存在")
        except Exception as e:
            print(f"检查K线数据失败: {e}")

        print()

        # 检查 funding_rate_data 表
        try:
            result = session.execute(text("""
                SELECT COUNT(*) as count
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                AND table_name = 'funding_rate_data'
            """))
            row = result.fetchone()
            if row[0] > 0:
                result = session.execute(text("SELECT COUNT(*) FROM funding_rate_data"))
                row = result.fetchone()
                print(f"资金费率 (funding_rate_data): {row[0]} 条记录")

                if row[0] > 0:
                    result = session.execute(text("""
                        SELECT DISTINCT symbol FROM funding_rate_data LIMIT 10
                    """))
                    symbols = [r[0] for r in result.fetchall()]
                    print(f"  包含币种: {', '.join(symbols)}")
            else:
                print("⚠️  资金费率表 (funding_rate_data) 不存在")
        except Exception as e:
            print(f"检查资金费率失败: {e}")

        print()
        print("=" * 80)

    except Exception as e:
        print(f"❌ 检查失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if session:
            session.close()

if __name__ == "__main__":
    check_all_caches()