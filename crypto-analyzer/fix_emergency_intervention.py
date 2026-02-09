#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复emergency_intervention表中的错误记录
"""
import pymysql
import os
import sys
from dotenv import load_dotenv

# Fix Windows encoding
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4'
}

conn = pymysql.connect(**db_config)
cursor = conn.cursor(pymysql.cursors.DictCursor)

print('修复emergency_intervention表')
print('=' * 100)

# 删除所有未过期的记录（因为逻辑有问题）
cursor.execute("""
    DELETE FROM emergency_intervention
    WHERE account_id = 2
    AND trading_type = 'usdt_futures'
    AND expires_at > NOW()
""")

deleted_count = cursor.rowcount
conn.commit()

print(f'✅ 已删除 {deleted_count} 条错误的干预记录')
print('系统将在下次Big4检测时重新生成正确的记录')

cursor.close()
conn.close()

print('=' * 100)
print('修复完成')
