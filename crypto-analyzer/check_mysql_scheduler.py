#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查MySQL事件调度器状态和日志"""
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
print("MySQL 事件调度器状态检查")
print("=" * 100)
print()

# 1. 检查事件调度器是否启用
print("📋 1. 事件调度器状态:")
print("-" * 100)
cursor.execute("SHOW VARIABLES LIKE 'event_scheduler'")
result = cursor.fetchone()
if result:
    status = result['Value']
    if status == 'ON':
        print(f"  ✅ 事件调度器: {status} (已启用)")
    else:
        print(f"  ⚠️  事件调度器: {status} (未启用)")
        print(f"  提示: 使用 SET GLOBAL event_scheduler = ON; 来启用")
else:
    print("  ❌ 无法获取事件调度器状态")

print()

# 2. 查看所有事件
print("📋 2. 当前数据库的所有事件:")
print("-" * 100)
cursor.execute(f"""
    SELECT
        EVENT_NAME as 'name',
        EVENT_TYPE as 'type',
        STATUS as 'status',
        INTERVAL_VALUE as 'interval_value',
        INTERVAL_FIELD as 'interval_field',
        STARTS as 'starts',
        ENDS as 'ends',
        LAST_EXECUTED as 'last_executed',
        CREATED as 'created',
        ON_COMPLETION as 'on_completion'
    FROM information_schema.EVENTS
    WHERE EVENT_SCHEMA = '{os.getenv('DB_NAME')}'
""")

events = cursor.fetchall()
if events:
    for idx, event in enumerate(events, 1):
        print(f"\n  事件 #{idx}: {event['name']}")
        print(f"    类型: {event['type']}")
        print(f"    状态: {event['status']}")
        if event['interval_value']:
            print(f"    执行间隔: 每 {event['interval_value']} {event['interval_field']}")
        if event['starts']:
            print(f"    开始时间: {event['starts']}")
        if event['ends']:
            print(f"    结束时间: {event['ends']}")
        print(f"    最后执行: {event['last_executed'] or '从未执行'}")
        print(f"    创建时间: {event['created']}")
        print(f"    完成后: {event['on_completion']}")
else:
    print("  ℹ️  当前数据库没有配置任何事件")

print()

# 3. 查看事件详细信息（包括SQL语句）
if events:
    print("📋 3. 事件详细信息:")
    print("-" * 100)
    for event in events:
        cursor.execute(f"SHOW CREATE EVENT `{event['name']}`")
        create_info = cursor.fetchone()
        if create_info:
            print(f"\n  事件: {event['name']}")
            print(f"  创建语句:")
            # 获取创建语句的键名（可能是 'Create Event' 或其他）
            for key, value in create_info.items():
                if 'Create' in key:
                    print(f"  {value}")
                    break

print()

# 4. 检查MySQL错误日志路径
print("📋 4. MySQL错误日志位置:")
print("-" * 100)
cursor.execute("SHOW VARIABLES LIKE 'log_error'")
result = cursor.fetchone()
if result:
    log_path = result['Value']
    print(f"  错误日志路径: {log_path}")
    print(f"  提示: 事件执行错误会记录在这个文件中")
else:
    print("  ⚠️  无法获取错误日志路径")

print()

# 5. 检查general log（如果启用）
print("📋 5. General Log 状态:")
print("-" * 100)
cursor.execute("SHOW VARIABLES LIKE 'general_log'")
result = cursor.fetchone()
if result:
    if result['Value'] == 'ON':
        print(f"  ✅ General Log: {result['Value']} (已启用)")
        cursor.execute("SHOW VARIABLES LIKE 'general_log_file'")
        log_file = cursor.fetchone()
        if log_file:
            print(f"  日志文件: {log_file['Value']}")
            print(f"  提示: 所有SQL执行都会记录在这个文件中（包括事件）")
    else:
        print(f"  ⚠️  General Log: {result['Value']} (未启用)")
        print(f"  提示: 使用 SET GLOBAL general_log = 'ON'; 来启用")

print()

# 6. 查看processlist（当前正在执行的事件）
print("📋 6. 当前正在执行的事件:")
print("-" * 100)
cursor.execute("""
    SELECT
        ID,
        USER,
        HOST,
        DB,
        COMMAND,
        TIME,
        STATE,
        INFO
    FROM information_schema.PROCESSLIST
    WHERE COMMAND = 'Connect' AND USER = 'event_scheduler'
    OR INFO LIKE '%EVENT%'
""")

processes = cursor.fetchall()
if processes:
    for proc in processes:
        print(f"  进程ID: {proc['ID']}")
        print(f"  用户: {proc['USER']}")
        print(f"  命令: {proc['COMMAND']}")
        print(f"  状态: {proc['STATE']}")
        print(f"  信息: {proc['INFO']}")
        print()
else:
    print("  ℹ️  当前没有正在执行的事件")

print()
print("=" * 100)
print("💡 提示:")
print("  1. 事件执行日志会记录在MySQL错误日志中")
print("  2. 如需查看实时日志，在服务器上使用: tail -f <错误日志路径>")
print("  3. Windows上可以在MySQL Data目录下查找 .err 文件")
print("  4. 如果启用general_log，所有SQL执行都会被记录")
print("=" * 100)

cursor.close()
conn.close()
