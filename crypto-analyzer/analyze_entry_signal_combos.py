#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析实际开仓的信号组合(通过entry_signal_type字段)
"""

import sys
import io
import pymysql
import json
from collections import defaultdict

# 设置标准输出编码为UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def analyze_entry_signal_combos():
    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    print("=" * 120)
    print("实际开仓信号组合分析(通过entry_signal_type)")
    print("=" * 120)

    # 查询最近的交易
    cursor.execute("""
        SELECT
            symbol, position_side, entry_score, entry_signal_type,
            signal_components, realized_pnl,
            open_time, close_time,
            TIMESTAMPDIFF(MINUTE, open_time, close_time) as holding_minutes
        FROM futures_positions
        WHERE close_time >= CONCAT(CURDATE() - INTERVAL 3 DAY, ' 20:00:00')
        AND status = 'closed'
        ORDER BY close_time DESC
        LIMIT 200
    """)

    trades = cursor.fetchall()

    if not trades:
        print("\n没有找到交易记录")
        cursor.close()
        conn.close()
        return

    print(f"\n找到 {len(trades)} 笔交易\n")

    # 统计每种信号组合的表现
    combo_stats = defaultdict(lambda: {
        'count': 0,
        'wins': 0,
        'losses': 0,
        'total_pnl': 0,
        'trades': []
    })

    for trade in trades:
        pnl = float(trade['realized_pnl'])
        entry_signal = trade['entry_signal_type'] or 'unknown'

        combo_stats[entry_signal]['count'] += 1
        combo_stats[entry_signal]['total_pnl'] += pnl

        if pnl > 0:
            combo_stats[entry_signal]['wins'] += 1
        else:
            combo_stats[entry_signal]['losses'] += 1

        combo_stats[entry_signal]['trades'].append({
            'symbol': trade['symbol'],
            'side': trade['position_side'],
            'score': trade['entry_score'],
            'pnl': pnl,
            'time': trade['open_time'],
            'holding_minutes': trade['holding_minutes']
        })

    # 按交易次数排序
    sorted_combos = sorted(combo_stats.items(), key=lambda x: x[1]['count'], reverse=True)

    print("信号组合表现统计:")
    print("=" * 120)
    print(f"{'信号组合':<60} {'次数':<8} {'胜率':<10} {'总盈亏':<15} {'平均':<12}")
    print("-" * 120)

    for combo, stats in sorted_combos[:30]:
        count = stats['count']
        wins = stats['wins']
        losses = stats['losses']
        total_pnl = stats['total_pnl']
        win_rate = (wins / count * 100) if count > 0 else 0
        avg_pnl = total_pnl / count if count > 0 else 0

        status = "✅" if total_pnl > 0 else "❌"

        # 截断过长的组合名称
        display_combo = combo[:58] if len(combo) > 58 else combo

        print(f"{status} {display_combo:<58} {count:<8} {win_rate:<9.1f}% ${total_pnl:<14.2f} ${avg_pnl:<11.2f}")

    # 详细分析表现最好和最差的组合
    print("\n" + "=" * 120)
    print("表现最好的信号组合 (总盈亏排名):")
    print("=" * 120)

    profitable_combos = sorted(
        [(k, v) for k, v in combo_stats.items() if v['total_pnl'] > 0 and v['count'] >= 3],
        key=lambda x: x[1]['total_pnl'],
        reverse=True
    )

    for i, (combo, stats) in enumerate(profitable_combos[:5], 1):
        count = stats['count']
        wins = stats['wins']
        total_pnl = stats['total_pnl']
        win_rate = (wins / count * 100) if count > 0 else 0
        avg_pnl = total_pnl / count if count > 0 else 0

        print(f"\n{i}. {combo}")
        print(f"   交易次数: {count} | 胜率: {win_rate:.1f}% ({wins}胜/{count-wins}负)")
        print(f"   总盈亏: ${total_pnl:.2f} | 平均: ${avg_pnl:.2f}")

        # 显示最近3笔交易
        print("   最近3笔:")
        for t in stats['trades'][:3]:
            status = "✅" if t['pnl'] > 0 else "❌"
            print(f"      {status} {t['symbol']} {t['side']} ${t['pnl']:.2f} | {t['time']}")

    print("\n" + "=" * 120)
    print("表现最差的信号组合 (总亏损排名):")
    print("=" * 120)

    losing_combos = sorted(
        [(k, v) for k, v in combo_stats.items() if v['total_pnl'] < 0 and v['count'] >= 3],
        key=lambda x: x[1]['total_pnl']
    )

    for i, (combo, stats) in enumerate(losing_combos[:5], 1):
        count = stats['count']
        wins = stats['wins']
        total_pnl = stats['total_pnl']
        win_rate = (wins / count * 100) if count > 0 else 0
        avg_pnl = total_pnl / count if count > 0 else 0

        print(f"\n{i}. {combo}")
        print(f"   交易次数: {count} | 胜率: {win_rate:.1f}% ({wins}胜/{count-wins}负)")
        print(f"   总亏损: ${total_pnl:.2f} | 平均: ${avg_pnl:.2f}")

        # 显示最近3笔交易
        print("   最近3笔:")
        for t in stats['trades'][:3]:
            status = "✅" if t['pnl'] > 0 else "❌"
            print(f"      {status} {t['symbol']} {t['side']} ${t['pnl']:.2f} | {t['time']}")

    cursor.close()
    conn.close()

    print("\n" + "=" * 120)

if __name__ == '__main__':
    analyze_entry_signal_combos()
