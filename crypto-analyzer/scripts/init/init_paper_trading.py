#!/usr/bin/env python3
"""
初始化模拟交易系统数据库
"""

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

import yaml
import pymysql
from loguru import logger


def init_database():
    """初始化数据库表"""
    print("=" * 80)
    print("初始化模拟交易系统数据库")
    print("=" * 80)
    print()

    # 读取配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    db_config = config.get('database', {}).get('mysql', {})

    # 连接数据库
    conn = pymysql.connect(
        host=db_config.get('host', 'localhost'),
        port=db_config.get('port', 3306),
        user=db_config.get('user', 'root'),
        password=db_config.get('password', ''),
        database=db_config.get('database', 'binance-data'),
        charset='utf8mb4'
    )

    try:
        with conn.cursor() as cursor:
            # 读取 SQL 文件
            sql_file = 'app/database/paper_trading_schema.sql'
            print(f"读取 SQL 文件: {sql_file}")

            with open(sql_file, 'r', encoding='utf-8') as f:
                sql_content = f.read()

            # 分割多个 SQL 语句
            statements = sql_content.split(';')

            success_count = 0
            for statement in statements:
                statement = statement.strip()
                if not statement or statement.startswith('--'):
                    continue

                try:
                    cursor.execute(statement)
                    success_count += 1
                except Exception as e:
                    # 如果是 CREATE TABLE IF NOT EXISTS，重复创建不算错误
                    if 'already exists' not in str(e).lower():
                        print(f"⚠️  执行 SQL 失败: {e}")
                        print(f"   语句: {statement[:100]}...")

            conn.commit()
            print(f"\n✅ 成功执行 {success_count} 条 SQL 语句")

            # 检查表是否创建成功
            cursor.execute("SHOW TABLES LIKE 'paper_trading_%'")
            tables = cursor.fetchall()
            print(f"\n已创建的模拟交易表 ({len(tables)} 个):")
            for table in tables:
                print(f"  - {table[0]}")

            # 检查默认账户
            cursor.execute("SELECT * FROM paper_trading_accounts WHERE is_default = TRUE")
            default_account = cursor.fetchone()
            if default_account:
                print(f"\n✅ 默认账户已创建:")
                print(f"   账户ID: {default_account[0]}")
                print(f"   账户名: {default_account[1]}")
                print(f"   初始资金: {default_account[3]} USDT")
            else:
                print(f"\n⚠️  默认账户未创建，请检查SQL脚本")

    except Exception as e:
        print(f"\n❌ 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        conn.close()

    print("\n" + "=" * 80)
    print("✅ 模拟交易系统数据库初始化完成！")
    print("=" * 80)
    print("\n下一步:")
    print("  1. 启动 Web 服务: python run.py")
    print("  2. 访问模拟交易页面: http://localhost:8000/paper-trading")
    print("  3. 或使用 API: http://localhost:8000/api/paper-trading/account")
    print()

    return True


if __name__ == "__main__":
    init_database()
