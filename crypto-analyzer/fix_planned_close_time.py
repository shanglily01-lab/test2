#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复现有持仓的planned_close_time"""
import pymysql
import sys
import io
from datetime import datetime
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
print(f"修复现有持仓的planned_close_time - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 100)
print()

# 查询所有planned_close_time为NULL的开仓持仓
cursor.execute("""
    SELECT
        id,
        symbol,
        position_side,
        created_at,
        max_hold_minutes,
        account_id
    FROM futures_positions
    WHERE status = 'open'
    AND planned_close_time IS NULL
    ORDER BY account_id, created_at
""")

positions = cursor.fetchall()

if not positions:
    print("✅ 没有需要修复的持仓（所有持仓都已有planned_close_time）")
else:
    print(f"📋 发现 {len(positions)} 个持仓需要修复planned_close_time")
    print()

    u_margined_count = sum(1 for p in positions if p['account_id'] == 2)
    coin_margined_count = sum(1 for p in positions if p['account_id'] == 3)

    print(f"  • U本位期货 (account_id=2): {u_margined_count} 个")
    print(f"  • 币本位期货 (account_id=3): {coin_margined_count} 个")
    print()

    # 更新每个持仓的planned_close_time
    updated_count = 0
    for pos in positions:
        # 计算planned_close_time = created_at + max_hold_minutes
        cursor.execute("""
            UPDATE futures_positions
            SET planned_close_time = DATE_ADD(created_at, INTERVAL max_hold_minutes MINUTE),
                updated_at = NOW()
            WHERE id = %s
        """, (pos['id'],))

        updated_count += 1

        if updated_count <= 5:  # 只显示前5个
            print(f"  ✓ ID {pos['id']} | {pos['symbol']} {pos['position_side']} | "
                  f"持仓时长: {pos['max_hold_minutes']}分钟")

    conn.commit()

    print()
    print(f"✅ 成功更新 {updated_count} 个持仓的planned_close_time")
    print()

# 验证更新结果
cursor.execute("""
    SELECT
        account_id,
        COUNT(*) as total,
        SUM(CASE WHEN planned_close_time IS NULL THEN 1 ELSE 0 END) as null_count,
        SUM(CASE WHEN planned_close_time IS NOT NULL THEN 1 ELSE 0 END) as set_count
    FROM futures_positions
    WHERE status = 'open'
    GROUP BY account_id
""")

result = cursor.fetchall()

print("📊 验证结果:")
print("-" * 80)
for row in result:
    account_type = "U本位期货" if row['account_id'] == 2 else "币本位期货"
    print(f"  {account_type}: 总计 {row['total']} 个持仓")
    print(f"    - planned_close_time 已设置: {row['set_count']} 个 ✅")
    print(f"    - planned_close_time 为NULL: {row['null_count']} 个 {'❌' if row['null_count'] > 0 else ''}")
    print()

# 检查即将到期的持仓（30分钟内）
cursor.execute("""
    SELECT
        id,
        symbol,
        position_side,
        account_id,
        planned_close_time,
        TIMESTAMPDIFF(MINUTE, NOW(), planned_close_time) as minutes_until_close
    FROM futures_positions
    WHERE status = 'open'
    AND planned_close_time IS NOT NULL
    AND planned_close_time <= DATE_ADD(NOW(), INTERVAL 30 MINUTE)
    ORDER BY planned_close_time ASC
    LIMIT 10
""")

upcoming_closes = cursor.fetchall()

if upcoming_closes:
    print("⏰ 即将进入智能平仓监控的持仓（30分钟内）:")
    print("-" * 80)
    for pos in upcoming_closes:
        account_type = "U本位" if pos['account_id'] == 2 else "币本位"
        minutes = pos['minutes_until_close']
        if minutes < 0:
            time_str = f"超时 {abs(minutes)} 分钟 ⚠️"
        else:
            time_str = f"还有 {minutes} 分钟"

        print(f"  ID {pos['id']} | {account_type} | {pos['symbol']} {pos['position_side']} | "
              f"计划平仓: {pos['planned_close_time'].strftime('%H:%M')} | {time_str}")
else:
    print("ℹ️  暂时没有即将进入智能平仓监控的持仓")

print()
print("=" * 100)
print("💡 下一步:")
print("  1. SmartExitOptimizer会在计划平仓时间前30分钟开始监控")
print("  2. 监控期间会根据盈亏情况智能决定是否提前平仓")
print("  3. 确保 smart_trader_service.py 正在运行")
print("  4. 可以通过 check_monitoring_status.py 检查监控状态")
print("=" * 100)

cursor.close()
conn.close()
