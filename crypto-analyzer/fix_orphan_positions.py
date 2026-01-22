#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复没有对应 futures_trades 记录的 orphan positions"""
import sys
import io
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'd:\test2\crypto-analyzer')

import pymysql
import uuid
from decimal import Decimal
from datetime import datetime

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data',
    'charset': 'utf8mb4'
}

print("=" * 80)
print("修复 Orphan Positions (没有 futures_trades 记录的平仓持仓)")
print("=" * 80)
print()

conn = pymysql.connect(**db_config)
cursor = conn.cursor(pymysql.cursors.DictCursor)

# 1. 查找所有 orphan positions
print("1. 查找 orphan positions...")
cursor.execute("""
    SELECT p.*
    FROM futures_positions p
    LEFT JOIN futures_trades t ON t.position_id = p.id AND t.side LIKE 'CLOSE%'
    WHERE p.account_id = 2
        AND p.status = 'closed'
        AND p.entry_signal_type LIKE 'SMART_BRAIN%'
        AND t.id IS NULL
        AND DATE(p.close_time) = '2026-01-22'
    ORDER BY p.id
""")

orphan_positions = cursor.fetchall()
print(f"   找到 {len(orphan_positions)} 个 orphan positions")
print()

if len(orphan_positions) == 0:
    print("没有需要修复的记录")
    cursor.close()
    conn.close()
    sys.exit(0)

# 2. 询问用户是否确认修复
print(f"将为这 {len(orphan_positions)} 个持仓补全以下记录:")
print("  - futures_orders (平仓订单)")
print("  - futures_trades (交易记录)")
print()
print("示例 (前5个):")
for i, pos in enumerate(orphan_positions[:5], 1):
    print(f"  {i}. ID {pos['id']}: {pos['symbol']} {pos['position_side']} | "
          f"盈亏={pos['realized_pnl']:.2f} | 平仓时间={pos['close_time']}")
print()

confirm = input("确认修复? (yes/no): ").strip().lower()
if confirm not in ['yes', 'y']:
    print("取消修复")
    cursor.close()
    conn.close()
    sys.exit(0)

print()
print("=" * 80)
print("开始修复...")
print("=" * 80)
print()

# 3. 为每个 orphan position 创建 orders 和 trades
fee_rate = Decimal('0.0004')
fixed_count = 0
failed_count = 0

for pos in orphan_positions:
    try:
        position_id = pos['id']
        symbol = pos['symbol']
        position_side = pos['position_side']
        leverage = pos['leverage']
        quantity = Decimal(str(pos['quantity']))
        entry_price = Decimal(str(pos['entry_price']))
        mark_price = Decimal(str(pos['mark_price']))  # 平仓价
        realized_pnl = Decimal(str(pos['realized_pnl']))
        close_time = pos['close_time']
        account_id = pos['account_id']

        # 计算值
        side = f"CLOSE_{position_side}"
        close_value = quantity * mark_price
        open_value = quantity * entry_price

        # 反推原始盈亏和手续费
        # realized_pnl = pnl - fee
        # fee = close_value * fee_rate
        fee = close_value * fee_rate
        pnl = realized_pnl + fee

        # 计算盈亏百分比
        if open_value > 0:
            pnl_pct = (pnl / open_value) * 100
        else:
            pnl_pct = Decimal('0')

        # 计算 ROI
        margin = Decimal(str(pos['margin']))
        if margin > 0:
            roi = (pnl / margin) * 100
        else:
            roi = Decimal('0')

        # 生成 order_id 和 trade_id
        order_id = f"FUT-{uuid.uuid4().hex[:16].upper()}"
        trade_id = f"T-{uuid.uuid4().hex[:16].upper()}"

        # 插入 futures_orders
        cursor.execute("""
            INSERT INTO futures_orders (
                account_id, order_id, position_id, symbol,
                side, order_type, leverage,
                price, quantity, executed_quantity,
                total_value, executed_value,
                fee, fee_rate, status,
                avg_fill_price, fill_time,
                realized_pnl, pnl_pct,
                order_source, notes
            ) VALUES (
                %s, %s, %s, %s,
                %s, 'MARKET', %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, 'FILLED',
                %s, %s,
                %s, %s,
                %s, %s
            )
        """, (
            account_id, order_id, position_id, symbol,
            side, leverage,
            float(mark_price), float(quantity), float(quantity),
            float(close_value), float(close_value),
            float(fee), float(fee_rate),
            float(mark_price), close_time,
            float(realized_pnl), float(pnl_pct),
            'strategy', 'backfill'
        ))

        # 插入 futures_trades
        cursor.execute("""
            INSERT INTO futures_trades (
                account_id, order_id, position_id, trade_id,
                symbol, side, price, quantity, notional_value,
                leverage, margin, fee, fee_rate,
                realized_pnl, pnl_pct, roi,
                entry_price, trade_time
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s
            )
        """, (
            account_id, order_id, position_id, trade_id,
            symbol, side, float(mark_price), float(quantity), float(close_value),
            leverage, float(margin), float(fee), float(fee_rate),
            float(realized_pnl), float(pnl_pct), float(roi),
            float(entry_price), close_time
        ))

        conn.commit()
        fixed_count += 1

        if fixed_count % 10 == 0:
            print(f"   已修复 {fixed_count}/{len(orphan_positions)} 个")

    except Exception as e:
        print(f"   ❌ 修复 Position {position_id} 失败: {e}")
        failed_count += 1
        conn.rollback()

print()
print("=" * 80)
print("修复完成!")
print("=" * 80)
print(f"成功: {fixed_count} 个")
print(f"失败: {failed_count} 个")
print()

# 4. 验证修复结果
print("验证修复结果...")
cursor.execute("""
    SELECT COUNT(*) as remaining
    FROM futures_positions p
    LEFT JOIN futures_trades t ON t.position_id = p.id AND t.side LIKE 'CLOSE%'
    WHERE p.account_id = 2
        AND p.status = 'closed'
        AND p.entry_signal_type LIKE 'SMART_BRAIN%'
        AND t.id IS NULL
        AND DATE(p.close_time) = '2026-01-22'
""")

remaining = cursor.fetchone()['remaining']
print(f"剩余未修复: {remaining} 个")

if remaining == 0:
    print("✅ 所有 orphan positions 已修复!")
else:
    print(f"⚠️  还有 {remaining} 个 orphan positions 未修复")

cursor.close()
conn.close()

print()
print("现在用户应该能在"合约交易页面"看到这些记录了")
print("=" * 80)
