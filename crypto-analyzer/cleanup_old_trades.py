#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
清理 2026-01-20 之前的所有历史交易数据
包括 futures_positions 和 futures_trades
"""
import pymysql
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

def cleanup_old_trades():
    """清理1.20之前的历史交易数据"""

    cutoff_date = '2026-01-20 00:00:00'

    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', '3306')),
        'user': os.getenv('DB_USER', 'root'),
        'password': os.getenv('DB_PASSWORD', ''),
        'database': os.getenv('DB_NAME', 'binance-data'),
        'charset': 'utf8mb4'
    }

    try:
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        print(f"\n准备清理 {cutoff_date} 之前的历史交易数据...\n")

        # 1. 统计 futures_positions 中要删除的记录
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM futures_positions
            WHERE created_at < %s
        """, (cutoff_date,))
        positions_count = cursor.fetchone()['count']

        # 2. 统计 futures_trades 中要删除的记录
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM futures_trades
            WHERE created_at < %s
        """, (cutoff_date,))
        trades_count = cursor.fetchone()['count']

        print(f"发现需要删除的记录:")
        print(f"  - futures_positions: {positions_count} 条")
        print(f"  - futures_trades: {trades_count} 条")

        if positions_count == 0 and trades_count == 0:
            print("\n没有需要删除的记录")
            cursor.close()
            conn.close()
            return

        # 3. 删除 futures_positions
        if positions_count > 0:
            print(f"\n开始删除 futures_positions 中 {cutoff_date} 之前的记录...")
            cursor.execute("""
                DELETE FROM futures_positions
                WHERE created_at < %s
            """, (cutoff_date,))
            print(f"✅ 已删除 {cursor.rowcount} 条 futures_positions 记录")

        # 4. 删除 futures_trades
        if trades_count > 0:
            print(f"\n开始删除 futures_trades 中 {cutoff_date} 之前的记录...")
            cursor.execute("""
                DELETE FROM futures_trades
                WHERE created_at < %s
            """, (cutoff_date,))
            print(f"✅ 已删除 {cursor.rowcount} 条 futures_trades 记录")

        conn.commit()

        # 5. 验证删除结果
        cursor.execute("SELECT COUNT(*) as count FROM futures_positions")
        remaining_positions = cursor.fetchone()['count']

        cursor.execute("SELECT COUNT(*) as count FROM futures_trades")
        remaining_trades = cursor.fetchone()['count']

        print(f"\n清理完成!")
        print(f"剩余记录:")
        print(f"  - futures_positions: {remaining_positions} 条")
        print(f"  - futures_trades: {remaining_trades} 条")

        # 6. 显示最早的记录时间
        cursor.execute("""
            SELECT MIN(created_at) as earliest
            FROM futures_positions
        """)
        earliest_pos = cursor.fetchone()['earliest']

        cursor.execute("""
            SELECT MIN(created_at) as earliest
            FROM futures_trades
        """)
        earliest_trade = cursor.fetchone()['earliest']

        print(f"\n当前最早记录时间:")
        print(f"  - futures_positions: {earliest_pos}")
        print(f"  - futures_trades: {earliest_trade}")

        cursor.close()
        conn.close()

        print("\n✅ 所有历史数据清理完成")

    except Exception as e:
        print(f"❌ 清理失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    print("=" * 80)
    print("清理 2026-01-20 之前的历史交易数据")
    print("=" * 80)

    # 确认操作
    confirm = input("\n[WARNING] 这将永久删除 2026-01-20 之前的所有交易记录，是否继续? (yes/no): ")

    if confirm.lower() == 'yes':
        cleanup_old_trades()
    else:
        print("操作已取消")
