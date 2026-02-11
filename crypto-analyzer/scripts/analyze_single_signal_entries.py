#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析单一信号开仓情况 - 找出所有单信号开仓记录
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pymysql
from dotenv import load_dotenv
from datetime import datetime, timedelta

# 加载环境变量
load_dotenv()

# 数据库配置
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data')
}

print("=" * 100)
print("分析单一信号开仓情况")
print("=" * 100)

# 连接数据库
conn = pymysql.connect(**DB_CONFIG, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
cursor = conn.cursor()

# 查询最近30天的所有已平仓记录
cursor.execute("""
    SELECT
        id,
        symbol,
        position_side,
        entry_signal_type,
        realized_pnl,
        unrealized_pnl_pct,
        open_time,
        close_time,
        notes,
        TIMESTAMPDIFF(MINUTE, open_time, close_time) as holding_minutes
    FROM futures_positions
    WHERE status = 'CLOSED'
    AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
    ORDER BY close_time DESC
""")

all_trades = cursor.fetchall()

print(f"\n总交易数: {len(all_trades)}")

# 识别单一信号（不包含+号的信号）
single_signal_trades = []
for trade in all_trades:
    signal = trade['entry_signal_type'] or ''

    # 判断是否为单一信号（不包含+号，或者只包含简单描述）
    if signal and '+' not in signal and 'TREND_' not in signal:
        # 排除组合信号的简化表示
        if not any(keyword in signal for keyword in ['momentum', 'position', 'volume', 'trend', 'breakdown', 'breakthrough']):
            single_signal_trades.append(trade)
        # 如果只包含单个关键词，也算单一信号
        elif signal.count('_') <= 1:
            single_signal_trades.append(trade)

print(f"单一信号交易数: {len(single_signal_trades)}")

# 统计单一信号类型
signal_stats = {}
for trade in single_signal_trades:
    signal = trade['entry_signal_type']
    side = trade['position_side']
    key = f"{signal}_{side}"

    if key not in signal_stats:
        signal_stats[key] = {
            'signal': signal,
            'side': side,
            'count': 0,
            'wins': 0,
            'losses': 0,
            'total_pnl': 0.0,
            'trades': []
        }

    signal_stats[key]['count'] += 1
    pnl = float(trade['realized_pnl'] or 0)
    signal_stats[key]['total_pnl'] += pnl
    signal_stats[key]['trades'].append(trade)

    if pnl > 0:
        signal_stats[key]['wins'] += 1
    else:
        signal_stats[key]['losses'] += 1

# 计算胜率并排序
signal_list = []
for key, stats in signal_stats.items():
    stats['win_rate'] = (stats['wins'] / stats['count'] * 100) if stats['count'] > 0 else 0
    signal_list.append(stats)

signal_list.sort(key=lambda x: x['count'], reverse=True)

# 显示单一信号统计
print("\n" + "=" * 100)
print("单一信号统计（按次数排序）")
print("=" * 100)

total_single_pnl = 0
for stats in signal_list:
    total_single_pnl += stats['total_pnl']
    print(f"\n信号: {stats['signal']}")
    print(f"方向: {stats['side']}")
    print(f"次数: {stats['count']}")
    print(f"胜率: {stats['win_rate']:.1f}% ({stats['wins']}胜/{stats['losses']}负)")
    print(f"总盈亏: {stats['total_pnl']:+.2f}U")
    print(f"平均盈亏: {stats['total_pnl']/stats['count']:+.2f}U")

    # 显示详细交易
    print("  交易详情:")
    for trade in stats['trades'][:5]:  # 只显示前5笔
        pnl = float(trade['realized_pnl'] or 0)
        print(f"    [{trade['id']:4d}] {trade['symbol']:12s} | {pnl:+8.2f}U | {trade['notes'][:50]}")

print("\n" + "=" * 100)
print("单一信号总体表现")
print("=" * 100)
print(f"单一信号交易数: {len(single_signal_trades)} / {len(all_trades)} ({len(single_signal_trades)/len(all_trades)*100:.1f}%)")
print(f"单一信号总盈亏: {total_single_pnl:+.2f}U")
print(f"平均盈亏: {total_single_pnl/len(single_signal_trades):+.2f}U/单")

# 查找所有包含特定关键词但没有组合的信号
print("\n" + "=" * 100)
print("可疑的简单信号（可能是单一或双信号组合）")
print("=" * 100)

cursor.execute("""
    SELECT
        entry_signal_type as signal_type,
        position_side,
        COUNT(*) as count,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl
    FROM futures_positions
    WHERE status = 'CLOSED'
    AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
    AND entry_signal_type IS NOT NULL
    AND (
        -- 不包含+号的
        entry_signal_type NOT LIKE '%+%'
        OR
        -- 只包含1个+号的（2个信号组合）
        (LENGTH(entry_signal_type) - LENGTH(REPLACE(entry_signal_type, '+', ''))) = 1
    )
    GROUP BY entry_signal_type, position_side
    HAVING count >= 1
    ORDER BY count DESC, total_pnl ASC
""")

simple_signals = cursor.fetchall()

print(f"\n找到 {len(simple_signals)} 种简单信号（单一或双信号）:\n")

for sig in simple_signals:
    signal = sig['signal_type'] or 'NULL'
    side = sig['position_side']
    count = sig['count']
    wins = sig['wins']
    total_pnl = float(sig['total_pnl'] or 0)
    avg_pnl = float(sig['avg_pnl'] or 0)
    win_rate = (wins / count * 100) if count > 0 else 0

    # 判断信号类型
    plus_count = signal.count('+')
    if plus_count == 0:
        signal_type = "单一信号"
    elif plus_count == 1:
        signal_type = "双信号"
    else:
        signal_type = f"{plus_count+1}信号组合"

    print(f"[{signal_type}] {signal[:60]}")
    print(f"  方向: {side:5s} | 次数: {count:3d} | 胜率: {win_rate:5.1f}% | 总盈亏: {total_pnl:+8.2f}U | 平均: {avg_pnl:+6.2f}U")
    print()

# 建议禁用的单一信号
print("\n" + "=" * 100)
print("建议禁用的单一/简单信号")
print("=" * 100)

bad_signals = []
for sig in simple_signals:
    signal = sig['signal_type'] or 'NULL'
    side = sig['position_side']
    count = sig['count']
    wins = sig['wins']
    total_pnl = float(sig['total_pnl'] or 0)
    win_rate = (wins / count * 100) if count > 0 else 0
    plus_count = signal.count('+')

    # 禁用条件：
    # 1. 单一信号（+号=0）且表现差
    # 2. 或者双信号（+号=1）且胜率<40%或亏损>50U
    should_ban = False
    reason = ""

    if plus_count == 0:
        # 单一信号：更严格
        if win_rate < 50 or total_pnl < -20:
            should_ban = True
            reason = f"单一信号不充分，胜率{win_rate:.1f}%，亏损{total_pnl:.2f}U"
    elif plus_count == 1:
        # 双信号：略宽松
        if (win_rate < 40 and count >= 5) or total_pnl < -50:
            should_ban = True
            reason = f"双信号表现差，胜率{win_rate:.1f}%，亏损{total_pnl:.2f}U"

    if should_ban:
        bad_signals.append({
            'signal': signal,
            'side': side,
            'count': count,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'reason': reason
        })

if bad_signals:
    print(f"\n建议禁用 {len(bad_signals)} 个信号:\n")
    for sig in bad_signals:
        print(f"信号: {sig['signal']}")
        print(f"方向: {sig['side']}")
        print(f"统计: {sig['count']}单，胜率{sig['win_rate']:.1f}%，亏损{sig['total_pnl']:.2f}U")
        print(f"原因: {sig['reason']}")
        print()

        # 生成SQL
        print(f"SQL: INSERT INTO signal_blacklist (signal_type, position_side, reason, win_rate, total_loss, order_count, is_active, blacklist_level)")
        print(f"     VALUES ('{sig['signal']}', '{sig['side']}', '{sig['reason']}', {sig['win_rate']/100:.4f}, {-sig['total_pnl']:.2f}, {sig['count']}, 1, 3)")
        print(f"     ON DUPLICATE KEY UPDATE reason=VALUES(reason), win_rate=VALUES(win_rate), total_loss=VALUES(total_loss), order_count=VALUES(order_count), updated_at=NOW();")
        print()
else:
    print("\n暂无需要禁用的信号")

cursor.close()
conn.close()

print("\n" + "=" * 100)
print("分析完成")
print("=" * 100)
