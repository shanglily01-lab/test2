#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析评分与盈利的关系
找出真正盈利的信号模式
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pymysql
from datetime import datetime, timedelta
import os
import json
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data'),
    'charset': 'utf8mb4'
}

def analyze_score_vs_profit(days=7):
    """分析评分与盈利的关系"""
    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    print("="*100)
    print(f"📊 评分与盈利关系分析 (最近{days}天)")
    print("="*100)

    # 获取有评分的已平仓订单
    cursor.execute("""
        SELECT
            symbol, position_side,
            entry_score,
            signal_components,
            realized_pnl,
            unrealized_pnl_pct as roi_pct,
            open_time, close_time,
            notes
        FROM futures_positions
        WHERE account_id = 2
        AND status = 'closed'
        AND close_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
        AND entry_score IS NOT NULL
        ORDER BY close_time DESC
    """, (days,))

    orders = cursor.fetchall()

    if not orders:
        print("\n❌ 没有找到订单数据")
        cursor.close()
        conn.close()
        return

    print(f"\n📈 总订单数: {len(orders)}")

    # 1. 按评分区间统计
    print("\n" + "="*100)
    print("1️⃣ 按评分区间统计 (评分越高真的越赚钱吗？)")
    print("="*100)

    score_ranges = {
        '150+分': (150, 999),
        '120-149分': (120, 149),
        '90-119分': (90, 119),
        '60-89分': (60, 89),
        '<60分': (0, 59),
    }

    for range_name, (min_score, max_score) in score_ranges.items():
        range_orders = [o for o in orders if min_score <= o['entry_score'] <= max_score]
        if not range_orders:
            continue

        wins = len([o for o in range_orders if o['realized_pnl'] > 0])
        total_pnl = sum(o['realized_pnl'] for o in range_orders)
        avg_pnl = total_pnl / len(range_orders)
        win_rate = wins / len(range_orders) * 100 if range_orders else 0

        status = "✅盈利" if total_pnl > 0 else "❌亏损"
        print(f"\n{range_name}: {len(range_orders)}单 | 胜率{win_rate:.1f}% | "
              f"总盈亏{total_pnl:+.2f}U | 平均{avg_pnl:+.2f}U/单 {status}")

    # 2. 按信号组合统计
    print("\n" + "="*100)
    print("2️⃣ 最盈利的信号组合TOP10")
    print("="*100)

    signal_stats = defaultdict(lambda: {'count': 0, 'wins': 0, 'pnl': 0.0, 'scores': []})

    for o in orders:
        if not o['signal_components']:
            continue
        try:
            components = json.loads(o['signal_components'])
            # 生成信号组合字符串（按字母排序保证一致性）
            signal_key = '+'.join(sorted(components.keys()))

            signal_stats[signal_key]['count'] += 1
            if o['realized_pnl'] > 0:
                signal_stats[signal_key]['wins'] += 1
            signal_stats[signal_key]['pnl'] += o['realized_pnl']
            signal_stats[signal_key]['scores'].append(o['entry_score'])
        except:
            continue

    # 按总盈亏排序
    sorted_signals = sorted(signal_stats.items(), key=lambda x: x[1]['pnl'], reverse=True)

    print(f"\n{'信号组合':<60s} | {'订单数':<6s} | {'胜率':<8s} | {'总盈亏':<12s} | {'平均分'}")
    print("-"*100)
    for signal_key, stats in sorted_signals[:10]:
        win_rate = stats['wins'] / stats['count'] * 100 if stats['count'] > 0 else 0
        avg_score = sum(stats['scores']) / len(stats['scores']) if stats['scores'] else 0
        avg_pnl = stats['pnl'] / stats['count']
        status = "✅" if stats['pnl'] > 0 else "❌"

        # 缩短信号名称显示
        short_signal = signal_key[:55] + '...' if len(signal_key) > 58 else signal_key

        print(f"{short_signal:<60s} | {stats['count']:<6d} | {win_rate:>6.1f}% | "
              f"{stats['pnl']:+11.2f}U | {avg_score:.0f}分 {status}")

    # 3. 最亏损的信号组合
    print("\n" + "="*100)
    print("3️⃣ 最亏损的信号组合TOP10 (这些应该避免)")
    print("="*100)

    print(f"\n{'信号组合':<60s} | {'订单数':<6s} | {'胜率':<8s} | {'总盈亏':<12s} | {'平均分'}")
    print("-"*100)
    for signal_key, stats in sorted_signals[-10:]:
        win_rate = stats['wins'] / stats['count'] * 100 if stats['count'] > 0 else 0
        avg_score = sum(stats['scores']) / len(stats['scores']) if stats['scores'] else 0
        avg_pnl = stats['pnl'] / stats['count']

        short_signal = signal_key[:55] + '...' if len(signal_key) > 58 else signal_key

        print(f"{short_signal:<60s} | {stats['count']:<6d} | {win_rate:>6.1f}% | "
              f"{stats['pnl']:+11.2f}U | {avg_score:.0f}分 ❌")

    # 4. 单个信号组件表现
    print("\n" + "="*100)
    print("4️⃣ 单个信号组件表现分析")
    print("="*100)

    component_stats = defaultdict(lambda: {'count': 0, 'wins': 0, 'pnl': 0.0})

    for o in orders:
        if not o['signal_components']:
            continue
        try:
            components = json.loads(o['signal_components'])
            for component in components.keys():
                component_stats[component]['count'] += 1
                if o['realized_pnl'] > 0:
                    component_stats[component]['wins'] += 1
                component_stats[component]['pnl'] += o['realized_pnl']
        except:
            continue

    # 按出现次数排序
    sorted_components = sorted(component_stats.items(), key=lambda x: x[1]['count'], reverse=True)

    print(f"\n{'组件名称':<30s} | {'出现次数':<8s} | {'胜率':<8s} | {'总盈亏':<12s} | {'平均盈亏'}")
    print("-"*100)
    for component, stats in sorted_components:
        win_rate = stats['wins'] / stats['count'] * 100 if stats['count'] > 0 else 0
        avg_pnl = stats['pnl'] / stats['count']
        status = "✅" if stats['pnl'] > 0 else "❌"

        print(f"{component:<30s} | {stats['count']:<8d} | {win_rate:>6.1f}% | "
              f"{stats['pnl']:+11.2f}U | {avg_pnl:+9.2f}U {status}")

    # 5. 关键发现总结
    print("\n" + "="*100)
    print("5️⃣ 关键发现总结")
    print("="*100)

    # 找出盈利最好的评分区间
    best_range = None
    best_pnl = float('-inf')
    for range_name, (min_score, max_score) in score_ranges.items():
        range_orders = [o for o in orders if min_score <= o['entry_score'] <= max_score]
        if range_orders:
            total_pnl = sum(o['realized_pnl'] for o in range_orders)
            if total_pnl > best_pnl:
                best_pnl = total_pnl
                best_range = (range_name, len(range_orders), total_pnl)

    if best_range:
        print(f"\n✅ 最盈利的评分区间: {best_range[0]} ({best_range[1]}单, 总盈亏{best_range[2]:+.2f}U)")

    # 找出胜率最高的信号组合（至少5单）
    high_winrate_signals = [(k, v) for k, v in signal_stats.items() if v['count'] >= 5]
    if high_winrate_signals:
        best_signal = max(high_winrate_signals, key=lambda x: x[1]['wins']/x[1]['count'])
        win_rate = best_signal[1]['wins'] / best_signal[1]['count'] * 100
        print(f"\n✅ 胜率最高的信号组合(≥5单): {best_signal[0][:60]}")
        print(f"   胜率{win_rate:.1f}% | {best_signal[1]['count']}单 | 盈亏{best_signal[1]['pnl']:+.2f}U")

    # 找出盈利最多的信号组合
    if sorted_signals:
        best_profit_signal = sorted_signals[0]
        win_rate = best_profit_signal[1]['wins'] / best_profit_signal[1]['count'] * 100
        print(f"\n💰 盈利最多的信号组合: {best_profit_signal[0][:60]}")
        print(f"   {best_profit_signal[1]['count']}单 | 胜率{win_rate:.1f}% | 总盈亏{best_profit_signal[1]['pnl']:+.2f}U")

    cursor.close()
    conn.close()

    print("\n" + "="*100)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='分析评分与盈利关系')
    parser.add_argument('--days', type=int, default=7, help='分析最近N天的数据 (默认7天)')
    args = parser.parse_args()

    analyze_score_vs_profit(args.days)
