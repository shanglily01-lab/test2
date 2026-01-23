#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修正 futures_positions 表中的 close_time 为 UTC+0
将所有已平仓记录的 close_time 减去8小时 (从UTC+8转换为UTC+0)
"""
import pymysql
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

load_dotenv()

def fix_close_time():
    """修正平仓时间为UTC+0"""

    # 数据库连接配置
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

        # 1. 查询所有已平仓的记录 (close_time 不为空)
        cursor.execute("""
            SELECT id, symbol, close_time, created_at
            FROM futures_positions
            WHERE status = 'closed' AND close_time IS NOT NULL
            ORDER BY close_time DESC
            LIMIT 100
        """)

        closed_positions = cursor.fetchall()

        if not closed_positions:
            print("没有找到已平仓的记录")
            cursor.close()
            conn.close()
            return

        print(f"找到 {len(closed_positions)} 条已平仓记录")
        print("\n修正前的时间 (前10条):")
        for i, pos in enumerate(closed_positions[:10]):
            print(f"{i+1}. ID:{pos['id']} {pos['symbol']} close_time: {pos['close_time']}")

        # 2. 将 close_time 减去8小时 (UTC+8 -> UTC+0)
        print(f"\n开始修正时间 (减去8小时)...")

        cursor.execute("""
            UPDATE futures_positions
            SET close_time = DATE_SUB(close_time, INTERVAL 8 HOUR)
            WHERE status = 'closed' AND close_time IS NOT NULL
        """)

        affected_rows = cursor.rowcount
        conn.commit()

        print(f"✅ 成功修正 {affected_rows} 条记录")

        # 3. 验证修正结果
        cursor.execute("""
            SELECT id, symbol, close_time, created_at
            FROM futures_positions
            WHERE status = 'closed' AND close_time IS NOT NULL
            ORDER BY close_time DESC
            LIMIT 10
        """)

        updated_positions = cursor.fetchall()

        print("\n修正后的时间 (前10条):")
        for i, pos in enumerate(updated_positions):
            print(f"{i+1}. ID:{pos['id']} {pos['symbol']} close_time: {pos['close_time']}")

        cursor.close()
        conn.close()

        print("\n✅ 所有 close_time 已修正为 UTC+0")

    except Exception as e:
        print(f"❌ 修正失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    print("=" * 80)
    print("修正 futures_positions 表的 close_time 为 UTC+0")
    print("=" * 80)

    # 确认操作
    confirm = input("\n[WARNING] 这将修改所有已平仓记录的 close_time (减去8小时)，是否继续? (yes/no): ")

    if confirm.lower() == 'yes':
        fix_close_time()
    else:
        print("操作已取消")
