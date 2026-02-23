#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
删除今天20:00后的开单
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pymysql
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST', '13.212.252.171'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'admin'),
    'password': os.getenv('DB_PASSWORD', 'Tonny@1000'),
    'database': os.getenv('DB_NAME', 'binance-data'),
    'charset': 'utf8mb4'
}

def delete_after_2000():
    """删除今天20:00后的开单"""
    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    # 今天北京时间20:00 = UTC 12:00
    start_time = '2026-02-23 12:00:00'

    print("="*80)
    print(f"检查删除范围: UTC {start_time} 到现在")
    print(f"           (北京时间 2026-02-23 20:00 到现在)")
    print("="*80)

    # 1. 先查看要删除的记录
    cursor.execute("""
        SELECT id, symbol, position_side, entry_score,
               open_time, entry_price, notes
        FROM futures_positions
        WHERE account_id = 2
        AND open_time >= %s
        ORDER BY open_time DESC
    """, (start_time,))

    positions = cursor.fetchall()

    print(f"\n找到 {len(positions)} 个持仓记录:")
    if positions:
        for p in positions:
            print(f"\nID: {p['id']}")
            print(f"  币种: {p['symbol']}")
            print(f"  方向: {p['position_side']}")
            print(f"  评分: {p['entry_score']}")
            print(f"  开仓: {p['open_time']}")
            print(f"  价格: {p['entry_price']}")
            print(f"  备注: {p['notes']}")

    # 2. 确认删除
    if positions:
        print(f"\n{'='*80}")
        print(f"准备删除 {len(positions)} 条记录")
        print(f"{'='*80}")

        # 删除 futures_positions
        cursor.execute("""
            DELETE FROM futures_positions
            WHERE account_id = 2
            AND open_time >= %s
        """, (start_time,))
        deleted_positions = cursor.rowcount

        # 删除 futures_orders
        cursor.execute("""
            DELETE FROM futures_orders
            WHERE created_at >= %s
        """, (start_time,))
        deleted_orders = cursor.rowcount

        # 删除 futures_trades
        cursor.execute("""
            DELETE FROM futures_trades
            WHERE created_at >= %s
        """, (start_time,))
        deleted_trades = cursor.rowcount

        conn.commit()

        print(f"\n✅ 删除完成:")
        print(f"   futures_positions: {deleted_positions} 条")
        print(f"   futures_orders: {deleted_orders} 条")
        print(f"   futures_trades: {deleted_trades} 条")
        print(f"   总计: {deleted_positions + deleted_orders + deleted_trades} 条")
    else:
        print("\n✅ 没有需要删除的记录")

    print("\n" + "="*80)
    cursor.close()
    conn.close()

if __name__ == '__main__':
    delete_after_2000()
