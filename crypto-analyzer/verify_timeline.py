#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
import pymysql

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
cursor = conn.cursor()

print("=== 震荡市策略开仓方式变化 ===\n")

# 统计不同来源的震荡市持仓
cursor.execute("""
    SELECT 
        source,
        COUNT(*) as count,
        MIN(created_at) as earliest,
        MAX(created_at) as latest
    FROM futures_positions
    WHERE (entry_reason LIKE '%震荡市%' OR entry_reason LIKE '%布林%')
       OR (entry_signal_type LIKE '%RANGE%')
    GROUP BY source
    ORDER BY earliest DESC
""")

results = cursor.fetchall()

for row in results:
    print(f"来源: {row['source']}")
    print(f"  数量: {row['count']}")
    print(f"  最早: {row['earliest']}")
    print(f"  最新: {row['latest']}")
    print("-" * 80)

# 找到分界点
print("\n=== 查找模式切换时间点 ===\n")

cursor.execute("""
    SELECT 
        created_at,
        source,
        symbol,
        entry_signal_type
    FROM futures_positions
    WHERE (entry_reason LIKE '%震荡市%' OR entry_reason LIKE '%布林%')
       OR (entry_signal_type LIKE '%RANGE%')
    ORDER BY created_at DESC
    LIMIT 20
""")

results = cursor.fetchall()

for row in results:
    print(f"{row['created_at']} | {row['source']:20s} | {row['symbol']:15s} | {row['entry_signal_type']}")

cursor.close()
conn.close()
