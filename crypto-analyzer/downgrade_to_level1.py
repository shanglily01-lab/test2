#!/usr/bin/env python3
"""
将Level 2中盈利为正的交易对降级到Level 1
给它们一个恢复的机会
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import pymysql
from dotenv import load_dotenv
import os

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST', '13.212.252.171'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'admin'),
    'password': os.getenv('DB_PASSWORD', 'Tonny@1000'),
    'database': os.getenv('DB_NAME', 'binance-data'),
    'charset': 'utf8mb4'
}

conn = pymysql.connect(**db_config)
cursor = conn.cursor()

print('=== 查找Level 2中盈利为正的交易对 ===')
cursor.execute('''
    SELECT symbol, rating_level, margin_multiplier, total_trades, win_rate,
           total_loss_amount, total_profit_amount, reason
    FROM trading_symbol_rating
    WHERE rating_level = 2
    AND (total_profit_amount - total_loss_amount) > 0
    ORDER BY (total_profit_amount - total_loss_amount) DESC
''')

profitable_symbols = cursor.fetchall()

if not profitable_symbols:
    print('没有找到盈利为正的Level 2交易对')
else:
    print(f'\n找到 {len(profitable_symbols)} 个盈利为正的Level 2交易对:')
    for row in profitable_symbols:
        symbol, level, margin, trades, wr, loss, profit, reason = row
        pnl = float(profit) - float(loss)
        print(f'{symbol:15} | {trades}笔 | 胜率{float(wr):.1%} | 盈利${pnl:.2f}')

    print(f'\n=== 开始降级到Level 1 (0.25x倍率) ===')
    downgraded_count = 0

    for row in profitable_symbols:
        symbol = row[0]
        current_level = row[1]

        cursor.execute('''
            UPDATE trading_symbol_rating
            SET rating_level = 1,
                margin_multiplier = 0.25,
                previous_level = %s,
                level_changed_at = NOW(),
                level_change_reason = '盈利为正，降级到Level 1给予恢复机会',
                updated_at = NOW()
            WHERE symbol = %s
        ''', (current_level, symbol))

        print(f'✅ 降级: {symbol} (Level {current_level} -> Level 1, 倍率 0.125x -> 0.25x)')
        downgraded_count += 1

    conn.commit()

    print(f'\n=== 执行结果 ===')
    print(f'成功降级: {downgraded_count}个交易对')
    print(f'新倍率: 0.25x (原0.125x的2倍)')

    # 验证结果
    print('\n=== 验证: 更新后的Level 1交易对 ===')
    cursor.execute('''
        SELECT symbol, rating_level, margin_multiplier, total_trades, win_rate,
               total_loss_amount, total_profit_amount
        FROM trading_symbol_rating
        WHERE rating_level = 1
        ORDER BY (total_profit_amount - total_loss_amount) DESC
        LIMIT 20
    ''')
    level1_list = cursor.fetchall()
    for row in level1_list:
        symbol, level, margin, trades, wr, loss, profit = row
        pnl = float(profit) - float(loss)
        print(f'{symbol:15} Level {level} | {float(margin)}x倍率 | {trades}笔 | 胜率{float(wr):.1%} | 盈亏${pnl:.2f}')

cursor.close()
conn.close()

print('\n✅ 完成！表现好的交易对已从Level 2降级到Level 1，保证金倍数提升至0.25x')
