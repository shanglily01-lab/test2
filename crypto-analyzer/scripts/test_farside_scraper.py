#!/usr/bin/env python3
"""
测试 Farside ETF 爬虫
用于验证 Farside.co.uk 网站的 ETF 数据抓取功能
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import yaml
from datetime import date
from app.collectors.crypto_etf_collector import CryptoETFCollector
from app.database.db_service import DatabaseService


def test_farside_scraper():
    """测试 Farside 爬虫功能"""
    print("\n" + "=" * 80)
    print("Farside ETF 爬虫测试")
    print("=" * 80 + "\n")

    # 1. 测试不连接数据库，仅爬取数据
    print("阶段 1: 测试数据爬取（不连接数据库）")
    print("-" * 80)

    collector = CryptoETFCollector(db_service=None)

    # 测试 BTC ETF
    print("\n>>> 测试 BTC ETF 数据爬取...")
    btc_data = collector.fetch_farside_data('BTC')

    if btc_data:
        print(f"\n✅ 成功爬取 {len(btc_data)} 个 BTC ETF 数据:")
        print(f"\n{'Ticker':<12} {'日期':<12} {'资金流入':<15}")
        print("-" * 40)
        for etf in btc_data[:10]:  # 显示前10个
            flow = etf['net_inflow']
            flow_str = f"${flow:,.0f}" if flow >= 0 else f"-${abs(flow):,.0f}"
            print(f"{etf['ticker']:<12} {etf['trade_date']} {flow_str:>15}")

        if len(btc_data) > 10:
            print(f"... 还有 {len(btc_data) - 10} 个 ETF")
    else:
        print("❌ BTC ETF 数据爬取失败")

    # 测试 ETH ETF
    print("\n>>> 测试 ETH ETF 数据爬取...")
    eth_data = collector.fetch_farside_data('ETH')

    if eth_data:
        print(f"\n✅ 成功爬取 {len(eth_data)} 个 ETH ETF 数据:")
        print(f"\n{'Ticker':<12} {'日期':<12} {'资金流入':<15}")
        print("-" * 40)
        for etf in eth_data[:10]:  # 显示前10个
            flow = etf['net_inflow']
            flow_str = f"${flow:,.0f}" if flow >= 0 else f"-${abs(flow):,.0f}"
            print(f"{etf['ticker']:<12} {etf['trade_date']} {flow_str:>15}")

        if len(eth_data) > 10:
            print(f"... 还有 {len(eth_data) - 10} 个 ETF")
    else:
        print("❌ ETH ETF 数据爬取失败")

    # 2. 测试完整流程（包括数据库保存）
    print("\n\n阶段 2: 测试完整数据采集流程（包括数据库保存）")
    print("-" * 80)

    try:
        # 加载配置
        config_path = project_root / 'config.yaml'
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        # 初始化数据库
        db_service = DatabaseService(config['database'])
        print("\n✅ 数据库连接成功")

        # 创建带数据库连接的采集器
        collector_with_db = CryptoETFCollector(db_service)

        # 执行完整采集流程
        print("\n>>> 开始完整数据采集...")
        results = collector_with_db.collect_daily_data(
            target_date=date.today(),
            asset_types=['BTC', 'ETH']
        )

        print("\n采集结果汇总:")
        print("-" * 40)
        for asset_type, stats in results.items():
            print(f"{asset_type}: 保存 {stats['saved']} 条, 失败 {stats['failed']} 条")

        # 关闭数据库连接
        db_service.close()
        print("\n✅ 数据库连接已关闭")

    except FileNotFoundError:
        print("\n⚠️  未找到 config.yaml，跳过数据库保存测试")
    except Exception as e:
        print(f"\n❌ 数据库保存测试失败: {e}")

    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80 + "\n")


if __name__ == '__main__':
    test_farside_scraper()
