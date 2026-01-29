#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复信号权重配置"""

import pymysql
import sys
import io
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
    cursorclass=pymysql.cursors.DictCursor,
    charset='utf8mb4'
)

cursor = conn.cursor()

print('=' * 100)
print('修复信号权重配置')
print('=' * 100)
print()

# 修复 momentum_down_3pct 权重 (下跌应该做空)
print('1. 修复 momentum_down_3pct 权重...')
cursor.execute('''
    INSERT INTO signal_scoring_weights (signal_component, weight_long, weight_short, base_weight, is_active)
    VALUES ('momentum_down_3pct', 0, 15, 15, TRUE)
    ON DUPLICATE KEY UPDATE
        weight_long = 0,
        weight_short = 15,
        base_weight = 15,
        is_active = TRUE
''')
print('   ✓ momentum_down_3pct: LONG=0, SHORT=15 (下跌做空)')

# 修复 momentum_up_3pct 权重 (上涨应该做多)
print('2. 修复 momentum_up_3pct 权重...')
cursor.execute('''
    INSERT INTO signal_scoring_weights (signal_component, weight_long, weight_short, base_weight, is_active)
    VALUES ('momentum_up_3pct', 15, 0, 15, TRUE)
    ON DUPLICATE KEY UPDATE
        weight_long = 15,
        weight_short = 0,
        base_weight = 15,
        is_active = TRUE
''')
print('   ✓ momentum_up_3pct: LONG=15, SHORT=0 (上涨做多)')

conn.commit()

# 验证修复
print()
print('=' * 100)
print('验证修复结果')
print('=' * 100)
print()

cursor.execute('''
    SELECT signal_component, weight_long, weight_short
    FROM signal_scoring_weights
    WHERE signal_component IN ('momentum_down_3pct', 'momentum_up_3pct')
''')

weights = cursor.fetchall()
for w in weights:
    print(f"{w['signal_component']:<25} LONG={w['weight_long']:<5} SHORT={w['weight_short']:<5}")

print()
print('=' * 100)
print('✅ 权重修复完成!')
print('=' * 100)
print()
print('下一步:')
print('  1. 重启 smart_trader 服务使修复生效')
print('  2. 观察新开仓位的信号组合是否正确')
print()

cursor.close()
conn.close()
