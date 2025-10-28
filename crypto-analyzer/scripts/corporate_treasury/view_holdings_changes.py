#!/usr/bin/env python3
"""
查看企业金库持仓变化
自动计算增持/减持
"""

import sys
import os
from datetime import datetime
import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

import mysql.connector

# 加载配置
config_path = os.path.join(os.path.dirname(__file__), '../../config.yaml')
with open(config_path, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config['database']['mysql']

def get_db_connection():
    """获取数据库连接"""
    return mysql.connector.connect(
        host=db_config['host'],
        port=db_config['port'],
        user=db_config['user'],
        password=db_config['password'],
        database=db_config['database']
    )

def view_holdings_changes(company_name=None, asset_type=None, days=90):
    """
    查看持仓变化历史

    Args:
        company_name: 公司名称（None表示所有公司）
        asset_type: 资产类型（BTC/ETH，None表示所有）
        days: 查看最近多少天的数据
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # 构建查询条件
    where_clauses = ["p.purchase_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)"]
    params = [days]

    if company_name:
        where_clauses.append("c.company_name = %s")
        params.append(company_name)

    if asset_type:
        where_clauses.append("p.asset_type = %s")
        params.append(asset_type)

    where_sql = " AND ".join(where_clauses)

    # 查询购买记录（按日期排序）
    query = f"""
        SELECT
            c.company_name,
            c.ticker_symbol,
            p.purchase_date,
            p.asset_type,
            p.quantity,
            p.average_price,
            p.total_amount,
            p.cumulative_holdings,
            p.announcement_url
        FROM corporate_treasury_purchases p
        JOIN corporate_treasury_companies c ON p.company_id = c.id
        WHERE {where_sql}
        ORDER BY c.company_name, p.asset_type, p.purchase_date DESC
    """

    cursor.execute(query, params)
    records = cursor.fetchall()

    cursor.close()
    conn.close()

    if not records:
        print("❌ 没有找到记录")
        return

    # 按公司和资产分组
    grouped = {}
    for record in records:
        key = (record['company_name'], record['asset_type'])
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(record)

    # 显示结果
    print("\n" + "="*100)
    print("📊 企业金库持仓变化追踪")
    print("="*100)

    for (company, asset), purchases in grouped.items():
        ticker = f" ({purchases[0]['ticker_symbol']})" if purchases[0]['ticker_symbol'] else ""
        print(f"\n{'='*100}")
        print(f"🏢 {company}{ticker} - {asset}")
        print(f"{'='*100}")

        # 最新持仓
        if purchases[0]['cumulative_holdings']:
            print(f"📊 最新持仓: {purchases[0]['cumulative_holdings']:,.2f} {asset}")
            print()

        # 逐条显示购买记录（从旧到新）
        print(f"{'日期':<12} {'变化':<15} {'价格':<15} {'金额':<20} {'累计持仓':<20} {'状态'}")
        print("-" * 100)

        previous_holdings = None
        for i, purchase in enumerate(reversed(purchases)):
            date_str = purchase['purchase_date'].strftime('%Y-%m-%d')
            quantity = purchase['quantity']
            price = f"${purchase['average_price']:,.0f}" if purchase['average_price'] else "-"
            amount = f"${purchase['total_amount']:,.0f}" if purchase['total_amount'] else "-"
            cumulative = f"{purchase['cumulative_holdings']:,.2f}" if purchase['cumulative_holdings'] else "-"

            # 计算变化
            if purchase['cumulative_holdings'] and previous_holdings is not None:
                change = purchase['cumulative_holdings'] - previous_holdings
                if change > 0:
                    change_str = f"+{change:,.2f}"
                    status = "🟢 增持"
                elif change < 0:
                    change_str = f"{change:,.2f}"
                    status = "🔴 减持"
                else:
                    change_str = "0.00"
                    status = "⚪ 持平"
            else:
                change_str = f"+{quantity:,.2f}"
                status = "🆕 首次"

            print(f"{date_str:<12} {change_str:<15} {price:<15} {amount:<20} {cumulative:<20} {status}")

            if purchase['cumulative_holdings']:
                previous_holdings = purchase['cumulative_holdings']

        # 统计汇总
        print()
        total_quantity = sum(p['quantity'] for p in purchases if p['quantity'])
        total_amount = sum(p['total_amount'] for p in purchases if p['total_amount'])
        avg_price = total_amount / total_quantity if total_quantity and total_amount else None

        print(f"📈 统计汇总（最近{days}天）:")
        print(f"   累计购买: {total_quantity:,.2f} {asset}")
        if total_amount:
            print(f"   总投资: ${total_amount:,.0f}")
        if avg_price:
            print(f"   平均成本: ${avg_price:,.2f}")

        # 计算收益（如果有最新价格）
        if purchases[0]['cumulative_holdings'] and avg_price:
            # 这里可以从数据库获取最新BTC/ETH价格来计算收益
            pass

    print("\n" + "="*100)

def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='查看企业金库持仓变化')
    parser.add_argument('--company', help='公司名称（如：Strategy Inc）')
    parser.add_argument('--asset', choices=['BTC', 'ETH'], help='资产类型')
    parser.add_argument('--days', type=int, default=90, help='查看最近多少天（默认90天）')

    args = parser.parse_args()

    view_holdings_changes(
        company_name=args.company,
        asset_type=args.asset,
        days=args.days
    )

if __name__ == '__main__':
    main()