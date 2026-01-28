#!/usr/bin/env python3
"""
统计开仓后2小时内平仓的订单
"""
import mysql.connector
from datetime import datetime, timedelta

DB_CONFIG = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def analyze_quick_closes():
    """分析快速平仓的订单"""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    # 查询所有已平仓的持仓
    cursor.execute("""
        SELECT
            id, symbol, position_side,
            open_time, close_time,
            margin, realized_pnl,
            entry_score,
            TIMESTAMPDIFF(MINUTE, open_time, close_time) as hold_minutes
        FROM futures_positions
        WHERE status = 'closed'
        AND account_id = 2
        AND open_time IS NOT NULL
        AND close_time IS NOT NULL
        AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
        ORDER BY open_time DESC
    """)

    all_closed = cursor.fetchall()

    if not all_closed:
        print("No closed positions found")
        cursor.close()
        conn.close()
        return

    # 统计分析
    quick_closes = []  # 2小时内平仓
    normal_closes = []  # 2小时后平仓

    for pos in all_closed:
        hold_minutes = pos['hold_minutes']
        if hold_minutes <= 120:  # 2小时 = 120分钟
            quick_closes.append(pos)
        else:
            normal_closes.append(pos)

    total = len(all_closed)
    quick_count = len(quick_closes)
    normal_count = len(normal_closes)
    quick_pct = (quick_count / total * 100) if total > 0 else 0

    print(f"=== 最近7天平仓订单分析 ===\n")
    print(f"总平仓订单: {total}")
    print(f"2小时内平仓: {quick_count} ({quick_pct:.1f}%)")
    print(f"2小时后平仓: {normal_count} ({100-quick_pct:.1f}%)")
    print()

    # 快速平仓盈亏分析
    if quick_closes:
        quick_profit = sum(p['realized_pnl'] or 0 for p in quick_closes)
        quick_win = sum(1 for p in quick_closes if (p['realized_pnl'] or 0) > 0)
        quick_loss = sum(1 for p in quick_closes if (p['realized_pnl'] or 0) < 0)
        quick_win_rate = (quick_win / quick_count * 100) if quick_count > 0 else 0

        print(f"=== 2小时内平仓详情 ===")
        print(f"总盈亏: ${quick_profit:.2f}")
        print(f"盈利单: {quick_win} ({quick_win_rate:.1f}%)")
        print(f"亏损单: {quick_loss} ({100-quick_win_rate:.1f}%)")
        print(f"平均持仓时长: {sum(p['hold_minutes'] for p in quick_closes) / quick_count:.0f} 分钟")
        print()

    # 正常持仓盈亏分析
    if normal_closes:
        normal_profit = sum(p['realized_pnl'] or 0 for p in normal_closes)
        normal_win = sum(1 for p in normal_closes if (p['realized_pnl'] or 0) > 0)
        normal_loss = sum(1 for p in normal_closes if (p['realized_pnl'] or 0) < 0)
        normal_win_rate = (normal_win / normal_count * 100) if normal_count > 0 else 0

        print(f"=== 2小时后平仓详情 ===")
        print(f"总盈亏: ${normal_profit:.2f}")
        print(f"盈利单: {normal_win} ({normal_win_rate:.1f}%)")
        print(f"亏损单: {normal_loss} ({100-normal_win_rate:.1f}%)")
        print(f"平均持仓时长: {sum(p['hold_minutes'] for p in normal_closes) / normal_count:.0f} 分钟")
        print()

    # 按持仓时长分段统计
    print(f"=== 持仓时长分布 ===")
    ranges = [
        (0, 30, "0-30分钟"),
        (30, 60, "30-60分钟"),
        (60, 120, "1-2小时"),
        (120, 240, "2-4小时"),
        (240, 360, "4-6小时"),
        (360, 999999, "6小时以上")
    ]

    for min_m, max_m, label in ranges:
        range_positions = [p for p in all_closed if min_m <= p['hold_minutes'] < max_m]
        count = len(range_positions)
        pct = (count / total * 100) if total > 0 else 0
        if count > 0:
            avg_pnl = sum(p['realized_pnl'] or 0 for p in range_positions) / count
            win_count = sum(1 for p in range_positions if (p['realized_pnl'] or 0) > 0)
            win_rate = (win_count / count * 100) if count > 0 else 0
            print(f"{label:12s}: {count:3d}单 ({pct:5.1f}%) | 平均盈亏: ${avg_pnl:+7.2f} | 胜率: {win_rate:5.1f}%")

    print()

    # 显示最近10个快速平仓的例子
    if quick_closes:
        print(f"=== 最近10个2小时内平仓的例子 ===")
        for i, pos in enumerate(quick_closes[:10], 1):
            pnl = pos['realized_pnl'] or 0
            print(f"{i:2d}. {pos['symbol']:15s} {pos['position_side']:5s} | "
                  f"持仓{pos['hold_minutes']:3d}分钟 | "
                  f"盈亏${pnl:+7.2f} | "
                  f"开仓:{pos['open_time'].strftime('%m-%d %H:%M')}")

    cursor.close()
    conn.close()

if __name__ == '__main__':
    analyze_quick_closes()
