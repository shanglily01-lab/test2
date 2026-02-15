#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查分批建仓订单"""
import sys
import os
from datetime import datetime, timedelta
import pymysql
from dotenv import load_dotenv

# 设置UTF-8输出
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 加载环境变量
load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data')
}

conn = pymysql.connect(**db_config)
cursor = conn.cursor(pymysql.cursors.DictCursor)

print('='*100)
print('📊 分批建仓订单检查报告')
print('='*100)

# 1. 查询V2分批建仓订单
print('\n【1】V2 K线回调订单（kline_pullback_v2）')
print('-'*100)
cursor.execute("""
    SELECT id, symbol, position_side, entry_price, quantity, margin,
           entry_signal_type, status, open_time,
           batch_plan, batch_filled
    FROM futures_positions
    WHERE entry_signal_type = 'kline_pullback_v2'
    ORDER BY open_time DESC
    LIMIT 10
""")

v2_orders = cursor.fetchall()
print(f'找到 {len(v2_orders)} 个V2订单')
if v2_orders:
    for order in v2_orders:
        batch_status = order.get('status', 'N/A')
        print(f"  ID={order['id']:4d} {order['symbol']:15s} {order['position_side']:5s} "
              f"状态={batch_status:10s} 入场价={order['entry_price']:10.4f} "
              f"数量={order['quantity']:8.2f} 时间={order['open_time']}")

        # 显示batch详情
        if order.get('batch_filled'):
            import json
            try:
                batch_filled = json.loads(order['batch_filled'])
                filled_count = len(batch_filled.get('batches', []))
                print(f"       已完成批次: {filled_count}/3")
            except:
                pass
else:
    print('  ❌ 没有找到任何V2 K线回调订单')

# 2. 查询所有building状态的订单（未完成分批建仓）
print('\n【2】building状态的订单（未完成分批建仓）')
print('-'*100)
cursor.execute("""
    SELECT id, symbol, position_side, entry_signal_type, status, open_time, entry_signal_time
    FROM futures_positions
    WHERE status = 'building'
    ORDER BY open_time DESC
    LIMIT 10
""")

building_orders = cursor.fetchall()
print(f'找到 {len(building_orders)} 个building状态订单')
if building_orders:
    for order in building_orders:
        pos_type = order.get('entry_signal_type', 'N/A')
        signal_time = order.get('entry_signal_time', 'N/A')
        print(f"  ID={order['id']:4d} {order['symbol']:15s} {order['position_side']:5s} "
              f"类型={pos_type:20s} 信号时间={signal_time}")
else:
    print('  ✅ 没有building状态的订单')

# 3. 查询最近3小时的所有订单
print('\n【3】最近3小时的所有订单')
print('-'*100)
three_hours_ago = (datetime.now() - timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S')
cursor.execute("""
    SELECT id, symbol, position_side, entry_signal_type, status, open_time
    FROM futures_positions
    WHERE open_time >= %s
    ORDER BY open_time DESC
    LIMIT 30
""", (three_hours_ago,))

recent_orders = cursor.fetchall()
print(f'找到 {len(recent_orders)} 个最近3小时的订单')
if recent_orders:
    for order in recent_orders:
        batch_status = order.get('status') or 'N/A'
        pos_type = order.get('entry_signal_type') or 'N/A'
        print(f"  ID={order['id']:4d} {order['symbol']:15s} {order['position_side']:5s} "
              f"类型={pos_type:20s} 状态={batch_status:10s} 时间={order['open_time']}")
else:
    print('  ❌ 最近3小时没有任何新订单')

# 4. 检查V1分批建仓订单（price_percentile）
print('\n【4】V1价格分位数订单（用于对比）')
print('-'*100)
cursor.execute("""
    SELECT COUNT(*) as count
    FROM futures_positions
    WHERE source = 'smart_trader_batch' AND entry_signal_type != 'kline_pullback_v2'
""")
v1_count = cursor.fetchone()
print(f'找到 {v1_count["count"]} 个V1分批建仓订单（非V2）')

cursor.close()
conn.close()

print('\n' + '='*100)
print('检查完成！')
print('='*100)
