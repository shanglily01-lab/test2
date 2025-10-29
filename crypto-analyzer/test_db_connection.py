#!/usr/bin/env python3
"""
快速测试数据库连接
"""

import sys
import yaml
from pathlib import Path

print("=" * 60)
print("数据库连接测试")
print("=" * 60)

# 1. 加载配置
config_path = Path("config.yaml")
if not config_path.exists():
    print(f"❌ 配置文件不存在: {config_path}")
    sys.exit(1)

with open(config_path, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config.get('database', {}).get('mysql', {})
print(f"\n数据库配置:")
print(f"  Host: {db_config.get('host')}")
print(f"  Port: {db_config.get('port')}")
print(f"  User: {db_config.get('user')}")
print(f"  Database: {db_config.get('database')}")

# 2. 测试连接
try:
    import pymysql
    print(f"\n正在连接到 MySQL...")

    connection = pymysql.connect(
        host=db_config.get('host', 'localhost'),
        port=db_config.get('port', 3306),
        user=db_config.get('user', 'root'),
        password=db_config.get('password', ''),
        database=db_config.get('database', 'crypto_analyzer'),
        connect_timeout=5
    )

    print("✅ 数据库连接成功！")

    # 3. 检查表
    cursor = connection.cursor()

    print(f"\n检查数据表:")

    tables = [
        'price_data',
        'futures_data',
        'investment_recommendations',
        'news',
        'ema_signals',
        'technical_indicators_cache',
        'price_stats_24h',
        'hyperliquid_symbol_aggregation',
        'investment_recommendations_cache',
        'news_sentiment_aggregation',
        'funding_rate_stats'
    ]

    for table in tables:
        try:
            cursor.execute(f"SHOW TABLES LIKE '{table}'")
            result = cursor.fetchone()
            if result:
                cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
                count = cursor.fetchone()[0]
                status = "✅" if count > 0 else "⚠️ "
                print(f"  {status} {table}: {count:,} 条记录")
            else:
                print(f"  ❌ {table}: 表不存在")
        except Exception as e:
            print(f"  ❌ {table}: 查询失败 - {e}")

    cursor.close()
    connection.close()

    print("\n" + "=" * 60)
    print("✅ 数据库测试完成")
    print("=" * 60)

except ImportError:
    print("\n❌ pymysql 模块未安装")
    print("   请运行: pip install pymysql")
    sys.exit(1)

except pymysql.err.OperationalError as e:
    print(f"\n❌ 数据库连接失败: {e}")
    print("\n可能的原因:")
    print("  1. MySQL服务未启动")
    print("     Windows: 打开服务管理器，确认 MySQL 服务正在运行")
    print("  2. 用户名或密码错误")
    print("     检查 config.yaml 中的 database.mysql.password")
    print("  3. 数据库不存在")
    print("     使用 MySQL Workbench 或命令行创建 'binance-data' 数据库")
    print("  4. 防火墙阻止连接")
    sys.exit(1)

except Exception as e:
    print(f"\n❌ 未知错误: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
