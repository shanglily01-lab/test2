"""
检查合约数据详细情况
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import yaml
from sqlalchemy import text
from datetime import datetime, timedelta
from app.database.db_service import DatabaseService

# 加载配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_service = DatabaseService(config.get('database', {}))
session = db_service.get_session()

print("=" * 120)
print("检查合约数据详细情况")
print("=" * 120 + "\n")

# 1. 检查持仓量数据表
print("1. 检查 binance_futures_open_interest 表:\n")
try:
    sql = text("""
        SELECT
            symbol,
            timestamp,
            open_interest,
            open_interest_value
        FROM binance_futures_open_interest
        ORDER BY timestamp DESC
        LIMIT 10
    """)
    results = session.execute(sql).fetchall()

    if results:
        print(f"{'币种':<15} {'时间':<22} {'持仓量':<20} {'持仓额(USDT)':<20}")
        print("-" * 120)
        for row in results:
            print(f"{row[0]:<15} {row[1]} {float(row[2]) if row[2] else 0:<19,.4f} ${float(row[3]) if row[3] else 0:<19,.2f}")
        print(f"\n✅ 持仓量表有数据，最新时间: {results[0][1]}")
    else:
        print("❌ 持仓量表无数据")

except Exception as e:
    print(f"❌ 查询失败: {e}")

# 2. 检查多空比数据表
print("\n" + "=" * 120)
print("\n2. 检查 binance_futures_long_short_ratio 表:\n")
try:
    sql = text("""
        SELECT
            symbol,
            timestamp,
            long_short_ratio,
            long_account,
            short_account
        FROM binance_futures_long_short_ratio
        ORDER BY timestamp DESC
        LIMIT 10
    """)
    results = session.execute(sql).fetchall()

    if results:
        print(f"{'币种':<15} {'时间':<22} {'多空比':<15} {'多头账户%':<15} {'空头账户%':<15}")
        print("-" * 120)
        for row in results:
            print(f"{row[0]:<15} {row[1]} {float(row[2]) if row[2] else 0:<14.4f} {float(row[3]) if row[3] else 0:<14.2f} {float(row[4]) if row[4] else 0:<14.2f}")
        print(f"\n✅ 多空比表有数据，最新时间: {results[0][1]}")
    else:
        print("❌ 多空比表无数据")

except Exception as e:
    print(f"❌ 查询失败: {e}")

# 3. 检查资金费率数据表
print("\n" + "=" * 120)
print("\n3. 检查 binance_futures_funding_rate 表:\n")
try:
    sql = text("""
        SELECT
            symbol,
            funding_time,
            funding_rate
        FROM binance_futures_funding_rate
        ORDER BY funding_time DESC
        LIMIT 10
    """)
    results = session.execute(sql).fetchall()

    if results:
        print(f"{'币种':<15} {'时间':<22} {'资金费率%':<15}")
        print("-" * 120)
        for row in results:
            print(f"{row[0]:<15} {row[1]} {float(row[2])*100 if row[2] else 0:<14.4f}%")
        print(f"\n✅ 资金费率表有数据，最新时间: {results[0][1]}")
    else:
        print("❌ 资金费率表无数据")

except Exception as e:
    print(f"❌ 查询失败: {e}")

# 4. 检查最近30分钟的数据
print("\n" + "=" * 120)
print("\n4. 检查最近30分钟的合约数据:\n")

start_time = datetime.utcnow() - timedelta(minutes=30)

try:
    sql = text("""
        SELECT
            COUNT(*) as total,
            MAX(timestamp) as latest_time
        FROM binance_futures_open_interest
        WHERE timestamp >= :start_time
    """)
    result = session.execute(sql, {"start_time": start_time}).fetchone()
    print(f"持仓量: 最近30分钟有 {result[0]} 条数据，最新时间: {result[1]}")

    sql = text("""
        SELECT
            COUNT(*) as total,
            MAX(timestamp) as latest_time
        FROM binance_futures_long_short_ratio
        WHERE timestamp >= :start_time
    """)
    result = session.execute(sql, {"start_time": start_time}).fetchone()
    print(f"多空比: 最近30分钟有 {result[0]} 条数据，最新时间: {result[1]}")

except Exception as e:
    print(f"❌ 查询失败: {e}")

# 5. 统计各币种数据
print("\n" + "=" * 120)
print("\n5. 各币种合约数据统计:\n")

try:
    sql = text("""
        SELECT
            symbol,
            COUNT(*) as count,
            MAX(timestamp) as latest_time
        FROM binance_futures_open_interest
        GROUP BY symbol
        ORDER BY symbol
    """)
    results = session.execute(sql).fetchall()

    if results:
        print(f"{'币种':<15} {'持仓量数据条数':<20} {'最新时间':<22}")
        print("-" * 120)
        for row in results:
            print(f"{row[0]:<15} {row[1]:<20} {row[2]}")
    else:
        print("❌ 无持仓量数据")

except Exception as e:
    print(f"❌ 查询失败: {e}")

session.close()

print("\n" + "=" * 120)
print("\n诊断结论:")
print("- 如果所有表都有数据且时间是最近的，说明采集正常，问题在前端或API")
print("- 如果表无数据或数据过旧，说明采集器有问题")
print("- UTC时间比北京时间慢8小时")
print("=" * 120)
