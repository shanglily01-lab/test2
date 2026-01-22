#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析超级大脑交易表现 - 扩展版(最近7天)"""
import sys
import io
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'd:\test2\crypto-analyzer')

import pymysql
from datetime import datetime, timedelta

# 服务器数据库配置
db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data',
    'charset': 'utf8mb4'
}

print("=" * 80)
print("超级大脑交易表现分析 - 最近7天")
print("=" * 80)
print()

conn = pymysql.connect(**db_config)
cursor = conn.cursor(pymysql.cursors.DictCursor)

# 0. 先检查各账户的超级大脑交易数量
print("=== 各账户超级大脑交易数量(最近7天) ===")
cursor.execute("""
    SELECT
        account_id,
        COUNT(*) as total_trades,
        SUM(realized_pnl) as total_pnl,
        MIN(close_time) as first_trade,
        MAX(close_time) as last_trade
    FROM futures_positions
    WHERE status = 'closed'
        AND entry_signal_type LIKE 'SMART_BRAIN%'
        AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
    GROUP BY account_id
    ORDER BY account_id
""")
for row in cursor.fetchall():
    print(f"账户 {row['account_id']}: {row['total_trades']}笔, 总盈亏{row['total_pnl']:.2f} USDT")
    print(f"  首笔: {row['first_trade']}, 末笔: {row['last_trade']}")

# 查找有超级大脑交易的账户
cursor.execute("""
    SELECT DISTINCT account_id
    FROM futures_positions
    WHERE status = 'closed'
        AND entry_signal_type LIKE 'SMART_BRAIN%'
        AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
""")
accounts = [row['account_id'] for row in cursor.fetchall()]

if not accounts:
    print("\n未找到任何超级大脑交易记录(最近7天)")
    cursor.close()
    conn.close()
    sys.exit(0)

# 使用第一个有数据的账户
target_account = accounts[0]
print(f"\n使用账户: {target_account}")
print("=" * 80)

# 1. 查看最近7天的交易概况
print("\n=== 最近7天交易概况 ===")
cursor.execute(f"""
    SELECT
        COUNT(*) as total_trades,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl,
        MAX(realized_pnl) as max_win,
        MIN(realized_pnl) as max_loss
    FROM futures_positions
    WHERE status = 'closed'
        AND account_id = {target_account}
        AND entry_signal_type LIKE 'SMART_BRAIN%'
        AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
""")
stats_7d = cursor.fetchone()
if stats_7d and stats_7d['total_trades']:
    win_rate = (stats_7d['wins'] / stats_7d['total_trades'] * 100) if stats_7d['total_trades'] > 0 else 0
    print(f"总交易: {stats_7d['total_trades']}笔")
    print(f"盈利: {stats_7d['wins']}笔 | 亏损: {stats_7d['losses']}笔")
    print(f"胜率: {win_rate:.1f}%")
    print(f"总盈亏: {stats_7d['total_pnl']:.2f} USDT")
    print(f"平均盈亏: {stats_7d['avg_pnl']:.2f} USDT")
    print(f"最大盈利: {stats_7d['max_win']:.2f} USDT")
    print(f"最大亏损: {stats_7d['max_loss']:.2f} USDT")

# 2. 按分数段和方向统计
print("\n=== 各分数段和方向表现(最近7天) ===")
cursor.execute(f"""
    SELECT
        entry_signal_type,
        position_side,
        COUNT(*) as cnt,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl
    FROM futures_positions
    WHERE status = 'closed'
        AND account_id = {target_account}
        AND entry_signal_type LIKE 'SMART_BRAIN%'
        AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
    GROUP BY entry_signal_type, position_side
    ORDER BY total_pnl DESC
""")
for row in cursor.fetchall():
    signal = row['entry_signal_type']
    side = "做多" if row['position_side'] == 'LONG' else "做空"
    win_rate = (row['wins'] / row['cnt'] * 100) if row['cnt'] > 0 else 0
    print(f"{signal}({side}): {row['cnt']}笔, 胜率{win_rate:.1f}%, 总盈亏{row['total_pnl']:.2f}, 平均{row['avg_pnl']:.2f}")

# 3. 最近亏损的20笔交易
print("\n=== 最近亏损的20笔交易 ===")
cursor.execute(f"""
    SELECT
        symbol,
        entry_signal_type,
        position_side,
        entry_price,
        mark_price,
        quantity,
        realized_pnl,
        notes as close_reason,
        open_time,
        close_time,
        TIMESTAMPDIFF(MINUTE, open_time, close_time) as holding_minutes
    FROM futures_positions
    WHERE status = 'closed'
        AND account_id = {target_account}
        AND entry_signal_type LIKE 'SMART_BRAIN%'
        AND realized_pnl < 0
        AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
    ORDER BY realized_pnl ASC
    LIMIT 20
""")
for i, row in enumerate(cursor.fetchall(), 1):
    side = "做多" if row['position_side'] == 'LONG' else "做空"
    print(f"\n{i}. {row['symbol']} - {row['entry_signal_type']}({side})")
    print(f"   开仓: {row['entry_price']:.4f} -> 平仓: {row['mark_price']:.4f}")
    print(f"   数量: {row['quantity']:.4f}, 亏损: {row['realized_pnl']:.2f} USDT")
    print(f"   持仓: {row['holding_minutes']}分钟, 平仓原因: {row['close_reason']}")
    print(f"   时间: {row['close_time'].strftime('%Y-%m-%d %H:%M:%S')}")

# 4. 按天统计
print("\n=== 每日盈亏统计(最近7天) ===")
cursor.execute(f"""
    SELECT
        DATE(close_time) as trade_date,
        COUNT(*) as cnt,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as daily_pnl
    FROM futures_positions
    WHERE status = 'closed'
        AND account_id = {target_account}
        AND entry_signal_type LIKE 'SMART_BRAIN%'
        AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
    GROUP BY DATE(close_time)
    ORDER BY trade_date DESC
""")
for row in cursor.fetchall():
    win_rate = (row['wins'] / row['cnt'] * 100) if row['cnt'] > 0 else 0
    print(f"{row['trade_date']}: {row['cnt']}笔, 胜率{win_rate:.1f}%, 盈亏{row['daily_pnl']:.2f} USDT")

# 5. 当前持仓情况
print("\n=== 当前持仓情况 ===")
cursor.execute(f"""
    SELECT
        symbol,
        entry_signal_type,
        position_side,
        entry_price,
        quantity,
        unrealized_pnl,
        unrealized_pnl_pct,
        open_time,
        TIMESTAMPDIFF(MINUTE, open_time, NOW()) as holding_minutes
    FROM futures_positions
    WHERE status = 'open'
        AND account_id = {target_account}
        AND entry_signal_type LIKE 'SMART_BRAIN%'
    ORDER BY unrealized_pnl ASC
""")
open_positions = cursor.fetchall()
if open_positions:
    total_unrealized = sum(p['unrealized_pnl'] for p in open_positions)
    print(f"持仓数量: {len(open_positions)}个")
    print(f"未实现盈亏: {total_unrealized:.2f} USDT")
    print()
    for i, row in enumerate(open_positions, 1):
        side = "做多" if row['position_side'] == 'LONG' else "做空"
        print(f"{i}. {row['symbol']} - {row['entry_signal_type']}({side})")
        print(f"   开仓价: {row['entry_price']:.4f}, 数量: {row['quantity']:.4f}")
        print(f"   未实现盈亏: {row['unrealized_pnl']:.2f} ({row['unrealized_pnl_pct']:.2f}%)")
        print(f"   持仓: {row['holding_minutes']}分钟")
else:
    print("当前无持仓")

cursor.close()
conn.close()

print("\n" + "=" * 80)
print("分析完成")
print("=" * 80)
