#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查SmartExitOptimizer监控状态"""
import pymysql
from datetime import datetime
from dotenv import load_dotenv
import os

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
print(f"SmartExitOptimizer监控状态检查 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 100)
print()

# 1. 检查开仓持仓数量
cursor.execute("""
    SELECT
        account_id,
        COUNT(*) as count,
        SUM(margin) as total_margin
    FROM futures_positions
    WHERE status = 'open'
    GROUP BY account_id
""")

open_positions = cursor.fetchall()

if open_positions:
    print("📊 当前开仓持仓:")
    for pos in open_positions:
        print(f"  账户{pos['account_id']}: {pos['count']}个持仓，总保证金${pos['total_margin']:.2f}")
else:
    print("✅ 没有开仓持仓")

print()

# 2. 检查持仓详情
cursor.execute("""
    SELECT
        id,
        symbol,
        position_side,
        created_at,
        planned_close_time,
        TIMESTAMPDIFF(MINUTE, created_at, NOW()) as holding_minutes,
        margin,
        unrealized_pnl,
        unrealized_pnl_pct
    FROM futures_positions
    WHERE status = 'open'
    ORDER BY created_at DESC
""")

positions = cursor.fetchall()

if positions:
    print(f"📋 持仓详情 (共{len(positions)}个):")
    print("-" * 100)
    for pos in positions:
        holding_time = f"{pos['holding_minutes']}分钟" if pos['holding_minutes'] else "未知"
        planned = pos['planned_close_time'].strftime('%H:%M') if pos['planned_close_time'] else "无"
        pnl_str = f"{pos['unrealized_pnl_pct']:.2f}%" if pos['unrealized_pnl_pct'] else "N/A"

        print(f"  ID {pos['id']} | {pos['symbol']} {pos['position_side']} | "
              f"持仓{holding_time} | 计划平仓{planned} | "
              f"盈亏{pnl_str} | 保证金${pos['margin']:.2f}")

print()

# 3. 检查是否有超时未平仓的持仓
cursor.execute("""
    SELECT
        id,
        symbol,
        position_side,
        planned_close_time,
        TIMESTAMPDIFF(MINUTE, planned_close_time, NOW()) as overtime_minutes
    FROM futures_positions
    WHERE status = 'open'
    AND planned_close_time IS NOT NULL
    AND planned_close_time < NOW()
""")

overtime_positions = cursor.fetchall()

if overtime_positions:
    print(f"⚠️  超时未平仓的持仓 (共{len(overtime_positions)}个):")
    print("-" * 100)
    for pos in overtime_positions:
        print(f"  ID {pos['id']} | {pos['symbol']} {pos['position_side']} | "
              f"计划平仓: {pos['planned_close_time'].strftime('%H:%M')} | "
              f"超时{pos['overtime_minutes']}分钟 ❌")
    print()
    print("🔴 警告：这些持仓应该已经被SmartExitOptimizer平仓，但仍然是open状态！")
    print("   说明：SmartExitOptimizer监控可能没有在运行")
else:
    print("✅ 没有超时未平仓的持仓")

print()

# 4. 检查最近的平仓记录
cursor.execute("""
    SELECT
        id,
        symbol,
        position_side,
        close_reason,
        close_time,
        created_at,
        TIMESTAMPDIFF(MINUTE, created_at, close_time) as holding_minutes
    FROM futures_positions
    WHERE status = 'closed'
    AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
    ORDER BY close_time DESC
    LIMIT 10
""")

recent_closes = cursor.fetchall()

if recent_closes:
    print(f"📝 最近24小时平仓记录 (共{len(recent_closes)}个):")
    print("-" * 100)
    for pos in recent_closes:
        close_time_str = pos['close_time'].strftime('%H:%M:%S') if pos['close_time'] else "未知"
        print(f"  ID {pos['id']} | {pos['symbol']} {pos['position_side']} | "
              f"平仓时间{close_time_str} | 持仓{pos['holding_minutes']}分钟 | "
              f"原因: {pos['close_reason']}")
else:
    print("ℹ️  最近24小时没有平仓记录")

print()
print("=" * 100)

# 5. 诊断建议
if overtime_positions:
    print()
    print("💡 诊断建议:")
    print("  1. 检查 smart_trader_service.py 是否在运行:")
    print("     ps aux | grep smart_trader")
    print()
    print("  2. 如果在运行，检查日志中是否有错误:")
    print("     tail -100 logs/smart_trader.log | grep -E 'ERROR|监控|SmartExit'")
    print()
    print("  3. 如果没在运行，启动服务:")
    print("     nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &")
    print()

cursor.close()
conn.close()
