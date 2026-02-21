#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""删除18:59-19:11的持仓数据"""
import pymysql
import sys
import io
from datetime import datetime
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
print(f"删除18:59-19:11的持仓数据 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 100)
print()

# 时间范围 (UTC时间，数据库时间-8小时)
# 北京时间 18:59:48 = UTC 10:59:48
# 北京时间 19:11:14 = UTC 11:11:14
start_time = datetime(2026, 2, 21, 10, 59, 48)
end_time = datetime(2026, 2, 21, 11, 11, 14)

print(f"⏰ 删除时间范围:")
print(f"   UTC: {start_time} 到 {end_time}")
print(f"   北京时间: 2026-02-21 18:59:48 到 19:11:14")
print()

# 预览要删除的数据
print("📋 预览将要删除的持仓:")
print("-" * 100)

cursor.execute("""
    SELECT id, symbol, position_side, entry_price, quantity, margin, created_at
    FROM futures_positions
    WHERE created_at >= %s AND created_at <= %s
    ORDER BY created_at DESC
""", (start_time, end_time))

positions = cursor.fetchall()

if not positions:
    print("✅ 没有找到匹配的持仓")
    cursor.close()
    conn.close()
    sys.exit(0)

print(f"找到 {len(positions)} 个持仓:")
print()
for pos in positions:
    print(f"  ID {pos['id']:5d} | {pos['symbol']:20s} {pos['position_side']:5s} | "
          f"价格: {float(pos['entry_price']):10.6f} | 数量: {float(pos['quantity']):15.8f} | "
          f"保证金: ${float(pos['margin']):6.2f} | {pos['created_at']}")

print()

# 统计关联数据
cursor.execute("""
    SELECT COUNT(*) as count
    FROM futures_orders
    WHERE position_id IN (
        SELECT id FROM futures_positions
        WHERE created_at >= %s AND created_at <= %s
    )
""", (start_time, end_time))
orders_count = cursor.fetchone()['count']

cursor.execute("""
    SELECT COUNT(*) as count
    FROM futures_trades
    WHERE position_id IN (
        SELECT id FROM futures_positions
        WHERE created_at >= %s AND created_at <= %s
    )
""", (start_time, end_time))
trades_count = cursor.fetchone()['count']

print(f"📊 关联数据:")
print(f"  • futures_orders: {orders_count} 条")
print(f"  • futures_trades: {trades_count} 条")
print()

total_count = len(positions) + orders_count + trades_count
print(f"📊 总计: {total_count} 条记录")
print()

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

# 删除数据（按依赖顺序）
try:
    # 1. 删除 futures_trades
    cursor.execute("""
        DELETE FROM futures_trades
        WHERE position_id IN (
            SELECT id FROM futures_positions
            WHERE created_at >= %s AND created_at <= %s
        )
    """, (start_time, end_time))
    trades_deleted = cursor.rowcount
    print(f"  ✓ futures_trades: 删除 {trades_deleted} 条")

    # 2. 删除 futures_orders
    cursor.execute("""
        DELETE FROM futures_orders
        WHERE position_id IN (
            SELECT id FROM futures_positions
            WHERE created_at >= %s AND created_at <= %s
        )
    """, (start_time, end_time))
    orders_deleted = cursor.rowcount
    print(f"  ✓ futures_orders: 删除 {orders_deleted} 条")

    # 3. 删除 futures_positions
    cursor.execute("""
        DELETE FROM futures_positions
        WHERE created_at >= %s AND created_at <= %s
    """, (start_time, end_time))
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
cursor.execute("""
    SELECT COUNT(*) as count
    FROM futures_positions
    WHERE created_at >= %s AND created_at <= %s
""", (start_time, end_time))
remaining = cursor.fetchone()['count']

print(f"🔍 验证: 剩余 {remaining} 条持仓 {'✅' if remaining == 0 else '⚠️'}")
print()
print("=" * 100)

cursor.close()
conn.close()
