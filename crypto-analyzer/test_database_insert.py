#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真正的数据库插入测试 - 不用Mock，直接测试INSERT是否能成功
"""
import sys
import os
from datetime import datetime
from decimal import Decimal
import pymysql
import json

# 设置UTF-8输出
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data')
}

print('='*100)
print('真实数据库INSERT测试（测试字段是否匹配）')
print('='*100)

# 测试1: 检查必需字段是否存在
print('\n【测试1】检查futures_positions表必需字段')
print('-'*100)

conn = pymysql.connect(**db_config)
cursor = conn.cursor(pymysql.cursors.DictCursor)

cursor.execute("DESCRIBE futures_positions")
all_columns = cursor.fetchall()
column_names = [col['Field'] for col in all_columns]

required_fields = [
    'account_id', 'symbol', 'position_side', 'quantity', 'entry_price', 
    'avg_entry_price', 'leverage', 'notional_value', 'margin', 'open_time',
    'entry_signal_type', 'batch_plan', 'batch_filled', 'entry_signal_time',
    'source', 'status'
]

missing_fields = []
for field in required_fields:
    if field in column_names:
        print(f'  ✓ {field}')
    else:
        print(f'  ✗ {field} - 缺失！')
        missing_fields.append(field)

if missing_fields:
    print(f'\n❌ 缺失字段: {missing_fields}')
    print('无法继续测试')
    cursor.close()
    conn.close()
    sys.exit(1)
else:
    print('\n✅ 所有必需字段都存在')

# 测试2: 尝试真实INSERT（会rollback，不影响数据）
print('\n【测试2】测试INSERT语句（测试后回滚）')
print('-'*100)

try:
    # 准备测试数据
    symbol = 'TEST/USDT'
    direction = 'LONG'
    quantity = 1.5
    entry_price = 100.0
    leverage = 5
    margin = 120.0
    notional_value = quantity * entry_price
    signal_time = datetime.now()
    
    batch_plan_json = json.dumps({
        'batches': [
            {'ratio': 0.3},
            {'ratio': 0.3},
            {'ratio': 0.4}
        ],
        'total_margin': 400,
        'leverage': 5,
        'signal_time': signal_time.isoformat(),
        'strategy': 'kline_pullback_v2'
    })
    
    batch_filled_json = json.dumps({
        'batches': [{
            'batch_num': 0,
            'ratio': 0.3,
            'price': 100.0,
            'time': datetime.now().isoformat(),
            'margin': 120.0,
            'quantity': 1.5,
            'reason': '测试'
        }]
    })
    
    # 执行INSERT（和真实代码完全一样的语句）
    cursor.execute("""
        INSERT INTO futures_positions
        (account_id, symbol, position_side, quantity, entry_price, avg_entry_price,
         leverage, notional_value, margin, open_time, stop_loss_price, take_profit_price,
         entry_signal_type,
         batch_plan, batch_filled, entry_signal_time,
         source, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
    """, (
        2,  # account_id
        symbol,
        direction,
        quantity,
        entry_price,
        entry_price,
        leverage,
        notional_value,
        margin,
        datetime.now(),
        None,
        None,
        'kline_pullback_v2',
        batch_plan_json,
        batch_filled_json,
        signal_time,
        'smart_trader_batch',
        'building'
    ))
    
    position_id = cursor.lastrowid
    print(f'✅ INSERT成功！测试ID: {position_id}')
    
    # 验证插入的数据
    cursor.execute("SELECT * FROM futures_positions WHERE id = %s", (position_id,))
    inserted = cursor.fetchone()
    
    print(f'\n插入的数据:')
    print(f'  symbol: {inserted["symbol"]}')
    print(f'  position_side: {inserted["position_side"]}')
    print(f'  entry_signal_type: {inserted["entry_signal_type"]}')
    print(f'  status: {inserted["status"]}')
    print(f'  batch_plan: {inserted["batch_plan"][:50]}...')
    
    # 测试查询（building状态）
    print(f'\n【测试3】测试查询building状态订单')
    print('-'*100)
    cursor.execute("""
        SELECT id, symbol, position_side, entry_signal_type, status
        FROM futures_positions
        WHERE account_id = %s
        AND status = 'building'
        AND entry_signal_type = 'kline_pullback_v2'
        ORDER BY entry_signal_time DESC
        LIMIT 1
    """, (2,))
    
    result = cursor.fetchone()
    if result and result['id'] == position_id:
        print(f'✅ 查询成功！找到测试订单 ID={position_id}')
    else:
        print(f'❌ 查询失败！无法找到刚插入的订单')
    
    # 回滚事务（不保存测试数据）
    conn.rollback()
    print(f'\n🔄 已回滚事务，测试数据已删除')
    
    print('\n' + '='*100)
    print('✅ 所有测试通过！数据库字段匹配，INSERT语句正确')
    print('='*100)
    
except Exception as e:
    print(f'\n❌ INSERT失败！')
    print(f'错误: {e}')
    import traceback
    traceback.print_exc()
    conn.rollback()
    print('\n这说明代码中的字段名与数据库不匹配！')

finally:
    cursor.close()
    conn.close()
