#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析今天的交易表现
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pymysql
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST', '13.212.252.171'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'admin'),
    'password': os.getenv('DB_PASSWORD', 'Tonny@1000'),
    'database': os.getenv('DB_NAME', 'binance-data'),
    'charset': 'utf8mb4'
}

def analyze_today():
    """分析今天的交易"""
    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    print("="*80)
    print(f"今天交易分析 - {datetime.now().strftime('%Y-%m-%d')}")
    print("="*80)

    # 今天的已平仓订单
    cursor.execute("""
        SELECT
            symbol, position_side,
            entry_price,
            realized_pnl, unrealized_pnl_pct as roi_pct,
            open_time, close_time,
            notes,
            entry_score
        FROM futures_positions
        WHERE account_id = 2
        AND status = 'closed'
        AND DATE(close_time) = CURDATE()
        ORDER BY close_time DESC
    """)

    today_orders = cursor.fetchall()

    if not today_orders:
        print("\n❌ 今天还没有已平仓的订单")
        cursor.close()
        conn.close()
        return

    # 统计数据
    total_orders = len(today_orders)
    wins = [o for o in today_orders if o['realized_pnl'] > 0]
    losses = [o for o in today_orders if o['realized_pnl'] < 0]

    total_pnl = sum(o['realized_pnl'] for o in today_orders)
    win_rate = len(wins) / total_orders * 100 if total_orders > 0 else 0

    print(f"\n📊 今日概况:")
    print(f"总订单数: {total_orders}")
    print(f"盈利单数: {len(wins)} | 亏损单数: {len(losses)}")
    print(f"胜率: {win_rate:.1f}%")
    print(f"总盈亏: {total_pnl:.2f} USDT {'✅' if total_pnl > 0 else '❌'}")

    # 按方向统计
    longs = [o for o in today_orders if o['position_side'] == 'LONG']
    shorts = [o for o in today_orders if o['position_side'] == 'SHORT']

    print(f"\n📈 做多: {len(longs)}单")
    if longs:
        long_pnl = sum(o['realized_pnl'] for o in longs)
        long_wins = len([o for o in longs if o['realized_pnl'] > 0])
        print(f"   胜率: {long_wins}/{len(longs)} = {long_wins/len(longs)*100:.1f}%")
        print(f"   盈亏: {long_pnl:.2f} USDT")

    print(f"\n📉 做空: {len(shorts)}单")
    if shorts:
        short_pnl = sum(o['realized_pnl'] for o in shorts)
        short_wins = len([o for o in shorts if o['realized_pnl'] > 0])
        print(f"   胜率: {short_wins}/{len(shorts)} = {short_wins/len(shorts)*100:.1f}%")
        print(f"   盈亏: {short_pnl:.2f} USDT")

    # 平仓原因统计
    print(f"\n🔍 平仓原因统计:")
    close_reasons = {}
    for o in today_orders:
        reason = o['notes'] or '未知'
        if reason not in close_reasons:
            close_reasons[reason] = {'count': 0, 'pnl': 0}
        close_reasons[reason]['count'] += 1
        close_reasons[reason]['pnl'] += o['realized_pnl']

    for reason, stats in sorted(close_reasons.items(), key=lambda x: x[1]['count'], reverse=True):
        print(f"   {reason}: {stats['count']}单, 盈亏{stats['pnl']:.2f}U")

    # 最大亏损订单
    if losses:
        print(f"\n💀 最大亏损订单TOP5:")
        worst_orders = sorted(losses, key=lambda x: x['realized_pnl'])[:5]
        for i, o in enumerate(worst_orders, 1):
            duration = (o['close_time'] - o['open_time']).total_seconds() / 60
            print(f"   {i}. {o['symbol']:15s} {o['position_side']:5s} | "
                  f"亏损: {o['realized_pnl']:7.2f}U ({o['roi_pct']:.1f}%) | "
                  f"持仓{duration:.0f}分钟 | {o['notes']}")

    # 最大盈利订单
    if wins:
        print(f"\n🏆 最大盈利订单TOP5:")
        best_orders = sorted(wins, key=lambda x: x['realized_pnl'], reverse=True)[:5]
        for i, o in enumerate(best_orders, 1):
            duration = (o['close_time'] - o['open_time']).total_seconds() / 60
            print(f"   {i}. {o['symbol']:15s} {o['position_side']:5s} | "
                  f"盈利: {o['realized_pnl']:7.2f}U ({o['roi_pct']:.1f}%) | "
                  f"持仓{duration:.0f}分钟 | {o['notes']}")

    # 亏损币种统计
    print(f"\n📊 亏损币种TOP10:")
    cursor.execute("""
        SELECT
            symbol,
            COUNT(*) as trade_count,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(realized_pnl) as total_pnl
        FROM futures_positions
        WHERE account_id = 2
        AND status = 'closed'
        AND DATE(close_time) = CURDATE()
        GROUP BY symbol
        HAVING total_pnl < 0
        ORDER BY total_pnl ASC
        LIMIT 10
    """)

    losing_symbols = cursor.fetchall()
    for row in losing_symbols:
        win_rate = row['wins'] / row['trade_count'] * 100
        print(f"   {row['symbol']:15s} | {row['trade_count']}单 "
              f"胜率{win_rate:.0f}% | 亏损 {row['total_pnl']:.2f}U")

    # 检查当前持仓
    print(f"\n📦 当前持仓:")
    cursor.execute("""
        SELECT
            symbol, position_side,
            margin, leverage,
            unrealized_pnl, unrealized_pnl_pct,
            open_time,
            TIMESTAMPDIFF(MINUTE, open_time, NOW()) as hold_minutes
        FROM futures_positions
        WHERE account_id = 2
        AND status = 'open'
        ORDER BY open_time DESC
    """)

    open_positions = cursor.fetchall()
    if open_positions:
        print(f"   共 {len(open_positions)} 个持仓:")
        for pos in open_positions:
            pnl_indicator = "✅" if pos['unrealized_pnl'] > 0 else "❌"
            print(f"   {pnl_indicator} {pos['symbol']:15s} {pos['position_side']:5s} | "
                  f"浮盈: {pos['unrealized_pnl']:7.2f}U ({pos['unrealized_pnl_pct']:.1f}%) | "
                  f"持仓{pos['hold_minutes']}分钟")
    else:
        print("   当前无持仓")

    cursor.close()
    conn.close()

    print("\n" + "="*80)

if __name__ == '__main__':
    analyze_today()
