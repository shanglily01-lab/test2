#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从positions表生成trades表记录
将2月6日的买入和2月7日的卖出记录插入paper_trading_trades表
"""
import pymysql
from datetime import datetime, timezone
from dotenv import load_dotenv
import os
import sys
import io
import uuid

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

ACCOUNT_ID = 1

def get_connection():
    return pymysql.connect(**DB_CONFIG)

def generate_trade_id(symbol, side, timestamp):
    """生成交易ID"""
    dt = datetime.fromtimestamp(timestamp/1000)
    time_str = dt.strftime('%Y%m%d%H%M%S')
    unique_id = str(uuid.uuid4())[:8]
    return f"TRADE_{time_str}_{unique_id}"

def generate_order_id(symbol, side, timestamp):
    """生成订单ID"""
    dt = datetime.fromtimestamp(timestamp/1000)
    time_str = dt.strftime('%Y%m%d%H%M%S')
    unique_id = str(uuid.uuid4())[:8]
    return f"ORDER_{time_str}_{unique_id}"

def populate_trades():
    """填充交易记录"""
    print("=" * 80)
    print("从positions表生成trades表记录")
    print("=" * 80)

    conn = get_connection()
    cursor = conn.cursor()

    # 清空现有trades
    cursor.execute("DELETE FROM paper_trading_trades WHERE account_id = %s", (ACCOUNT_ID,))
    print(f"\n已清空现有交易记录，删除了 {cursor.rowcount} 条")

    # 1. 从positions表获取所有持仓（包括open和closed）
    cursor.execute("""
        SELECT
            symbol, quantity, avg_entry_price, total_cost,
            current_price, unrealized_pnl, unrealized_pnl_pct,
            status, created_at, updated_at
        FROM paper_trading_positions
        WHERE account_id = %s
        ORDER BY created_at ASC
    """, (ACCOUNT_ID,))

    positions = cursor.fetchall()

    if not positions:
        print("\n❌ 没有找到持仓数据")
        cursor.close()
        conn.close()
        return

    print(f"\n找到 {len(positions)} 个持仓记录")

    buy_count = 0
    sell_count = 0

    # 2. 为每个持仓生成BUY交易记录
    print("\n" + "-" * 80)
    print("生成BUY交易记录")
    print("-" * 80)

    for pos in positions:
        symbol = pos['symbol']
        quantity = float(pos['quantity'])
        buy_price = float(pos['avg_entry_price'])
        total_cost = float(pos['total_cost'])
        buy_time = pos['created_at']
        buy_timestamp = int(buy_time.timestamp() * 1000)

        # 手续费0.1%
        fee = total_cost * 0.001

        order_id = generate_order_id(symbol, 'BUY', buy_timestamp)
        trade_id = generate_trade_id(symbol, 'BUY', buy_timestamp)

        try:
            cursor.execute("""
                INSERT INTO paper_trading_trades (
                    account_id, order_id, trade_id, symbol, side,
                    price, quantity, total_amount, fee, fee_asset,
                    trade_time, created_at
                ) VALUES (
                    %s, %s, %s, %s, 'BUY',
                    %s, %s, %s, %s, 'USDT',
                    %s, %s
                )
            """, (
                ACCOUNT_ID, order_id, trade_id, symbol,
                buy_price, quantity, total_cost, fee,
                buy_time, buy_time
            ))

            buy_count += 1
            print(f"  ✅ {buy_count:2d}. BUY  {symbol:12} @ {buy_price:10.6f} x {quantity:10.2f} = ${total_cost:8.2f}")

        except Exception as e:
            print(f"  ❌ BUY {symbol} 失败: {e}")

    # 3. 为已关闭的持仓生成SELL交易记录
    print("\n" + "-" * 80)
    print("生成SELL交易记录")
    print("-" * 80)

    for pos in positions:
        if pos['status'] != 'closed':
            continue

        # 跳过没有卖出价格的记录
        if pos['current_price'] is None:
            continue

        symbol = pos['symbol']
        quantity = float(pos['quantity'])
        buy_price = float(pos['avg_entry_price'])
        sell_price = float(pos['current_price'])
        total_cost = float(pos['total_cost'])
        sell_amount = quantity * sell_price
        pnl = float(pos['unrealized_pnl'])
        pnl_pct = float(pos['unrealized_pnl_pct'])

        sell_time = pos['updated_at']
        sell_timestamp = int(sell_time.timestamp() * 1000)

        # 手续费0.1%
        fee = sell_amount * 0.001
        net_amount = sell_amount - fee

        order_id = generate_order_id(symbol, 'SELL', sell_timestamp)
        trade_id = generate_trade_id(symbol, 'SELL', sell_timestamp)

        try:
            cursor.execute("""
                INSERT INTO paper_trading_trades (
                    account_id, order_id, trade_id, symbol, side,
                    price, quantity, total_amount, fee, fee_asset,
                    realized_pnl, pnl_pct, cost_price,
                    trade_time, created_at
                ) VALUES (
                    %s, %s, %s, %s, 'SELL',
                    %s, %s, %s, %s, 'USDT',
                    %s, %s, %s,
                    %s, %s
                )
            """, (
                ACCOUNT_ID, order_id, trade_id, symbol,
                sell_price, quantity, sell_amount, fee,
                pnl, pnl_pct * 100,  # 转换为百分比
                buy_price,
                sell_time, sell_time
            ))

            sell_count += 1
            print(f"  ✅ {sell_count:2d}. SELL {symbol:12} @ {sell_price:10.6f} x {quantity:10.2f} = ${sell_amount:8.2f} (盈亏: ${pnl:+8.2f})")

        except Exception as e:
            print(f"  ❌ SELL {symbol} 失败: {e}")

    conn.commit()

    # 4. 统计
    print("\n" + "=" * 80)
    print("交易记录生成完成")
    print("=" * 80)
    print(f"BUY 记录数:  {buy_count}")
    print(f"SELL 记录数: {sell_count}")
    print(f"总记录数:    {buy_count + sell_count}")

    # 5. 验证
    cursor.execute("SELECT COUNT(*) as count FROM paper_trading_trades WHERE account_id = %s", (ACCOUNT_ID,))
    total = cursor.fetchone()['count']
    print(f"\n数据库中总记录数: {total}")

    cursor.close()
    conn.close()

if __name__ == '__main__':
    populate_trades()
