#!/usr/bin/env python3
"""
修复planned_close_time为NULL的持仓
为这些持仓补充计划平仓时间
"""
import mysql.connector
from datetime import datetime, timedelta

DB_CONFIG = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def fix_null_planned_close_time():
    """修复planned_close_time为NULL的持仓"""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    # 查询所有planned_close_time为NULL的open状态持仓
    cursor.execute("""
        SELECT id, symbol, position_side, entry_score, created_at, open_time
        FROM futures_positions
        WHERE status = 'open'
        AND planned_close_time IS NULL
        ORDER BY id
    """)

    positions = cursor.fetchall()

    if not positions:
        print("OK - No positions to fix")
        cursor.close()
        conn.close()
        return

    print(f"Found {len(positions)} positions to fix:\n")

    for pos in positions:
        position_id = pos['id']
        symbol = pos['symbol']
        direction = pos['position_side']
        entry_score = pos['entry_score'] or 30
        open_time = pos['open_time'] or pos['created_at']

        # 根据entry_score计算持仓时长
        if entry_score >= 45:
            max_hold_minutes = 360  # 6小时
        elif entry_score >= 30:
            max_hold_minutes = 240  # 4小时
        else:
            max_hold_minutes = 120  # 2小时

        # 从open_time开始计算planned_close_time
        if isinstance(open_time, str):
            open_time = datetime.fromisoformat(open_time)

        planned_close_time = open_time + timedelta(minutes=max_hold_minutes)

        print(f"持仓 {position_id} ({symbol} {direction}):")
        print(f"  entry_score: {entry_score}")
        print(f"  open_time: {open_time}")
        print(f"  计算的 planned_close_time: {planned_close_time}")

        # 更新数据库
        cursor.execute("""
            UPDATE futures_positions
            SET planned_close_time = %s,
                updated_at = NOW()
            WHERE id = %s
        """, (planned_close_time, position_id))

        print(f"  OK - Updated\n")

    conn.commit()
    cursor.close()
    conn.close()

    print(f"SUCCESS - Fixed {len(positions)} positions")

if __name__ == '__main__':
    fix_null_planned_close_time()
