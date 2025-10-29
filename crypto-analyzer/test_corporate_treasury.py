#!/usr/bin/env python3
"""
测试企业金库API - 诊断工具
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

import mysql.connector
from mysql.connector import pooling
import yaml

print("=" * 70)
print("企业金库API诊断工具")
print("=" * 70)

# 加载配置
config_path = project_root / 'config.yaml'
with open(config_path, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

mysql_config = config.get('database', {}).get('mysql', {})

db_config = {
    "host": mysql_config.get('host', 'localhost'),
    "port": mysql_config.get('port', 3306),
    "user": mysql_config.get('user', 'root'),
    "password": mysql_config.get('password', ''),
    "database": mysql_config.get('database', 'binance-data'),
}

print(f"\n数据库配置:")
print(f"  Host: {db_config['host']}")
print(f"  Port: {db_config['port']}")
print(f"  Database: {db_config['database']}")

# 连接数据库
try:
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor(dictionary=True)
    print("\n✅ 数据库连接成功")
except Exception as e:
    print(f"\n❌ 数据库连接失败: {e}")
    sys.exit(1)

# 检查MySQL版本
cursor.execute("SELECT VERSION() as version")
version = cursor.fetchone()
print(f"\nMySQL版本: {version['version']}")

# 检查表记录数
print("\n检查数据表:")
cursor.execute("SELECT COUNT(*) as cnt FROM corporate_treasury_companies")
companies_count = cursor.fetchone()['cnt']
print(f"  corporate_treasury_companies: {companies_count} 条记录")

cursor.execute("SELECT COUNT(*) as cnt FROM corporate_treasury_purchases")
purchases_count = cursor.fetchone()['cnt']
print(f"  corporate_treasury_purchases: {purchases_count} 条记录")

if companies_count == 0:
    print("\n❌ corporate_treasury_companies 表为空！")
    print("   需要运行数据采集脚本导入企业金库数据")
    sys.exit(1)

if purchases_count == 0:
    print("\n❌ corporate_treasury_purchases 表为空！")
    print("   需要运行数据采集脚本导入购买记录")
    sys.exit(1)

# 测试窗口函数支持
print("\n测试窗口函数支持 (ROW_NUMBER):")
try:
    cursor.execute("""
        SELECT
            company_id,
            cumulative_holdings,
            ROW_NUMBER() OVER (PARTITION BY company_id ORDER BY purchase_date DESC) as rn
        FROM corporate_treasury_purchases
        LIMIT 1
    """)
    result = cursor.fetchone()
    print("  ✅ 窗口函数支持正常 (MySQL 8.0+)")
except Exception as e:
    print(f"  ❌ 窗口函数不支持: {e}")
    print("  ⚠️  需要MySQL 8.0或更高版本")
    print("\n使用兼容查询方案...")

# 测试获取汇总数据（兼容版本）
print("\n测试汇总查询:")
try:
    # 获取BTC价格
    cursor.execute("""
        SELECT average_price
        FROM corporate_treasury_purchases
        WHERE average_price > 0
        ORDER BY purchase_date DESC
        LIMIT 1
    """)
    btc_price_result = cursor.fetchone()
    current_btc_price = btc_price_result['average_price'] if btc_price_result else 100000
    print(f"  当前BTC价格: ${current_btc_price:,.2f}")

    # 统计数据（兼容版本 - 不使用窗口函数）
    cursor.execute("""
        SELECT
            COUNT(DISTINCT c.id) as total_companies,
            COALESCE(SUM(p.cumulative_holdings), 0) as total_btc_holdings
        FROM corporate_treasury_companies c
        LEFT JOIN corporate_treasury_purchases p ON c.id = p.company_id
        WHERE c.is_active = 1
        GROUP BY c.id
    """)
    stats = cursor.fetchall()

    if stats:
        total_companies = len(stats)
        total_btc = sum(float(s['total_btc_holdings'] or 0) for s in stats)
        total_value_usd = total_btc * current_btc_price

        print(f"  ✅ 查询成功")
        print(f"\n汇总数据:")
        print(f"  监控公司: {total_companies}")
        print(f"  BTC总持仓: {total_btc:,.2f} BTC")
        print(f"  总市值: ${total_value_usd:,.2f}")
    else:
        print("  ⚠️  查询返回空结果")

    # 获取Top持仓公司（简化版本）
    cursor.execute("""
        SELECT
            c.company_name,
            c.ticker_symbol,
            p.cumulative_holdings as btc_holdings,
            p.purchase_date as last_update
        FROM corporate_treasury_companies c
        INNER JOIN corporate_treasury_purchases p ON c.id = p.company_id
        WHERE c.is_active = 1 AND p.cumulative_holdings > 0
        ORDER BY p.cumulative_holdings DESC, p.purchase_date DESC
        LIMIT 10
    """)
    top_holders = cursor.fetchall()

    print(f"\nTop 10 持仓公司:")
    for i, holder in enumerate(top_holders, 1):
        print(f"  {i}. {holder['company_name']} ({holder['ticker_symbol']}): {holder['btc_holdings']:,.2f} BTC")

except Exception as e:
    print(f"  ❌ 查询失败: {e}")
    import traceback
    traceback.print_exc()

cursor.close()
conn.close()

print("\n" + "=" * 70)
print("诊断完成")
print("=" * 70)
