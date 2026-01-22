#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""更新服务器端entry_score字段"""
import sys
import io
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'd:\test2\crypto-analyzer')

import pymysql

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data',
    'charset': 'utf8mb4'
}

conn = pymysql.connect(**db_config)
cursor = conn.cursor(pymysql.cursors.DictCursor)

print("=" * 80)
print("检查并更新 entry_score 字段")
print("=" * 80)
print()

# 1. 检查当前字段类型
print("=== 当前 entry_score 字段信息 ===")
cursor.execute("""
    SELECT
        COLUMN_NAME,
        COLUMN_TYPE,
        IS_NULLABLE,
        COLUMN_DEFAULT,
        COLUMN_COMMENT
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'binance-data'
        AND TABLE_NAME = 'futures_positions'
        AND COLUMN_NAME = 'entry_score'
""")
current_field = cursor.fetchone()

if current_field:
    print(f"字段名: {current_field['COLUMN_NAME']}")
    print(f"类型: {current_field['COLUMN_TYPE']}")
    print(f"允许NULL: {current_field['IS_NULLABLE']}")
    print(f"默认值: {current_field['COLUMN_DEFAULT']}")
    print(f"注释: {current_field['COLUMN_COMMENT']}")

    # 检查类型是否正确
    current_type = current_field['COLUMN_TYPE']
    if current_type == 'int(11)':
        print("\n✓ 字段类型正确 (int)")
        needs_update = False
    elif 'decimal' in current_type:
        print(f"\n⚠️ 字段类型是 {current_type}，应该改为 int")
        needs_update = True
    else:
        print(f"\n⚠️ 字段类型不匹配: {current_type}")
        needs_update = True

    # 如果需要更新，修改字段类型
    if needs_update:
        print("\n正在修改字段类型...")
        try:
            cursor.execute("""
                ALTER TABLE futures_positions
                MODIFY COLUMN entry_score INT(11) DEFAULT NULL COMMENT '开仓时的超级大脑评分'
            """)
            conn.commit()
            print("✓ 字段类型已更新为 INT(11)")
        except Exception as e:
            print(f"✗ 更新失败: {e}")
            conn.rollback()
else:
    print("✗ entry_score 字段不存在")

print()

# 2. 检查字段位置
print("=== 检查字段位置 ===")
cursor.execute("""
    SELECT COLUMN_NAME
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'binance-data'
        AND TABLE_NAME = 'futures_positions'
    ORDER BY ORDINAL_POSITION
""")
columns = [row['COLUMN_NAME'] for row in cursor.fetchall()]

# 找到 entry_signal_type 和 entry_score 的位置
try:
    signal_type_idx = columns.index('entry_signal_type')
    score_idx = columns.index('entry_score')

    print(f"entry_signal_type 位置: {signal_type_idx}")
    print(f"entry_score 位置: {score_idx}")

    if score_idx == signal_type_idx + 1:
        print("✓ entry_score 紧跟在 entry_signal_type 之后")
    else:
        print(f"⚠️ entry_score 不在 entry_signal_type 之后")
        print(f"  当前顺序: ...{columns[signal_type_idx]} -> {columns[signal_type_idx+1]} -> ...")
except ValueError as e:
    print(f"✗ 字段查找失败: {e}")

print()

# 3. 检查是否有数据
print("=== 检查现有数据 ===")
cursor.execute("""
    SELECT
        COUNT(*) as total,
        COUNT(entry_score) as with_score,
        MIN(entry_score) as min_score,
        MAX(entry_score) as max_score,
        AVG(entry_score) as avg_score
    FROM futures_positions
    WHERE account_id = 2
        AND entry_signal_type LIKE 'SMART_BRAIN%'
""")
stats = cursor.fetchone()

print(f"总记录数: {stats['total']}")
print(f"有评分的: {stats['with_score']}")
if stats['with_score'] > 0:
    print(f"评分范围: {stats['min_score']} - {stats['max_score']}")
    print(f"平均评分: {stats['avg_score']:.1f}")

print()

# 4. 显示最近10条记录的entry_score
print("=== 最近10条记录的entry_score ===")
cursor.execute("""
    SELECT
        id,
        symbol,
        entry_signal_type,
        entry_score,
        open_time
    FROM futures_positions
    WHERE account_id = 2
        AND entry_signal_type LIKE 'SMART_BRAIN%'
    ORDER BY id DESC
    LIMIT 10
""")
recent = cursor.fetchall()

for r in recent:
    score = r['entry_score'] if r['entry_score'] else 'NULL'
    print(f"ID {r['id']}: {r['symbol']} | {r['entry_signal_type']} | 评分: {score} | {r['open_time']}")

cursor.close()
conn.close()

print("\n" + "=" * 80)
print("检查完成")
print("=" * 80)
