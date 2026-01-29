#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证数据库中的已平仓交易记录"""

import pymysql
import os
from dotenv import load_dotenv

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
print('验证已平仓交易记录')
print('=' * 100)
print()

# 统计已平仓交易
cursor.execute("SELECT COUNT(*) as total FROM paper_trading_trades WHERE side = 'SELL' AND realized_pnl IS NOT NULL")
result = cursor.fetchone()
print(f"已平仓交易记录数: {result['total']}")
print()

# 查询详细记录
cursor.execute("""
    SELECT symbol, side, price, cost_price, quantity, realized_pnl, pnl_pct, trade_time
    FROM paper_trading_trades
    WHERE side = 'SELL' AND realized_pnl IS NOT NULL
    ORDER BY trade_time DESC
    LIMIT 10
""")
trades = cursor.fetchall()

if trades:
    print('已平仓交易详情:')
    print('-' * 100)
    for i, t in enumerate(trades, 1):
        print(f"\n{i}. {t['symbol']}")
        print(f"   数量: {float(t['quantity']):.6f}")
        print(f"   成本价: ${float(t['cost_price']):.2f}")
        print(f"   卖出价: ${float(t['price']):.2f}")
        print(f"   盈亏: ${float(t['realized_pnl']):+.2f} ({float(t['pnl_pct']):+.2f}%)")
        print(f"   时间: {t['trade_time']}")
else:
    print('没有已平仓交易记录')

print()
print('=' * 100)

cursor.close()
conn.close()
