#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日交易报告
- 连接服务器端数据库
- 统计当天所有平仓交易的盈亏情况
- 分析各交易对表现
- 分析平仓原因分布
"""

import sys
import io
import mysql.connector
from datetime import datetime, timedelta
from collections import defaultdict

# 设置标准输出为UTF-8编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 服务器数据库配置
DB_CONFIG = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def get_daily_report(days_ago=0):
    """
    获取指定日期的交易报告

    Args:
        days_ago: 0=今天, 1=昨天, 2=前天...
    """
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    # 计算日期范围
    target_date = datetime.now() - timedelta(days=days_ago)
    date_str = target_date.strftime('%Y-%m-%d')

    print('='*100)
    print(f'📊 交易报告 - {date_str} ({["今天", "昨天", "前天"][days_ago] if days_ago < 3 else f"{days_ago}天前"})')
    print('='*100)
    print()

    # 查询当天的所有平仓交易
    query = '''
    SELECT
        symbol,
        position_side,
        entry_price,
        mark_price,
        quantity,
        leverage,
        realized_pnl,
        entry_signal_type,
        open_time,
        close_time,
        TIMESTAMPDIFF(MINUTE, open_time, close_time) as holding_minutes,
        notes
    FROM futures_positions
    WHERE status = 'CLOSED'
        AND DATE(close_time) = %s
    ORDER BY close_time DESC
    '''

    cursor.execute(query, (date_str,))
    positions = cursor.fetchall()

    if not positions:
        print(f'📭 {date_str} 没有交易记录')
        cursor.close()
        conn.close()
        return

    # ==================== 总体统计 ====================
    total_trades = len(positions)
    total_pnl = 0
    win_count = 0
    loss_count = 0
    win_pnl = 0
    loss_pnl = 0

    # 按策略统计
    strategy_stats = defaultdict(lambda: {
        'count': 0, 'pnl': 0, 'wins': 0, 'losses': 0,
        'win_pnl': 0, 'loss_pnl': 0
    })

    # 按交易对统计
    symbol_stats = defaultdict(lambda: {
        'count': 0, 'pnl': 0, 'wins': 0, 'losses': 0,
        'long_count': 0, 'short_count': 0,
        'long_pnl': 0, 'short_pnl': 0
    })

    # 按平仓原因统计
    close_reason_stats = defaultdict(int)

    # 按持仓时长统计
    duration_buckets = {
        '< 30m': 0,
        '30m-1h': 0,
        '1h-2h': 0,
        '2h-4h': 0,
        '4h-8h': 0,
        '> 8h': 0
    }

    for pos in positions:
        pnl = float(pos['realized_pnl'] or 0)
        symbol = pos['symbol']
        signal_type = pos['entry_signal_type'] or 'unknown'
        holding_minutes = pos['holding_minutes'] or 0

        # 总体统计
        total_pnl += pnl
        if pnl > 0:
            win_count += 1
            win_pnl += pnl
        else:
            loss_count += 1
            loss_pnl += pnl

        # 策略统计
        strategy_stats[signal_type]['count'] += 1
        strategy_stats[signal_type]['pnl'] += pnl
        if pnl > 0:
            strategy_stats[signal_type]['wins'] += 1
            strategy_stats[signal_type]['win_pnl'] += pnl
        else:
            strategy_stats[signal_type]['losses'] += 1
            strategy_stats[signal_type]['loss_pnl'] += pnl

        # 交易对统计
        symbol_stats[symbol]['count'] += 1
        symbol_stats[symbol]['pnl'] += pnl
        if pnl > 0:
            symbol_stats[symbol]['wins'] += 1
        else:
            symbol_stats[symbol]['losses'] += 1

        if pos['position_side'] == 'LONG':
            symbol_stats[symbol]['long_count'] += 1
            symbol_stats[symbol]['long_pnl'] += pnl
        else:
            symbol_stats[symbol]['short_count'] += 1
            symbol_stats[symbol]['short_pnl'] += pnl

        # 平仓原因统计
        notes = pos['notes'] or ''
        reason = 'unknown'

        # 尝试多种格式解析
        if 'close_reason:' in notes:
            reason = notes.split('close_reason:')[1].split('|')[0].strip()
        elif '|' in notes:
            # 格式: reason_code|param1:value|param2:value
            reason = notes.split('|')[0].strip()
        elif notes and notes != '':
            # 直接使用notes作为原因
            reason = notes.strip()

        close_reason_stats[reason] += 1

        # 持仓时长统计
        if holding_minutes < 30:
            duration_buckets['< 30m'] += 1
        elif holding_minutes < 60:
            duration_buckets['30m-1h'] += 1
        elif holding_minutes < 120:
            duration_buckets['1h-2h'] += 1
        elif holding_minutes < 240:
            duration_buckets['2h-4h'] += 1
        elif holding_minutes < 480:
            duration_buckets['4h-8h'] += 1
        else:
            duration_buckets['> 8h'] += 1

    # 计算统计指标
    win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
    avg_win = win_pnl / win_count if win_count > 0 else 0
    avg_loss = loss_pnl / loss_count if loss_count > 0 else 0
    profit_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    expected_value = (win_rate/100 * avg_win) + ((100-win_rate)/100 * avg_loss)

    # ==================== 输出总体统计 ====================
    print('📈 总体表现')
    print('-'*100)
    print(f'总交易笔数: {total_trades}')
    print(f'总盈亏: ${total_pnl:+.2f}')
    print(f'胜率: {win_rate:.1f}% ({win_count}胜 / {loss_count}败)')
    print(f'平均盈利: ${avg_win:.2f}')
    print(f'平均亏损: ${avg_loss:.2f}')
    print(f'盈亏比: {profit_loss_ratio:.2f}')
    print(f'期望值: ${expected_value:.2f}')
    print()

    # ==================== 按策略统计 ====================
    print('🎯 策略表现')
    print('-'*100)
    print(f"{'策略':<30} {'笔数':>6} {'总盈亏':>10} {'胜率':>8} {'期望值':>10}")
    print('-'*100)

    strategy_list = sorted(strategy_stats.items(), key=lambda x: x[1]['pnl'], reverse=True)
    for strategy, stats in strategy_list:
        count = stats['count']
        pnl = stats['pnl']
        wins = stats['wins']
        wr = (wins / count * 100) if count > 0 else 0
        avg_w = stats['win_pnl'] / wins if wins > 0 else 0
        avg_l = stats['loss_pnl'] / stats['losses'] if stats['losses'] > 0 else 0
        ev = (wr/100 * avg_w) + ((100-wr)/100 * avg_l)

        strategy_short = strategy[:28] if len(strategy) > 28 else strategy
        print(f"{strategy_short:<30} {count:>6} ${pnl:>9.2f} {wr:>6.1f}% ${ev:>9.2f}")
    print()

    # ==================== 交易对表现 TOP 10 ====================
    print('💰 交易对表现 (按盈亏排序 TOP 10)')
    print('-'*100)
    print(f"{'交易对':<15} {'笔数':>6} {'总盈亏':>10} {'胜率':>8} {'做多':>12} {'做空':>12}")
    print('-'*100)

    symbol_list = sorted(symbol_stats.items(), key=lambda x: x[1]['pnl'], reverse=True)
    for symbol, stats in symbol_list[:10]:
        count = stats['count']
        pnl = stats['pnl']
        wins = stats['wins']
        wr = (wins / count * 100) if count > 0 else 0
        long_info = f"{stats['long_count']}笔${stats['long_pnl']:+.2f}" if stats['long_count'] > 0 else '-'
        short_info = f"{stats['short_count']}笔${stats['short_pnl']:+.2f}" if stats['short_count'] > 0 else '-'

        print(f"{symbol:<15} {count:>6} ${pnl:>9.2f} {wr:>6.1f}% {long_info:>12} {short_info:>12}")
    print()

    # ==================== 最差交易对 TOP 5 ====================
    print('⚠️  最差交易对 TOP 5')
    print('-'*100)
    print(f"{'交易对':<15} {'笔数':>6} {'总盈亏':>10} {'胜率':>8} {'做多':>12} {'做空':>12}")
    print('-'*100)

    worst_symbols = sorted(symbol_stats.items(), key=lambda x: x[1]['pnl'])[:5]
    for symbol, stats in worst_symbols:
        count = stats['count']
        pnl = stats['pnl']
        wins = stats['wins']
        wr = (wins / count * 100) if count > 0 else 0
        long_info = f"{stats['long_count']}笔${stats['long_pnl']:+.2f}" if stats['long_count'] > 0 else '-'
        short_info = f"{stats['short_count']}笔${stats['short_count']:+.2f}" if stats['short_count'] > 0 else '-'

        print(f"{symbol:<15} {count:>6} ${pnl:>9.2f} {wr:>6.1f}% {long_info:>12} {short_info:>12}")
    print()

    # ==================== 平仓原因分布 ====================
    print('📊 平仓原因分布')
    print('-'*100)
    print(f"{'原因':<40} {'笔数':>10} {'占比':>10}")
    print('-'*100)

    reason_list = sorted(close_reason_stats.items(), key=lambda x: x[1], reverse=True)
    for reason, count in reason_list:
        percentage = (count / total_trades * 100) if total_trades > 0 else 0
        reason_short = reason[:38] if len(reason) > 38 else reason
        print(f"{reason_short:<40} {count:>10} {percentage:>9.1f}%")
    print()

    # ==================== 持仓时长分布 ====================
    print('⏱️  持仓时长分布')
    print('-'*100)
    print(f"{'时长':<15} {'笔数':>10} {'占比':>10}")
    print('-'*100)

    for duration, count in duration_buckets.items():
        percentage = (count / total_trades * 100) if total_trades > 0 else 0
        print(f"{duration:<15} {count:>10} {percentage:>9.1f}%")
    print()

    # ==================== 最近10笔交易 ====================
    print('📋 最近10笔交易')
    print('-'*100)
    print(f"{'时间':<16} {'交易对':<12} {'方向':<6} {'入场价':<10} {'出场价':<10} {'盈亏%':<8} {'盈亏$':<10} {'持仓':<8} {'原因':<30}")
    print('-'*100)

    for pos in positions[:10]:
        pnl = float(pos['realized_pnl'] or 0)
        holding = pos['holding_minutes'] or 0
        entry = float(pos['entry_price'] or 0)
        exit_p = float(pos['mark_price'] or 0)

        # 计算价格变化百分比
        if pos['position_side'] == 'LONG':
            price_change = (exit_p - entry) / entry * 100 if entry > 0 else 0
        else:
            price_change = (entry - exit_p) / entry * 100 if entry > 0 else 0

        # 保证金盈亏百分比
        leverage = pos['leverage'] or 10
        margin_pnl_pct = price_change * leverage

        # 持仓时长显示
        if holding < 60:
            hold_str = f"{holding}m"
        elif holding < 1440:
            hold_str = f"{holding/60:.1f}h"
        else:
            hold_str = f"{holding/1440:.1f}d"

        # 提取平仓原因
        notes = pos['notes'] or ''
        reason = 'unknown'

        # 尝试多种格式解析
        if 'close_reason:' in notes:
            reason = notes.split('close_reason:')[1].split('|')[0].strip()
        elif '|' in notes:
            # 格式: reason_code|param1:value
            reason = notes.split('|')[0].strip()
        elif notes and notes != '':
            reason = notes.strip()

        reason = reason[:28] if len(reason) > 28 else reason
        close_time_str = pos['close_time'].strftime('%m-%d %H:%M')

        print(f"{close_time_str:<16} {pos['symbol']:<12} {pos['position_side']:<6} "
              f"${entry:<9.4f} ${exit_p:<9.4f} {margin_pnl_pct:>6.1f}% "
              f"${pnl:>8.2f} {hold_str:<8} {reason:<30}")

    print()
    print('='*100)

    cursor.close()
    conn.close()

def compare_reports():
    """对比今天和昨天的报告"""
    print()
    print('='*100)
    print('📅 今天 vs 昨天对比')
    print('='*100)
    print()
    get_daily_report(days_ago=0)  # 今天
    print()
    get_daily_report(days_ago=1)  # 昨天
    print()

def main():
    """主函数 - 生成今天的报告"""
    import sys

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == 'compare':
            compare_reports()
        elif arg.isdigit():
            days_ago = int(arg)
            print()
            get_daily_report(days_ago=days_ago)
            print()
        else:
            print('用法:')
            print('  python daily_trading_report.py           # 今天的报告')
            print('  python daily_trading_report.py compare   # 今天 vs 昨天对比')
            print('  python daily_trading_report.py 1         # 昨天的报告')
            print('  python daily_trading_report.py 2         # 前天的报告')
    else:
        # 默认显示今天的报告
        print()
        get_daily_report(days_ago=0)
        print()

if __name__ == '__main__':
    main()
