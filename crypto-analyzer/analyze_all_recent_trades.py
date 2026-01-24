#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析最近24小时的所有交易（不限数量）
"""

import sys
import io
import pymysql
from datetime import datetime, timedelta
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

def analyze_all_trades():
    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    # 查询昨晚8点到现在的所有已平仓交易
    cursor.execute("""
        SELECT
            symbol, position_side as side, entry_price, mark_price as exit_price, quantity,
            realized_pnl, unrealized_pnl_pct as pnl_pct, margin,
            open_time, close_time, notes,
            TIMESTAMPDIFF(MINUTE, open_time, close_time) as holding_minutes
        FROM futures_positions
        WHERE status = 'closed'
        AND close_time >= CONCAT(CURDATE() - INTERVAL 1 DAY, ' 20:00:00')
        ORDER BY close_time DESC
    """)

    trades = cursor.fetchall()
    cursor.close()
    conn.close()

    if not trades:
        print("最近24小时没有交易记录")
        return

    total_count = len(trades)
    wins = sum(1 for t in trades if float(t['realized_pnl']) > 0)
    losses = sum(1 for t in trades if float(t['realized_pnl']) < 0)
    breakeven = sum(1 for t in trades if float(t['realized_pnl']) == 0)

    total_pnl = sum(float(t['realized_pnl']) for t in trades)
    total_profit = sum(float(t['realized_pnl']) for t in trades if float(t['realized_pnl']) > 0)
    total_loss = sum(float(t['realized_pnl']) for t in trades if float(t['realized_pnl']) < 0)

    print("=" * 100)
    print(f"昨晚8点到现在交易分析（全部{total_count}笔）")
    print("=" * 100)
    print()
    print(f"交易总数: {total_count}")
    print(f"盈利单: {wins} ({wins/total_count*100:.1f}%)")
    print(f"亏损单: {losses} ({losses/total_count*100:.1f}%)")
    print(f"平单: {breakeven}")
    print(f"胜率: {wins/total_count*100:.1f}%")
    print()
    print(f"总盈亏: ${total_pnl:.2f}")
    print(f"总盈利: +${total_profit:.2f}")
    print(f"总亏损: ${total_loss:.2f}")
    print(f"盈亏比: {abs(total_profit/total_loss):.2f}:1" if total_loss != 0 else "N/A")
    print()
    print(f"平均持仓时长: {sum(t['holding_minutes'] for t in trades)/total_count:.0f}分钟 ({sum(t['holding_minutes'] for t in trades)/total_count/60:.1f}小时)")

    # 按平仓原因统计
    print()
    print("=" * 100)
    print("按平仓原因统计")
    print("=" * 100)

    reason_stats = defaultdict(lambda: {'count': 0, 'wins': 0, 'losses': 0, 'pnl': 0, 'holding_time': 0})

    for trade in trades:
        notes = str(trade['notes']) if trade['notes'] else '未知'
        # 提取主要原因
        if 'hard_stop_loss' in notes:
            reason = 'hard_stop_loss'
        elif 'hedge_loss_cut' in notes:
            reason = 'hedge_loss_cut'
        elif 'TIMEOUT_4H' in notes:
            reason = 'TIMEOUT_4H'
        elif 'STAGED_TIMEOUT' in notes:
            if 'STAGED_TIMEOUT_1H' in notes:
                reason = 'STAGED_TIMEOUT_1H'
            elif 'STAGED_TIMEOUT_2H' in notes:
                reason = 'STAGED_TIMEOUT_2H'
            elif 'STAGED_TIMEOUT_3H' in notes:
                reason = 'STAGED_TIMEOUT_3H'
            elif 'STAGED_TIMEOUT_4H' in notes:
                reason = 'STAGED_TIMEOUT_4H'
            else:
                reason = 'STAGED_TIMEOUT_其他'
        elif 'DYNAMIC_TIMEOUT' in notes:
            reason = 'DYNAMIC_TIMEOUT'
        elif 'TOP_DETECTED' in notes or 'BOTTOM_DETECTED' in notes:
            reason = '智能顶底识别'
        elif 'STOP_LOSS' in notes:
            reason = 'STOP_LOSS'
        elif '止盈' in notes or 'take_profit' in notes.lower():
            reason = '止盈'
        elif 'reverse_signal' in notes:
            reason = '反转信号'
        else:
            reason = '其他'

        reason_stats[reason]['count'] += 1
        pnl = float(trade['realized_pnl'])
        if pnl > 0:
            reason_stats[reason]['wins'] += 1
        elif pnl < 0:
            reason_stats[reason]['losses'] += 1
        reason_stats[reason]['pnl'] += pnl
        reason_stats[reason]['holding_time'] += trade['holding_minutes']

    # 按数量排序
    sorted_reasons = sorted(reason_stats.items(), key=lambda x: x[1]['count'], reverse=True)

    for reason, stats in sorted_reasons:
        count = stats['count']
        wins = stats['wins']
        losses = stats['losses']
        pnl = stats['pnl']
        avg_holding = stats['holding_time'] / count if count > 0 else 0
        win_rate = wins / count * 100 if count > 0 else 0

        print(f"\n{reason}:")
        print(f"  数量: {count} ({count/total_count*100:.1f}%)")
        print(f"  胜率: {win_rate:.1f}% ({wins}胜/{losses}负)")
        print(f"  总盈亏: ${pnl:.2f}")
        print(f"  平均盈亏: ${pnl/count:.2f}")
        print(f"  平均持仓: {avg_holding:.0f}分钟")

    # 按交易对统计
    print()
    print("=" * 100)
    print("按交易对统计（只显示交易5次以上的）")
    print("=" * 100)

    symbol_stats = defaultdict(lambda: {'count': 0, 'wins': 0, 'losses': 0, 'pnl': 0})

    for trade in trades:
        symbol = trade['symbol']
        pnl = float(trade['realized_pnl'])
        symbol_stats[symbol]['count'] += 1
        if pnl > 0:
            symbol_stats[symbol]['wins'] += 1
        elif pnl < 0:
            symbol_stats[symbol]['losses'] += 1
        symbol_stats[symbol]['pnl'] += pnl

    # 过滤交易5次以上的,按盈亏排序
    filtered_symbols = {k: v for k, v in symbol_stats.items() if v['count'] >= 5}
    sorted_symbols = sorted(filtered_symbols.items(), key=lambda x: x[1]['pnl'])

    for symbol, stats in sorted_symbols:
        count = stats['count']
        wins = stats['wins']
        losses = stats['losses']
        pnl = stats['pnl']
        win_rate = wins / count * 100 if count > 0 else 0

        print(f"\n{symbol}:")
        print(f"  数量: {count}笔")
        print(f"  胜率: {win_rate:.1f}% ({wins}胜/{losses}负)")
        print(f"  总盈亏: ${pnl:.2f}")
        print(f"  平均盈亏: ${pnl/count:.2f}")

    print()
    print("=" * 100)


if __name__ == '__main__':
    analyze_all_trades()
