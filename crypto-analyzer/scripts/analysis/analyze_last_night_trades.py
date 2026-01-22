#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析1月21日晚上8点到现在的超级大脑交易"""
import sys
import io
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'd:\test2\crypto-analyzer')

import pymysql
from datetime import datetime

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data',
    'charset': 'utf8mb4'
}

conn = pymysql.connect(**db_config)
cursor = conn.cursor(pymysql.cursors.DictCursor)

print("=" * 80)
print("1月21日晚上8点 到 现在 - 超级大脑交易分析")
print("=" * 80)
print()

# 总体概况
print("=== 总体概况 ===")
cursor.execute("""
    SELECT
        COUNT(*) as total_trades,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl,
        MAX(realized_pnl) as max_win,
        MIN(realized_pnl) as max_loss
    FROM futures_positions
    WHERE status = 'closed'
        AND account_id = 2
        AND entry_signal_type LIKE 'SMART_BRAIN%'
        AND close_time >= '2026-01-21 20:00:00'
""")
stats = cursor.fetchone()
if stats and stats['total_trades']:
    win_rate = (stats['wins'] / stats['total_trades'] * 100) if stats['total_trades'] > 0 else 0
    print(f"总交易: {stats['total_trades']}笔")
    print(f"盈利: {stats['wins']}笔 | 亏损: {stats['losses']}笔")
    print(f"胜率: {win_rate:.1f}%")
    print(f"总盈亏: {stats['total_pnl']:.2f} USDT")
    print(f"平均盈亏: {stats['avg_pnl']:.2f} USDT")
    print(f"最大盈利: {stats['max_win']:.2f} USDT")
    print(f"最大亏损: {stats['max_loss']:.2f} USDT")
else:
    print("无交易记录")

# 按分数段和方向统计
print("\n=== 各分数段和方向表现 ===")
cursor.execute("""
    SELECT
        entry_signal_type,
        position_side,
        COUNT(*) as cnt,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl
    FROM futures_positions
    WHERE status = 'closed'
        AND account_id = 2
        AND entry_signal_type LIKE 'SMART_BRAIN%'
        AND close_time >= '2026-01-21 20:00:00'
    GROUP BY entry_signal_type, position_side
    ORDER BY total_pnl DESC
""")
print(f"{'信号类型':<25} {'方向':<6} {'数量':<6} {'胜率':<8} {'总盈亏':<12} {'平均':<10}")
print("-" * 80)
for row in cursor.fetchall():
    signal = row['entry_signal_type']
    side = "做多" if row['position_side'] == 'LONG' else "做空"
    win_rate = (row['wins'] / row['cnt'] * 100) if row['cnt'] > 0 else 0
    print(f"{signal:<25} {side:<6} {row['cnt']:<6} {win_rate:<7.1f}% {row['total_pnl']:<11.2f} {row['avg_pnl']:<10.2f}")

# 按平仓原因统计
print("\n=== 平仓原因统计 ===")
cursor.execute("""
    SELECT
        notes,
        COUNT(*) as cnt,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl
    FROM futures_positions
    WHERE status = 'closed'
        AND account_id = 2
        AND entry_signal_type LIKE 'SMART_BRAIN%'
        AND close_time >= '2026-01-21 20:00:00'
    GROUP BY notes
    ORDER BY cnt DESC
    LIMIT 15
""")
print(f"{'平仓原因':<40} {'数量':<6} {'胜率':<8} {'总盈亏':<12} {'平均':<10}")
print("-" * 80)
for row in cursor.fetchall():
    reason = row['notes'] if row['notes'] else '未知'
    if len(reason) > 38:
        reason = reason[:35] + '...'
    win_rate = (row['wins'] / row['cnt'] * 100) if row['cnt'] > 0 else 0
    print(f"{reason:<40} {row['cnt']:<6} {win_rate:<7.1f}% {row['total_pnl']:<11.2f} {row['avg_pnl']:<10.2f}")

# 所有亏损交易(按亏损从大到小)
print("\n=== 所有亏损交易详情(按亏损从大到小) ===")
cursor.execute("""
    SELECT
        symbol,
        entry_signal_type,
        position_side,
        entry_price,
        mark_price,
        quantity,
        realized_pnl,
        notes as close_reason,
        open_time,
        close_time,
        TIMESTAMPDIFF(MINUTE, open_time, close_time) as holding_minutes
    FROM futures_positions
    WHERE status = 'closed'
        AND account_id = 2
        AND entry_signal_type LIKE 'SMART_BRAIN%'
        AND realized_pnl < 0
        AND close_time >= '2026-01-21 20:00:00'
    ORDER BY realized_pnl ASC
""")
losses = cursor.fetchall()
print(f"总亏损交易: {len(losses)}笔")
print()
for i, row in enumerate(losses, 1):
    side = "做多" if row['position_side'] == 'LONG' else "做空"
    hours = row['holding_minutes'] // 60
    minutes = row['holding_minutes'] % 60
    print(f"{i}. {row['symbol']} - {row['entry_signal_type']}({side})")
    print(f"   开仓: {row['entry_price']:.4f} -> 平仓: {row['mark_price']:.4f}")
    print(f"   数量: {row['quantity']:.4f}, 亏损: {row['realized_pnl']:.2f} USDT")
    print(f"   持仓: {hours}小时{minutes}分钟, 平仓原因: {row['close_reason']}")
    print(f"   开仓: {row['open_time']}, 平仓: {row['close_time']}")
    print()

# 所有盈利交易(按盈利从大到小)
print("=" * 80)
print("=== 所有盈利交易详情(按盈利从大到小) ===")
cursor.execute("""
    SELECT
        symbol,
        entry_signal_type,
        position_side,
        entry_price,
        mark_price,
        quantity,
        realized_pnl,
        notes as close_reason,
        open_time,
        close_time,
        TIMESTAMPDIFF(MINUTE, open_time, close_time) as holding_minutes
    FROM futures_positions
    WHERE status = 'closed'
        AND account_id = 2
        AND entry_signal_type LIKE 'SMART_BRAIN%'
        AND realized_pnl > 0
        AND close_time >= '2026-01-21 20:00:00'
    ORDER BY realized_pnl DESC
""")
profits = cursor.fetchall()
print(f"总盈利交易: {len(profits)}笔")
print()
for i, row in enumerate(profits, 1):
    side = "做多" if row['position_side'] == 'LONG' else "做空"
    hours = row['holding_minutes'] // 60
    minutes = row['holding_minutes'] % 60
    print(f"{i}. {row['symbol']} - {row['entry_signal_type']}({side})")
    print(f"   开仓: {row['entry_price']:.4f} -> 平仓: {row['mark_price']:.4f}")
    print(f"   数量: {row['quantity']:.4f}, 盈利: {row['realized_pnl']:.2f} USDT")
    print(f"   持仓: {hours}小时{minutes}分钟, 平仓原因: {row['close_reason']}")
    print(f"   开仓: {row['open_time']}, 平仓: {row['close_time']}")
    print()

# 当前持仓
print("=" * 80)
print("=== 当前持仓情况 ===")
cursor.execute("""
    SELECT
        symbol,
        entry_signal_type,
        position_side,
        entry_price,
        quantity,
        unrealized_pnl,
        unrealized_pnl_pct,
        open_time,
        TIMESTAMPDIFF(MINUTE, open_time, NOW()) as holding_minutes
    FROM futures_positions
    WHERE status = 'open'
        AND account_id = 2
        AND entry_signal_type LIKE 'SMART_BRAIN%'
    ORDER BY unrealized_pnl ASC
""")
open_positions = cursor.fetchall()
if open_positions:
    total_unrealized = sum(p['unrealized_pnl'] for p in open_positions)
    print(f"持仓数量: {len(open_positions)}个")
    print(f"未实现盈亏: {total_unrealized:.2f} USDT")
    print()
    for i, row in enumerate(open_positions, 1):
        side = "做多" if row['position_side'] == 'LONG' else "做空"
        hours = row['holding_minutes'] // 60
        minutes = row['holding_minutes'] % 60
        print(f"{i}. {row['symbol']} - {row['entry_signal_type']}({side})")
        print(f"   开仓价: {row['entry_price']:.4f}, 数量: {row['quantity']:.4f}")
        print(f"   未实现盈亏: {row['unrealized_pnl']:.2f} ({row['unrealized_pnl_pct']:.2f}%)")
        print(f"   持仓: {hours}小时{minutes}分钟, 开仓时间: {row['open_time']}")
else:
    print("当前无持仓")

cursor.close()
conn.close()

print("\n" + "=" * 80)
print("分析完成")
print("=" * 80)
