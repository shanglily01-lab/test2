#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Run weight optimization for Smart Brain scoring components
"""

import sys
sys.path.insert(0, 'app/services')
from scoring_weight_optimizer import ScoringWeightOptimizer

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def main():
    optimizer = ScoringWeightOptimizer(db_config)

    print('=' * 100)
    print('Smart Brain Scoring Weight Optimization')
    print('=' * 100)

    # Run DRY RUN first
    print('\n[DRY RUN MODE] Analyzing and calculating weight adjustments...\n')

    result = optimizer.adjust_weights(dry_run=True)

    # Show current weights
    if result.get('current_weights'):
        print('Current Weights:')
        print('-' * 100)
        print(f"{'Component':<25} {'LONG Weight':>12} {'SHORT Weight':>12}")
        print('-' * 100)
        for w in sorted(result['current_weights'], key=lambda x: x['component']):
            print(f"{w['component']:<25} {w['weight_long']:>12.1f} {w['weight_short']:>12.1f}")
        print()

    # Show recommended adjustments
    if result.get('adjusted') and len(result['adjusted']) > 0:
        print('\n' + '=' * 100)
        print(f'Recommended Weight Adjustments: {len(result["adjusted"])} changes')
        print('=' * 100)

        for adj in sorted(result['adjusted'], key=lambda x: abs(x['adjustment']), reverse=True):
            print(f"\n{adj['component']} ({adj['side']}):")
            print(f"  Current Weight: {adj['old_weight']:.1f}")
            print(f"  New Weight: {adj['new_weight']:.1f}  (Change: {adj['adjustment']:+d})")
            print(f"  Performance Score: {adj['performance_score']:.2f}")
            print(f"  Win Rate: {adj['win_rate']*100:.1f}%")
            print(f"  Avg PnL: ${adj['avg_pnl']:.2f}")
            print(f"  Total Orders: {adj['orders']}")

        print('\n' + '=' * 100)
        print('Apply these changes? (y/n): ', end='')

        choice = input().strip().lower()

        if choice == 'y':
            print('\n[APPLYING CHANGES] Running optimization with database update...\n')
            real_result = optimizer.adjust_weights(dry_run=False)

            if real_result.get('adjusted'):
                print(f'\n✅ Successfully adjusted {len(real_result["adjusted"])} weights!')
                print('\nAdjusted components:')
                for adj in real_result['adjusted']:
                    print(f"  - {adj['component']} ({adj['side']}): {adj['old_weight']:.1f} -> {adj['new_weight']:.1f}")
            else:
                print('\n❌ No changes were made')
        else:
            print('\n[CANCELLED] No changes made to database')
    else:
        print('\n' + '=' * 100)
        print('No weight adjustments recommended at this time')
        print('=' * 100)
        print('\nPossible reasons:')
        print('  - Not enough data (need at least 5 orders per component)')
        print('  - All components are performing within acceptable range')
        print('  - Components already at min/max weight limits (5-30)')

    if result.get('skipped'):
        print(f'\n[INFO] Skipped components (not in weight table): {result["skipped"]}')

    print('\n' + '=' * 100)
    print('Optimization analysis completed')
    print('=' * 100)

if __name__ == '__main__':
    main()
