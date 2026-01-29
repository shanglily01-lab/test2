#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查看模拟交易表结构"""

import pymysql
import sys
import io
from dotenv import load_dotenv
import os

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'),
    cursorclass=pymysql.cursors.DictCursor,
    charset='utf8mb4'
)

cursor = conn.cursor()

print('=' * 100)
print('模拟交易相关表结构')
print('=' * 100)
print()

# 查找所有模拟交易相关的表
cursor.execute("SHOW TABLES LIKE 'paper_trading%'")
tables = cursor.fetchall()

for table_row in tables:
    table_name = list(table_row.values())[0]
    print(f"表: {table_name}")
    print('-' * 100)

    cursor.execute(f"DESCRIBE {table_name}")
    columns = cursor.fetchall()

    print(f"{'字段名':<30} {'类型':<30} {'允许NULL':<10} {'键':<10} {'默认值':<20}")
    print('-' * 100)

    for col in columns:
        print(f"{col['Field']:<30} {col['Type']:<30} {col['Null']:<10} {col['Key']:<10} {str(col['Default']):<20}")

    # 查询记录数
    cursor.execute(f"SELECT COUNT(*) as cnt FROM {table_name}")
    count = cursor.fetchone()['cnt']
    print(f"\n记录数: {count}\n")
    print('=' * 100)
    print()

cursor.close()
conn.close()
