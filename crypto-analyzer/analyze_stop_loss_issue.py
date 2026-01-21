#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
分析止盈止损设置的问题
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

conn = pymysql.connect(**db_config, cursorclass=DictCursor)
cursor = conn.cursor()

# 基础统计
cursor.execute('''
    SELECT
        COUNT(*) as total,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) as losses,
        AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END) as avg_win,
        AVG(CASE WHEN realized_pnl <= 0 THEN realized_pnl END) as avg_loss,
        SUM(realized_pnl) as total_pnl
    FROM futures_positions
    WHERE source = 'smart_trader'
    AND status = 'closed'
''')

stats = cursor.fetchone()

print('=' * 100)
print('STOP LOSS/TAKE PROFIT PROBLEM ANALYSIS')
print('=' * 100)

win_rate = float(stats['wins']) / float(stats['total']) * 100
avg_win = float(stats['avg_win'] or 0)
avg_loss = float(stats['avg_loss'] or 0)

print(f'\nCurrent Performance:')
print(f'  Total Trades: {stats["total"]}')
print(f'  Win Rate: {win_rate:.1f}% ({stats["wins"]}/{stats["total"]})')
print(f'  Avg Win: ${avg_win:.2f}')
print(f'  Avg Loss: ${avg_loss:.2f}')
print(f'  Total PnL: ${stats["total_pnl"]:.2f}')

# 盈亏比分析
win_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0
break_even_wr = abs(avg_loss) / (abs(avg_loss) + avg_win) * 100 if avg_loss != 0 else 0

print(f'\nRisk/Reward Analysis:')
print(f'  Win/Loss Ratio: {win_loss_ratio:.2f}:1')
print(f'  Break-even Win Rate Needed: {break_even_wr:.1f}%')
print(f'  Current Win Rate: {win_rate:.1f}%')
print(f'  Gap: {win_rate - break_even_wr:.1f}% (NEGATIVE = LOSING MONEY!)')

# 理论计算
print(f'\n{"=" * 100}')
print('THEORETICAL CALCULATION:')
print('=' * 100)

print(f'\nWith current settings (+2% TP / -3% SL):')
print(f'  Expected: Avg Win ~ 2%, Avg Loss ~ -3%')
print(f'  Actual: Avg Win = {avg_win/400*100:.2f}%, Avg Loss = {avg_loss/400*100:.2f}%')
print(f'  (Based on $400 position size)')

# 按理论计算需要多少胜率
theoretical_breakeven = 3.0 / (2.0 + 3.0) * 100
print(f'\nTheoretical Break-even Win Rate: {theoretical_breakeven:.1f}%')
print(f'  Formula: Loss% / (Win% + Loss%) = 3 / (2 + 3) = 60%')
print(f'\nYou NEED {theoretical_breakeven:.0f}% win rate to break even!')
print(f'But you only have {win_rate:.1f}% win rate')
print(f'Shortfall: {theoretical_breakeven - win_rate:.1f}%')

# 问题诊断
print(f'\n{"=" * 100}')
print('PROBLEM DIAGNOSIS:')
print('=' * 100)

print(f'\nProblem 1: Win Rate Too Low')
print(f'  - Current: {win_rate:.1f}%')
print(f'  - Needed: {theoretical_breakeven:.1f}%')
print(f'  - Solution: Improve signal quality (weight optimization helps)')

print(f'\nProblem 2: Risk/Reward Ratio Unfavorable')
print(f'  - Current setting: +2% / -3% (Risk 1.5x more than reward)')
print(f'  - Better setting: +3% / -2% (Reward 1.5x more than risk)')
print(f'  - Or: +2% / -1.5% (same 60% breakeven but smaller losses)')

# 建议的设置
print(f'\n{"=" * 100}')
print('RECOMMENDED SOLUTIONS:')
print('=' * 100)

print(f'\nOption 1: Improve Risk/Reward Ratio')
print(f'  - Change to: +3% TP / -2% SL')
print(f'  - Break-even win rate: 40%')
print(f'  - Your current {win_rate:.1f}% would be LOSING but closer')

print(f'\nOption 2: Tighter Stop Loss')
print(f'  - Change to: +2% TP / -1.5% SL')
print(f'  - Break-even win rate: 42.9%')
print(f'  - Smaller losses, same target profit')

print(f'\nOption 3: Higher Take Profit (Recommended!)')
print(f'  - Change to: +4% TP / -2% SL')
print(f'  - Break-even win rate: 33.3%')
print(f'  - Your {win_rate:.1f}% is still below but much closer!')

print(f'\nOption 4: Asymmetric Risk/Reward')
print(f'  - Change to: +5% TP / -2% SL')
print(f'  - Break-even win rate: 28.6%')
print(f'  - Your {win_rate:.1f}% would be PROFITABLE!')

# 按平仓原因分析
cursor.execute('''
    SELECT
        CASE
            WHEN notes LIKE '%stop_loss%' OR notes LIKE '%sl%' THEN 'Stop Loss'
            WHEN notes LIKE '%take_profit%' OR notes LIKE '%tp%' THEN 'Take Profit'
            WHEN notes LIKE 'N/A' OR notes IS NULL OR notes = '' THEN 'Unknown'
            ELSE 'Other'
        END as reason_type,
        COUNT(*) as count,
        AVG(realized_pnl) as avg_pnl,
        SUM(realized_pnl) as total_pnl
    FROM futures_positions
    WHERE source = 'smart_trader'
    AND status = 'closed'
    GROUP BY reason_type
    ORDER BY count DESC
''')

reasons = cursor.fetchall()

print(f'\n{"=" * 100}')
print('Close Reason Analysis:')
print('=' * 100)
print(f'{"Reason":<20} {"Count":>10} {"Avg PnL":>15} {"Total PnL":>15}')
print('-' * 100)

for r in reasons:
    print(f"{r['reason_type']:<20} {r['count']:>10} ${r['avg_pnl']:>14.2f} ${r['total_pnl']:>14.2f}")

# 具体数值分析
cursor.execute('''
    SELECT
        COUNT(*) as count,
        AVG(unrealized_pnl_pct) as avg_pct,
        MIN(unrealized_pnl_pct) as min_pct,
        MAX(unrealized_pnl_pct) as max_pct
    FROM futures_positions
    WHERE source = 'smart_trader'
    AND status = 'closed'
    AND realized_pnl > 0
''')

win_stats = cursor.fetchone()

cursor.execute('''
    SELECT
        COUNT(*) as count,
        AVG(unrealized_pnl_pct) as avg_pct,
        MIN(unrealized_pnl_pct) as min_pct,
        MAX(unrealized_pnl_pct) as max_pct
    FROM futures_positions
    WHERE source = 'smart_trader'
    AND status = 'closed'
    AND realized_pnl <= 0
''')

loss_stats = cursor.fetchone()

print(f'\n{"=" * 100}')
print('Detailed PnL Percentage Analysis:')
print('=' * 100)

print(f'\nWinning Trades:')
print(f'  Count: {win_stats["count"]}')
print(f'  Avg: {win_stats["avg_pct"]:.2f}%')
print(f'  Min: {win_stats["min_pct"]:.2f}%')
print(f'  Max: {win_stats["max_pct"]:.2f}%')

print(f'\nLosing Trades:')
print(f'  Count: {loss_stats["count"]}')
print(f'  Avg: {loss_stats["avg_pct"]:.2f}%')
print(f'  Min: {loss_stats["min_pct"]:.2f}%')
print(f'  Max: {loss_stats["max_pct"]:.2f}%')

print(f'\n{"=" * 100}')

cursor.close()
conn.close()
