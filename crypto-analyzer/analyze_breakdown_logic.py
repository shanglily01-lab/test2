#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析破位信号的逻辑和历史表现
"""

import pymysql
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4'
}

conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
cursor = conn.cursor()

print("=" * 100)
print("破位信号逻辑分析")
print("=" * 100)

# 1. 查看破位信号的代码逻辑（已禁用）
print("\n[当前状态] 破位/突破信号已被完全禁用 (2026-02-09)")
print("\n[原始逻辑] 破位追空触发条件:")
print("  1. position_pct < 30  (价格在72H低位30%以下)")
print("  2. net_power_1h <= -2 (1H强力空头量能)")
print("  3. 或 (net_power_1h <= -2 AND net_power_15m <= -2) (双周期确认)")
print("\n[V5.1逻辑] 增加Big4过滤:")
print("  4. Big4强度 >= 70")
print("  5. Big4方向 = BEARISH")
print("  → 只有在Big4强趋势下跌时才允许破位追空")

# 2. 查询最近30天所有包含'breakdown'或'breakout'的订单
print("\n" + "=" * 100)
print("[历史数据] 最近30天破位/突破信号表现")
print("=" * 100)

cursor.execute('''
    SELECT
        DATE(open_time) as trade_date,
        position_side,
        COUNT(*) as count,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl
    FROM futures_positions
    WHERE account_id = 2
    AND status = 'closed'
    AND open_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
    AND notes LIKE '%breakdown%' OR notes LIKE '%breakout%'
    GROUP BY DATE(open_time), position_side
    ORDER BY trade_date DESC, position_side
''')

breakdown_history = cursor.fetchall()

if breakdown_history:
    print(f"\n{'Date':<12} {'Side':<6} {'Count':>5} {'Wins':>5} {'WinRate':>8} {'AvgPNL':>10} {'TotalPNL':>12}")
    print("-" * 100)

    total_count = 0
    total_wins = 0
    total_pnl = 0

    for row in breakdown_history:
        date = row['trade_date'].strftime('%Y-%m-%d')
        side = row['position_side']
        count = row['count']
        wins = row['wins']
        win_rate = wins / count * 100 if count > 0 else 0
        avg_pnl = float(row['avg_pnl'])
        total = float(row['total_pnl'])

        total_count += count
        total_wins += wins
        total_pnl += total

        print(f"{date:<12} {side:<6} {count:5d} {wins:5d} {win_rate:7.1f}% ${avg_pnl:9.2f} ${total:11.2f}")

    overall_win_rate = total_wins / total_count * 100 if total_count > 0 else 0
    print("-" * 100)
    print(f"{'TOTAL':<12} {'':6} {total_count:5d} {total_wins:5d} {overall_win_rate:7.1f}% ${total_pnl/total_count:9.2f} ${total_pnl:11.2f}")
else:
    print("\n没有找到破位/突破相关的订单")

# 3. 分析不同信号组成的表现
print("\n" + "=" * 100)
print("[信号组成分析] 最近30天所有信号类型表现")
print("=" * 100)

cursor.execute('''
    SELECT
        position_side,
        COUNT(*) as count,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl
    FROM futures_positions
    WHERE account_id = 2
    AND status = 'closed'
    AND open_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
    GROUP BY position_side
''')

all_signals = cursor.fetchall()

if all_signals:
    print(f"\n{'Side':<6} {'Count':>5} {'Wins':>5} {'WinRate':>8} {'AvgPNL':>10} {'TotalPNL':>12}")
    print("-" * 100)

    for row in all_signals:
        side = row['position_side']
        count = row['count']
        wins = row['wins']
        win_rate = wins / count * 100 if count > 0 else 0
        avg_pnl = float(row['avg_pnl'])
        total = float(row['total_pnl'])

        print(f"{side:<6} {count:5d} {wins:5d} {win_rate:7.1f}% ${avg_pnl:9.2f} ${total:11.2f}")

# 4. 分析2月8日的市场状态和信号
print("\n" + "=" * 100)
print("[2月8日] 市场状态和交易记录")
print("=" * 100)

cursor.execute('''
    SELECT symbol, signal, strength, created_at
    FROM big4_signals
    WHERE DATE(created_at) = '2026-02-08'
    ORDER BY created_at DESC
    LIMIT 10
''')

big4_feb8 = cursor.fetchall()

if big4_feb8:
    print("\nBig4信号 (2月8日):")
    for b in big4_feb8:
        print(f"  {b['created_at'].strftime('%H:%M:%S')} | {b['symbol']:<10} {b['signal']:<8} 强度:{b['strength']:3.0f}")

cursor.execute('''
    SELECT
        symbol, position_side, realized_pnl,
        open_time, close_time, close_reason
    FROM futures_positions
    WHERE account_id = 2
    AND DATE(open_time) = '2026-02-08'
    AND status = 'closed'
    ORDER BY open_time DESC
    LIMIT 10
''')

feb8_trades = cursor.fetchall()

if feb8_trades:
    print(f"\n2月8日交易记录 (前10笔):")
    for t in feb8_trades:
        pnl = float(t['realized_pnl'])
        result = 'WIN' if pnl > 0 else 'LOSS'
        print(f"  [{result}] {t['symbol']:<12} {t['position_side']:<5} ${pnl:+7.2f} | {t['close_reason']}")

# 5. 查看当前的信号权重配置
print("\n" + "=" * 100)
print("[当前配置] 信号评分权重 (从代码中)")
print("=" * 100)
print("\n已禁用的信号:")
print("  - breakout_long (突破追涨): 20分")
print("  - breakdown_short (破位追空): 20分")
print("\n保留的信号:")
print("  - position_low (低位): 20分")
print("  - position_high (高位): 20分")
print("  - trend_1h_bull (1H多头趋势): 20分")
print("  - trend_1h_bear (1H空头趋势): 20分")
print("  - volume_power_bull (强力多头量能): 25分")
print("  - volume_power_bear (强力空头量能): 25分")
print("  - consecutive_bull (连续阳线): 15分")
print("  - consecutive_bear (连续阴线): 15分")
print("  - momentum_up_3pct (上涨3%): 10分")
print("  - momentum_down_3pct (下跌3%): 10分")

print("\n开仓阈值: 35分")

cursor.close()
conn.close()

print("\n" + "=" * 100)
print("分析完成")
print("=" * 100)
