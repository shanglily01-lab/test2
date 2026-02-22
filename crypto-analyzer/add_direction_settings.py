#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
添加交易方向控制设置
在adaptive_params表中添加允许做多/做空的配置项
"""
import pymysql
import os
import sys
from dotenv import load_dotenv

# 设置UTF-8输出
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

load_dotenv()

# 数据库配置
db_config = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'trading_user'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data'),
    'charset': 'utf8mb4'
}

# 要添加的系统设置
direction_settings = [
    ('allow_long', 1, 'system', '是否允许做多: 1=允许, 0=禁止'),
    ('allow_short', 1, 'system', '是否允许做空: 1=允许, 0=禁止'),
]

try:
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()

    print("添加交易方向控制设置...")

    for param_key, param_value, param_type, description in direction_settings:
        cursor.execute("""
            INSERT INTO adaptive_params (param_key, param_value, param_type, description, updated_by)
            VALUES (%s, %s, %s, %s, 'system')
            ON DUPLICATE KEY UPDATE
                param_value = VALUES(param_value),
                description = VALUES(description),
                updated_at = NOW()
        """, (param_key, param_value, param_type, description))
        print(f"  ✅ {param_key} = {param_value} ({description})")

    conn.commit()

    # 查询确认
    print("\n当前交易方向设置:")
    cursor.execute("""
        SELECT param_key, param_value, description
        FROM adaptive_params
        WHERE param_key IN ('allow_long', 'allow_short')
        ORDER BY param_key
    """)

    results = cursor.fetchall()
    for row in results:
        param_key, param_value, description = row
        status = "✅ 允许" if int(param_value) == 1 else "❌ 禁止"
        direction = "做多" if "long" in param_key else "做空"
        print(f"  {direction}: {status} ({description})")

    print(f"\n成功! 交易方向控制设置已添加")
    print("\n使用方法:")
    print("  - 禁止做多: UPDATE adaptive_params SET param_value = 0 WHERE param_key = 'allow_long';")
    print("  - 禁止做空: UPDATE adaptive_params SET param_value = 0 WHERE param_key = 'allow_short';")
    print("  - 启用做多: UPDATE adaptive_params SET param_value = 1 WHERE param_key = 'allow_long';")
    print("  - 启用做空: UPDATE adaptive_params SET param_value = 1 WHERE param_key = 'allow_short';")

    cursor.close()
    conn.close()

except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
