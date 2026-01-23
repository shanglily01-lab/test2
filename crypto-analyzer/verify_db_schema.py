#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证数据库Schema更新状态"""

import pymysql

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

conn = pymysql.connect(**db_config)
cursor = conn.cursor()

print('=' * 80)
print('检查新增的表')
print('=' * 80)
tables = ['trading_symbol_rating', 'symbol_volatility_profile', 'optimization_logs']
for table in tables:
    cursor.execute(f"SHOW TABLES LIKE '{table}'")
    result = cursor.fetchone()
    if result:
        print(f'OK {table} - 表已创建')
        cursor.execute(f'SELECT COUNT(*) FROM {table}')
        count = cursor.fetchone()[0]
        print(f'   当前记录数: {count}')
    else:
        print(f'FAIL {table} - 表不存在')

print('\n' + '=' * 80)
print('检查futures_positions表的新字段')
print('=' * 80)
cursor.execute('SHOW COLUMNS FROM futures_positions LIKE "max_hold_minutes"')
if cursor.fetchone():
    print('OK max_hold_minutes - 字段已添加')
else:
    print('FAIL max_hold_minutes - 字段不存在')

cursor.execute('SHOW COLUMNS FROM futures_positions LIKE "timeout_at"')
if cursor.fetchone():
    print('OK timeout_at - 字段已添加')
else:
    print('FAIL timeout_at - 字段不存在')

cursor.execute('SHOW COLUMNS FROM futures_positions LIKE "entry_score"')
if cursor.fetchone():
    print('OK entry_score - 字段已添加')
else:
    print('FAIL entry_score - 字段不存在')

print('\n' + '=' * 80)
print('检查adaptive_params新增的参数')
print('=' * 80)

# 问题1: 超时参数
cursor.execute('SELECT COUNT(*) FROM adaptive_params WHERE param_type="timeout"')
timeout_count = cursor.fetchone()[0]
print(f'问题1 - 动态超时参数: {timeout_count} 个')

# 问题2: 黑名单参数
cursor.execute('SELECT COUNT(*) FROM adaptive_params WHERE param_type="blacklist"')
blacklist_count = cursor.fetchone()[0]
print(f'问题2 - 黑名单参数: {blacklist_count} 个')

# 问题3: 对冲参数
cursor.execute('SELECT COUNT(*) FROM adaptive_params WHERE param_type="hedge"')
hedge_count = cursor.fetchone()[0]
print(f'问题3 - 对冲优化参数: {hedge_count} 个')

cursor.execute('SELECT param_key, param_value FROM adaptive_params WHERE param_type="hedge"')
hedge_params = cursor.fetchall()
for param in hedge_params:
    print(f'  {param[0]} = {param[1]}')

# 问题4: 止盈参数
cursor.execute('SELECT COUNT(*) FROM adaptive_params WHERE param_type="take_profit"')
tp_count = cursor.fetchone()[0]
print(f'问题4 - 动态止盈参数: {tp_count} 个')

print(f'\n总计新增参数: {timeout_count + blacklist_count + hedge_count + tp_count} 个')

print('\n' + '=' * 80)
print('数据库Schema验证完成')
print('=' * 80)

cursor.close()
conn.close()
