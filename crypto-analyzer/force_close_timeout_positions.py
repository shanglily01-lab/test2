#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""强制平掉超时持仓"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import pymysql
import asyncio
from datetime import datetime
from pathlib import Path

# 读取数据库配置
env_path = Path(__file__).parent / '.env'
db_config = {}
for line in open(env_path, encoding='utf-8'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        key, value = line.split('=', 1)
        if key.startswith('DB_'):
            db_key = key.replace('DB_', '').lower()
            db_config[db_key] = value

conn = pymysql.connect(
    host=db_config['host'],
    port=int(db_config.get('port', 3306)),
    user=db_config['user'],
    password=db_config['password'],
    database=db_config['name'],
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

cursor = conn.cursor()

# 查找所有超时的open持仓
cursor.execute("""
    SELECT
        id, symbol, position_side,
        TIMESTAMPDIFF(MINUTE, open_time, NOW()) as holding_minutes,
        max_hold_minutes, timeout_at,
        entry_price, quantity, margin
    FROM futures_positions
    WHERE status = 'open'
    AND account_id = 2
    AND timeout_at IS NOT NULL
    AND NOW() > timeout_at
    ORDER BY holding_minutes DESC
""")

timeout_positions = cursor.fetchall()

print(f"Found {len(timeout_positions)} timeout positions")
print("=" * 100)

if not timeout_positions:
    print("No timeout positions found")
    cursor.close()
    conn.close()
    sys.exit(0)

for pos in timeout_positions:
    print(f"ID {pos['id']}: {pos['symbol']:<12} {pos['position_side']:<6} | "
          f"Holding: {pos['holding_minutes']}m (Max: {pos['max_hold_minutes']}m)")

print("\n" + "=" * 100)
user_input = input("Confirm to close all timeout positions? (yes/no): ")

if user_input.lower() != 'yes':
    print("Cancelled")
    cursor.close()
    conn.close()
    sys.exit(0)

print("\nClosing timeout positions...")

for pos in timeout_positions:
    try:
        # 更新状态为closed
        cursor.execute("""
            UPDATE futures_positions
            SET status = 'closed',
                close_time = NOW(),
                notes = CONCAT(COALESCE(notes, ''), '
[强制平仓] 持仓超时(', %s, '分钟) 手动强制平仓'),
                updated_at = NOW()
            WHERE id = %s
        """, (pos['holding_minutes'], pos['id']))

        # 释放冻结资金
        cursor.execute("""
            UPDATE futures_trading_accounts
            SET frozen_balance = frozen_balance - %s,
                updated_at = NOW()
            WHERE id = 2
        """, (float(pos['margin']),))

        conn.commit()
        print(f"OK - Closed position {pos['id']} {pos['symbol']}")

    except Exception as e:
        conn.rollback()
        print(f"ERROR - Failed to close position {pos['id']}: {e}")

cursor.close()
conn.close()

print("\n" + "=" * 100)
print("Done")
