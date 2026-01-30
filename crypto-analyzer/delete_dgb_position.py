#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""删除指定持仓(如DGB或其他有问题的持仓)"""

import pymysql
import sys
import io
from dotenv import load_dotenv
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

# 要删除或关闭的交易对列表
SYMBOLS_TO_HANDLE = ['DOT/USD', 'ADA/USD']

conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME')
)

cursor = conn.cursor(pymysql.cursors.DictCursor)

print('=' * 100)
print(f'处理问题持仓: {", ".join(SYMBOLS_TO_HANDLE)}')
print('=' * 100)
print()

try:
    for symbol in SYMBOLS_TO_HANDLE:
        print(f'🔍 检查 {symbol}:')
        print('-' * 100)

        # 查找该交易对的所有持仓
        cursor.execute("""
            SELECT id, account_id, symbol, position_side, quantity,
                   entry_price, unrealized_pnl, status, open_time
            FROM futures_positions
            WHERE symbol = %s
            ORDER BY open_time DESC
        """, (symbol,))

        positions = cursor.fetchall()

        if not positions:
            print(f'  ✓ 未找到 {symbol} 的持仓\n')
            continue

        print(f'  找到 {len(positions)} 个持仓:\n')

        for pos in positions:
            acc_name = 'U本位' if pos['account_id'] == 2 else '币本位' if pos['account_id'] == 3 else f'账户{pos["account_id"]}'
            status_emoji = '🟢' if pos['status'] == 'open' else '⚪'

            print(f'  {status_emoji} ID:{pos["id"]} | {acc_name} | {pos["position_side"]:<5} | '
                  f'qty:{float(pos["quantity"]):>10.4f} | '
                  f'价格:${float(pos["entry_price"]):>8.2f} | '
                  f'状态:{pos["status"]}')

            if pos['status'] == 'open':
                print(f'     🔴 持仓开启中 - 可能导致价格获取失败')

        print()

        # 询问操作
        open_positions = [p for p in positions if p['status'] == 'open']

        if open_positions:
            print(f'⚠️ 处理方案:')
            print(f'  1. 将持仓状态改为"closed" (不影响实际交易,仅停止监控)')
            print(f'  2. 删除持仓记录 (谨慎,会丢失历史数据)')
            print(f'  3. 跳过,不处理')
            print()

            # 自动选择方案1 (最安全)
            choice = '1'
            print(f'选择: 方案{choice} - 标记为已关闭')
            print()

            if choice == '1':
                for pos in open_positions:
                    cursor.execute("""
                        UPDATE futures_positions
                        SET status = 'closed',
                            close_time = NOW(),
                            realized_pnl = unrealized_pnl,
                            notes = CONCAT(IFNULL(notes, ''), ' | 系统自动关闭(价格获取失败)')
                        WHERE id = %s
                    """, (pos['id'],))

                    print(f'  ✓ ID:{pos["id"]} 已标记为关闭')

                conn.commit()
                print(f'  ✓ 已提交更改\n')

            elif choice == '2':
                for pos in open_positions:
                    cursor.execute("DELETE FROM futures_positions WHERE id = %s", (pos['id'],))
                    print(f'  ✓ ID:{pos["id"]} 已删除')

                conn.commit()
                print(f'  ✓ 已提交更改\n')

            else:
                print(f'  跳过处理\n')

    print('=' * 100)
    print('✅ 处理完成')
    print('=' * 100)

except Exception as e:
    print(f'✗ 处理失败: {e}')
    import traceback
    traceback.print_exc()
    conn.rollback()
finally:
    cursor.close()
    conn.close()
