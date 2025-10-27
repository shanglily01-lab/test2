#!/usr/bin/env python3
"""
检查ETF数据库中的数据
"""

import mysql.connector
from datetime import datetime, timedelta

# 数据库配置
db_config = {
    'host': '192.168.1.101',
    'user': 'root',
    'password': '123456',
    'database': 'binance-data'
}

try:
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor(dictionary=True)

    print('=' * 80)
    print('检查 crypto_etf_daily_summary 表')
    print('=' * 80)
    print()

    # 检查表是否存在
    cursor.execute("SHOW TABLES LIKE 'crypto_etf_daily_summary'")
    if not cursor.fetchone():
        print("❌ 表 crypto_etf_daily_summary 不存在!")
        exit(1)

    print("✅ 表 crypto_etf_daily_summary 存在")
    print()

    # 查看表结构
    cursor.execute("DESCRIBE crypto_etf_daily_summary")
    columns = cursor.fetchall()
    print("表结构:")
    for col in columns:
        print(f"  - {col['Field']}: {col['Type']}")
    print()

    # 检查总记录数
    cursor.execute("SELECT COUNT(*) as total FROM crypto_etf_daily_summary")
    total = cursor.fetchone()['total']
    print(f"总记录数: {total}")
    print()

    # 按资产类型统计
    cursor.execute("""
        SELECT asset_type,
               COUNT(*) as count,
               MIN(trade_date) as earliest_date,
               MAX(trade_date) as latest_date
        FROM crypto_etf_daily_summary
        GROUP BY asset_type
    """)

    results = cursor.fetchall()
    print("按资产类型统计:")
    for row in results:
        print(f"  {row['asset_type']}:")
        print(f"    记录数: {row['count']}")
        print(f"    最早日期: {row['earliest_date']}")
        print(f"    最新日期: {row['latest_date']}")
    print()

    # 查看最近7天的BTC数据
    print("最近的 BTC ETF 数据:")
    cursor.execute("""
        SELECT trade_date, total_net_inflow, total_aum, etf_count
        FROM crypto_etf_daily_summary
        WHERE asset_type = 'BTC'
        ORDER BY trade_date DESC
        LIMIT 10
    """)
    btc_data = cursor.fetchall()
    if btc_data:
        for row in btc_data:
            inflow = float(row['total_net_inflow']) if row['total_net_inflow'] else 0
            aum = float(row['total_aum']) if row['total_aum'] else 0
            print(f"  {row['trade_date']}: 净流入=${inflow:,.2f}, AUM=${aum:,.2f}, ETF数量={row['etf_count']}")
    else:
        print("  ❌ 没有BTC数据")
    print()

    # 查看最近7天的ETH数据
    print("最近的 ETH ETF 数据:")
    cursor.execute("""
        SELECT trade_date, total_net_inflow, total_aum, etf_count
        FROM crypto_etf_daily_summary
        WHERE asset_type = 'ETH'
        ORDER BY trade_date DESC
        LIMIT 10
    """)
    eth_data = cursor.fetchall()
    if eth_data:
        for row in eth_data:
            inflow = float(row['total_net_inflow']) if row['total_net_inflow'] else 0
            aum = float(row['total_aum']) if row['total_aum'] else 0
            print(f"  {row['trade_date']}: 净流入=${inflow:,.2f}, AUM=${aum:,.2f}, ETF数量={row['etf_count']}")
    else:
        print("  ❌ 没有ETH数据")
    print()

    # 测试日期过滤查询（模拟 get_etf_summary 的查询）
    print("=" * 80)
    print("测试日期过滤查询 (模拟 get_etf_summary)")
    print("=" * 80)
    print()

    # 计算7天前的日期
    start_date = (datetime.now() - timedelta(days=7)).date()
    print(f"查询起始日期: {start_date}")
    print(f"当前日期: {datetime.now().date()}")
    print()

    # BTC查询
    cursor.execute("""
        SELECT trade_date, total_net_inflow, total_aum, etf_count
        FROM crypto_etf_daily_summary
        WHERE asset_type = 'BTC'
        AND trade_date >= %s
        ORDER BY trade_date DESC
        LIMIT 7
    """, (start_date,))

    btc_results = cursor.fetchall()
    print(f"BTC 查询结果 (trade_date >= {start_date}):")
    if btc_results:
        print(f"  找到 {len(btc_results)} 条记录")
        for row in btc_results:
            inflow = float(row['total_net_inflow']) if row['total_net_inflow'] else 0
            print(f"  {row['trade_date']}: 净流入=${inflow:,.2f}")
    else:
        print("  ❌ 查询返回0条记录!")
        print()
        print("  原因分析:")
        print("  1. 检查最新数据日期是否在7天内")
        cursor.execute("""
            SELECT MAX(trade_date) as latest FROM crypto_etf_daily_summary WHERE asset_type = 'BTC'
        """)
        latest = cursor.fetchone()['latest']
        if latest:
            days_diff = (datetime.now().date() - latest).days
            print(f"     最新数据: {latest} (距今 {days_diff} 天)")
            if days_diff > 7:
                print(f"     ⚠️  最新数据超过7天，需要扩大查询范围!")
    print()

    # ETH查询
    cursor.execute("""
        SELECT trade_date, total_net_inflow, total_aum, etf_count
        FROM crypto_etf_daily_summary
        WHERE asset_type = 'ETH'
        AND trade_date >= %s
        ORDER BY trade_date DESC
        LIMIT 7
    """, (start_date,))

    eth_results = cursor.fetchall()
    print(f"ETH 查询结果 (trade_date >= {start_date}):")
    if eth_results:
        print(f"  找到 {len(eth_results)} 条记录")
        for row in eth_results:
            inflow = float(row['total_net_inflow']) if row['total_net_inflow'] else 0
            print(f"  {row['trade_date']}: 净流入=${inflow:,.2f}")
    else:
        print("  ❌ 查询返回0条记录!")
        print()
        print("  原因分析:")
        print("  1. 检查最新数据日期是否在7天内")
        cursor.execute("""
            SELECT MAX(trade_date) as latest FROM crypto_etf_daily_summary WHERE asset_type = 'ETH'
        """)
        latest = cursor.fetchone()['latest']
        if latest:
            days_diff = (datetime.now().date() - latest).days
            print(f"     最新数据: {latest} (距今 {days_diff} 天)")
            if days_diff > 7:
                print(f"     ⚠️  最新数据超过7天，需要扩大查询范围!")

    cursor.close()
    conn.close()

    print()
    print("=" * 80)
    print("检查完成")
    print("=" * 80)

except Exception as e:
    print(f"❌ 错误: {e}")
    import traceback
    traceback.print_exc()
