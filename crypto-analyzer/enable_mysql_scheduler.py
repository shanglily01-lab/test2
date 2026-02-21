#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启用MySQL事件调度器"""
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
    cursorclass=pymysql.cursors.DictCursor
)

cursor = conn.cursor()

print("=" * 100)
print("启用MySQL事件调度器")
print("=" * 100)
print()

# 检查当前状态
cursor.execute("SHOW VARIABLES LIKE 'event_scheduler'")
result = cursor.fetchone()
current_status = result['Value'] if result else 'UNKNOWN'
print(f"📋 当前状态: {current_status}")
print()

if current_status == 'ON':
    print("✅ 事件调度器已经是启用状态，无需操作")
else:
    print("🔧 正在启用事件调度器...")
    try:
        cursor.execute("SET GLOBAL event_scheduler = ON")
        print("✅ 事件调度器已启用")

        # 验证
        cursor.execute("SHOW VARIABLES LIKE 'event_scheduler'")
        result = cursor.fetchone()
        new_status = result['Value'] if result else 'UNKNOWN'
        print(f"📋 新状态: {new_status}")

    except Exception as e:
        print(f"❌ 启用失败: {e}")
        print()
        print("💡 提示: 如果权限不足，需要在服务器上手动执行:")
        print("   mysql> SET GLOBAL event_scheduler = ON;")

print()
print("=" * 100)
print("💡 提示:")
print("  1. 事件调度器启用后，所有ENABLED状态的事件会自动执行")
print("  2. update_coin_scores_every_5min 会每5分钟执行一次")
print("  3. 执行日志会记录在: /var/log/mariadb/mariadb.log")
print("  4. 重启MySQL服务后需要重新启用调度器（或在my.cnf中配置）")
print("=" * 100)

cursor.close()
conn.close()
