#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修正 futures_trades 表中的时间字段为 UTC+0
将 trade_time 和 created_at 减去8小时 (从UTC+8转换为UTC+0)
"""
import pymysql
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

load_dotenv()

def fix_futures_trades_time():
    """修正 futures_trades 表的时间为UTC+0"""

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

        # 1. 查询最近的交易记录
        cursor.execute("""
            SELECT trade_id, symbol, side, trade_time, created_at
            FROM futures_trades
            ORDER BY trade_time DESC
            LIMIT 10
        """)

        recent_trades = cursor.fetchall()

        if not recent_trades:
            print("没有找到交易记录")
            cursor.close()
            conn.close()
            return

        print(f"找到交易记录")
        print("\n修正前的时间 (前10条):")
        for i, trade in enumerate(recent_trades):
            print(f"{i+1}. {trade['symbol']} {trade['side']} trade_time: {trade['trade_time']}, created_at: {trade['created_at']}")

        # 2. 统计需要修正的记录数
        cursor.execute("SELECT COUNT(*) as count FROM futures_trades")
        total_count = cursor.fetchone()['count']
        print(f"\n总共有 {total_count} 条交易记录")

        # 3. 将 trade_time 和 created_at 减去8小时 (UTC+8 -> UTC+0)
        print(f"\n开始修正时间 (减去8小时)...")

        # 修正 trade_time
        cursor.execute("""
            UPDATE futures_trades
            SET trade_time = DATE_SUB(trade_time, INTERVAL 8 HOUR)
            WHERE trade_time IS NOT NULL
        """)
        trade_time_affected = cursor.rowcount

        # 修正 created_at
        cursor.execute("""
            UPDATE futures_trades
            SET created_at = DATE_SUB(created_at, INTERVAL 8 HOUR)
            WHERE created_at IS NOT NULL
        """)
        created_at_affected = cursor.rowcount

        conn.commit()

        print(f"✅ 成功修正 trade_time: {trade_time_affected} 条记录")
        print(f"✅ 成功修正 created_at: {created_at_affected} 条记录")

        # 4. 验证修正结果
        cursor.execute("""
            SELECT trade_id, symbol, side, trade_time, created_at
            FROM futures_trades
            ORDER BY trade_time DESC
            LIMIT 10
        """)

        updated_trades = cursor.fetchall()

        print("\n修正后的时间 (前10条):")
        for i, trade in enumerate(updated_trades):
            print(f"{i+1}. {trade['symbol']} {trade['side']} trade_time: {trade['trade_time']}, created_at: {trade['created_at']}")

        cursor.close()
        conn.close()

        print("\n✅ 所有时间字段已修正为 UTC+0")

    except Exception as e:
        print(f"❌ 修正失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    print("=" * 80)
    print("修正 futures_trades 表的时间字段为 UTC+0")
    print("=" * 80)

    # 确认操作
    confirm = input("\n[WARNING] 这将修改所有交易记录的 trade_time 和 created_at (减去8小时)，是否继续? (yes/no): ")

    if confirm.lower() == 'yes':
        fix_futures_trades_time()
    else:
        print("操作已取消")
