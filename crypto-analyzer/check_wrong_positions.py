#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查错误账户的持仓"""

import pymysql
import sys
import io
from dotenv import load_dotenv
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME')
)

cursor = conn.cursor(pymysql.cursors.DictCursor)

print('=' * 100)
print('检查可能在错误账户的持仓')
print('=' * 100)
print()

try:
    # 查找U本位账户(account_id=2)中的USD结尾交易对
    print('🔍 U本位账户(account_id=2)中的币本位持仓(USD结尾):')
    print('-' * 100)

    cursor.execute("""
        SELECT id, symbol, position_side, quantity, entry_price,
               mark_price, unrealized_pnl, status, open_time
        FROM futures_positions
        WHERE account_id = 2
        AND symbol LIKE '%/USD'
        AND status = 'open'
        ORDER BY open_time DESC
    """)

    usdt_wrong = cursor.fetchall()

    if usdt_wrong:
        print(f'找到 {len(usdt_wrong)} 个错误持仓:\n')
        for pos in usdt_wrong:
            print(f'  ❌ ID:{pos["id"]} | {pos["symbol"]:<12} | {pos["position_side"]:<5} | '
                  f'数量:{float(pos["quantity"]):>8.4f} | '
                  f'入场价:${float(pos["entry_price"]):>8.2f} | '
                  f'状态:{pos["status"]}')
            print(f'     开仓时间: {pos["open_time"]}')
            print()
    else:
        print('  ✓ 没有发现错误持仓\n')

    print('=' * 100)

    # 查找币本位账户(account_id=3)中的USDT结尾交易对
    print('🔍 币本位账户(account_id=3)中的U本位持仓(USDT结尾):')
    print('-' * 100)

    cursor.execute("""
        SELECT id, symbol, position_side, quantity, entry_price,
               mark_price, unrealized_pnl, status, open_time
        FROM futures_positions
        WHERE account_id = 3
        AND symbol LIKE '%/USDT'
        AND status = 'open'
        ORDER BY open_time DESC
    """)

    coin_wrong = cursor.fetchall()

    if coin_wrong:
        print(f'找到 {len(coin_wrong)} 个错误持仓:\n')
        for pos in coin_wrong:
            print(f'  ❌ ID:{pos["id"]} | {pos["symbol"]:<12} | {pos["position_side"]:<5} | '
                  f'数量:{float(pos["quantity"]):>8.4f} | '
                  f'入场价:${float(pos["entry_price"]):>8.2f} | '
                  f'状态:{pos["status"]}')
            print(f'     开仓时间: {pos["open_time"]}')
            print()
    else:
        print('  ✓ 没有发现错误持仓\n')

    print('=' * 100)
    print('📊 账户持仓统计')
    print('=' * 100)
    print()

    # 统计各账户的持仓分布
    cursor.execute("""
        SELECT account_id,
               COUNT(*) as total,
               SUM(CASE WHEN symbol LIKE '%/USDT' THEN 1 ELSE 0 END) as usdt_count,
               SUM(CASE WHEN symbol LIKE '%/USD' THEN 1 ELSE 0 END) as usd_count
        FROM futures_positions
        WHERE status = 'open'
        GROUP BY account_id
    """)

    stats = cursor.fetchall()

    for stat in stats:
        acc_id = stat['account_id']
        acc_name = 'U本位实盘' if acc_id == 2 else '币本位合约' if acc_id == 3 else f'未知账户({acc_id})'

        print(f'{acc_name} (account_id={acc_id}):')
        print(f'  总持仓: {stat["total"]}')
        print(f'  USDT结尾: {stat["usdt_count"]} (U本位)')
        print(f'  USD结尾: {stat["usd_count"]} (币本位)')

        if acc_id == 2 and stat["usd_count"] > 0:
            print(f'  ⚠️ 发现 {stat["usd_count"]} 个币本位持仓在U本位账户!')
        elif acc_id == 3 and stat["usdt_count"] > 0:
            print(f'  ⚠️ 发现 {stat["usdt_count"]} 个U本位持仓在币本位账户!')

        print()

except Exception as e:
    print(f'✗ 查询失败: {e}')
    import traceback
    traceback.print_exc()
finally:
    cursor.close()
    conn.close()

print('=' * 100)
