#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将表现不佳的信号组合加入黑名单"""

import pymysql
import sys
import io
from dotenv import load_dotenv
import os

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

# 需要加入黑名单的信号组合(胜率<30% 或 亏损>$60)
# 格式: signal_type (信号组件), position_side (LONG/SHORT), 统计数据
bad_signals = [
    {
        'signal_type': 'breakdown_short + momentum_down_3pct + position_low + volatility_high + volume_power_1h_bear',
        'position_side': 'LONG',
        'reason': '严重逻辑矛盾: 空头破位信号却做多',
        'trades': 8,
        'win_rate': 0.0,
        'total_loss': -417.64
    },
    {
        'signal_type': 'breakdown_short + momentum_down_3pct + position_low + volatility_high + volume_power_bear',
        'position_side': 'SHORT',
        'reason': '低位破位做空易反弹,风险高',
        'trades': 8,
        'win_rate': 0.25,
        'total_loss': -114.21
    },
    {
        'signal_type': 'breakout_long + position_high + volume_power_bull',
        'position_side': 'LONG',
        'reason': '高位追涨买在顶部,风险极高',
        'trades': 6,
        'win_rate': 0.0,
        'total_loss': -203.70
    },
    {
        'signal_type': 'momentum_down_3pct + position_low + trend_1d_bear + volatility_high',
        'position_side': 'SHORT',
        'reason': '缺乏量能确认,单纯趋势信号',
        'trades': 6,
        'win_rate': 0.333,
        'total_loss': -81.85
    },
    {
        'signal_type': 'breakdown_short + momentum_down_3pct + position_low + trend_1h_bear + volatility_high + volume_power_bear',
        'position_side': 'SHORT',
        'reason': '低位做空高风险,易遭反弹',
        'trades': 6,
        'win_rate': 0.333,
        'total_loss': -99.33
    },
    {
        'signal_type': 'position_mid + volume_power_bull',
        'position_side': 'LONG',
        'reason': '信号太弱,仅2个组件',
        'trades': 4,
        'win_rate': 0.0,
        'total_loss': -100.85
    },
    {
        'signal_type': 'position_low + volume_power_bull',
        'position_side': 'LONG',
        'reason': '信号太弱,仅2个组件,易诱多',
        'trades': 3,
        'win_rate': 0.333,
        'total_loss': -90.22
    },
    {
        'signal_type': 'position_low + volatility_high + volume_power_1h_bull',
        'position_side': 'LONG',
        'reason': '低位量能可能是诱多陷阱',
        'trades': 2,
        'win_rate': 0.0,
        'total_loss': -116.76
    },
    {
        'signal_type': 'position_mid + volatility_high + volume_power_1h_bull',
        'position_side': 'LONG',
        'reason': '信号太弱,缺乏趋势确认',
        'trades': 2,
        'win_rate': 0.0,
        'total_loss': -9.55
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

print('=' * 120)
print('将表现不佳的信号组合加入黑名单')
print('=' * 120)
print()

try:
    # 添加信号到黑名单
    added_count = 0
    updated_count = 0

    for sig in bad_signals:
        signal_type = sig['signal_type']
        position_side = sig['position_side']

        # 检查是否已存在
        cursor.execute("""
            SELECT id FROM signal_blacklist
            WHERE signal_type = %s AND position_side = %s
        """, (signal_type, position_side))

        existing = cursor.fetchone()

        if existing:
            # 更新统计信息
            cursor.execute("""
                UPDATE signal_blacklist
                SET reason = %s,
                    total_loss = %s,
                    win_rate = %s,
                    order_count = %s,
                    updated_at = NOW(),
                    is_active = 1
                WHERE signal_type = %s AND position_side = %s
            """, (sig['reason'], sig['total_loss'], sig['win_rate'],
                  sig['trades'], signal_type, position_side))
            updated_count += 1
            print(f'✓ 更新: {signal_type[:70]} ({position_side})')
        else:
            # 插入新记录
            cursor.execute("""
                INSERT INTO signal_blacklist (
                    signal_type, position_side, reason,
                    total_loss, win_rate, order_count, is_active
                ) VALUES (%s, %s, %s, %s, %s, %s, 1)
            """, (signal_type, position_side, sig['reason'],
                  sig['total_loss'], sig['win_rate'], sig['trades']))
            added_count += 1
            print(f'✓ 新增: {signal_type[:70]} ({position_side})')

        print(f'  数据: {sig["trades"]}笔交易, 胜率{sig["win_rate"]*100:.1f}%, 亏损${sig["total_loss"]:.2f}')
        print(f'  原因: {sig["reason"]}')
        print()

    conn.commit()

    print('=' * 120)
    print(f'✅ 完成! 新增:{added_count}, 更新:{updated_count}')
    print('=' * 120)
    print()

    # 显示当前黑名单统计
    cursor.execute("""
        SELECT COUNT(*) as total,
               SUM(order_count) as total_trades,
               SUM(total_loss) as total_loss_sum
        FROM signal_blacklist
        WHERE is_active = 1
    """)

    stats = cursor.fetchone()

    if stats:
        print(f'📊 黑名单统计:')
        print(f'   活跃信号组合数: {stats["total"]}')
        print(f'   历史交易数: {stats["total_trades"] or 0}')
        print(f'   历史总亏损: ${float(stats["total_loss_sum"] or 0):.2f}')
        print()

    # 显示所有黑名单(按亏损排序)
    print('🚫 当前黑名单列表 (按亏损从大到小):')
    print('-' * 120)

    cursor.execute("""
        SELECT signal_type, position_side, reason,
               order_count, win_rate, total_loss
        FROM signal_blacklist
        WHERE is_active = 1
        ORDER BY total_loss ASC
        LIMIT 20
    """)

    blacklist_items = cursor.fetchall()

    for item in blacklist_items:
        loss = float(item['total_loss'] or 0)
        wr = float(item['win_rate'] or 0) * 100
        trades = item['order_count'] or 0
        side = item['position_side']

        side_emoji = '🟢' if side == 'LONG' else '🔴'

        print(f'{side_emoji} {item["signal_type"][:60]:<62} {side:<5} | '
              f'{trades:>3}笔 {wr:>5.1f}% ${loss:>8.2f} | '
              f'{item["reason"][:35]}')

except Exception as e:
    print(f'✗ 操作失败: {e}')
    import traceback
    traceback.print_exc()
    conn.rollback()
finally:
    cursor.close()
    conn.close()

print('=' * 120)
