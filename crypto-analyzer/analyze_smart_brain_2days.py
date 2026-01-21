#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Analyze Smart Brain trading performance for the last 2 days
"""

import pymysql
from pymysql.cursors import DictCursor
from datetime import datetime, timedelta
import json
import os
from dotenv import load_dotenv

# 加载.env文件
load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data')
}

try:
    conn = pymysql.connect(**db_config, cursorclass=DictCursor)
    cursor = conn.cursor()

    # Query last 2 days of smart_trader trades
    cursor.execute('''
        SELECT
            id,
            symbol,
            position_side,
            entry_price,
            mark_price,
            quantity,
            leverage,
            entry_score,
            signal_components,
            realized_pnl,
            unrealized_pnl,
            unrealized_pnl_pct,
            status,
            open_time,
            close_time,
            holding_hours,
            notes
        FROM futures_positions
        WHERE source = 'smart_trader'
        AND open_time >= DATE_SUB(NOW(), INTERVAL 2 DAY)
        ORDER BY open_time DESC
    ''')

    rows = cursor.fetchall()

    print('=' * 100)
    print(f'Smart Brain Trading Analysis (Last 2 Days)')
    print(f'Query Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print('=' * 100)

    if not rows:
        print('\nNo trades found in last 2 days')
    else:
        # Statistics
        total_trades = len(rows)
        open_positions = [r for r in rows if r['status'] == 'open']
        closed_positions = [r for r in rows if r['status'] == 'closed']

        # Closed position stats
        total_realized_pnl = sum(r['realized_pnl'] or 0 for r in closed_positions)
        winning_trades = [r for r in closed_positions if (r['realized_pnl'] or 0) > 0]
        losing_trades = [r for r in closed_positions if (r['realized_pnl'] or 0) < 0]
        win_rate = len(winning_trades) / len(closed_positions) * 100 if closed_positions else 0

        # Open position stats
        total_unrealized_pnl = sum(r['unrealized_pnl'] or 0 for r in open_positions)

        print(f'\nOverall Statistics:')
        print(f'  Total Trades: {total_trades}')
        print(f'  Open Positions: {len(open_positions)}')
        print(f'  Closed Positions: {len(closed_positions)}')
        if closed_positions:
            print(f'  Win Rate: {win_rate:.1f}% ({len(winning_trades)}/{len(closed_positions)})')
            print(f'  Total Realized PnL: ${total_realized_pnl:.2f}')
            avg_win = sum(r['realized_pnl'] for r in winning_trades) / len(winning_trades) if winning_trades else 0
            avg_loss = sum(r['realized_pnl'] for r in losing_trades) / len(losing_trades) if losing_trades else 0
            print(f'  Avg Win: ${avg_win:.2f}')
            print(f'  Avg Loss: ${avg_loss:.2f}')
            if avg_loss != 0:
                profit_factor = abs(avg_win * len(winning_trades) / (avg_loss * len(losing_trades)))
                print(f'  Profit Factor: {profit_factor:.2f}')
        if open_positions:
            print(f'  Total Unrealized PnL: ${total_unrealized_pnl:.2f}')

        # Direction distribution
        long_trades = [r for r in rows if r['position_side'] == 'LONG']
        short_trades = [r for r in rows if r['position_side'] == 'SHORT']
        print(f'\nDirection Distribution:')
        print(f'  LONG: {len(long_trades)} ({len(long_trades)/total_trades*100:.1f}%)')
        print(f'  SHORT: {len(short_trades)} ({len(short_trades)/total_trades*100:.1f}%)')

        # Score distribution (only for trades with scores)
        scored_trades = [r for r in rows if r['entry_score'] is not None]
        if scored_trades:
            avg_score = sum(r['entry_score'] for r in scored_trades) / len(scored_trades)
            max_score = max(r['entry_score'] for r in scored_trades)
            min_score = min(r['entry_score'] for r in scored_trades)
            print(f'\nEntry Score Statistics:')
            print(f'  Avg Score: {avg_score:.1f}')
            print(f'  Max Score: {max_score}')
            print(f'  Min Score: {min_score}')

        # Closed positions detail
        if closed_positions:
            print(f'\n{"=" * 100}')
            print(f'Closed Positions ({len(closed_positions)} trades):')
            print(f'{"=" * 100}')
            for r in closed_positions:
                pnl_color = '+' if (r['realized_pnl'] or 0) > 0 else ''
                print(f"\n[{r['id']}] {r['symbol']} {r['position_side']} | Score: {r['entry_score'] or 'N/A'}")
                print(f"  Entry: ${r['entry_price']:.6f} | Holding: {r['holding_hours'] or 0}h")
                print(f"  PnL: {pnl_color}${r['realized_pnl']:.2f}")
                print(f"  Close Reason: {r['notes'] or 'N/A'}")
                if r['signal_components']:
                    try:
                        components = json.loads(r['signal_components'])
                        print(f"  Signal Components: {components}")
                    except:
                        print(f"  Signal Components: {r['signal_components']}")

        # Open positions detail
        if open_positions:
            print(f'\n{"=" * 100}')
            print(f'Open Positions ({len(open_positions)} trades):')
            print(f'{"=" * 100}')
            for r in open_positions:
                pnl_color = '+' if (r['unrealized_pnl'] or 0) > 0 else ''
                pnl_pct = r['unrealized_pnl_pct'] or 0
                print(f"\n[{r['id']}] {r['symbol']} {r['position_side']} | Score: {r['entry_score'] or 'N/A'}")
                print(f"  Entry: ${r['entry_price']:.6f} | Current: ${r['mark_price']:.6f}")
                print(f"  Unrealized PnL: {pnl_color}${r['unrealized_pnl']:.2f} ({pnl_pct:+.2f}%)")
                print(f"  Open Time: {r['open_time']}")
                if r['signal_components']:
                    try:
                        components = json.loads(r['signal_components'])
                        print(f"  Signal Components: {components}")
                    except:
                        print(f"  Signal Components: {r['signal_components']}")

        # Signal component analysis (for closed positions with signal_components)
        closed_with_signals = [r for r in closed_positions if r['signal_components']]
        if closed_with_signals:
            print(f'\n{"=" * 100}')
            print('Signal Component Performance Analysis:')
            print(f'{"=" * 100}')

            # Count component usage and PnL
            component_stats = {}
            for r in closed_with_signals:
                try:
                    components = json.loads(r['signal_components'])
                    pnl = r['realized_pnl'] or 0
                    for comp_name, comp_value in components.items():
                        if comp_name not in component_stats:
                            component_stats[comp_name] = {
                                'count': 0,
                                'wins': 0,
                                'total_pnl': 0,
                                'pnls': []
                            }
                        component_stats[comp_name]['count'] += 1
                        component_stats[comp_name]['total_pnl'] += pnl
                        component_stats[comp_name]['pnls'].append(pnl)
                        if pnl > 0:
                            component_stats[comp_name]['wins'] += 1
                except:
                    pass

            if component_stats:
                print(f'\nComponent Usage & Performance:')
                for comp_name, stats in sorted(component_stats.items(), key=lambda x: x[1]['count'], reverse=True):
                    win_rate = stats['wins'] / stats['count'] * 100
                    avg_pnl = stats['total_pnl'] / stats['count']
                    print(f"\n  {comp_name}:")
                    print(f"    Used: {stats['count']} times")
                    print(f"    Win Rate: {win_rate:.1f}% ({stats['wins']}/{stats['count']})")
                    print(f"    Avg PnL: ${avg_pnl:.2f}")
                    print(f"    Total PnL: ${stats['total_pnl']:.2f}")

    cursor.close()
    conn.close()

except Exception as e:
    print(f'ERROR: {e}')
    import traceback
    traceback.print_exc()
