#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""删除17:00之后的所有持仓、订单、交易数据"""
import pymysql
import sys
import io
from datetime import datetime, date
from dotenv import load_dotenv
import os

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 检查是否有 --confirm 参数
auto_confirm = '--confirm' in sys.argv

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
print(f"删除17:00之后的数据 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 100)
print()

# 计算今天17:00的时间点（数据库使用UTC时间）
# 当地时间17:00 = UTC 09:00 (UTC+8时区)
today = date.today()
cutoff_time = datetime(today.year, today.month, today.day, 9, 0, 0)  # UTC 09:00 = 北京时间17:00

print(f"⏰ 删除时间点: 北京时间 {today} 17:00:00 (UTC {cutoff_time})")
print()

# 预览要删除的数据
print("📋 预览将要删除的数据:")
print("-" * 100)

# 1. futures_positions
cursor.execute("""
    SELECT COUNT(*) as count
    FROM futures_positions
    WHERE created_at >= %s
""", (cutoff_time,))
positions_count = cursor.fetchone()['count']
print(f"  • futures_positions: {positions_count} 条")

# 2. futures_orders
cursor.execute("""
    SELECT COUNT(*) as count
    FROM futures_orders
    WHERE created_at >= %s
""", (cutoff_time,))
orders_count = cursor.fetchone()['count']
print(f"  • futures_orders: {orders_count} 条")

# 3. futures_trades
cursor.execute("""
    SELECT COUNT(*) as count
    FROM futures_trades
    WHERE created_at >= %s
""", (cutoff_time,))
trades_count = cursor.fetchone()['count']
print(f"  • futures_trades: {trades_count} 条")

print()
total_count = positions_count + orders_count + trades_count
print(f"📊 总计: {total_count} 条记录")
print()

if total_count == 0:
    print("✅ 没有需要删除的数据")
    cursor.close()
    conn.close()
    sys.exit(0)

# 确认删除
if not auto_confirm:
    print("⚠️  确认要删除这些数据吗？")
    print("   输入 'YES' 继续，其他任意键取消")
    confirmation = input(">>> ").strip()

    if confirmation != 'YES':
        print("❌ 已取消删除操作")
        cursor.close()
        conn.close()
        sys.exit(0)
else:
    print("✅ 自动确认模式 (--confirm)")
    print()

print()
print("🗑️  开始删除...")
print()

# 删除数据（按依赖顺序：先删除trades和orders，最后删除positions）
try:
    # 1. 删除 futures_trades
    cursor.execute("""
        DELETE FROM futures_trades
        WHERE created_at >= %s
    """, (cutoff_time,))
    trades_deleted = cursor.rowcount
    print(f"  ✓ futures_trades: 删除 {trades_deleted} 条")

    # 2. 删除 futures_orders
    cursor.execute("""
        DELETE FROM futures_orders
        WHERE created_at >= %s
    """, (cutoff_time,))
    orders_deleted = cursor.rowcount
    print(f"  ✓ futures_orders: 删除 {orders_deleted} 条")

    # 3. 删除 futures_positions
    cursor.execute("""
        DELETE FROM futures_positions
        WHERE created_at >= %s
    """, (cutoff_time,))
    positions_deleted = cursor.rowcount
    print(f"  ✓ futures_positions: 删除 {positions_deleted} 条")

    # 提交事务
    conn.commit()

    print()
    print(f"✅ 删除完成！共删除 {trades_deleted + orders_deleted + positions_deleted} 条记录")
    print()

except Exception as e:
    conn.rollback()
    print()
    print(f"❌ 删除失败: {e}")
    print()
    cursor.close()
    conn.close()
    sys.exit(1)

# 验证删除结果
print("🔍 验证删除结果:")
print("-" * 100)

cursor.execute("""
    SELECT COUNT(*) as count
    FROM futures_positions
    WHERE created_at >= %s
""", (cutoff_time,))
remaining_positions = cursor.fetchone()['count']

cursor.execute("""
    SELECT COUNT(*) as count
    FROM futures_orders
    WHERE created_at >= %s
""", (cutoff_time,))
remaining_orders = cursor.fetchone()['count']

cursor.execute("""
    SELECT COUNT(*) as count
    FROM futures_trades
    WHERE created_at >= %s
""", (cutoff_time,))
remaining_trades = cursor.fetchone()['count']

print(f"  • futures_positions: {remaining_positions} 条 {'✅' if remaining_positions == 0 else '⚠️'}")
print(f"  • futures_orders: {remaining_orders} 条 {'✅' if remaining_orders == 0 else '⚠️'}")
print(f"  • futures_trades: {remaining_trades} 条 {'✅' if remaining_trades == 0 else '⚠️'}")

print()
print("=" * 100)

cursor.close()
conn.close()
