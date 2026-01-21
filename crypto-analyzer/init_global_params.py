#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
初始化全局自适应参数
"""
import pymysql
import os
from dotenv import load_dotenv

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data'),
    'charset': 'utf8mb4'
}

# 全局参数配置
GLOBAL_PARAMS = [
    ('long_take_profit_pct', 0.05, 'global', '做多全局止盈比例'),
    ('long_stop_loss_pct', 0.02, 'global', '做多全局止损比例'),
    ('short_take_profit_pct', 0.05, 'global', '做空全局止盈比例'),
    ('short_stop_loss_pct', 0.02, 'global', '做空全局止损比例'),
]

print("Connecting to database...")
conn = pymysql.connect(**db_config)
cursor = conn.cursor(pymysql.cursors.DictCursor)

print("\nInitializing global parameters...")

for param_key, param_value, param_type, description in GLOBAL_PARAMS:
    # 检查是否已存在
    cursor.execute("""
        SELECT id FROM adaptive_params
        WHERE param_key = %s AND param_type = %s
    """, (param_key, param_type))

    existing = cursor.fetchone()

    if existing:
        print(f"  [SKIP] {param_key} already exists")
    else:
        cursor.execute("""
            INSERT INTO adaptive_params
            (param_key, param_value, param_type, description, updated_by)
            VALUES (%s, %s, %s, %s, 'init_script')
        """, (param_key, param_value, param_type, description))
        print(f"  [OK] Created {param_key} = {param_value} ({description})")

conn.commit()

# 验证结果
cursor.execute("""
    SELECT param_key, param_value, param_type, description, updated_at
    FROM adaptive_params
    WHERE param_type = 'global'
    ORDER BY param_key
""")

results = cursor.fetchall()

print("\n" + "="*80)
print("Global parameters configured:")
print("="*80)
for row in results:
    print(f"  {row['param_key']:25s} = {float(row['param_value'])*100:6.2f}% ({row['description']})")
    print(f"    Updated: {row['updated_at']}")

print("\n[SUCCESS] Global parameters initialized!")

cursor.close()
conn.close()
