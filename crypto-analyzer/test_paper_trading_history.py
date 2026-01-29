#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试模拟交易历史记录功能"""

import pymysql
import sys
import io
from dotenv import load_dotenv
import os
from decimal import Decimal
from datetime import datetime

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
print('测试模拟交易历史记录功能')
print('=' * 100)
print()

# 1. 检查是否有测试账户
print('1. 检查测试账户...')
cursor.execute("SELECT * FROM paper_trading_accounts WHERE id = 1")
account = cursor.fetchone()

if account:
    print(f"   ✓ 找到账户ID=1: {account['account_name']}, 余额: ${account['current_balance']:.2f}")
else:
    print('   ✗ 账户ID=1不存在，使用第一个可用账户或创建新账户...')
    cursor.execute("SELECT * FROM paper_trading_accounts ORDER BY id LIMIT 1")
    account = cursor.fetchone()
    if account:
        print(f"   ✓ 使用账户ID={account['id']}: {account['account_name']}, 余额: ${account['current_balance']:.2f}")
    else:
        print('   ✗ 没有任何账户，请先在网页上创建模拟交易账户')
        conn.close()
        sys.exit(1)

print()

# 2. 插入测试交易记录
print('2. 插入测试交易记录...')

test_trades = [
    {
        'symbol': 'BTC/USDT',
        'side': 'BUY',
        'price': 45000.00,
        'quantity': 0.1,
        'realized_pnl': None,
        'pnl_pct': None
    },
    {
        'symbol': 'BTC/USDT',
        'side': 'SELL',
        'price': 46000.00,
        'quantity': 0.1,
        'realized_pnl': 100.00,
        'pnl_pct': 2.22
    },
    {
        'symbol': 'ETH/USDT',
        'side': 'BUY',
        'price': 2500.00,
        'quantity': 1.0,
        'realized_pnl': None,
        'pnl_pct': None
    },
    {
        'symbol': 'ETH/USDT',
        'side': 'SELL',
        'price': 2550.00,
        'quantity': 1.0,
        'realized_pnl': 50.00,
        'pnl_pct': 2.00
    }
]

for i, trade in enumerate(test_trades, 1):
    order_id = f"ORDER_TEST_{i:03d}"
    trade_id = f"TRADE_TEST_{i:03d}"

    total_amount = trade['price'] * trade['quantity']
    fee = total_amount * 0.001  # 0.1% 手续费
    cost_price = trade['price'] if trade['side'] == 'BUY' else trade['price']

    cursor.execute("""
        INSERT INTO paper_trading_trades
        (account_id, order_id, trade_id, symbol, side, price, quantity,
         total_amount, fee, fee_asset, realized_pnl, pnl_pct, cost_price, trade_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        account['id'],  # 使用找到的账户ID
        order_id,
        trade_id,
        trade['symbol'],
        trade['side'],
        trade['price'],
        trade['quantity'],
        total_amount,
        fee,
        'USDT',
        trade['realized_pnl'],
        trade['pnl_pct'],
        cost_price,
        datetime.utcnow()
    ))

    print(f"   ✓ 插入测试交易 {i}: {trade['symbol']} {trade['side']} @ ${trade['price']}")

conn.commit()
print()

# 3. 验证数据
print('3. 验证交易记录...')
cursor.execute("""
    SELECT COUNT(*) as total FROM paper_trading_trades WHERE account_id = %s
""", (account['id'],))
result = cursor.fetchone()
print(f"   总记录数: {result['total']}")

print()

# 4. 查询测试数据
print('4. 查询交易历史...')
cursor.execute("""
    SELECT
        t.trade_id,
        t.symbol,
        t.side,
        t.price,
        t.quantity,
        t.realized_pnl,
        t.pnl_pct,
        t.trade_time
    FROM paper_trading_trades t
    WHERE t.account_id = %s
    ORDER BY t.trade_time DESC
""", (account['id'],))

trades = cursor.fetchall()
if trades:
    print(f"   找到 {len(trades)} 条记录:")
    print()
    print(f"   {'交易ID':<20} {'币种':<15} {'方向':<6} {'价格':<12} {'数量':<12} {'盈亏%':<10} {'盈亏$':<12}")
    print('   ' + '-' * 100)
    for trade in trades:
        pnl_pct = trade['pnl_pct'] if trade['pnl_pct'] else 0
        realized_pnl = trade['realized_pnl'] if trade['realized_pnl'] else 0
        print(f"   {trade['trade_id']:<20} "
              f"{trade['symbol']:<15} "
              f"{trade['side']:<6} "
              f"{trade['price']:<12.2f} "
              f"{trade['quantity']:<12.6f} "
              f"{pnl_pct:<10.2f} "
              f"{realized_pnl:<12.2f}")

print()
print('=' * 100)
print('✅ 测试完成! 现在可以访问模拟交易页面查看交易历史')
print('=' * 100)
print()
print('提示:')
print('  1. 刷新模拟交易页面 (http://localhost:8000/paper-trading)')
print('  2. 点击"交易历史"标签页')
print('  3. 应该能看到4条测试交易记录')
print()

cursor.close()
conn.close()
