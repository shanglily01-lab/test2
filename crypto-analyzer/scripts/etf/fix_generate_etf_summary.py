#!/usr/bin/env python3
"""
从 crypto_etf_flows 和 crypto_etf_products 生成 crypto_etf_daily_summary 汇总数据
修复版：自动检测表结构
"""

import mysql.connector
from datetime import datetime
import yaml
from decimal import Decimal

# 从配置文件读取数据库配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = {
    'host': config['database']['mysql']['host'],
    'port': config['database']['mysql']['port'],
    'user': config['database']['mysql']['user'],
    'password': config['database']['mysql']['password'],
    'database': config['database']['mysql']['database']
}

try:
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor(dictionary=True)

    print('=' * 80)
    print('ETF 数据汇总脚本 (修复版)')
    print('=' * 80)
    print()

    # 步骤 0: 检查表结构
    print('步骤 0: 检查表结构')
    print('-' * 80)

    cursor.execute("DESCRIBE crypto_etf_flows")
    flows_columns = [col['Field'] for col in cursor.fetchall()]
    print(f"crypto_etf_flows 列: {', '.join(flows_columns)}")

    cursor.execute("DESCRIBE crypto_etf_products")
    products_columns = [col['Field'] for col in cursor.fetchall()]
    print(f"crypto_etf_products 列: {', '.join(products_columns)}")

    print()

    # 先查看原始数据
    print('步骤 1: 检查原始数据')
    print('-' * 80)

    # 查看 crypto_etf_products 表
    cursor.execute("SELECT COUNT(*) as count FROM crypto_etf_products")
    products_count = cursor.fetchone()['count']
    print(f"crypto_etf_products 表: {products_count} 条记录")

    if products_count > 0:
        cursor.execute("SELECT * FROM crypto_etf_products LIMIT 2")
        print("\n样本数据:")
        for row in cursor.fetchall():
            print(f"  {row}")

    print()

    # 查看 crypto_etf_flows 表
    cursor.execute("SELECT COUNT(*) as count FROM crypto_etf_flows")
    flows_count = cursor.fetchone()['count']
    print(f"crypto_etf_flows 表: {flows_count} 条记录")

    if flows_count > 0:
        cursor.execute("SELECT * FROM crypto_etf_flows ORDER BY trade_date DESC LIMIT 2")
        print("\n最近的数据:")
        for row in cursor.fetchall():
            print(f"  {row}")

        # 查看日期范围
        cursor.execute("""
            SELECT
                MIN(trade_date) as earliest_date,
                MAX(trade_date) as latest_date,
                COUNT(DISTINCT trade_date) as trading_days
            FROM crypto_etf_flows
        """)
        date_info = cursor.fetchone()
        print(f"\n日期范围:")
        print(f"  最早: {date_info['earliest_date']}")
        print(f"  最新: {date_info['latest_date']}")
        print(f"  交易日数: {date_info['trading_days']}")

    print()
    print('=' * 80)
    print('步骤 2: 生成每日汇总数据')
    print('-' * 80)
    print()

    if flows_count == 0:
        print("❌ crypto_etf_flows 表没有数据，无法生成汇总!")
        exit(1)

    # 获取所有交易日期
    cursor.execute("SELECT DISTINCT trade_date FROM crypto_etf_flows ORDER BY trade_date")
    trade_dates = [row['trade_date'] for row in cursor.fetchall()]

    print(f"找到 {len(trade_dates)} 个交易日")
    print()

    # 清空汇总表
    cursor.execute("DELETE FROM crypto_etf_daily_summary")
    conn.commit()
    print("✅ 已清空 crypto_etf_daily_summary 表")
    print()

    # 构建动态查询 - 只查询存在的字段
    select_fields = ['p.asset_type', 'f.ticker', 'f.net_inflow', 'f.gross_inflow', 'f.gross_outflow', 'f.aum']

    # 检查持仓字段（支持单独的btc_holdings/eth_holdings或统一的holdings）
    has_btc_holdings = 'btc_holdings' in flows_columns
    has_eth_holdings = 'eth_holdings' in flows_columns
    has_holdings = 'holdings' in flows_columns

    if has_btc_holdings:
        select_fields.append('f.btc_holdings')
    if has_eth_holdings:
        select_fields.append('f.eth_holdings')
    if has_holdings:
        select_fields.append('f.holdings')

    query = f"""
        SELECT {', '.join(select_fields)}
        FROM crypto_etf_flows f
        JOIN crypto_etf_products p ON f.ticker = p.ticker
        WHERE f.trade_date = %s
    """

    print(f"查询字段: {', '.join(select_fields)}")
    print()

    # 为每个交易日生成汇总
    success_count = 0
    for trade_date in trade_dates:
        cursor.execute(query, (trade_date,))
        flows = cursor.fetchall()

        if not flows:
            continue

        # 按资产类型分组汇总
        asset_summary = {}

        for flow in flows:
            asset_type = flow['asset_type']

            if asset_type not in asset_summary:
                asset_summary[asset_type] = {
                    'etfs': [],
                    'total_net_inflow': Decimal(0),
                    'total_gross_inflow': Decimal(0),
                    'total_gross_outflow': Decimal(0),
                    'total_aum': Decimal(0),
                    'total_holdings': Decimal(0),
                    'inflow_count': 0,
                    'outflow_count': 0
                }

            summary = asset_summary[asset_type]
            summary['etfs'].append(flow)

            net_inflow = Decimal(str(flow['net_inflow'])) if flow['net_inflow'] else Decimal(0)
            gross_inflow = Decimal(str(flow['gross_inflow'])) if flow['gross_inflow'] else Decimal(0)
            gross_outflow = Decimal(str(flow['gross_outflow'])) if flow['gross_outflow'] else Decimal(0)
            aum = Decimal(str(flow['aum'])) if flow['aum'] else Decimal(0)

            # 累加持仓量（支持btc_holdings/eth_holdings或统一的holdings字段）
            holdings = Decimal(0)
            if 'btc_holdings' in flow and flow['btc_holdings']:
                holdings = Decimal(str(flow['btc_holdings']))
            elif 'eth_holdings' in flow and flow['eth_holdings']:
                holdings = Decimal(str(flow['eth_holdings']))
            elif 'holdings' in flow and flow['holdings']:
                holdings = Decimal(str(flow['holdings']))

            if holdings > 0:
                summary['total_holdings'] += holdings

            summary['total_net_inflow'] += net_inflow
            summary['total_gross_inflow'] += gross_inflow
            summary['total_gross_outflow'] += gross_outflow
            summary['total_aum'] += aum

            if net_inflow > 0:
                summary['inflow_count'] += 1
            elif net_inflow < 0:
                summary['outflow_count'] += 1

        # 插入汇总数据
        for asset_type, summary in asset_summary.items():
            # 找出最大流入和流出的ETF
            top_inflow = max(summary['etfs'], key=lambda x: x['net_inflow'] if x['net_inflow'] else 0)
            top_outflow = min(summary['etfs'], key=lambda x: x['net_inflow'] if x['net_inflow'] else 0)

            cursor.execute("""
                INSERT INTO crypto_etf_daily_summary (
                    trade_date,
                    asset_type,
                    total_net_inflow,
                    total_gross_inflow,
                    total_gross_outflow,
                    total_aum,
                    total_holdings,
                    etf_count,
                    inflow_count,
                    outflow_count,
                    top_inflow_ticker,
                    top_inflow_amount,
                    top_outflow_ticker,
                    top_outflow_amount
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                trade_date,
                asset_type,
                summary['total_net_inflow'],
                summary['total_gross_inflow'],
                summary['total_gross_outflow'],
                summary['total_aum'],
                summary['total_holdings'],
                len(summary['etfs']),
                summary['inflow_count'],
                summary['outflow_count'],
                top_inflow['ticker'],
                top_inflow['net_inflow'],
                top_outflow['ticker'],
                top_outflow['net_inflow']
            ))

            success_count += 1
            print(f"✅ {trade_date} - {asset_type}: 净流入=${summary['total_net_inflow']:,.2f}, ETF数量={len(summary['etfs'])}")

    conn.commit()

    print()
    print('=' * 80)
    print('汇总完成')
    print('=' * 80)
    print(f"✅ 成功生成 {success_count} 条汇总记录")
    print()

    # 验证结果
    cursor.execute("SELECT COUNT(*) as count FROM crypto_etf_daily_summary")
    final_count = cursor.fetchone()['count']
    print(f"crypto_etf_daily_summary 表当前记录数: {final_count}")

    # 显示最近的汇总数据
    print()
    print("最近的汇总数据:")
    cursor.execute("""
        SELECT trade_date, asset_type, total_net_inflow, total_aum, etf_count
        FROM crypto_etf_daily_summary
        ORDER BY trade_date DESC, asset_type
        LIMIT 10
    """)

    for row in cursor.fetchall():
        print(f"  {row['trade_date']} - {row['asset_type']}: 净流入=${float(row['total_net_inflow']):,.2f}, AUM=${float(row['total_aum']):,.2f}, ETF数量={row['etf_count']}")

    cursor.close()
    conn.close()

    print()
    print("=" * 80)
    print("✅ 全部完成！现在可以刷新Dashboard查看ETF数据了")
    print("=" * 80)

except Exception as e:
    print(f"❌ 错误: {e}")
    import traceback
    traceback.print_exc()


