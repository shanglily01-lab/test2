#!/usr/bin/env python3
"""
ETF 数据和缓存状态检查脚本

使用方法:
python scripts/check_etf_status.py
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import yaml
from datetime import datetime
from loguru import logger
from sqlalchemy import create_engine, text
from urllib.parse import quote_plus


def main():
    """主函数"""

    # 加载配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    db_config = config['database']['mysql']

    try:
        # 构建数据库连接（密码需要 URL 编码以处理特殊字符）
        password_encoded = quote_plus(db_config['password'])
        db_url = (
            f"mysql+pymysql://{db_config['user']}:{password_encoded}"
            f"@{db_config['host']}:{db_config['port']}/{db_config['database']}"
        )
        engine = create_engine(db_url)

        print("\n" + "="*80)
        print("ETF 数据和缓存状态检查")
        print("="*80 + "\n")

        with engine.connect() as conn:
            # 1. 检查 ETF 原始数据
            print("1️⃣  ETF 原始数据 (crypto_etf_flows)")
            print("-" * 80)

            result = conn.execute(text('''
                SELECT
                    COUNT(*) as total_records,
                    COUNT(DISTINCT ticker) as unique_etfs,
                    MIN(trade_date) as earliest_date,
                    MAX(trade_date) as latest_date
                FROM crypto_etf_flows
            ''')).fetchone()

            print(f"总记录数: {result[0]}")
            print(f"ETF 数量: {result[1]}")
            print(f"日期范围: {result[2]} ~ {result[3]}")

            # 最新的 ETF 数据
            print(f"\n最新的 ETF 数据（前5条）:")
            results = conn.execute(text('''
                SELECT ticker, trade_date, net_inflow, aum, data_source
                FROM crypto_etf_flows
                ORDER BY trade_date DESC, ticker
                LIMIT 5
            ''')).fetchall()

            for row in results:
                print(f"  {row[0]}: {row[1]} | 净流入: ${row[2]:,.0f} | AUM: ${row[3]:,.0f} | 来源: {row[4]}")

            # 2. 检查投资建议缓存
            print(f"\n2️⃣  投资建议缓存 (investment_recommendations_cache)")
            print("-" * 80)

            result = conn.execute(text('''
                SELECT COUNT(*) as count
                FROM investment_recommendations_cache
            ''')).fetchone()

            print(f"缓存记录数: {result[0]}")

            if result[0] > 0:
                print(f"\n最新的投资建议:")
                results = conn.execute(text('''
                    SELECT
                        symbol,
                        recommendation,
                        score,
                        etf_factor,
                        technical_score,
                        news_score,
                        funding_score,
                        updated_at
                    FROM investment_recommendations_cache
                    ORDER BY updated_at DESC
                    LIMIT 5
                ''')).fetchall()

                for row in results:
                    print(f"\n  {row[0]}:")
                    print(f"    建议: {row[1]} | 综合评分: {row[2]:.1f}")
                    print(f"    ETF 因素: {row[3]:.1f} | 技术: {row[4]:.1f} | 新闻: {row[5]:.1f} | 资金费率: {row[6]:.1f}")
                    print(f"    更新时间: {row[7]}")

                    # 检查更新时间是否过期
                    if row[7]:
                        age = (datetime.now() - row[7]).total_seconds() / 60
                        if age > 10:
                            print(f"    ⚠️  数据已过期 {age:.0f} 分钟（应每5分钟更新）")
                        else:
                            print(f"    ✅ 数据新鲜（{age:.0f} 分钟前更新）")
            else:
                print("⚠️  投资建议缓存为空！")

            # 3. 检查技术指标缓存
            print(f"\n3️⃣  技术指标缓存 (technical_indicators_cache)")
            print("-" * 80)

            result = conn.execute(text('''
                SELECT COUNT(*) as count, MAX(updated_at) as latest_update
                FROM technical_indicators_cache
            ''')).fetchone()

            print(f"缓存记录数: {result[0]}")
            print(f"最后更新: {result[1]}")

            if result[1]:
                age = (datetime.now() - result[1]).total_seconds() / 60
                if age > 10:
                    print(f"⚠️  数据已过期 {age:.0f} 分钟")
                else:
                    print(f"✅ 数据新鲜（{age:.0f} 分钟前更新）")

            # 4. 检查价格统计缓存
            print(f"\n4️⃣  价格统计缓存 (price_stats_24h)")
            print("-" * 80)

            result = conn.execute(text('''
                SELECT COUNT(*) as count, MAX(updated_at) as latest_update
                FROM price_stats_24h
            ''')).fetchone()

            print(f"缓存记录数: {result[0]}")
            print(f"最后更新: {result[1]}")

            if result[1]:
                age = (datetime.now() - result[1]).total_seconds() / 60
                if age > 5:
                    print(f"⚠️  数据已过期 {age:.0f} 分钟")
                else:
                    print(f"✅ 数据新鲜（{age:.0f} 分钟前更新）")

            # 5. 诊断结果
            print(f"\n5️⃣  诊断结果")
            print("-" * 80)

            # 检查是否有 ETF 数据
            etf_count = conn.execute(text('SELECT COUNT(*) FROM crypto_etf_flows')).fetchone()[0]
            cache_count = conn.execute(text('SELECT COUNT(*) FROM investment_recommendations_cache')).fetchone()[0]

            if etf_count == 0:
                print("❌ 问题: ETF 原始数据为空")
                print("   解决: 运行 scripts/etf/interactive_input.py 录入数据")
            elif cache_count == 0:
                print("❌ 问题: 投资建议缓存为空")
                print("   解决: 运行 python scripts/manual_update_cache.py --recommendations")
            else:
                latest_cache = conn.execute(text(
                    'SELECT MAX(updated_at) FROM investment_recommendations_cache'
                )).fetchone()[0]

                if latest_cache:
                    age = (datetime.now() - latest_cache).total_seconds() / 60
                    if age > 10:
                        print(f"⚠️  问题: 缓存已过期 {age:.0f} 分钟（应每5分钟更新）")
                        print("   可能原因: scheduler.py 没有运行")
                        print("   解决方案:")
                        print("     1. 检查 scheduler.py 是否在运行: ps aux | grep scheduler")
                        print("     2. 手动更新缓存: python scripts/manual_update_cache.py --recommendations")
                        print("     3. 启动 scheduler: python app/scheduler.py")
                    else:
                        print(f"✅ 一切正常！缓存 {age:.0f} 分钟前更新")
                        print("   如果 Dashboard 仍未显示 ETF 分析，请刷新页面")

        print("\n" + "="*80 + "\n")

    except Exception as e:
        print(f"\n❌ 数据库连接失败: {e}")
        print("\n可能的原因:")
        print("  1. 数据库服务未启动")
        print("  2. config.yaml 中的数据库配置不正确")
        print("  3. 网络连接问题（如果数据库在远程）")
        print("\n如果数据库在 Windows 本地，请在 Windows 环境运行此脚本")
        sys.exit(1)


if __name__ == '__main__':
    main()