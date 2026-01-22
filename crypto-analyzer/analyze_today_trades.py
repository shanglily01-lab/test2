#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析今天（1月22日）的超级大脑交易"""
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
print("今天（1月22日）超级大脑交易分析")
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
        AND DATE(close_time) = '2026-01-22'
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
    print("今天无交易记录")

# 按分数段和方向统计
print("\n=== 各评分段表现 ===")
cursor.execute("""
    SELECT
        entry_score,
        position_side,
        COUNT(*) as cnt,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl
    FROM futures_positions
    WHERE status = 'closed'
        AND account_id = 2
        AND entry_signal_type LIKE 'SMART_BRAIN%'
        AND DATE(close_time) = '2026-01-22'
        AND entry_score IS NOT NULL
    GROUP BY entry_score, position_side
    ORDER BY entry_score DESC, position_side
""")
print(f"{'评分':<6} {'方向':<6} {'数量':<6} {'胜率':<8} {'总盈亏':<12} {'平均':<10}")
print("-" * 60)
for row in cursor.fetchall():
    side = "做多" if row['position_side'] == 'LONG' else "做空"
    win_rate = (row['wins'] / row['cnt'] * 100) if row['cnt'] > 0 else 0
    print(f"{row['entry_score']:<6} {side:<6} {row['cnt']:<6} {win_rate:<7.1f}% {row['total_pnl']:<11.2f} {row['avg_pnl']:<10.2f}")

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
        AND DATE(close_time) = '2026-01-22'
    GROUP BY notes
    ORDER BY cnt DESC
""")
print(f"{'平仓原因':<40} {'数量':<6} {'胜率':<8} {'总盈亏':<12} {'平均':<10}")
print("-" * 80)
for row in cursor.fetchall():
    reason = row['notes'] if row['notes'] else '未知'
    if len(reason) > 38:
        reason = reason[:35] + '...'
    win_rate = (row['wins'] / row['cnt'] * 100) if row['cnt'] > 0 else 0
    print(f"{reason:<40} {row['cnt']:<6} {win_rate:<7.1f}% {row['total_pnl']:<11.2f} {row['avg_pnl']:<10.2f}")

# 按交易对统计（只显示有交易的）
print("\n=== 各交易对表现（前20） ===")
cursor.execute("""
    SELECT
        symbol,
        COUNT(*) as cnt,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl
    FROM futures_positions
    WHERE status = 'closed'
        AND account_id = 2
        AND entry_signal_type LIKE 'SMART_BRAIN%'
        AND DATE(close_time) = '2026-01-22'
    GROUP BY symbol
    ORDER BY total_pnl DESC
    LIMIT 20
""")
print(f"{'交易对':<15} {'数量':<6} {'胜率':<8} {'总盈亏':<12} {'平均':<10}")
print("-" * 60)
for row in cursor.fetchall():
    win_rate = (row['wins'] / row['cnt'] * 100) if row['cnt'] > 0 else 0
    print(f"{row['symbol']:<15} {row['cnt']:<6} {win_rate:<7.1f}% {row['total_pnl']:<11.2f} {row['avg_pnl']:<10.2f}")

# 按小时统计
print("\n=== 每小时交易统计 ===")
cursor.execute("""
    SELECT
        HOUR(close_time) as hour,
        COUNT(*) as cnt,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as hourly_pnl
    FROM futures_positions
    WHERE status = 'closed'
        AND account_id = 2
        AND entry_signal_type LIKE 'SMART_BRAIN%'
        AND DATE(close_time) = '2026-01-22'
    GROUP BY HOUR(close_time)
    ORDER BY hour
""")
print(f"{'时间':<10} {'数量':<6} {'胜率':<8} {'小时盈亏':<12}")
print("-" * 40)
for row in cursor.fetchall():
    win_rate = (row['wins'] / row['cnt'] * 100) if row['cnt'] > 0 else 0
    print(f"{row['hour']:02d}:00     {row['cnt']:<6} {win_rate:<7.1f}% {row['hourly_pnl']:<11.2f}")

# 最大亏损的10笔
print("\n=== 今天最大亏损的10笔 ===")
cursor.execute("""
    SELECT
        symbol,
        entry_signal_type,
        entry_score,
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
        AND DATE(close_time) = '2026-01-22'
    ORDER BY realized_pnl ASC
    LIMIT 10
""")
for i, row in enumerate(cursor.fetchall(), 1):
    side = "做多" if row['position_side'] == 'LONG' else "做空"
    score = row['entry_score'] if row['entry_score'] else 'N/A'
    hours = row['holding_minutes'] // 60
    minutes = row['holding_minutes'] % 60
    print(f"\n{i}. {row['symbol']} - 评分{score}分({side})")
    print(f"   开仓: {row['entry_price']:.4f} -> 平仓: {row['mark_price']:.4f}")
    print(f"   数量: {row['quantity']:.4f}, 亏损: {row['realized_pnl']:.2f} USDT")
    print(f"   持仓: {hours}小时{minutes}分钟, 平仓原因: {row['close_reason']}")
    print(f"   时间: {row['close_time']}")

# 最大盈利的10笔
print("\n=== 今天最大盈利的10笔 ===")
cursor.execute("""
    SELECT
        symbol,
        entry_signal_type,
        entry_score,
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
        AND DATE(close_time) = '2026-01-22'
    ORDER BY realized_pnl DESC
    LIMIT 10
""")
for i, row in enumerate(cursor.fetchall(), 1):
    side = "做多" if row['position_side'] == 'LONG' else "做空"
    score = row['entry_score'] if row['entry_score'] else 'N/A'
    hours = row['holding_minutes'] // 60
    minutes = row['holding_minutes'] % 60
    print(f"\n{i}. {row['symbol']} - 评分{score}分({side})")
    print(f"   开仓: {row['entry_price']:.4f} -> 平仓: {row['mark_price']:.4f}")
    print(f"   数量: {row['quantity']:.4f}, 盈利: {row['realized_pnl']:.2f} USDT")
    print(f"   持仓: {hours}小时{minutes}分钟, 平仓原因: {row['close_reason']}")
    print(f"   时间: {row['close_time']}")

# 当前持仓
print("\n=== 当前持仓情况 ===")
cursor.execute("""
    SELECT
        symbol,
        entry_signal_type,
        entry_score,
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
        score = row['entry_score'] if row['entry_score'] else 'N/A'
        hours = row['holding_minutes'] // 60
        minutes = row['holding_minutes'] % 60
        print(f"{i}. {row['symbol']} - 评分{score}分({side})")
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
