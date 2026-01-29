#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查模拟交易历史记录"""

import pymysql
import sys
import io
from dotenv import load_dotenv
import os

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'),
    cursorclass=pymysql.cursors.DictCursor,
    charset='utf8mb4'
)

cursor = conn.cursor()

print('=' * 100)
print('检查模拟交易历史记录')
print('=' * 100)
print()

# 1. 检查 paper_trading_trades 表是否存在
print('1. 检查 paper_trading_trades 表...')
cursor.execute("SHOW TABLES LIKE 'paper_trading_trades'")
if cursor.fetchone():
    print('   ✓ paper_trading_trades 表存在')
else:
    print('   ✗ paper_trading_trades 表不存在')
    conn.close()
    sys.exit(1)

print()

# 2. 检查表结构
print('2. 检查表结构...')
cursor.execute("DESCRIBE paper_trading_trades")
columns = cursor.fetchall()
print(f'   表有 {len(columns)} 个字段:')
for col in columns:
    print(f"   - {col['Field']}: {col['Type']}")

print()

# 3. 检查数据量
print('3. 检查数据量...')
cursor.execute("SELECT COUNT(*) as total FROM paper_trading_trades")
result = cursor.fetchone()
print(f"   总记录数: {result['total']}")

print()

# 4. 查询最近10条交易记录
print('4. 最近10条交易记录...')
cursor.execute("""
    SELECT
        t.trade_id,
        t.symbol,
        t.side,
        t.price,
        t.quantity,
        t.realized_pnl,
        t.pnl_pct,
        t.trade_time,
        o.order_source
    FROM paper_trading_trades t
    LEFT JOIN paper_trading_orders o ON t.order_id = o.order_id
    ORDER BY t.trade_time DESC
    LIMIT 10
""")

trades = cursor.fetchall()

if trades:
    print(f"   找到 {len(trades)} 条记录:")
    print()
    print(f"   {'交易ID':<10} {'币种':<15} {'方向':<6} {'价格':<12} {'数量':<12} {'盈亏%':<10} {'盈亏$':<12} {'交易时间':<20} {'来源':<10}")
    print('   ' + '-' * 120)
    for trade in trades:
        print(f"   {trade['trade_id']:<10} "
              f"{trade['symbol']:<15} "
              f"{trade['side']:<6} "
              f"{trade['price']:<12.6f} "
              f"{trade['quantity']:<12.6f} "
              f"{trade['pnl_pct'] if trade['pnl_pct'] else 0:<10.2f} "
              f"{trade['realized_pnl'] if trade['realized_pnl'] else 0:<12.2f} "
              f"{trade['trade_time'].strftime('%Y-%m-%d %H:%M:%S'):<20} "
              f"{trade.get('order_source', 'manual'):<10}")
else:
    print('   ✗ 没有找到任何交易记录')

print()

# 5. 按方向统计
print('5. 按交易方向统计...')
cursor.execute("""
    SELECT
        side,
        COUNT(*) as count,
        SUM(realized_pnl) as total_pnl
    FROM paper_trading_trades
    GROUP BY side
""")

stats = cursor.fetchall()
if stats:
    for stat in stats:
        print(f"   {stat['side']}: {stat['count']}笔, 总盈亏: ${stat['total_pnl'] or 0:.2f}")
else:
    print('   无数据')

print()

# 6. 检查是否有已平仓的持仓记录
print('6. 检查已平仓的持仓...')
cursor.execute("""
    SELECT
        COUNT(*) as total,
        SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) as closed_count
    FROM paper_trading_positions
""")

pos_stats = cursor.fetchone()
print(f"   总持仓记录: {pos_stats['total']}")
print(f"   已平仓记录: {pos_stats['closed_count']}")

print()
print('=' * 100)

cursor.close()
conn.close()
