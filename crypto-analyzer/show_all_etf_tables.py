#!/usr/bin/env python3
"""
检查所有ETF相关表的数据
"""

import mysql.connector
import yaml

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
    print('查找所有ETF相关的表')
    print('=' * 80)
    print()

    # 查找所有包含 'etf' 的表
    cursor.execute("SHOW TABLES LIKE '%etf%'")
    etf_tables = cursor.fetchall()

    if not etf_tables:
        print("❌ 没有找到任何包含'etf'的表!")
        print()
        print("正在查找所有表...")
        cursor.execute("SHOW TABLES")
        all_tables = cursor.fetchall()
        print(f"\n数据库中共有 {len(all_tables)} 个表:")
        for table in all_tables[:20]:  # 只显示前20个
            table_name = list(table.values())[0]
            cursor.execute(f"SELECT COUNT(*) as count FROM `{table_name}`")
            count = cursor.fetchone()['count']
            print(f"  - {table_name}: {count} 条记录")
    else:
        print(f"找到 {len(etf_tables)} 个ETF相关的表:")
        print()

        for table_dict in etf_tables:
            table_name = list(table_dict.values())[0]
            print(f"表名: {table_name}")
            print("-" * 80)

            # 获取记录数
            cursor.execute(f"SELECT COUNT(*) as count FROM `{table_name}`")
            count = cursor.fetchone()['count']
            print(f"总记录数: {count}")

            if count > 0:
                # 显示表结构
                cursor.execute(f"DESCRIBE `{table_name}`")
                columns = cursor.fetchall()
                print("\n列名:")
                col_names = [col['Field'] for col in columns]
                print(f"  {', '.join(col_names)}")

                # 显示最近5条数据
                cursor.execute(f"SELECT * FROM `{table_name}` ORDER BY id DESC LIMIT 5")
                rows = cursor.fetchall()
                print(f"\n最近 {len(rows)} 条数据:")
                for i, row in enumerate(rows, 1):
                    print(f"\n  记录 {i}:")
                    for key, value in row.items():
                        if value is not None:
                            print(f"    {key}: {value}")

            print()
            print("=" * 80)
            print()

    cursor.close()
    conn.close()

except Exception as e:
    print(f"❌ 错误: {e}")
    import traceback
    traceback.print_exc()
