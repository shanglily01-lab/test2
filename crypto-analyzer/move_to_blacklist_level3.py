#!/usr/bin/env python3
"""
将表现极差的交易对升级到黑名单Level 3 (完全禁止交易)
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import pymysql
from dotenv import load_dotenv
import os
from datetime import datetime

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

print('=== 准备升级到黑名单Level 3 (完全禁止交易) ===\n')

# 要封禁的垃圾交易对
blacklist_level3 = [
    ('1000PEPE/USDT', 4, 1, -26.57, 25.0, '4笔订单，1赢3输，胜率25%，亏损$26.57'),
    ('TRB/USDT', 4, 1, -29.66, 25.0, '4笔订单，1赢3输，胜率25%，亏损$29.66'),
    ('TRX/USDT', 4, 1, -33.82, 25.0, '4笔订单，1赢3输，胜率25%，亏损$33.82'),
    ('LIT/USDT', 1, 0, -43.03, 0.0, '1笔订单，0赢1输，胜率0%，亏损$43.03'),
    ('FOGO/USDT', 7, 3, -89.41, 42.9, '7笔订单，3赢4输，胜率42.9%，亏损$89.41'),
    ('CHZ/USDT', 5, 0, -187.73, 0.0, '5笔订单，0赢5输，胜率0%，亏损$187.73'),
    ('DOGE/USDT', 5, 0, -202.51, 0.0, '5笔订单，0赢5输，胜率0%，亏损$202.51'),
    ('DASH/USDT', 7, 1, -279.68, 14.3, '7笔订单，1赢6输，胜率14.3%，亏损$279.68'),
]

today = datetime.now().date()
added_count = 0
updated_count = 0

for symbol, total_orders, win_orders, total_pnl, win_rate, reason in blacklist_level3:
    loss_orders = total_orders - win_orders
    total_loss = abs(total_pnl) if total_pnl < 0 else 0
    total_profit = total_pnl if total_pnl > 0 else 0

    # 检查是否已存在
    cursor.execute('SELECT id, rating_level FROM trading_symbol_rating WHERE symbol = %s', (symbol,))
    existing = cursor.fetchone()

    if existing:
        existing_id, existing_level = existing
        # 升级到Level 3
        cursor.execute('''
            UPDATE trading_symbol_rating
            SET rating_level = 3,
                margin_multiplier = 0.0,
                reason = %s,
                hard_stop_loss_count = %s,
                total_loss_amount = %s,
                total_profit_amount = %s,
                win_rate = %s,
                total_trades = %s,
                previous_level = %s,
                level_changed_at = NOW(),
                level_change_reason = '表现极差，永久封禁',
                stats_start_date = %s,
                stats_end_date = %s,
                updated_at = NOW()
            WHERE symbol = %s
        ''', (reason, loss_orders, total_loss, total_profit, win_rate/100, total_orders,
              existing_level, today, today, symbol))
        print(f'☠️  封禁: {symbol:15} (Level {existing_level} -> Level 3, 0.0x倍率)')
        print(f'     原因: {reason}')
        updated_count += 1
    else:
        # 新增Level 3
        cursor.execute('''
            INSERT INTO trading_symbol_rating (
                symbol, rating_level, margin_multiplier, score_bonus,
                reason, hard_stop_loss_count, total_loss_amount, total_profit_amount,
                win_rate, total_trades, stats_start_date, stats_end_date
            ) VALUES (
                %s, 3, 0.0, 0,
                %s, %s, %s, %s,
                %s, %s, %s, %s
            )
        ''', (symbol, reason, loss_orders, total_loss, total_profit,
              win_rate/100, total_orders, today, today))
        print(f'☠️  新增封禁: {symbol:15} (Level 3, 0.0x倍率)')
        print(f'     原因: {reason}')
        added_count += 1

conn.commit()

print(f'\n=== 执行结果 ===')
print(f'新增封禁: {added_count}个交易对')
print(f'升级封禁: {updated_count}个交易对')
print(f'总计: {added_count + updated_count}个交易对已永久封禁 (Level 3, 0.0x倍率)')

# 验证结果
print('\n=== 验证: 当前黑名单Level 3交易对 ===')
cursor.execute('''
    SELECT symbol, rating_level, margin_multiplier, total_trades, win_rate,
           total_loss_amount, reason
    FROM trading_symbol_rating
    WHERE rating_level = 3
    ORDER BY total_loss_amount DESC
    LIMIT 20
''')
level3_list = cursor.fetchall()

for row in level3_list:
    symbol, level, margin, trades, wr, loss, reason = row
    print(f'{symbol:15} | {float(margin)}x倍率 | {trades}笔 | 胜率{float(wr):.1%} | 亏损${float(loss):.2f}')

cursor.close()
conn.close()

print('\n☠️  这些垃圾交易对已被永久封禁，系统将不再交易它们！')
