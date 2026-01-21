#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
运行高级自适应优化
1. 优化每个交易对的止盈止损
2. 优化仓位分配(交易对和信号)
"""

import sys
import os
from dotenv import load_dotenv

# 加载.env文件
load_dotenv()

sys.path.insert(0, 'app/services')

from advanced_adaptive_optimizer import AdvancedAdaptiveOptimizer

# 数据库配置 - 从.env读取
db_config = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data')
}

def main():
    optimizer = AdvancedAdaptiveOptimizer(db_config)

    print('=' * 100)
    print('ADVANCED ADAPTIVE OPTIMIZATION')
    print('=' * 100)

    # 1. 优化止盈止损
    print('\n[1/2] Optimizing Stop Loss / Take Profit for each symbol...')
    print('-' * 100)

    tp_sl_result = optimizer.optimize_symbol_risk_params(days=7, dry_run=True)

    if tp_sl_result['adjusted']:
        print(f'\nFound {len(tp_sl_result["adjusted"])} symbols to optimize:')
        print('-' * 100)
        print(f'{"Symbol":<12} {"Side":<6} {"Current TP":<12} {"New TP":<12} {"Current SL":<12} {"New SL":<12} {"Win Rate":<10}')
        print('-' * 100)

        for adj in tp_sl_result['adjusted']:
            print(f"{adj['symbol']:<12} {adj['side']:<6} "
                  f"{adj['current_tp']*100:>10.2f}% {adj['new_tp']*100:>10.2f}% "
                  f"{adj['current_sl']*100:>10.2f}% {adj['new_sl']*100:>10.2f}% "
                  f"{adj['win_rate']*100:>8.1f}%")

        print(f'\nReason examples:')
        for adj in tp_sl_result['adjusted'][:3]:
            print(f"  {adj['symbol']} ({adj['side']}): {adj['reason']}")
    else:
        print('\nNo TP/SL adjustments needed')

    # 2. 优化仓位倍数
    print(f'\n\n[2/2] Optimizing Position Multipliers...')
    print('-' * 100)

    pos_result = optimizer.optimize_position_multipliers(days=7, dry_run=True)

    # 交易对仓位优化
    symbol_adj = pos_result['symbol_adjustments']
    if symbol_adj:
        print(f'\nSymbol Position Multipliers ({len(symbol_adj)} adjustments):')
        print('-' * 100)
        print(f'{"Symbol":<12} {"Current":<10} {"New":<10} {"Win Rate":<12} {"Total PnL":<12} {"Score":<10}')
        print('-' * 100)

        for adj in symbol_adj:
            print(f"{adj['symbol']:<12} {adj['current_multiplier']:<10.2f} "
                  f"{adj['new_multiplier']:<10.2f} {adj['win_rate']*100:>10.1f}% "
                  f"${adj['total_pnl']:>10.2f} {adj['performance_score']:>8.2f}")
    else:
        print('\nNo symbol position adjustments needed')

    # 信号组件仓位优化
    signal_adj = pos_result['signal_adjustments']
    if signal_adj:
        print(f'\n\nSignal Component Position Multipliers ({len(signal_adj)} adjustments):')
        print('-' * 100)
        print(f'{"Component":<25} {"Side":<6} {"Current":<10} {"New":<10} {"Win Rate":<12} {"Score":<10}')
        print('-' * 100)

        for adj in signal_adj:
            print(f"{adj['component']:<25} {adj['side']:<6} "
                  f"{adj['current_multiplier']:<10.2f} {adj['new_multiplier']:<10.2f} "
                  f"{adj['win_rate']*100:>10.1f}% {adj['performance_score']:>8.2f}")
    else:
        print('\nNo signal position adjustments needed')

    # 汇总
    total_adjustments = (len(tp_sl_result['adjusted']) +
                        len(symbol_adj) +
                        len(signal_adj))

    print(f'\n\n{"=" * 100}')
    print('SUMMARY')
    print('=' * 100)
    print(f'  Total symbols analyzed: {tp_sl_result["total_analyzed"]}')
    print(f'  TP/SL adjustments: {len(tp_sl_result["adjusted"])}')
    print(f'  Symbol position adjustments: {len(symbol_adj)}')
    print(f'  Signal position adjustments: {len(signal_adj)}')
    print(f'  Total adjustments: {total_adjustments}')

    if total_adjustments > 0:
        print(f'\n{"=" * 100}')
        choice = input('\nApply these optimizations? (y/n): ').strip().lower()

        if choice == 'y':
            print('\n[APPLYING] Running optimization with database updates...\n')

            # 应用优化
            tp_sl_result = optimizer.optimize_symbol_risk_params(days=7, dry_run=False)
            pos_result = optimizer.optimize_position_multipliers(days=7, dry_run=False)

            print(f'\n{"=" * 100}')
            print('SUCCESS! Optimizations applied to database')
            print('=' * 100)
            print('\nNEXT STEPS:')
            print('  1. Restart smart_trader_service.py to load new parameters')
            print('  2. Monitor performance over next 24-48 hours')
            print('  3. Run optimization again in 3-5 days')
            print('=' * 100)
        else:
            print('\n[CANCELLED] No changes made')
    else:
        print('\nNo optimizations needed at this time')

    print('=' * 100)

if __name__ == '__main__':
    main()
