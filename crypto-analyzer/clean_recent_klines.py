#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清除最近3天的合约K线数据"""
import sys
import os
import mysql.connector
from dotenv import load_dotenv
from datetime import datetime, timedelta

# 设置UTF-8输出
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

conn = mysql.connector.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME')
)

cursor = conn.cursor(dictionary=True)

print('=' * 100)
print('清除最近3天的合约K线数据')
print('=' * 100)
print()

# 先统计要删除的数据量
cursor.execute('''
    SELECT
        timeframe,
        COUNT(*) as count
    FROM kline_data
    WHERE exchange = 'binance_futures'
    AND timestamp >= DATE_SUB(NOW(), INTERVAL 3 DAY)
    GROUP BY timeframe
    ORDER BY timeframe
''')

stats = cursor.fetchall()

print('即将删除的数据：')
total_count = 0
for row in stats:
    print(f'  {row["timeframe"]:>4}: {row["count"]:>6} 条')
    total_count += row['count']

print(f'\n总计: {total_count} 条')
print()

# 确认删除
confirm = input('确认删除？(输入 yes 继续): ')

if confirm.lower() != 'yes':
    print('已取消删除')
    cursor.close()
    conn.close()
    sys.exit(0)

# 执行删除
print('\n开始删除...')

cursor.execute('''
    DELETE FROM kline_data
    WHERE exchange = 'binance_futures'
    AND timestamp >= DATE_SUB(NOW(), INTERVAL 3 DAY)
''')

deleted_count = cursor.rowcount
conn.commit()

print(f'✅ 成功删除 {deleted_count} 条记录')
print()
print('=' * 100)

cursor.close()
conn.close()
