#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""创建币本位合约交易账户"""

import pymysql
import sys
import io
from dotenv import load_dotenv
import os
from decimal import Decimal

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
print('创建币本位合约交易账户')
print('=' * 100)
print()

# 检查账户是否已存在
cursor.execute("""
    SELECT * FROM futures_trading_accounts WHERE user_id = 1000
""")

account = cursor.fetchone()

if account:
    print(f"✓ 账户已存在:")
    print(f"  账户ID: {account['id']}")
    print(f"  用户ID: {account['user_id']}")
    print(f"  账户名称: {account['account_name']}")
    print(f"  初始资金: ${account['initial_balance']:.2f}")
    print(f"  当前余额: ${account['current_balance']:.2f}")
    print(f"  账户类型: {account.get('account_type', 'N/A')}")
else:
    print("创建新的币本位合约账户...")

    # 创建账户
    cursor.execute("""
        INSERT INTO futures_trading_accounts
        (user_id, account_name, initial_balance, current_balance, total_profit_loss,
         win_rate, total_trades, account_type, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
    """, (
        1000,  # user_id
        '币本位合约账户',  # account_name
        100000.00,  # initial_balance
        100000.00,  # current_balance
        0.00,  # total_profit_loss
        0.00,  # win_rate
        0,  # total_trades
        'coin_futures'  # account_type
    ))

    conn.commit()

    # 获取新创建的账户
    cursor.execute("""
        SELECT * FROM futures_trading_accounts WHERE user_id = 1000
    """)

    account = cursor.fetchone()

    print(f"✓ 账户创建成功:")
    print(f"  账户ID: {account['id']}")
    print(f"  用户ID: {account['user_id']}")
    print(f"  账户名称: {account['account_name']}")
    print(f"  初始资金: ${account['initial_balance']:.2f}")
    print(f"  当前余额: ${account['current_balance']:.2f}")
    print(f"  账户类型: {account.get('account_type', 'N/A')}")

print()
print('=' * 100)

cursor.close()
conn.close()
