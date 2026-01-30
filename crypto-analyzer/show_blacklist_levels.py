#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""显示黑名单分级详情"""

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
    database=os.getenv('DB_NAME')
)

cursor = conn.cursor(pymysql.cursors.DictCursor)

print('=' * 120)
print('黑名单分级详情 (trading_symbol_rating)')
print('=' * 120)
print()

try:
    # 查询所有黑名单交易对
    cursor.execute("""
        SELECT symbol, rating_level, margin_multiplier, score_bonus,
               total_loss_amount, total_profit_amount, win_rate, total_trades,
               level_change_reason, level_changed_at
        FROM trading_symbol_rating
        WHERE rating_level > 0
        ORDER BY rating_level DESC, total_loss_amount DESC
    """)

    ratings = cursor.fetchall()

    # 统计
    level_1_count = len([r for r in ratings if r['rating_level'] == 1])
    level_2_count = len([r for r in ratings if r['rating_level'] == 2])
    level_3_count = len([r for r in ratings if r['rating_level'] == 3])

    print(f'📊 总计: {len(ratings)} 个交易对在黑名单中')
    print(f'   - 1级黑名单: {level_1_count} 个 (保证金倍数 0.25, 评分门槛 +5)')
    print(f'   - 2级黑名单: {level_2_count} 个 (保证金倍数 0.125, 评分门槛 +10)')
    print(f'   - 3级黑名单: {level_3_count} 个 (永久禁止交易)')
    print()

    # 3级黑名单
    if level_3_count > 0:
        print('🚫 黑名单 3 级 - 永久禁止交易')
        print('-' * 120)
        level_3_items = [r for r in ratings if r['rating_level'] == 3]
        for idx, item in enumerate(level_3_items, 1):
            net_pnl = float(item['total_profit_amount']) - float(item['total_loss_amount'])
            print(f'{idx:2d}. {item["symbol"]:<15} '
                  f'亏损:${float(item["total_loss_amount"]):>8.2f} '
                  f'盈利:${float(item["total_profit_amount"]):>8.2f} '
                  f'净值:${net_pnl:>8.2f} '
                  f'胜率:{float(item["win_rate"])*100:>5.1f}% '
                  f'交易:{item["total_trades"]:>3}单')
            if item["level_change_reason"]:
                print(f'    原因: {item["level_change_reason"]}')
        print()

    # 2级黑名单
    if level_2_count > 0:
        print('⚠️  黑名单 2 级 - 保证金倍数 0.125 (严格限制)')
        print('-' * 120)
        level_2_items = [r for r in ratings if r['rating_level'] == 2]
        for idx, item in enumerate(level_2_items, 1):
            net_pnl = float(item['total_profit_amount']) - float(item['total_loss_amount'])
            print(f'{idx:2d}. {item["symbol"]:<15} '
                  f'亏损:${float(item["total_loss_amount"]):>8.2f} '
                  f'盈利:${float(item["total_profit_amount"]):>8.2f} '
                  f'净值:${net_pnl:>8.2f} '
                  f'胜率:{float(item["win_rate"])*100:>5.1f}% '
                  f'交易:{item["total_trades"]:>3}单 '
                  f'保证金:{float(item["margin_multiplier"]):.3f}')
            if item["level_change_reason"]:
                print(f'    原因: {item["level_change_reason"]}')
        print()

    # 1级黑名单
    if level_1_count > 0:
        print('⚡ 黑名单 1 级 - 保证金倍数 0.25 (轻度限制)')
        print('-' * 120)
        level_1_items = [r for r in ratings if r['rating_level'] == 1]
        for idx, item in enumerate(level_1_items, 1):
            net_pnl = float(item['total_profit_amount']) - float(item['total_loss_amount'])
            print(f'{idx:2d}. {item["symbol"]:<15} '
                  f'亏损:${float(item["total_loss_amount"]):>8.2f} '
                  f'盈利:${float(item["total_profit_amount"]):>8.2f} '
                  f'净值:${net_pnl:>8.2f} '
                  f'胜率:{float(item["win_rate"])*100:>5.1f}% '
                  f'交易:{item["total_trades"]:>3}单 '
                  f'保证金:{float(item["margin_multiplier"]):.3f}')
            if item["level_change_reason"]:
                print(f'    原因: {item["level_change_reason"]}')
        print()

    # 汇总统计
    print('=' * 120)
    print('📈 统计汇总')
    print('=' * 120)

    for level in [3, 2, 1]:
        level_items = [r for r in ratings if r['rating_level'] == level]
        if level_items:
            total_loss = sum(float(r['total_loss_amount']) for r in level_items)
            total_profit = sum(float(r['total_profit_amount']) for r in level_items)
            total_trades = sum(r['total_trades'] for r in level_items)
            avg_win_rate = sum(float(r['win_rate']) for r in level_items) / len(level_items) if level_items else 0

            print(f'Level {level}: {len(level_items):2d}个交易对, '
                  f'总亏损:${total_loss:>9.2f}, '
                  f'总盈利:${total_profit:>9.2f}, '
                  f'净值:${total_profit-total_loss:>9.2f}, '
                  f'平均胜率:{avg_win_rate*100:>5.1f}%, '
                  f'总交易:{total_trades:>4}单')

except Exception as e:
    print(f'✗ 查询失败: {e}')
    import traceback
    traceback.print_exc()
finally:
    cursor.close()
    conn.close()

print('=' * 120)
