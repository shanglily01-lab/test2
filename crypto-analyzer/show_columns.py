#!/usr/bin/env python3
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

conn = pymysql.connect(**db_config)
cursor = conn.cursor()

cursor.execute("SHOW COLUMNS FROM futures_positions WHERE Field LIKE '%price%' OR Field LIKE '%profit%' OR Field LIKE '%loss%'")
results = cursor.fetchall()

print("价格和盈亏相关字段:")
for row in results:
    print(f"  {row[0]}")

cursor.close()
conn.close()
