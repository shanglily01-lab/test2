#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析最近交易 - 找出垃圾信号
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pymysql
from dotenv import load_dotenv
from datetime import datetime, timedelta

# 加载环境变量
load_dotenv()

# 数据库配置
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data')
}

print("=" * 100)
print("最近24小时交易分析")
print("=" * 100)

# 连接数据库
conn = pymysql.connect(**DB_CONFIG, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
cursor = conn.cursor()

# 查询最近24小时的已平仓持仓
cursor.execute("""
    SELECT
        id,
        symbol,
        position_side as side,
        entry_price,
        mark_price as exit_price,
        realized_pnl as profit_loss,
        unrealized_pnl_pct as profit_loss_pct,
        entry_signal_type as signal_combination,
        open_time as entry_time,
        close_time as exit_time,
        notes as exit_reason,
        TIMESTAMPDIFF(MINUTE, open_time, close_time) as holding_minutes
    FROM futures_positions
    WHERE close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
      AND status = 'CLOSED'
    ORDER BY close_time DESC
""")

trades = cursor.fetchall()

print(f"\n总交易数: {len(trades)}")

if not trades:
    print("没有最近24小时的交易记录")
    sys.exit(0)

# 统计数据
total_profit = sum(float(t['profit_loss'] or 0) for t in trades)
win_trades = [t for t in trades if float(t['profit_loss'] or 0) > 0]
loss_trades = [t for t in trades if float(t['profit_loss'] or 0) < 0]
win_rate = len(win_trades) / len(trades) * 100 if trades else 0

print(f"总盈亏: {total_profit:+.2f} USDT")
print(f"胜率: {win_rate:.1f}% ({len(win_trades)}胜 / {len(loss_trades)}负)")

# 按信号组合统计
signal_stats = {}
for trade in trades:
    signal = trade['signal_combination'] or 'unknown'
    side = trade['side']
    key = f"{signal}_{side}"

    if key not in signal_stats:
        signal_stats[key] = {
            'signal': signal,
            'side': side,
            'count': 0,
            'wins': 0,
            'losses': 0,
            'total_profit': 0.0,
            'trades': []
        }

    signal_stats[key]['count'] += 1
    profit = float(trade['profit_loss'] or 0)
    signal_stats[key]['total_profit'] += profit
    signal_stats[key]['trades'].append(trade)

    if profit > 0:
        signal_stats[key]['wins'] += 1
    else:
        signal_stats[key]['losses'] += 1

# 计算胜率并排序（按亏损从大到小）
signal_list = []
for key, stats in signal_stats.items():
    stats['win_rate'] = (stats['wins'] / stats['count'] * 100) if stats['count'] > 0 else 0
    signal_list.append(stats)

signal_list.sort(key=lambda x: x['total_profit'])

# 显示信号统计
print("\n" + "=" * 100)
print("信号组合表现统计（按亏损排序）")
print("=" * 100)

for stats in signal_list:
    profit_color = '+' if stats['total_profit'] >= 0 else ''
    print(f"\n{stats['signal'][:80]}")
    print(f"  方向: {stats['side']:5s} | 订单: {stats['count']:2d} | 胜率: {stats['win_rate']:5.1f}% ({stats['wins']}胜/{stats['losses']}负) | 盈亏: {profit_color}{stats['total_profit']:.2f}U")

    # 显示每笔交易详情
    for trade in stats['trades']:
        holding = trade['holding_minutes'] or 0
        profit = float(trade['profit_loss'] or 0)
        profit_pct = float(trade['profit_loss_pct'] or 0)
        profit_symbol = '+' if profit >= 0 else ''

        print(f"    [{trade['id']:4d}] {trade['symbol']:12s} | {trade['entry_time'].strftime('%m-%d %H:%M')} | "
              f"持仓{holding:3d}分 | {profit_symbol}{profit:7.2f}U ({profit_pct:+.2f}%) | {trade['exit_reason']}")

# 找出垃圾信号（胜率<40% 或 亏损较大）
print("\n" + "=" * 100)
print("垃圾信号建议（建议加入黑名单）")
print("=" * 100)

garbage_signals = []
for stats in signal_list:
    # 条件1: 胜率<40% 且订单>3笔
    # 条件2: 单个信号亏损>50U
    if (stats['win_rate'] < 40 and stats['count'] > 3) or stats['total_profit'] < -50:
        garbage_signals.append(stats)

if garbage_signals:
    print("\n建议禁用以下信号组合:\n")
    for stats in garbage_signals:
        print(f"信号: {stats['signal']}")
        print(f"方向: {stats['side']}")
        print(f"原因: {stats['count']}单{stats['win_rate']:.1f}%胜率，累计亏损{stats['total_profit']:.2f}U")
        print(f"SQL: INSERT INTO signal_blacklist (signal_type, position_side, reason, win_rate, total_loss, order_count, is_active) "
              f"VALUES ('{stats['signal']}', '{stats['side']}', '{stats['count']}单{stats['win_rate']:.1f}%胜率', "
              f"{stats['win_rate']/100:.4f}, {-stats['total_profit']:.2f}, {stats['count']}, 1);")
        print()
else:
    print("没有发现明显的垃圾信号（可能是市场环境问题）")

# 按交易对统计
print("\n" + "=" * 100)
print("交易对表现统计")
print("=" * 100)

symbol_stats = {}
for trade in trades:
    symbol = trade['symbol']
    if symbol not in symbol_stats:
        symbol_stats[symbol] = {
            'count': 0,
            'wins': 0,
            'total_profit': 0.0
        }

    symbol_stats[symbol]['count'] += 1
    profit = float(trade['profit_loss'] or 0)
    symbol_stats[symbol]['total_profit'] += profit
    if profit > 0:
        symbol_stats[symbol]['wins'] += 1

symbol_list = sorted(symbol_stats.items(), key=lambda x: x[1]['total_profit'])

for symbol, stats in symbol_list[:10]:  # 只显示前10个最差的
    win_rate = (stats['wins'] / stats['count'] * 100) if stats['count'] > 0 else 0
    print(f"{symbol:12s} | {stats['count']:2d}单 | 胜率{win_rate:5.1f}% | 盈亏{stats['total_profit']:+8.2f}U")

cursor.close()
conn.close()

print("\n" + "=" * 100)
print("分析完成")
print("=" * 100)
