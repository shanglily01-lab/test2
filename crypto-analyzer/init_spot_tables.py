#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""初始化现货交易表"""
import pymysql
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

conn = pymysql.connect(
    host='13.212.252.171',
    port=3306,
    user='admin',
    password='Tonny@1000',
    database='binance-data'
)

cursor = conn.cursor()

print("=" * 120)
print("初始化现货交易表")
print("=" * 120)

# 读取SQL文件并执行
with open('app/database/spot_trading_schema.sql', 'r', encoding='utf-8') as f:
    sql_content = f.read()

# 分割SQL语句并执行
sql_statements = sql_content.split(';')

for i, statement in enumerate(sql_statements, 1):
    statement = statement.strip()
    if statement and not statement.startswith('--'):
        try:
            cursor.execute(statement)
            conn.commit()
            # 提取表名用于日志
            if 'CREATE TABLE' in statement.upper():
                table_name = statement.split('TABLE')[1].split('(')[0].strip().split()[0].strip('`').replace('IF NOT EXISTS', '').strip()
                print(f"✅ 创建表: {table_name}")
            elif 'CREATE OR REPLACE VIEW' in statement.upper():
                view_name = statement.split('VIEW')[1].split('AS')[0].strip()
                print(f"✅ 创建视图: {view_name}")
            elif 'INSERT INTO' in statement.upper():
                table_name = statement.split('INTO')[1].split('(')[0].strip().split()[0].strip('`')
                print(f"✅ 初始化数据: {table_name}")
        except Exception as e:
            print(f"❌ 执行SQL失败 (语句 {i}): {e}")
            print(f"   SQL: {statement[:100]}...")

print("\n" + "=" * 120)
print("表创建完成，验证...")
print("=" * 120)

cursor.execute("SHOW TABLES LIKE 'spot_%'")
tables = cursor.fetchall()

print(f"\n现货交易表: {len(tables)} 个\n")
for table in tables:
    table_name = table[0]
    cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`")
    count = cursor.fetchone()[0]
    print(f"  {table_name:30s} - {count} 条记录")

cursor.close()
conn.close()

print("\n✅ 现货交易系统数据库初始化完成!")
