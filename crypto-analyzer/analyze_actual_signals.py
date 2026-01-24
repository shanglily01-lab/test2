#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析实际开仓时的信号组合
"""

import sys
import io
import pymysql
import json

# 设置标准输出编码为UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def analyze_actual_signals():
    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    print("=" * 120)
    print("分析实际开仓的信号组合(昨晚8点到现在)")
    print("=" * 120)

    # 查询最近的交易,包含信号组成
    cursor.execute("""
        SELECT
            symbol, position_side, entry_score,
            signal_components, realized_pnl,
            open_time, close_time,
            TIMESTAMPDIFF(MINUTE, open_time, close_time) as holding_minutes
        FROM futures_positions
        WHERE close_time >= CONCAT(CURDATE() - INTERVAL 1 DAY, ' 20:00:00')
        AND status = 'closed'
        AND signal_components IS NOT NULL
        ORDER BY close_time DESC
        LIMIT 50
    """)

    trades = cursor.fetchall()

    if not trades:
        print("\n没有找到包含信号组成的交易记录")
        cursor.close()
        conn.close()
        return

    print(f"\n找到 {len(trades)} 笔交易\n")

    # 统计各信号组合的表现
    from collections import defaultdict
    signal_stats = defaultdict(lambda: {'count': 0, 'pnl': 0, 'wins': 0, 'losses': 0})

    for trade in trades:
        pnl = float(trade['realized_pnl'])

        # 解析信号组成
        try:
            components = json.loads(trade['signal_components'])
        except:
            continue

        # 统计每个信号的表现
        for signal_name, signal_score in components.items():
            signal_stats[signal_name]['count'] += 1
            signal_stats[signal_name]['pnl'] += pnl
            if pnl > 0:
                signal_stats[signal_name]['wins'] += 1
            else:
                signal_stats[signal_name]['losses'] += 1

    # 按出现次数排序
    sorted_signals = sorted(signal_stats.items(), key=lambda x: x[1]['count'], reverse=True)

    print("各信号实际参与交易的统计:")
    print("-" * 120)
    print(f"{'信号名称':<30} {'出现次数':<12} {'总盈亏':<15} {'胜率':<12} {'平均盈亏':<15}")
    print("-" * 120)

    for signal_name, stats in sorted_signals:
        count = stats['count']
        pnl = stats['pnl']
        wins = stats['wins']
        win_rate = (wins / count * 100) if count > 0 else 0
        avg_pnl = pnl / count if count > 0 else 0

        print(f"{signal_name:<30} {count:<12} ${pnl:<14.2f} {win_rate:<11.1f}% ${avg_pnl:<14.2f}")

    # 分析具体的信号组合
    print("\n" + "=" * 120)
    print("最近10笔交易的信号组合详情:")
    print("=" * 120)

    for i, trade in enumerate(trades[:10], 1):
        pnl = float(trade['realized_pnl'])
        status = "✅盈利" if pnl > 0 else "❌亏损"

        print(f"\n#{i} {trade['symbol']} {trade['position_side']} | 评分:{trade['entry_score']} | {status} ${pnl:.2f}")
        print(f"   持仓: {trade['holding_minutes']}分钟")
        print(f"   时间: {trade['open_time']} -> {trade['close_time']}")

        try:
            components = json.loads(trade['signal_components'])
            print("   信号组成:")
            for signal_name, signal_score in sorted(components.items(), key=lambda x: x[1], reverse=True):
                print(f"      {signal_name}: {signal_score}分")
        except:
            print("   信号组成: 解析失败")

    cursor.close()
    conn.close()

    print("\n" + "=" * 120)

if __name__ == '__main__':
    analyze_actual_signals()
