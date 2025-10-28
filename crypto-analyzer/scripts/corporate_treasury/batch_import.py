#!/usr/bin/env python3
"""
企业金库数据批量导入工具
支持从 Bitcoin Treasuries 格式批量导入公司持仓数据
"""

import sys
import os
import re
from datetime import datetime
import yaml

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

def parse_bitcoin_treasuries_format(text):
    """
    解析 Bitcoin Treasuries 网站的复制格式

    示例格式：
    1
    Strategy
    🇺🇸	MSTR	640,808
    2
    MARA Holdings, Inc.
    🇺🇸	MARA	53,250

    返回：[(公司名, 股票代码, BTC数量), ...]
    """
    companies = []
    lines = text.strip().split('\n')

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # 跳过排名数字
        if line.isdigit():
            i += 1
            continue

        # 如果是公司名（不包含制表符）
        if '\t' not in line and line and not line.startswith('🇺🇸') and not line.startswith('🇯🇵'):
            company_name = line

            # 下一行应该是国旗、股票代码和数量
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()

                # 解析格式：🇺🇸	MSTR	640,808
                parts = next_line.split('\t')

                if len(parts) >= 3:
                    ticker = parts[1].strip()
                    btc_amount_str = parts[2].strip().replace(',', '')

                    try:
                        btc_amount = float(btc_amount_str)
                        companies.append((company_name, ticker, btc_amount))
                    except ValueError:
                        print(f"⚠️  跳过无效数量: {company_name} - {parts[2]}")

                i += 2  # 跳过下一行
                continue

        i += 1

    return companies

def import_companies(companies_data, purchase_date, asset_type='BTC', data_source='batch_import'):
    """
    批量导入公司持仓数据

    Args:
        companies_data: [(公司名, 股票代码, 持仓量), ...]
        purchase_date: 数据日期
        asset_type: 资产类型（BTC/ETH）
        data_source: 数据来源标记
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    success_count = 0
    skip_count = 0
    error_count = 0

    print(f"\n开始导入 {len(companies_data)} 家公司的数据...")
    print("="*80)

    for company_name, ticker, holdings in companies_data:
        try:
            # 1. 检查公司是否存在，不存在则创建
            cursor.execute("""
                SELECT id FROM corporate_treasury_companies
                WHERE company_name = %s OR ticker_symbol = %s
            """, (company_name, ticker))

            company = cursor.fetchone()

            if not company:
                # 创建新公司
                cursor.execute("""
                    INSERT INTO corporate_treasury_companies
                    (company_name, ticker_symbol, category, is_active)
                    VALUES (%s, %s, %s, 1)
                """, (company_name, ticker, 'holding'))

                company_id = cursor.lastrowid
                print(f"✅ 新增公司: {company_name} ({ticker})")
            else:
                company_id = company['id']

            # 2. 检查是否已有该日期的记录
            cursor.execute("""
                SELECT id, cumulative_holdings FROM corporate_treasury_purchases
                WHERE company_id = %s AND purchase_date = %s AND asset_type = %s
            """, (company_id, purchase_date, asset_type))

            existing = cursor.fetchone()

            if existing:
                # 如果持仓量相同，跳过
                if existing['cumulative_holdings'] and float(existing['cumulative_holdings']) == holdings:
                    skip_count += 1
                    print(f"⏭️  跳过（已存在）: {company_name} - {holdings:,.0f} {asset_type}")
                    continue

                # 更新记录
                cursor.execute("""
                    UPDATE corporate_treasury_purchases
                    SET cumulative_holdings = %s, updated_at = NOW()
                    WHERE id = %s
                """, (holdings, existing['id']))

                print(f"🔄 更新: {company_name} ({ticker}) - {holdings:,.0f} {asset_type}")
            else:
                # 3. 获取上一次的持仓量（计算购买数量）
                cursor.execute("""
                    SELECT cumulative_holdings FROM corporate_treasury_purchases
                    WHERE company_id = %s AND asset_type = %s
                    ORDER BY purchase_date DESC
                    LIMIT 1
                """, (company_id, asset_type))

                last_record = cursor.fetchone()
                last_holdings = float(last_record['cumulative_holdings']) if last_record and last_record['cumulative_holdings'] else 0

                # 计算购买数量
                quantity = holdings - last_holdings

                # 插入新记录
                cursor.execute("""
                    INSERT INTO corporate_treasury_purchases
                    (company_id, purchase_date, asset_type, quantity, cumulative_holdings, data_source)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (company_id, purchase_date, asset_type, quantity, holdings, data_source))

                change_str = f"+{quantity:,.0f}" if quantity >= 0 else f"{quantity:,.0f}"
                status = "🟢" if quantity > 0 else "🔴" if quantity < 0 else "⚪"

                print(f"{status} {company_name} ({ticker}): {change_str} {asset_type} → {holdings:,.0f}")
                success_count += 1

            conn.commit()

        except Error as e:
            error_count += 1
            print(f"❌ 错误: {company_name} - {e}")
            conn.rollback()

    cursor.close()
    conn.close()

    # 汇总
    print("\n" + "="*80)
    print(f"✅ 导入完成！")
    print(f"   成功: {success_count} 条")
    print(f"   跳过: {skip_count} 条")
    print(f"   错误: {error_count} 条")
    print("="*80)

def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='企业金库批量导入工具 - BTC持仓追踪')
    parser.add_argument('-f', '--file', help='从文件导入（如：import_template.txt）')
    parser.add_argument('-d', '--date', help='数据日期 (YYYY-MM-DD，默认=今天)')

    args = parser.parse_args()

    print("\n" + "="*80)
    print("📦 企业金库批量导入工具")
    print("="*80)
    print("\n支持的格式：")
    print("  1. Bitcoin Treasuries 网站复制格式")
    print("  2. 从文件导入（使用 -f 参数）")
    print("\n操作步骤：")
    print("  1. 访问 https://bitcointreasuries.net/")
    print("  2. 复制公司列表（包含排名、公司名、国旗、代码、持仓）")
    print("  3. 粘贴到下方，或保存到文件后使用 -f 参数")
    print("\n使用示例：")
    print("  python batch_import.py                           # 交互式输入")
    print("  python batch_import.py -f import_template.txt    # 从文件导入")
    print("  python batch_import.py -f data.txt -d 2025-10-28 # 指定日期")
    print("="*80)

    # 获取数据日期
    if args.date:
        purchase_date = args.date
    else:
        purchase_date = input("\n请输入数据日期 (YYYY-MM-DD，回车=今天): ").strip()
        if not purchase_date:
            purchase_date = datetime.now().strftime('%Y-%m-%d')

    print(f"数据日期: {purchase_date}")

    # 固定为 BTC
    asset_type = 'BTC'
    print(f"资产类型: {asset_type}")

    # 读取数据
    if args.file:
        # 从文件读取
        print(f"\n从文件读取: {args.file}")
        try:
            with open(args.file, 'r', encoding='utf-8') as f:
                text = f.read()
            print(f"✅ 成功读取文件")
        except FileNotFoundError:
            print(f"❌ 文件不存在: {args.file}")
            return
        except Exception as e:
            print(f"❌ 读取文件失败: {e}")
            return
    else:
        # 交互式输入
        print(f"\n请粘贴数据（粘贴完成后按 Ctrl+D (Linux/Mac) 或 Ctrl+Z (Windows) 然后回车）:")
        print("-" * 80)

        # 读取多行输入
        lines = []
        try:
            while True:
                line = input()
                lines.append(line)
        except EOFError:
            pass

        text = '\n'.join(lines)

    if not text.strip():
        print("❌ 没有输入数据")
        return

    # 解析数据
    print("\n解析数据中...")
    companies = parse_bitcoin_treasuries_format(text)

    if not companies:
        print("❌ 无法解析数据，请检查格式")
        print("\n提示：确保复制的数据包含：")
        print("  - 排名数字")
        print("  - 公司名称")
        print("  - 国旗 + 股票代码 + 持仓量（用制表符分隔）")
        return

    print(f"\n✅ 成功解析 {len(companies)} 家公司")
    print("\n预览前5条：")
    for i, (name, ticker, holdings) in enumerate(companies[:5], 1):
        print(f"  {i}. {name} ({ticker}): {holdings:,.0f} {asset_type}")

    if len(companies) > 5:
        print(f"  ... 还有 {len(companies) - 5} 条")

    # 确认导入
    confirm = input(f"\n确认导入这 {len(companies)} 条数据？(yes/no): ").strip().lower()

    if confirm in ['yes', 'y']:
        import_companies(companies, purchase_date, asset_type)
    else:
        print("❌ 已取消")

if __name__ == '__main__':
    main()