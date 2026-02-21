#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""删除14:00之后的所有持仓、交易和订单数据"""
import pymysql
import sys
import io
from datetime import datetime, date
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

# 计算今天14:00的时间点（数据库使用UTC时间）
# 当地时间14:00 = UTC 06:00 (UTC+8时区)
today = date.today()
cutoff_time = datetime(today.year, today.month, today.day, 6, 0, 0)  # UTC 06:00 = 北京时间14:00

print("=" * 100)
print(f"删除 {cutoff_time.strftime('%Y-%m-%d %H:%M:%S')} UTC (北京时间14:00) 之后的数据")
print("=" * 100)
print()

# 1. 检查要删除的数据
print("📊 检查要删除的数据...")
print("-" * 100)

# futures_positions
cursor.execute("""
    SELECT COUNT(*) as count
    FROM futures_positions
    WHERE created_at >= %s
""", (cutoff_time,))
positions_count = cursor.fetchone()['count']
print(f"  futures_positions: {positions_count} 条记录")

# futures_trades
cursor.execute("""
    SELECT COUNT(*) as count
    FROM futures_trades
    WHERE created_at >= %s
""", (cutoff_time,))
trades_count = cursor.fetchone()['count']
print(f"  futures_trades: {trades_count} 条记录")

# futures_orders
cursor.execute("""
    SELECT COUNT(*) as count
    FROM futures_orders
    WHERE created_at >= %s
""", (cutoff_time,))
orders_count = cursor.fetchone()['count']
print(f"  futures_orders: {orders_count} 条记录")

print()
total_count = positions_count + trades_count + orders_count

if total_count == 0:
    print("✅ 没有需要删除的数据")
    cursor.close()
    conn.close()
    sys.exit(0)

print(f"⚠️  总计将删除 {total_count} 条记录")
print()

# 2. 显示部分详情
if positions_count > 0:
    print("📋 持仓记录示例（前10条）:")
    cursor.execute("""
        SELECT id, symbol, position_side, created_at, status
        FROM futures_positions
        WHERE created_at >= %s
        ORDER BY created_at DESC
        LIMIT 10
    """, (cutoff_time,))

    positions = cursor.fetchall()
    for pos in positions:
        print(f"  ID {pos['id']} | {pos['symbol']} {pos['position_side']} | "
              f"{pos['created_at'].strftime('%Y-%m-%d %H:%M:%S')} | {pos['status']}")
    print()

# 3. 确认删除
print("=" * 100)
print("⚠️  警告：此操作不可恢复！")
print(f"即将删除 {cutoff_time.strftime('%Y-%m-%d %H:%M:%S')} 之后的所有数据:")
print(f"  - futures_positions: {positions_count} 条")
print(f"  - futures_trades: {trades_count} 条")
print(f"  - futures_orders: {orders_count} 条")
print("=" * 100)
print()

response = input("确认删除？输入 'YES' 继续: ")

if response != 'YES':
    print("❌ 取消删除操作")
    cursor.close()
    conn.close()
    sys.exit(0)

print()
print("🔄 开始删除...")
print()

# 4. 执行删除
deleted_counts = {}

# 删除 futures_positions
cursor.execute("""
    DELETE FROM futures_positions
    WHERE created_at >= %s
""", (cutoff_time,))
deleted_counts['positions'] = cursor.rowcount
print(f"✓ 删除 futures_positions: {deleted_counts['positions']} 条")

# 删除 futures_trades
cursor.execute("""
    DELETE FROM futures_trades
    WHERE created_at >= %s
""", (cutoff_time,))
deleted_counts['trades'] = cursor.rowcount
print(f"✓ 删除 futures_trades: {deleted_counts['trades']} 条")

# 删除 futures_orders
cursor.execute("""
    DELETE FROM futures_orders
    WHERE created_at >= %s
""", (cutoff_time,))
deleted_counts['orders'] = cursor.rowcount
print(f"✓ 删除 futures_orders: {deleted_counts['orders']} 条")

# 提交事务
conn.commit()

print()
print("=" * 100)
print("✅ 删除完成")
print(f"  - futures_positions: {deleted_counts['positions']} 条")
print(f"  - futures_trades: {deleted_counts['trades']} 条")
print(f"  - futures_orders: {deleted_counts['orders']} 条")
print(f"  - 总计: {sum(deleted_counts.values())} 条")
print("=" * 100)

# 5. 验证结果
print()
print("📊 验证剩余数据...")

cursor.execute("""
    SELECT COUNT(*) as count
    FROM futures_positions
    WHERE created_at >= %s
""", (cutoff_time,))
remaining_positions = cursor.fetchone()['count']

cursor.execute("""
    SELECT COUNT(*) as count
    FROM futures_trades
    WHERE created_at >= %s
""", (cutoff_time,))
remaining_trades = cursor.fetchone()['count']

cursor.execute("""
    SELECT COUNT(*) as count
    FROM futures_orders
    WHERE created_at >= %s
""", (cutoff_time,))
remaining_orders = cursor.fetchone()['count']

print(f"  futures_positions (>=14:00): {remaining_positions} 条")
print(f"  futures_trades (>=14:00): {remaining_trades} 条")
print(f"  futures_orders (>=14:00): {remaining_orders} 条")

if remaining_positions == 0 and remaining_trades == 0 and remaining_orders == 0:
    print()
    print("✅ 验证成功：14:00之后的数据已全部删除")
else:
    print()
    print("⚠️  警告：仍有数据残留")

cursor.close()
conn.close()
