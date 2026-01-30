#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调整黑名单等级"""

import pymysql
import sys
import io
from dotenv import load_dotenv
import os
from datetime import datetime, date, timedelta

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

print('=' * 100)
print('调整黑名单等级')
print('=' * 100)
print()

try:
    end_date = date.today()
    start_date = end_date - timedelta(days=7)

    # 1. RIVER/USDT: 从2级降到1级
    print('【降级】RIVER/USDT: 2级 → 1级')
    cursor.execute("""
        SELECT rating_level, total_loss_amount, total_profit_amount, win_rate, total_trades
        FROM trading_symbol_rating
        WHERE symbol = %s
    """, ('RIVER/USDT',))

    river_data = cursor.fetchone()
    if river_data:
        cursor.execute("""
            UPDATE trading_symbol_rating
            SET rating_level = 1,
                margin_multiplier = 0.25,
                score_bonus = 5,
                previous_level = %s,
                level_changed_at = NOW(),
                level_change_reason = '表现改善,净盈利$197.07,胜率50%%,降级到1级',
                stats_start_date = %s,
                stats_end_date = %s,
                updated_at = NOW()
            WHERE symbol = %s
        """, (river_data['rating_level'], start_date, end_date, 'RIVER/USDT'))
        print(f'✓ RIVER/USDT 已从 {river_data["rating_level"]} 级降到 1 级')
        print(f'  数据: 亏损${float(river_data["total_loss_amount"]):.2f}, '
              f'盈利${float(river_data["total_profit_amount"]):.2f}, '
              f'胜率{float(river_data["win_rate"])*100:.1f}%, '
              f'交易{river_data["total_trades"]}单')
    else:
        print('✗ RIVER/USDT 不在黑名单中')

    print()

    # 2. KAIA/USDT: 从1级移除到白名单 (level=0)
    print('【移出黑名单】KAIA/USDT: 1级 → 白名单')
    cursor.execute("""
        SELECT rating_level, total_loss_amount, total_profit_amount, win_rate, total_trades
        FROM trading_symbol_rating
        WHERE symbol = %s
    """, ('KAIA/USDT',))

    kaia_data = cursor.fetchone()
    if kaia_data:
        cursor.execute("""
            UPDATE trading_symbol_rating
            SET rating_level = 0,
                margin_multiplier = 1.0,
                score_bonus = 0,
                previous_level = %s,
                level_changed_at = NOW(),
                level_change_reason = '表现优秀,58.3%%胜率,净盈利$9.72,升级到白名单',
                stats_start_date = %s,
                stats_end_date = %s,
                updated_at = NOW()
            WHERE symbol = %s
        """, (kaia_data['rating_level'], start_date, end_date, 'KAIA/USDT'))
        print(f'✓ KAIA/USDT 已从 1 级升到白名单 (level=0)')
        print(f'  数据: 亏损${float(kaia_data["total_loss_amount"]):.2f}, '
              f'盈利${float(kaia_data["total_profit_amount"]):.2f}, '
              f'胜率{float(kaia_data["win_rate"])*100:.1f}%, '
              f'交易{kaia_data["total_trades"]}单')
    else:
        print('✗ KAIA/USDT 不在黑名单中')

    print()

    # 3. 0G/USDT: 从1级移除到白名单 (level=0)
    print('【移出黑名单】0G/USDT: 1级 → 白名单')
    cursor.execute("""
        SELECT rating_level, total_loss_amount, total_profit_amount, win_rate, total_trades
        FROM trading_symbol_rating
        WHERE symbol = %s
    """, ('0G/USDT',))

    og_data = cursor.fetchone()
    if og_data:
        cursor.execute("""
            UPDATE trading_symbol_rating
            SET rating_level = 0,
                margin_multiplier = 1.0,
                score_bonus = 0,
                previous_level = %s,
                level_changed_at = NOW(),
                level_change_reason = '表现优秀,54.5%%胜率,近期稳定,升级到白名单',
                stats_start_date = %s,
                stats_end_date = %s,
                updated_at = NOW()
            WHERE symbol = %s
        """, (og_data['rating_level'], start_date, end_date, '0G/USDT'))
        print(f'✓ 0G/USDT 已从 1 级升到白名单 (level=0)')
        print(f'  数据: 亏损${float(og_data["total_loss_amount"]):.2f}, '
              f'盈利${float(og_data["total_profit_amount"]):.2f}, '
              f'胜率{float(og_data["win_rate"])*100:.1f}%, '
              f'交易{og_data["total_trades"]}单')
    else:
        print('✗ 0G/USDT 不在黑名单中')

    print()

    # 4. ENSO/USDT: 从1级移除到白名单 (level=0)
    print('【移出黑名单】ENSO/USDT: 1级 → 白名单')
    cursor.execute("""
        SELECT rating_level, total_loss_amount, total_profit_amount, win_rate, total_trades
        FROM trading_symbol_rating
        WHERE symbol = %s
    """, ('ENSO/USDT',))

    enso_data = cursor.fetchone()
    if enso_data:
        cursor.execute("""
            UPDATE trading_symbol_rating
            SET rating_level = 0,
                margin_multiplier = 1.0,
                score_bonus = 0,
                previous_level = %s,
                level_changed_at = NOW(),
                level_change_reason = '表现优秀,净盈利$77.84,42.9%%胜率,升级到白名单',
                stats_start_date = %s,
                stats_end_date = %s,
                updated_at = NOW()
            WHERE symbol = %s
        """, (enso_data['rating_level'], start_date, end_date, 'ENSO/USDT'))
        print(f'✓ ENSO/USDT 已从 1 级升到白名单 (level=0)')
        print(f'  数据: 亏损${float(enso_data["total_loss_amount"]):.2f}, '
              f'盈利${float(enso_data["total_profit_amount"]):.2f}, '
              f'胜率{float(enso_data["win_rate"])*100:.1f}%, '
              f'交易{enso_data["total_trades"]}单')
    else:
        print('✗ ENSO/USDT 不在黑名单中')

    conn.commit()
    print()
    print('=' * 100)
    print('✅ 调整完成！')
    print('=' * 100)
    print()

    # 显示更新后的黑名单状态
    print('📊 更新后的黑名单统计:')
    cursor.execute("""
        SELECT rating_level, COUNT(*) as count
        FROM trading_symbol_rating
        WHERE rating_level > 0
        GROUP BY rating_level
        ORDER BY rating_level DESC
    """)

    stats = cursor.fetchall()
    total_blacklist = sum(s['count'] for s in stats)

    print(f'   总计: {total_blacklist} 个交易对在黑名单中')
    for stat in stats:
        level_name = {3: '3级(永久禁止)', 2: '2级(严格限制)', 1: '1级(轻度限制)'}.get(stat['rating_level'], f'{stat["rating_level"]}级')
        print(f'   - {level_name}: {stat["count"]} 个')

    print()
    print('🎯 升级到白名单的交易对:')
    cursor.execute("""
        SELECT symbol, win_rate, total_trades, level_change_reason
        FROM trading_symbol_rating
        WHERE rating_level = 0
        AND previous_level = 1
        AND level_changed_at >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
        ORDER BY symbol
    """)

    whitelist = cursor.fetchall()
    if whitelist:
        for item in whitelist:
            print(f'   ✓ {item["symbol"]:<15} (胜率:{float(item["win_rate"])*100:.1f}%, 交易:{item["total_trades"]}单)')
    else:
        print('   (无最近升级记录)')

except Exception as e:
    print(f'✗ 操作失败: {e}')
    import traceback
    traceback.print_exc()
    conn.rollback()
finally:
    cursor.close()
    conn.close()

print('=' * 100)
