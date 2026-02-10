#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析最近24小时的止损情况"""

import pymysql, os
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()
conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'),
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)
cursor = conn.cursor()

print("=" * 120)
print("最近24小时订单分析 - 止损情况")
print("=" * 120)

# 1. 总体统计
cursor.execute('''
    SELECT
        COUNT(*) as total,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl
    FROM futures_positions
    WHERE account_id = 2
    AND status = 'closed'
    AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
''')

summary = cursor.fetchone()
total = summary['total']
wins = summary['wins']
losses = summary['losses']
total_pnl = float(summary['total_pnl'])
avg_pnl = float(summary['avg_pnl'])
win_rate = wins / total * 100 if total > 0 else 0

print(f"\n总体统计:")
print(f"  总订单: {total} 笔")
print(f"  盈利: {wins} 笔 | 亏损: {losses} 笔")
print(f"  胜率: {win_rate:.1f}%")
print(f"  总盈亏: ${total_pnl:.2f}")
print(f"  平均盈亏: ${avg_pnl:.2f}")

# 2. 按平仓原因统计
print("\n" + "=" * 120)
print("平仓原因统计")
print("=" * 120)

cursor.execute('''
    SELECT
        CASE
            WHEN notes LIKE '%止损%' THEN '止损'
            WHEN notes LIKE '%止盈%' THEN '止盈'
            WHEN notes LIKE '%超时%' THEN '超时'
            WHEN notes LIKE '%手动%' THEN '手动'
            WHEN notes LIKE '%紧急%' THEN '紧急干预'
            WHEN notes LIKE '%熔断%' THEN '熔断'
            ELSE '其他'
        END as close_reason,
        COUNT(*) as cnt,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl
    FROM futures_positions
    WHERE account_id = 2
    AND status = 'closed'
    AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
    GROUP BY close_reason
    ORDER BY cnt DESC
''')

close_reasons = cursor.fetchall()

print(f"\n{'平仓原因':<12} {'数量':>6} {'盈利':>6} {'胜率':>8} {'平均PNL':>10} {'总PNL':>12}")
print("-" * 120)

for r in close_reasons:
    reason = r['close_reason']
    cnt = r['cnt']
    wins = r['wins']
    wr = wins / cnt * 100 if cnt > 0 else 0
    avg = float(r['avg_pnl'])
    total = float(r['total_pnl'])
    print(f"{reason:<12} {cnt:>6} {wins:>6} {wr:>7.1f}% ${avg:>9.2f} ${total:>11.2f}")

# 3. 详细止损订单分析
print("\n" + "=" * 120)
print("止损订单详细分析")
print("=" * 120)

cursor.execute('''
    SELECT
        symbol,
        position_side,
        entry_price,
        stop_loss_price,
        unrealized_pnl_pct,
        realized_pnl,
        holding_hours,
        notes,
        open_time,
        close_time
    FROM futures_positions
    WHERE account_id = 2
    AND status = 'closed'
    AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
    AND notes LIKE '%止损%'
    ORDER BY close_time DESC
''')

stop_loss_orders = cursor.fetchall()

if stop_loss_orders:
    print(f"\n止损订单数: {len(stop_loss_orders)} 笔")
    print(f"\n{'时间':<16} {'币种':<12} {'方向':<6} {'止损%':>8} {'PNL':>10} {'持仓时长':>10} {'原因':<60}")
    print("-" * 120)

    stop_loss_pnls = []
    for order in stop_loss_orders:
        time_str = order['close_time'].strftime('%m-%d %H:%M')
        symbol = order['symbol']
        side = order['position_side']
        stop_pct = float(order['unrealized_pnl_pct']) if order['unrealized_pnl_pct'] else 0
        pnl = float(order['realized_pnl'])
        hours = float(order['holding_hours']) if order['holding_hours'] else 0
        notes = order['notes'][:60] if order['notes'] else ''

        stop_loss_pnls.append(pnl)

        print(f"{time_str:<16} {symbol:<12} {side:<6} {stop_pct:>7.2f}% ${pnl:>9.2f} {hours:>9.2f}H {notes:<60}")

    # 止损统计
    print("\n止损统计:")
    print(f"  止损订单数: {len(stop_loss_pnls)} 笔")
    print(f"  止损总亏损: ${sum(stop_loss_pnls):.2f}")
    print(f"  止损平均亏损: ${sum(stop_loss_pnls)/len(stop_loss_pnls):.2f}")

    # 分析止损百分比分布
    stop_pcts = [float(o['unrealized_pnl_pct']) for o in stop_loss_orders if o['unrealized_pnl_pct']]
    if stop_pcts:
        print(f"  止损百分比范围: {min(stop_pcts):.2f}% ~ {max(stop_pcts):.2f}%")
        print(f"  止损百分比平均: {sum(stop_pcts)/len(stop_pcts):.2f}%")

else:
    print("\n没有止损订单")

# 4. 分析止盈订单
print("\n" + "=" * 120)
print("止盈订单分析")
print("=" * 120)

cursor.execute('''
    SELECT
        symbol,
        position_side,
        unrealized_pnl_pct,
        realized_pnl,
        holding_hours,
        notes,
        close_time
    FROM futures_positions
    WHERE account_id = 2
    AND status = 'closed'
    AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
    AND notes LIKE '%止盈%'
    ORDER BY close_time DESC
''')

take_profit_orders = cursor.fetchall()

if take_profit_orders:
    print(f"\n止盈订单数: {len(take_profit_orders)} 笔")
    print(f"\n{'时间':<16} {'币种':<12} {'方向':<6} {'盈利%':>8} {'PNL':>10} {'持仓时长':>10}")
    print("-" * 120)

    take_profit_pnls = []
    for order in take_profit_orders:
        time_str = order['close_time'].strftime('%m-%d %H:%M')
        symbol = order['symbol']
        side = order['position_side']
        profit_pct = float(order['unrealized_pnl_pct']) if order['unrealized_pnl_pct'] else 0
        pnl = float(order['realized_pnl'])
        hours = float(order['holding_hours']) if order['holding_hours'] else 0

        take_profit_pnls.append(pnl)

        print(f"{time_str:<16} {symbol:<12} {side:<6} {profit_pct:>7.2f}% ${pnl:>9.2f} {hours:>9.2f}H")

    print(f"\n止盈统计:")
    print(f"  止盈订单数: {len(take_profit_pnls)} 笔")
    print(f"  止盈总盈利: ${sum(take_profit_pnls):.2f}")
    print(f"  止盈平均盈利: ${sum(take_profit_pnls)/len(take_profit_pnls):.2f}")
else:
    print("\n没有止盈订单")

# 5. 盈亏比分析
print("\n" + "=" * 120)
print("盈亏比分析")
print("=" * 120)

if stop_loss_orders and take_profit_orders:
    avg_loss = abs(sum(stop_loss_pnls) / len(stop_loss_pnls))
    avg_profit = sum(take_profit_pnls) / len(take_profit_pnls)
    profit_loss_ratio = avg_profit / avg_loss if avg_loss > 0 else 0

    print(f"\n  平均止损: ${avg_loss:.2f}")
    print(f"  平均止盈: ${avg_profit:.2f}")
    print(f"  盈亏比: {profit_loss_ratio:.2f}:1")

    # 计算盈亏平衡所需胜率
    required_wr = avg_loss / (avg_loss + avg_profit) * 100
    print(f"  盈亏平衡所需胜率: {required_wr:.1f}%")
    print(f"  当前实际胜率: {win_rate:.1f}%")

    if win_rate < required_wr:
        print(f"  ⚠️  胜率不足! 需要 {required_wr:.1f}% 但只有 {win_rate:.1f}%")
    else:
        print(f"  ✅ 胜率达标! 需要 {required_wr:.1f}% 实际 {win_rate:.1f}%")

cursor.close()
conn.close()

print("\n" + "=" * 120)
