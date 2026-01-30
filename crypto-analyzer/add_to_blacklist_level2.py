#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将表现不佳的交易对添加到黑名单2级"""

import pymysql
import sys
import io
from dotenv import load_dotenv
import os
from datetime import datetime, date, timedelta

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

# 要添加到黑名单2级的交易对及其统计数据
symbols_to_blacklist = [
    {
        'symbol': 'ENA/USDT',
        'total_loss': 73.12,
        'total_profit': 0.00,
        'win_rate': 0.00,
        'total_trades': 2,
        'avg_loss': -36.56,
        'reason': '2单0%胜率,亏损$-73.12,平均ROI -21.10%'
    },
    {
        'symbol': 'NOM/USDT',
        'total_loss': 74.57,
        'total_profit': 49.48,
        'win_rate': 0.25,
        'total_trades': 4,
        'avg_loss': -41.35,
        'reason': '4单25%胜率,亏损$-74.57,平均ROI -21.10%'
    },
    {
        'symbol': 'ZKC/USDT',
        'total_loss': 104.87,
        'total_profit': 22.95,
        'win_rate': 0.286,
        'total_trades': 7,
        'avg_loss': -30.15,
        'reason': '7单28.6%胜率,亏损$-104.87,平均ROI -30.15%'
    },
    {
        'symbol': 'SENT/USDT',
        'total_loss': 199.23,
        'total_profit': 0.00,
        'win_rate': 0.00,
        'total_trades': 4,
        'avg_loss': -49.81,
        'reason': '4单0%胜率,亏损$-199.23,平均ROI -49.81%'
    },
    {
        'symbol': 'RIVER/USDT',
        'total_loss': 224.24,
        'total_profit': 0.00,
        'win_rate': 0.00,
        'total_trades': 3,
        'avg_loss': -74.75,
        'reason': '3单0%胜率,亏损$-224.24,平均ROI -74.75%'
    },
]

conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME')
)

cursor = conn.cursor(pymysql.cursors.DictCursor)

print('=' * 100)
print('将表现不佳的交易对添加到黑名单2级 (trading_symbol_rating)')
print('=' * 100)
print()

try:
    # 计算统计日期范围 (最近7天)
    end_date = date.today()
    start_date = end_date - timedelta(days=7)

    for item in symbols_to_blacklist:
        symbol = item['symbol']

        # 检查是否已存在
        cursor.execute("""
            SELECT id, rating_level, margin_multiplier, level_change_reason
            FROM trading_symbol_rating
            WHERE symbol = %s
        """, (symbol,))

        existing = cursor.fetchone()

        if existing:
            current_level = existing['rating_level']
            if current_level < 2:
                # 更新到2级
                cursor.execute("""
                    UPDATE trading_symbol_rating
                    SET rating_level = 2,
                        margin_multiplier = 0.125,
                        score_bonus = 10,
                        hard_stop_loss_count = 0,
                        total_loss_amount = %s,
                        total_profit_amount = %s,
                        win_rate = %s,
                        total_trades = %s,
                        previous_level = %s,
                        level_changed_at = NOW(),
                        level_change_reason = %s,
                        stats_start_date = %s,
                        stats_end_date = %s,
                        updated_at = NOW()
                    WHERE symbol = %s
                """, (
                    item['total_loss'], item['total_profit'], item['win_rate'],
                    item['total_trades'], current_level, item['reason'],
                    start_date, end_date, symbol
                ))
                print(f'✓ {symbol}: 已从{current_level}级更新到2级')
                print(f'  原因: {item["reason"]}')
            else:
                print(f'○ {symbol}: 已在{current_level}级黑名单中，无需更新')
        else:
            # 插入新记录
            cursor.execute("""
                INSERT INTO trading_symbol_rating (
                    symbol, rating_level, margin_multiplier, score_bonus,
                    hard_stop_loss_count, total_loss_amount, total_profit_amount,
                    win_rate, total_trades, previous_level, level_changed_at,
                    level_change_reason, stats_start_date, stats_end_date,
                    created_at, updated_at
                ) VALUES (
                    %s, 2, 0.125, 10, 0, %s, %s, %s, %s, 0, NOW(), %s, %s, %s, NOW(), NOW()
                )
            """, (
                symbol, item['total_loss'], item['total_profit'], item['win_rate'],
                item['total_trades'], item['reason'], start_date, end_date
            ))
            print(f'✓ {symbol}: 已添加到2级黑名单')
            print(f'  原因: {item["reason"]}')

    conn.commit()
    print()
    print('=' * 100)
    print('添加完成！')

    # 显示当前黑名单状态
    print()
    print('当前黑名单状态 (trading_symbol_rating):')
    cursor.execute("""
        SELECT symbol, rating_level, margin_multiplier, level_change_reason,
               total_loss_amount, win_rate, total_trades, updated_at
        FROM trading_symbol_rating
        WHERE rating_level > 0
        ORDER BY rating_level DESC, total_loss_amount DESC
    """)

    ratings = cursor.fetchall()
    print(f'共 {len(ratings)} 个交易对在黑名单中')
    print()

    for level in [3, 2, 1]:
        level_items = [r for r in ratings if r['rating_level'] == level]
        if level_items:
            print(f'--- 黑名单 {level} 级 ({len(level_items)}个) ---')
            for item in level_items:
                print(f'  {item["symbol"]:<15} '
                      f'亏损:{float(item["total_loss_amount"]):>8.2f} '
                      f'胜率:{float(item["win_rate"])*100:>5.1f}% '
                      f'交易:{item["total_trades"]:>3}单 '
                      f'保证金倍数:{float(item["margin_multiplier"]):.3f}')
                if item["level_change_reason"]:
                    print(f'      {item["level_change_reason"][:80]}')
            print()

except Exception as e:
    print(f'✗ 操作失败: {e}')
    import traceback
    traceback.print_exc()
    conn.rollback()
finally:
    cursor.close()
    conn.close()

print('=' * 100)
