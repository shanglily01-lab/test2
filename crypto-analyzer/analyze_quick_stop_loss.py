#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析快速止损问题 - 开仓后很快就被止损的交易
"""

import sys
import io
import pymysql
from datetime import datetime, timedelta

# 设置标准输出编码为UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def analyze_quick_stop_loss():
    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    print("=" * 100)
    print("分析最近24小时内快速止损的交易(持仓时间<30分钟)")
    print("=" * 100)

    # 查询昨晚8点到现在的所有快速止损交易
    cursor.execute("""
        SELECT
            symbol, position_side, entry_price, mark_price, realized_pnl,
            margin, open_time, close_time, notes, entry_signal_type,
            TIMESTAMPDIFF(MINUTE, open_time, close_time) as holding_minutes
        FROM futures_positions
        WHERE status = 'closed'
        AND close_time >= CONCAT(CURDATE() - INTERVAL 1 DAY, ' 20:00:00')
        AND TIMESTAMPDIFF(MINUTE, open_time, close_time) < 30
        AND notes LIKE '%stop_loss%'
        ORDER BY holding_minutes ASC
    """)

    trades = cursor.fetchall()

    if not trades:
        print("\n没有找到快速止损的交易")
    else:
        print(f"\n找到 {len(trades)} 笔快速止损交易:\n")

        total_loss = 0
        for t in trades:
            holding_time = t['holding_minutes']
            pnl = float(t['realized_pnl'])
            total_loss += pnl

            print(f"交易对: {t['symbol']}")
            print(f"  方向: {t['position_side']}")
            print(f"  信号: {t['entry_signal_type']}")
            print(f"  持仓时间: {holding_time}分钟")
            print(f"  开仓价: ${t['entry_price']}")
            print(f"  平仓价: ${t['mark_price']}")
            print(f"  盈亏: ${pnl:.2f}")
            print(f"  保证金: ${t['margin']}")
            print(f"  平仓原因: {t['notes']}")
            print()

        print(f"快速止损总亏损: ${total_loss:.2f}")

    # 统计各个交易对的快速止损次数
    print("\n" + "=" * 100)
    print("按交易对统计快速止损")
    print("=" * 100)

    cursor.execute("""
        SELECT
            symbol,
            COUNT(*) as count,
            SUM(realized_pnl) as total_pnl,
            AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)) as avg_holding_minutes
        FROM futures_positions
        WHERE status = 'closed'
        AND close_time >= CONCAT(CURDATE() - INTERVAL 1 DAY, ' 20:00:00')
        AND TIMESTAMPDIFF(MINUTE, open_time, close_time) < 30
        AND notes LIKE '%stop_loss%'
        GROUP BY symbol
        ORDER BY count DESC
    """)

    symbol_stats = cursor.fetchall()

    if symbol_stats:
        print()
        for s in symbol_stats:
            print(f"{s['symbol']}:")
            print(f"  快速止损次数: {s['count']}")
            print(f"  平均持仓: {s['avg_holding_minutes']:.1f}分钟")
            print(f"  总亏损: ${float(s['total_pnl']):.2f}")
            print()

    cursor.close()
    conn.close()

    print("=" * 100)

if __name__ == '__main__':
    analyze_quick_stop_loss()
