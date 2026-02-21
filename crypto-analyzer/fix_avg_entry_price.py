#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将entry_price复制到avg_entry_price（临时修复）"""
import pymysql
import sys
import io
from datetime import datetime
from dotenv import load_dotenv
import os

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'),
    cursorclass=pymysql.cursors.DictCursor
)

cursor = conn.cursor()

print("=" * 100)
print(f"修复avg_entry_price - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 100)
print()

# 查询所有avg_entry_price为NULL但entry_price不为NULL的持仓
cursor.execute("""
    SELECT id, symbol, position_side, entry_price, avg_entry_price
    FROM futures_positions
    WHERE status = 'open'
    AND entry_price IS NOT NULL
    AND (avg_entry_price IS NULL OR avg_entry_price = 0)
""")

positions = cursor.fetchall()

if not positions:
    print("✅ 没有需要修复的持仓")
    cursor.close()
    conn.close()
    sys.exit(0)

print(f"📋 发现 {len(positions)} 个持仓需要修复")
print()

for pos in positions:
    print(f"  ID {pos['id']} | {pos['symbol']} {pos['position_side']} | "
          f"entry_price={pos['entry_price']}")

print()
print(f"将 entry_price 复制到 avg_entry_price...")
print()

# 更新avg_entry_price
cursor.execute("""
    UPDATE futures_positions
    SET avg_entry_price = entry_price,
        updated_at = NOW()
    WHERE status = 'open'
    AND entry_price IS NOT NULL
    AND (avg_entry_price IS NULL OR avg_entry_price = 0)
""")

updated_count = cursor.rowcount
conn.commit()

print(f"✅ 成功更新 {updated_count} 个持仓")
print()

# 验证
cursor.execute("""
    SELECT id, symbol, entry_price, avg_entry_price
    FROM futures_positions
    WHERE status = 'open'
    AND (avg_entry_price IS NULL OR avg_entry_price = 0)
""")

remaining = cursor.fetchall()

if remaining:
    print(f"⚠️  仍有 {len(remaining)} 个持仓的avg_entry_price为空")
else:
    print("✅ 验证成功：所有开仓持仓的avg_entry_price都已设置")

print()
print("=" * 100)
print("💡 提示：这只是临时修复，建议重启服务使用新代码（不再依赖avg_entry_price）")
print("=" * 100)

cursor.close()
conn.close()
