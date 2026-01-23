#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检查futures_orders表中的notes字段内容
用于验证平仓订单的原因是否正确写入
"""
import pymysql
from datetime import datetime, timedelta
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data',
    'charset': 'utf8mb4'
}

try:
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    # 查询最近的平仓订单，看看notes字段的实际内容
    time_threshold = datetime.utcnow() - timedelta(hours=24)
    cursor.execute('''
        SELECT id, symbol, side, notes, fill_time, realized_pnl
        FROM futures_orders
        WHERE order_source = 'smart_trader'
        AND side IN ('SELL', 'BUY')
        AND fill_time >= %s
        ORDER BY fill_time DESC
        LIMIT 15
    ''', (time_threshold,))

    orders = cursor.fetchall()

    print('最近15条平仓订单的notes字段内容:')
    print('=' * 80)
    for order in orders:
        print(f"ID: {order['id']}")
        print(f"交易对: {order['symbol']}")
        print(f"方向: {order['side']}")
        print(f"平仓原因(notes): {repr(order['notes'])}")
        print(f"成交时间: {order['fill_time']}")
        print(f"盈亏: {order['realized_pnl']}")
        print('-' * 80)

    cursor.close()
    conn.close()

except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
