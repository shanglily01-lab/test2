#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将24H分析中表现差的信号加入黑名单"""

import pymysql
import sys
import io
from dotenv import load_dotenv
import os
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

# 要加入黑名单的信号
bad_signals = [
    {
        'signal_type': 'breakout_long + momentum_up_3pct + position_high + trend_1d_bull + volatility_high',
        'position_side': 'LONG',
        'reason': '0%胜率,亏损$45.42',
        'total_loss': -45.42,
        'win_rate': 0.0,
        'order_count': 1
    },
    {
        'signal_type': 'momentum_down_3pct + position_low + volatility_high',
        'position_side': 'SHORT',
        'reason': '0%胜率,亏损$45.36',
        'total_loss': -45.36,
        'win_rate': 0.0,
        'order_count': 2
    },
    {
        'signal_type': 'momentum_up_3pct + position_low + volatility_high + volume_power_bull',
        'position_side': 'LONG',
        'reason': '0%胜率,亏损$42.65',
        'total_loss': -42.65,
        'win_rate': 0.0,
        'order_count': 1
    },
    {
        'signal_type': 'breakdown_short + volatility_high',
        'position_side': 'SHORT',
        'reason': '0%胜率,亏损$41.36',
        'total_loss': -41.36,
        'win_rate': 0.0,
        'order_count': 3
    },
    {
        'signal_type': 'breakdown_short + momentum_down_3pct + trend_1h_bear + volatility_high + volume_power_1h_bear',
        'position_side': 'SHORT',
        'reason': '54.5%胜率但亏损$37.58',
        'total_loss': -37.58,
        'win_rate': 0.545,
        'order_count': 11
    },
    {
        'signal_type': 'position_low + trend_1h_bear + volatility_high',
        'position_side': 'SHORT',
        'reason': '0%胜率,亏损$35.60',
        'total_loss': -35.60,
        'win_rate': 0.0,
        'order_count': 2
    }
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
print('添加24H表现差的信号到黑名单')
print('=' * 100)
print()

try:
    added = 0
    skipped = 0

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
            print(f"⚠️ 跳过(已存在): {signal_type[:70]} ({position_side})")
            skipped += 1
            continue

        # 插入黑名单
        cursor.execute("""
            INSERT INTO signal_blacklist
            (signal_type, position_side, reason, total_loss, win_rate, order_count, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, 1, NOW(), NOW())
        """, (
            signal_type,
            position_side,
            sig['reason'],
            sig['total_loss'],
            sig['win_rate'],
            sig['order_count']
        ))

        print(f"✅ 已添加: {signal_type[:70]} ({position_side})")
        print(f"   原因: {sig['reason']}")
        print(f"   交易: {sig['order_count']}次 | 胜率: {sig['win_rate']*100:.1f}% | 亏损: ${sig['total_loss']:.2f}")
        print()
        added += 1

    conn.commit()

    print('=' * 100)
    print(f"✅ 操作完成")
    print(f"   新增: {added}个")
    print(f"   跳过: {skipped}个")
    print(f"   预期减少亏损: ${abs(sum(s['total_loss'] for s in bad_signals)):.2f}/天")
    print('=' * 100)
    print()

    # 显示当前黑名单总数
    cursor.execute("SELECT COUNT(*) as total FROM signal_blacklist WHERE is_active = 1")
    result = cursor.fetchone()
    print(f"📊 当前黑名单中共有 {result['total']} 个信号组合")
    print()

except Exception as e:
    print(f"✗ 操作失败: {e}")
    import traceback
    traceback.print_exc()
    conn.rollback()
finally:
    cursor.close()
    conn.close()
