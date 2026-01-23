#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析最近的交易记录，诊断盈亏情况
"""
import pymysql
from datetime import datetime, timedelta
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data',
    'charset': 'utf8mb4'
}

try:
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    # 查询最近24小时的平仓记录
    time_threshold = datetime.utcnow() - timedelta(hours=24)
    cursor.execute('''
        SELECT
            id, symbol, position_side,
            entry_price, mark_price,
            quantity, leverage,
            realized_pnl, unrealized_pnl_pct,
            notes as close_reason,
            open_time, close_time,
            TIMESTAMPDIFF(MINUTE, open_time, close_time) as holding_minutes
        FROM futures_positions
        WHERE status = 'closed'
        AND close_time >= %s
        AND account_id = 2
        ORDER BY close_time DESC
        LIMIT 30
    ''', (time_threshold,))

    positions = cursor.fetchall()

    print('=' * 100)
    print('最近24小时交易分析（最新30笔）')
    print('=' * 100)
    print()

    # 统计数据
    total_trades = len(positions)
    profit_trades = sum(1 for p in positions if p['realized_pnl'] > 0)
    loss_trades = sum(1 for p in positions if p['realized_pnl'] < 0)
    break_even = sum(1 for p in positions if p['realized_pnl'] == 0)

    total_pnl = sum(p['realized_pnl'] for p in positions)
    total_profit = sum(p['realized_pnl'] for p in positions if p['realized_pnl'] > 0)
    total_loss = sum(p['realized_pnl'] for p in positions if p['realized_pnl'] < 0)

    win_rate = (profit_trades / total_trades * 100) if total_trades > 0 else 0

    avg_holding = sum(p['holding_minutes'] for p in positions) / total_trades if total_trades > 0 else 0

    print(f"交易总数: {total_trades}")
    print(f"盈利单: {profit_trades} ({profit_trades/total_trades*100:.1f}%)")
    print(f"亏损单: {loss_trades} ({loss_trades/total_trades*100:.1f}%)")
    print(f"平单: {break_even}")
    print(f"胜率: {win_rate:.1f}%")
    print()
    print(f"总盈亏: ${total_pnl:.2f}")
    print(f"总盈利: +${total_profit:.2f}")
    print(f"总亏损: ${total_loss:.2f}")
    print(f"盈亏比: {abs(total_profit/total_loss) if total_loss != 0 else 0:.2f}:1")
    print()
    print(f"平均持仓时长: {avg_holding:.0f}分钟 ({avg_holding/60:.1f}小时)")
    print()

    # 按平仓原因分组统计
    print('=' * 100)
    print('平仓原因分析')
    print('=' * 100)
    cursor.execute('''
        SELECT
            notes as close_reason,
            COUNT(*) as count,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(realized_pnl) as total_pnl,
            AVG(realized_pnl) as avg_pnl,
            AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)) as avg_minutes
        FROM futures_positions
        WHERE status = 'closed'
        AND close_time >= %s
        AND account_id = 2
        GROUP BY notes
        ORDER BY count DESC
    ''', (time_threshold,))

    reason_stats = cursor.fetchall()

    for stat in reason_stats:
        reason = stat['close_reason'] or '未知'
        count = stat['count']
        wins = stat['wins']
        win_rate_reason = wins / count * 100
        total_pnl = stat['total_pnl']
        avg_pnl = stat['avg_pnl']
        avg_min = stat['avg_minutes']

        print(f"\n{reason}:")
        print(f"  数量: {count} ({count/total_trades*100:.1f}%)")
        print(f"  胜率: {win_rate_reason:.1f}% ({wins}胜/{count-wins}负)")
        print(f"  总盈亏: ${total_pnl:.2f}")
        print(f"  平均盈亏: ${avg_pnl:.2f}")
        print(f"  平均持仓: {avg_min:.0f}分钟")

    # 显示最差的10笔交易
    print()
    print('=' * 100)
    print('最差10笔交易')
    print('=' * 100)
    worst_trades = sorted(positions, key=lambda x: x['realized_pnl'])[:10]

    for i, trade in enumerate(worst_trades, 1):
        print(f"\n{i}. {trade['symbol']} {trade['position_side']}")
        print(f"   开: ${trade['entry_price']:.4f} → 平: ${trade['mark_price']:.4f}")
        print(f"   盈亏: ${trade['realized_pnl']:.2f} ({trade['unrealized_pnl_pct']:.2f}%)")
        print(f"   持仓: {trade['holding_minutes']}分钟")
        print(f"   原因: {trade['close_reason']}")
        print(f"   时间: {trade['close_time']}")

    # 显示最好的10笔交易
    print()
    print('=' * 100)
    print('最好10笔交易')
    print('=' * 100)
    best_trades = sorted(positions, key=lambda x: x['realized_pnl'], reverse=True)[:10]

    for i, trade in enumerate(best_trades, 1):
        print(f"\n{i}. {trade['symbol']} {trade['position_side']}")
        print(f"   开: ${trade['entry_price']:.4f} → 平: ${trade['mark_price']:.4f}")
        print(f"   盈亏: ${trade['realized_pnl']:.2f} ({trade['unrealized_pnl_pct']:.2f}%)")
        print(f"   持仓: {trade['holding_minutes']}分钟")
        print(f"   原因: {trade['close_reason']}")
        print(f"   时间: {trade['close_time']}")

    cursor.close()
    conn.close()

except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
