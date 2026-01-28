#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析现货交易策略 - 为什么错过了最近2天的大行情
"""
import pymysql
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST', '13.212.252.171'),
    port=int(os.getenv('DB_PORT', '3306')),
    user=os.getenv('DB_USER', 'admin'),
    password=os.getenv('DB_PASSWORD', 'Tonny@1000'),
    database='binance-data',
    cursorclass=pymysql.cursors.DictCursor
)

cursor = conn.cursor()

print("=" * 120)
print("现货交易信号执行分析 - 最近2天")
print("=" * 120)
print()

# 1. 查询最近2天的所有信号
cursor.execute("""
    SELECT
        symbol, signal_type, signal_strength, confidence_score,
        decision, decision_reason,
        execution_price, execution_quantity, execution_amount,
        signal_time, execution_time,
        is_executed, execution_status
    FROM paper_trading_signal_executions
    WHERE signal_time >= DATE_SUB(NOW(), INTERVAL 2 DAY)
    ORDER BY signal_time DESC
""")

signals = cursor.fetchall()

print(f"信号总数: {len(signals)}")
print()

# 统计决策分布
buy_signals = [s for s in signals if s['signal_type'] == 'BUY']
sell_signals = [s for s in signals if s['signal_type'] == 'SELL']
executed = [s for s in signals if s['is_executed'] == 1]
skipped = [s for s in signals if s['decision'] == 'SKIP']
rejected = [s for s in signals if s['decision'] == 'REJECT']

print("=" * 120)
print("信号分布统计:")
print("=" * 120)
print(f"买入信号: {len(buy_signals)} 笔")
print(f"卖出信号: {len(sell_signals)} 笔")
print(f"已执行: {len(executed)} 笔")
print(f"跳过(SKIP): {len(skipped)} 笔")
print(f"拒绝(REJECT): {len(rejected)} 笔")
print()

# 2. 分析被跳过/拒绝的买入信号
print("=" * 120)
print("被跳过/拒绝的买入信号 (错失的机会):")
print("=" * 120)
print()

missed_buys = [s for s in buy_signals if s['decision'] in ['SKIP', 'REJECT']]

for sig in missed_buys:
    print(f"交易对: {sig['symbol']}")
    print(f"信号时间: {sig['signal_time']}")
    print(f"信号强度: {sig['signal_strength']}")
    print(f"置信度: {sig['confidence_score']}")
    print(f"决策: {sig['decision']}")
    print(f"原因: {sig['decision_reason']}")
    print("-" * 120)
    print()

# 3. 查询当前持仓
cursor.execute("""
    SELECT
        symbol, quantity, available_quantity,
        avg_entry_price, current_price,
        unrealized_pnl, unrealized_pnl_pct,
        first_buy_time, holding_days,
        status
    FROM paper_trading_positions
    WHERE status = 'open'
    ORDER BY unrealized_pnl DESC
""")

positions = cursor.fetchall()

print("=" * 120)
print(f"当前持仓: {len(positions)} 个")
print("=" * 120)
print()

total_pnl = 0
for pos in positions:
    print(f"交易对: {pos['symbol']}")
    print(f"数量: {pos['quantity']}")
    print(f"平均成本: ${pos['avg_entry_price']}")
    print(f"当前价格: ${pos['current_price']}")
    print(f"浮盈: ${pos['unrealized_pnl']:.2f} ({pos['unrealized_pnl_pct']:.2f}%)")
    print(f"持仓天数: {pos['holding_days']} 天")
    print(f"建仓时间: {pos['first_buy_time']}")
    print("-" * 120)
    print()
    total_pnl += float(pos['unrealized_pnl'] or 0)

print("=" * 120)
print(f"总浮盈: ${total_pnl:.2f}")
print("=" * 120)
print()

# 4. 统计最近2天执行的买入
cursor.execute("""
    SELECT
        symbol, execution_price, execution_quantity, execution_amount,
        confidence_score, signal_time, execution_time
    FROM paper_trading_signal_executions
    WHERE signal_type = 'BUY'
      AND is_executed = 1
      AND signal_time >= DATE_SUB(NOW(), INTERVAL 2 DAY)
    ORDER BY signal_time DESC
""")

executed_buys = cursor.fetchall()

print("=" * 120)
print(f"最近2天执行的买入: {len(executed_buys)} 笔")
print("=" * 120)
print()

for buy in executed_buys:
    print(f"交易对: {buy['symbol']}")
    print(f"信号时间: {buy['signal_time']}")
    print(f"执行价格: ${buy['execution_price']}")
    print(f"数量: {buy['execution_quantity']}")
    print(f"金额: ${buy['execution_amount']:.2f}")
    print(f"置信度: {buy['confidence_score']}")
    print("-" * 120)
    print()

# 5. 检查账户余额
cursor.execute("""
    SELECT current_balance, frozen_balance, total_equity,
           unrealized_pnl, realized_pnl, total_profit_loss
    FROM paper_trading_accounts
    WHERE id = 1
""")

account = cursor.fetchone()

print("=" * 120)
print("账户资金状态:")
print("=" * 120)
print(f"当前余额: ${account['current_balance']:.2f}")
print(f"冻结资金: ${account['frozen_balance']:.2f}")
print(f"总权益: ${account['total_equity']:.2f}")
print(f"未实现盈亏: ${account['unrealized_pnl']:.2f}")
print(f"已实现盈亏: ${account['realized_pnl']:.2f}")
print(f"总盈亏: ${account['total_profit_loss']:.2f}")
print()

# 6. 分析问题
print("=" * 120)
print("问题分析:")
print("=" * 120)
print()

if len(missed_buys) > 0:
    print(f"[!] 错失了 {len(missed_buys)} 个买入信号")

    # 统计拒绝原因
    reasons = {}
    for sig in missed_buys:
        reason = sig['decision_reason'] or 'Unknown'
        reasons[reason] = reasons.get(reason, 0) + 1

    print("\n拒绝原因统计:")
    for reason, count in sorted(reasons.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {reason}: {count} 次")

if len(executed_buys) < 5:
    print(f"\n[!] 最近2天只执行了 {len(executed_buys)} 笔买入，执行率过低")

available = float(account['current_balance'] - account['frozen_balance'])
total_equity = float(account['total_equity'])
if available > total_equity * 0.7:
    print(f"\n[!] 可用资金占比 {available/total_equity*100:.1f}%，仓位过轻，未充分利用资金")

print()

cursor.close()
conn.close()
