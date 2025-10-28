#!/usr/bin/env python3
"""
企业金库数据交互式录入工具
支持录入购买记录、融资信息、股价数据
"""

import sys
import os
from datetime import datetime
from decimal import Decimal
import yaml

# 添加项目根目录到路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

import mysql.connector
from mysql.connector import Error

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

def list_companies():
    """列出所有公司"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT id, company_name, ticker_symbol, category
        FROM corporate_treasury_companies
        WHERE is_active = 1
        ORDER BY company_name
    """)

    companies = cursor.fetchall()
    cursor.close()
    conn.close()

    print("\n" + "="*80)
    print("📊 企业金库监控列表")
    print("="*80)

    if not companies:
        print("暂无公司数据")
        return []

    for i, company in enumerate(companies, 1):
        ticker = f" ({company['ticker_symbol']})" if company['ticker_symbol'] else ""
        print(f"{i}. {company['company_name']}{ticker} - {company['category']}")

    return companies

def input_purchase():
    """录入购买记录"""
    print("\n" + "="*80)
    print("💰 录入购买记录")
    print("="*80)

    companies = list_companies()
    if not companies:
        print("请先添加公司信息")
        return

    # 选择公司
    company_idx = int(input("\n请选择公司编号: ")) - 1
    if company_idx < 0 or company_idx >= len(companies):
        print("❌ 无效的公司编号")
        return

    company = companies[company_idx]
    company_id = company['id']

    print(f"\n已选择: {company['company_name']}")

    # 输入购买信息
    purchase_date = input("购买日期 (YYYY-MM-DD): ")
    asset_type = input("资产类型 (BTC/ETH): ").upper()

    if asset_type not in ['BTC', 'ETH']:
        print("❌ 资产类型只能是 BTC 或 ETH")
        return

    quantity = input("购买数量: ")
    average_price = input("平均价格(USD, 可选): ") or None
    total_amount = input("总金额(USD, 可选): ") or None
    cumulative_holdings = input("累计持仓量(可选): ") or None
    announcement_url = input("公告链接(可选): ") or None
    notes = input("备注(可选): ") or None

    # 插入数据库
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO corporate_treasury_purchases
            (company_id, purchase_date, asset_type, quantity, average_price,
             total_amount, cumulative_holdings, announcement_url, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (company_id, purchase_date, asset_type, quantity, average_price,
              total_amount, cumulative_holdings, announcement_url, notes))

        conn.commit()
        print(f"\n✅ 购买记录已保存！")
        print(f"   {company['company_name']} 购入 {quantity} {asset_type}")

    except Error as e:
        print(f"❌ 保存失败: {e}")
        conn.rollback()

    finally:
        cursor.close()
        conn.close()

def input_financing():
    """录入融资信息"""
    print("\n" + "="*80)
    print("💵 录入融资信息")
    print("="*80)

    companies = list_companies()
    if not companies:
        print("请先添加公司信息")
        return

    # 选择公司
    company_idx = int(input("\n请选择公司编号: ")) - 1
    if company_idx < 0 or company_idx >= len(companies):
        print("❌ 无效的公司编号")
        return

    company = companies[company_idx]
    company_id = company['id']

    print(f"\n已选择: {company['company_name']}")

    # 输入融资信息
    financing_date = input("融资日期 (YYYY-MM-DD): ")

    print("\n融资类型:")
    print("1. equity - 股权融资")
    print("2. convertible_note - 可转换债券")
    print("3. loan - 贷款")
    print("4. atm - ATM增发")
    print("5. other - 其他")

    type_choice = input("选择类型 (1-5): ")
    type_map = {'1': 'equity', '2': 'convertible_note', '3': 'loan', '4': 'atm', '5': 'other'}
    financing_type = type_map.get(type_choice, 'other')

    amount = input("融资金额(USD): ")
    purpose = input("用途说明: ")
    announcement_url = input("公告链接(可选): ") or None
    notes = input("备注(可选): ") or None

    # 插入数据库
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO corporate_treasury_financing
            (company_id, financing_date, financing_type, amount, purpose,
             announcement_url, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (company_id, financing_date, financing_type, amount, purpose,
              announcement_url, notes))

        conn.commit()
        print(f"\n✅ 融资信息已保存！")
        print(f"   {company['company_name']} {financing_type} 融资 ${amount}")

    except Error as e:
        print(f"❌ 保存失败: {e}")
        conn.rollback()

    finally:
        cursor.close()
        conn.close()

def input_stock_price():
    """录入股价数据"""
    print("\n" + "="*80)
    print("📈 录入股价数据")
    print("="*80)

    companies = list_companies()
    if not companies:
        print("请先添加公司信息")
        return

    # 选择公司
    company_idx = int(input("\n请选择公司编号: ")) - 1
    if company_idx < 0 or company_idx >= len(companies):
        print("❌ 无效的公司编号")
        return

    company = companies[company_idx]
    company_id = company['id']

    if not company['ticker_symbol']:
        print(f"❌ {company['company_name']} 没有股票代码，无法录入股价")
        return

    print(f"\n已选择: {company['company_name']} ({company['ticker_symbol']})")

    # 输入股价信息
    trade_date = input("交易日期 (YYYY-MM-DD): ")
    open_price = input("开盘价: ") or None
    close_price = input("收盘价: ")
    high_price = input("最高价(可选): ") or None
    low_price = input("最低价(可选): ") or None
    volume = input("成交量(可选): ") or None
    market_cap = input("市值(可选): ") or None
    change_pct = input("涨跌幅%(可选): ") or None

    # 插入数据库
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO corporate_treasury_stock_prices
            (company_id, trade_date, open_price, close_price, high_price,
             low_price, volume, market_cap, change_pct)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                open_price = VALUES(open_price),
                close_price = VALUES(close_price),
                high_price = VALUES(high_price),
                low_price = VALUES(low_price),
                volume = VALUES(volume),
                market_cap = VALUES(market_cap),
                change_pct = VALUES(change_pct)
        """, (company_id, trade_date, open_price, close_price, high_price,
              low_price, volume, market_cap, change_pct))

        conn.commit()
        print(f"\n✅ 股价数据已保存！")
        print(f"   {company['ticker_symbol']} {trade_date}: ${close_price}")

    except Error as e:
        print(f"❌ 保存失败: {e}")
        conn.rollback()

    finally:
        cursor.close()
        conn.close()

def view_summary():
    """查看汇总信息"""
    print("\n" + "="*80)
    print("📊 企业金库持仓汇总")
    print("="*80)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT * FROM corporate_treasury_summary
        ORDER BY btc_holdings DESC
    """)

    summaries = cursor.fetchall()
    cursor.close()
    conn.close()

    if not summaries:
        print("暂无数据")
        return

    for summary in summaries:
        print(f"\n{summary['company_name']} ({summary['ticker_symbol'] or 'N/A'})")
        print("-" * 60)

        if summary['btc_holdings']:
            print(f"  BTC持仓: {summary['btc_holdings']:,.2f} BTC")
            if summary['btc_total_investment']:
                print(f"  BTC投资: ${summary['btc_total_investment']:,.0f}")

        if summary['eth_holdings']:
            print(f"  ETH持仓: {summary['eth_holdings']:,.2f} ETH")
            if summary['eth_total_investment']:
                print(f"  ETH投资: ${summary['eth_total_investment']:,.0f}")

        if summary['last_purchase_date']:
            print(f"  最近购买: {summary['last_purchase_date']}")

        if summary['total_financing']:
            print(f"  总融资: ${summary['total_financing']:,.0f}")

        if summary['latest_stock_price']:
            change = f" ({summary['latest_change_pct']:+.2f}%)" if summary['latest_change_pct'] else ""
            print(f"  最新股价: ${summary['latest_stock_price']}{change}")

def main():
    """主菜单"""
    while True:
        print("\n" + "="*80)
        print("🏦 企业金库监控 - 数据录入工具")
        print("="*80)
        print("1. 录入购买记录")
        print("2. 录入融资信息")
        print("3. 录入股价数据")
        print("4. 查看持仓汇总")
        print("5. 查看公司列表")
        print("0. 退出")

        choice = input("\n请选择操作: ")

        if choice == '1':
            input_purchase()
        elif choice == '2':
            input_financing()
        elif choice == '3':
            input_stock_price()
        elif choice == '4':
            view_summary()
        elif choice == '5':
            list_companies()
        elif choice == '0':
            print("再见！")
            break
        else:
            print("❌ 无效的选择")

if __name__ == '__main__':
    main()