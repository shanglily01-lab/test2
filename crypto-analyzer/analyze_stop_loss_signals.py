#!/usr/bin/env python3
"""
分析24小时内所有止损单的买入信号分布
"""
import mysql.connector
from collections import Counter
from datetime import datetime, timedelta

DB_CONFIG = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def analyze_stop_loss_signals():
    """分析止损单的信号分布"""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    # 查询24小时内所有止损平仓的订单
    cursor.execute("""
        SELECT
            id, symbol, position_side, 
            margin, entry_price, entry_score,
            entry_signal_type, signal_components,
            open_time, close_time,
            realized_pnl, stop_loss_price, notes
        FROM futures_positions
        WHERE status = 'closed'
        AND account_id = 2
        AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
        AND (notes LIKE '%止损%' OR notes LIKE '%stop_loss%')
        ORDER BY close_time DESC
    """)

    stop_loss_positions = cursor.fetchall()

    if not stop_loss_positions:
        print("过去24小时内没有止损单")
        cursor.close()
        conn.close()
        return

    print(f"{'='*80}")
    print(f"24小时内止损单分析 (共{len(stop_loss_positions)}单)")
    print(f"{'='*80}\n")

    # 统计数据
    total_pnl = sum(p['realized_pnl'] or 0 for p in stop_loss_positions)
    long_count = sum(1 for p in stop_loss_positions if p['position_side'] == 'LONG')
    short_count = sum(1 for p in stop_loss_positions if p['position_side'] == 'SHORT')

    # 统计信号类型
    signal_counter = Counter()
    signal_component_counter = Counter()
    
    # 按symbol统计
    symbol_counter = Counter()
    
    # 按评分区间统计
    score_ranges = {
        '30-35分': 0,
        '36-40分': 0,
        '41-45分': 0,
        '46-50分': 0,
        '50分以上': 0
    }

    for pos in stop_loss_positions:
        # 统计信号类型
        if pos['entry_signal_type']:
            signal_counter[pos['entry_signal_type']] += 1
            
            # 拆分信号组件
            components = pos['entry_signal_type'].split(' + ')
            for comp in components:
                signal_component_counter[comp.strip()] += 1
        
        # 统计symbol
        symbol_counter[pos['symbol']] += 1
        
        # 统计评分区间
        score = pos['entry_score'] or 0
        if 30 <= score <= 35:
            score_ranges['30-35分'] += 1
        elif 36 <= score <= 40:
            score_ranges['36-40分'] += 1
        elif 41 <= score <= 45:
            score_ranges['41-45分'] += 1
        elif 46 <= score <= 50:
            score_ranges['46-50分'] += 1
        else:
            score_ranges['50分以上'] += 1

    # 输出统计结果
    print(f"总体统计:")
    print(f"  总止损单数: {len(stop_loss_positions)}")
    print(f"  总亏损金额: ${total_pnl:.2f}")
    print(f"  平均亏损: ${total_pnl/len(stop_loss_positions):.2f}")
    print(f"  多单: {long_count} ({long_count/len(stop_loss_positions)*100:.1f}%)")
    print(f"  空单: {short_count} ({short_count/len(stop_loss_positions)*100:.1f}%)")
    print()

    # 输出评分分布
    print(f"评分分布:")
    for range_name, count in sorted(score_ranges.items()):
        if count > 0:
            pct = count / len(stop_loss_positions) * 100
            print(f"  {range_name}: {count}单 ({pct:.1f}%)")
    print()

    # 输出信号组件统计 (Top 15)
    print(f"信号组件统计 (Top 15):")
    for component, count in signal_component_counter.most_common(15):
        pct = count / len(stop_loss_positions) * 100
        print(f"  {component:40s}: {count:3d}次 ({pct:5.1f}%)")
    print()

    # 输出完整信号组合统计 (Top 10)
    print(f"完整信号组合统计 (Top 10):")
    for signal, count in signal_counter.most_common(10):
        pct = count / len(stop_loss_positions) * 100
        print(f"  {count:2d}单 ({pct:5.1f}%) - {signal}")
    print()

    # 输出交易对统计 (Top 10)
    print(f"交易对统计 (Top 10):")
    for symbol, count in symbol_counter.most_common(10):
        pct = count / len(stop_loss_positions) * 100
        # 计算该symbol的总亏损
        symbol_pnl = sum(p['realized_pnl'] or 0 for p in stop_loss_positions if p['symbol'] == symbol)
        print(f"  {symbol:15s}: {count:2d}单 ({pct:5.1f}%) | 亏损: ${symbol_pnl:+7.2f}")
    print()

    # 输出最近10个止损单的详细信息
    print(f"最近10个止损单详情:")
    print(f"{'-'*120}")
    for i, pos in enumerate(stop_loss_positions[:10], 1):
        hold_minutes = (pos['close_time'] - pos['open_time']).total_seconds() / 60
        pnl = pos['realized_pnl'] or 0
        roi = (pnl / pos['margin'] * 100) if pos['margin'] else 0
        
        print(f"{i:2d}. {pos['symbol']:15s} {pos['position_side']:5s} | "
              f"评分:{pos['entry_score']:2d} | "
              f"保证金:${pos['margin']:6.2f} | "
              f"持仓:{hold_minutes:5.0f}分钟 | "
              f"盈亏:${pnl:+7.2f} ({roi:+6.2f}%)")
        print(f"    信号: {pos['entry_signal_type']}")
        print(f"    开仓: {pos['open_time'].strftime('%m-%d %H:%M')} | "
              f"平仓: {pos['close_time'].strftime('%m-%d %H:%M')}")
        print()

    cursor.close()
    conn.close()

if __name__ == '__main__':
    analyze_stop_loss_signals()
