#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析昨天Big4的表现和影响"""

import pymysql
import sys
import io
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME')
)

cursor = conn.cursor(pymysql.cursors.DictCursor)

print('=' * 120)
print('🌟 Big4 趋势检测系统 - 昨晚行情复盘')
print('=' * 120)
print()

try:
    # 定义时间范围
    yesterday_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    yesterday_end = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    print(f'分析时间段: {yesterday_start.strftime("%Y-%m-%d %H:%M")} ~ {yesterday_end.strftime("%Y-%m-%d %H:%M")}')
    print()

    # 1. 昨晚Big4市场信号分布
    print('=' * 120)
    print('📊 Part 1: Big4 市场信号统计')
    print('=' * 120)
    print()

    cursor.execute("""
        SELECT overall_signal, COUNT(*) as count,
               AVG(signal_strength) as avg_strength,
               AVG(bullish_count) as avg_bullish,
               AVG(bearish_count) as avg_bearish
        FROM big4_trend_history
        WHERE created_at >= %s AND created_at < %s
        GROUP BY overall_signal
        ORDER BY count DESC
    """, (yesterday_start, yesterday_end))

    signal_stats = cursor.fetchall()

    if signal_stats:
        total_signals = sum(s['count'] for s in signal_stats)
        print(f'昨晚共产生 {total_signals} 次信号检测\n')

        for stat in signal_stats:
            signal = stat['overall_signal']
            count = stat['count']
            pct = (count / total_signals * 100) if total_signals else 0
            avg_str = float(stat['avg_strength'] or 0)
            avg_bull = float(stat['avg_bullish'] or 0)
            avg_bear = float(stat['avg_bearish'] or 0)

            emoji = {'BULLISH': '🟢', 'BEARISH': '🔴', 'NEUTRAL': '⚪'}.get(signal, '⚪')

            print(f'{emoji} {signal:<10} {count:>4}次 ({pct:>5.1f}%) | '
                  f'平均强度:{avg_str:>5.1f} | 涨:{avg_bull:.1f}/跌:{avg_bear:.1f}')

        # 找出信号变化的关键时刻
        print()
        print('🔄 信号变化关键时刻:')
        print('-' * 120)

        cursor.execute("""
            SELECT created_at, overall_signal, signal_strength,
                   bullish_count, bearish_count, recommendation
            FROM big4_trend_history
            WHERE created_at >= %s AND created_at < %s
            ORDER BY created_at
        """, (yesterday_start, yesterday_end))

        all_signals = cursor.fetchall()

        if all_signals:
            prev_signal = None
            for sig in all_signals[:30]:  # 限制显示前30条
                curr_signal = sig['overall_signal']
                if curr_signal != prev_signal:
                    emoji = {'BULLISH': '🟢', 'BEARISH': '🔴', 'NEUTRAL': '⚪'}.get(curr_signal, '⚪')
                    print(f'{emoji} {sig["created_at"].strftime("%H:%M:%S")} → {curr_signal:<10} '
                          f'(强度:{float(sig["signal_strength"] or 0):>5.1f}, 涨:{sig["bullish_count"]}/跌:{sig["bearish_count"]})')
                    prev_signal = curr_signal
    else:
        print('⚠️ 昨晚无Big4信号记录')

    print()
    print('=' * 120)

    # 2. Big4四大天王自身表现
    print('📈 Part 2: Big4 四大天王价格表现')
    print('=' * 120)
    print()

    big4_symbols = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']

    for symbol in big4_symbols:
        cursor.execute("""
            SELECT open_price, close_price, high_price, low_price, volume
            FROM klines
            WHERE symbol = %s
            AND timeframe = '1h'
            AND open_time >= %s AND open_time < %s
            ORDER BY open_time
        """, (symbol, yesterday_start, yesterday_end))

        klines = cursor.fetchall()

        if klines:
            first_open = float(klines[0]['open_price'])
            last_close = float(klines[-1]['close_price'])
            highest = max(float(k['high_price']) for k in klines)
            lowest = min(float(k['low_price']) for k in klines)
            total_volume = sum(float(k['volume']) for k in klines)

            change_pct = ((last_close - first_open) / first_open * 100) if first_open else 0
            range_pct = ((highest - lowest) / first_open * 100) if first_open else 0

            trend_emoji = '🟢' if change_pct > 0 else '🔴' if change_pct < 0 else '⚪'

            # 计算涨跌K线比例
            bull_klines = sum(1 for k in klines if float(k['close_price']) > float(k['open_price']))
            bear_klines = sum(1 for k in klines if float(k['close_price']) < float(k['open_price']))

            print(f'{trend_emoji} {symbol:<12} | '
                  f'涨跌:{change_pct:>+7.2f}% | '
                  f'振幅:{range_pct:>6.2f}% | '
                  f'K线:阳{bull_klines}/阴{bear_klines} | '
                  f'成交量:{total_volume:>12,.0f}')

    print()
    print('=' * 120)

    # 3. 昨晚的交易表现（U本位 + 币本位）
    print('💰 Part 3: 昨晚交易表现 (所有账户)')
    print('=' * 120)
    print()

    # account_id: 2=U本位实盘, 3=币本位
    for account_id, account_name in [(2, 'U本位实盘'), (3, '币本位合约')]:
        cursor.execute("""
            SELECT symbol, side, entry_time, close_time,
                   realized_pnl, realized_pnl_pct, close_reason,
                   entry_signal_score, status
            FROM futures_positions
            WHERE account_id = %s
            AND entry_time >= %s AND entry_time < %s
            ORDER BY entry_time DESC
        """, (account_id, yesterday_start, datetime.now()))

        trades = cursor.fetchall()

        if trades:
            # 只统计已平仓的
            closed_trades = [t for t in trades if t['status'] == 'closed']
            open_trades = [t for t in trades if t['status'] == 'open']

            if closed_trades:
                total_pnl = sum(float(t['realized_pnl'] or 0) for t in closed_trades)
                win_trades = [t for t in closed_trades if float(t['realized_pnl'] or 0) > 0]
                loss_trades = [t for t in closed_trades if float(t['realized_pnl'] or 0) < 0]
                win_rate = (len(win_trades) / len(closed_trades) * 100) if closed_trades else 0

                print(f'🏦 {account_name} (account_id={account_id})')
                print(f'   总交易: {len(trades)}笔 (已平仓:{len(closed_trades)}, 持仓中:{len(open_trades)})')
                print(f'   总盈亏: ${total_pnl:>8.2f}')
                print(f'   胜率: {win_rate:>5.1f}% ({len(win_trades)}胜/{len(loss_trades)}负)')

                # 显示前5笔交易
                print(f'   前5笔交易:')
                for trade in closed_trades[:5]:
                    pnl = float(trade['realized_pnl'] or 0)
                    pnl_pct = float(trade['realized_pnl_pct'] or 0)
                    pnl_emoji = '✅' if pnl > 0 else '❌'

                    entry_time = trade['entry_time'].strftime('%H:%M')
                    close_time = trade['close_time'].strftime('%H:%M') if trade['close_time'] else '持仓'

                    print(f'     {pnl_emoji} {trade["symbol"]:<15} {trade["side"]:<5} '
                          f'{entry_time}->{close_time} '
                          f'${pnl:>7.2f} ({pnl_pct:>+6.2f}%)')
                print()
        else:
            print(f'🏦 {account_name}: 无交易记录\n')

except Exception as e:
    print(f'✗ 分析失败: {e}')
    import traceback
    traceback.print_exc()
finally:
    cursor.close()
    conn.close()

print('=' * 120)
