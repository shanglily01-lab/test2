#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
更新止盈止损设置
根据分析结果优化止盈止损比例
"""

import pymysql
from pymysql.cursors import DictCursor

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

print('=' * 100)
print('Stop Loss / Take Profit Update Tool')
print('=' * 100)

print('\nCurrent Problem Analysis:')
print('  - Current setting: +2% TP / -3% SL')
print('  - Break-even win rate needed: 60%')
print('  - Your actual win rate: 25.9%')
print('  - Result: LOSING MONEY (-$1,966)')

print('\n' + '=' * 100)
print('Recommended Options:')
print('=' * 100)

options = [
    {
        'name': 'Option 1: Balanced (Recommended)',
        'tp': 0.03,
        'sl': 0.02,
        'breakeven': 40.0,
        'desc': 'Better risk/reward ratio'
    },
    {
        'name': 'Option 2: Conservative',
        'tp': 0.04,
        'sl': 0.02,
        'breakeven': 33.3,
        'desc': 'Higher target, same risk'
    },
    {
        'name': 'Option 3: Aggressive (Best Match!)',
        'tp': 0.05,
        'sl': 0.02,
        'breakeven': 28.6,
        'desc': 'Would be PROFITABLE with your 25.9% win rate!'
    },
    {
        'name': 'Option 4: Very Aggressive',
        'tp': 0.06,
        'sl': 0.02,
        'breakeven': 25.0,
        'desc': 'Even lower breakeven point'
    },
    {
        'name': 'Option 5: Custom',
        'tp': None,
        'sl': None,
        'breakeven': None,
        'desc': 'Enter your own values'
    }
]

for i, opt in enumerate(options, 1):
    print(f'\n{i}. {opt["name"]}')
    if opt['tp']:
        print(f'   TP: +{opt["tp"]*100:.1f}% | SL: -{opt["sl"]*100:.1f}%')
        print(f'   Break-even win rate: {opt["breakeven"]:.1f}%')
    print(f'   {opt["desc"]}')

print('\n' + '=' * 100)
choice = input('Select option (1-5): ').strip()

try:
    choice_idx = int(choice) - 1
    if choice_idx < 0 or choice_idx >= len(options):
        print('Invalid choice')
        exit(1)

    selected = options[choice_idx]

    if selected['tp'] is None:
        # Custom input
        tp = float(input('Enter Take Profit % (e.g., 3.0 for 3%): ')) / 100
        sl = float(input('Enter Stop Loss % (e.g., 2.0 for 2%): ')) / 100
    else:
        tp = selected['tp']
        sl = selected['sl']

    print(f'\n{\"=\" * 100}')
    print('Selected Configuration:')
    print('=' * 100)
    print(f'  Take Profit: +{tp*100:.1f}%')
    print(f'  Stop Loss: -{sl*100:.1f}%')
    breakeven = sl / (tp + sl) * 100
    print(f'  Break-even Win Rate: {breakeven:.1f}%')
    print(f'  Your Current Win Rate: 25.9%')

    if breakeven < 25.9:
        print(f'  Result: PROFITABLE! (Win rate > breakeven)')
    elif breakeven < 30:
        print(f'  Result: Close to breakeven (only {breakeven - 25.9:.1f}% gap)')
    else:
        print(f'  Result: Still losing (need {breakeven - 25.9:.1f}% more win rate)')

    confirm = input(f'\nApply this configuration? (y/n): ').strip().lower()

    if confirm != 'y':
        print('Cancelled')
        exit(0)

    # Update database
    conn = pymysql.connect(**db_config, cursorclass=DictCursor)
    cursor = conn.cursor()

    # Check if adaptive_params table exists
    cursor.execute("SHOW TABLES LIKE 'adaptive_params'")
    if not cursor.fetchone():
        print('\nCreating adaptive_params table...')
        cursor.execute('''
            CREATE TABLE adaptive_params (
                id INT AUTO_INCREMENT PRIMARY KEY,
                param_key VARCHAR(50) UNIQUE NOT NULL,
                param_value DECIMAL(10,4) NOT NULL,
                updated_by VARCHAR(50),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        ''')

    # Update LONG params
    for key, value in [
        ('long_take_profit_pct', tp),
        ('long_stop_loss_pct', sl)
    ]:
        cursor.execute('''
            INSERT INTO adaptive_params (param_key, param_value, updated_by)
            VALUES (%s, %s, 'manual_update')
            ON DUPLICATE KEY UPDATE
                param_value = VALUES(param_value),
                updated_by = VALUES(updated_by)
        ''', (key, value))

    # Update SHORT params
    for key, value in [
        ('short_take_profit_pct', tp),
        ('short_stop_loss_pct', sl)
    ]:
        cursor.execute('''
            INSERT INTO adaptive_params (param_key, param_value, updated_by)
            VALUES (%s, %s, 'manual_update')
            ON DUPLICATE KEY UPDATE
                param_value = VALUES(param_value),
                updated_by = VALUES(updated_by)
        ''', (key, value))

    conn.commit()

    print('\n' + '=' * 100)
    print('SUCCESS! Configuration updated in database')
    print('=' * 100)

    # Show updated values
    cursor.execute('''
        SELECT param_key, param_value, updated_at
        FROM adaptive_params
        WHERE param_key LIKE '%take_profit%' OR param_key LIKE '%stop_loss%'
        ORDER BY param_key
    ''')

    rows = cursor.fetchall()

    print('\nCurrent Database Values:')
    print('-' * 100)
    for row in rows:
        print(f"  {row['param_key']:<30} {float(row['param_value'])*100:>6.1f}%    (Updated: {row['updated_at']})")

    print('\n' + '=' * 100)
    print('IMPORTANT: Restart smart_trader_service.py to apply changes!')
    print('=' * 100)

    cursor.close()
    conn.close()

except ValueError:
    print('Invalid input')
    exit(1)
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
    exit(1)
