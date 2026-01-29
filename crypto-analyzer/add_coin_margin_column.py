#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""添加coin_margin字段到futures_positions表"""

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
    database=os.getenv('DB_NAME')
)

cursor = conn.cursor()

print('=' * 100)
print('添加coin_margin字段到futures_positions表')
print('=' * 100)
print()

try:
    # 检查字段是否已存在
    cursor.execute("""
        SELECT COUNT(*) as cnt
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s
        AND TABLE_NAME = 'futures_positions'
        AND COLUMN_NAME = 'coin_margin'
    """, (os.getenv('DB_NAME'),))

    result = cursor.fetchone()

    if result[0] > 0:
        print('✓ coin_margin字段已存在，无需添加')
    else:
        print('添加coin_margin字段...')
        cursor.execute("""
            ALTER TABLE futures_positions
            ADD COLUMN coin_margin TINYINT(1) DEFAULT 0 COMMENT '是否币本位合约(0=U本位,1=币本位)'
        """)
        conn.commit()
        print('✓ 成功添加coin_margin字段')

except Exception as e:
    print(f'✗ 添加字段失败: {e}')
    import traceback
    traceback.print_exc()
finally:
    cursor.close()
    conn.close()

print()
print('=' * 100)
