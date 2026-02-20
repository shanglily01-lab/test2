"""
紧急批量平仓所有没有止损止盈的V2持仓
"""
import pymysql
import asyncio
import sys
import os

# 添加路径
sys.path.insert(0, os.path.dirname(__file__))

from app.trading.coin_futures_trading_engine import CoinFuturesTradingEngine

async def emergency_close_all_v2():
    """批量平仓所有V2持仓"""

    # 初始化交易引擎
    engine = CoinFuturesTradingEngine(account_id=3)  # 币本位账户

    # 连接数据库
    conn = pymysql.connect(
        host='13.212.252.171',
        user='admin',
        password='Tonny@1000',
        database='binance-data',
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )
    cursor = conn.cursor()

    # 查询所有需要平仓的V2持仓
    cursor.execute("""
        SELECT id, symbol, position_side, quantity, avg_entry_price, margin
        FROM futures_positions
        WHERE entry_signal_type = 'kline_pullback_v2'
        AND status = 'open'
        AND stop_loss_price IS NULL
        AND timeout_at IS NULL
        ORDER BY id
    """)

    positions = cursor.fetchall()

    print('=' * 80)
    print(f'Found {len(positions)} V2 positions to close')
    print('=' * 80)

    if not positions:
        print('No positions to close')
        return

    for pos in positions:
        print(f"\nClosing ID:{pos['id']} | {pos['symbol']} {pos['position_side']}")
        print(f"  Quantity: {pos['quantity']:.2f} | Margin: ${pos['margin']:.2f}")

    print('\n' + '=' * 80)
    confirm = input('Confirm to close all these positions? (yes/no): ')

    if confirm.lower() != 'yes':
        print('Cancelled')
        return

    print('\nClosing positions...')
    success_count = 0
    fail_count = 0

    for pos in positions:
        try:
            symbol = pos['symbol']
            side = pos['position_side']
            quantity = pos['quantity']

            # 执行平仓
            result = await engine.close_position(
                symbol=symbol,
                position_side=side,
                quantity=quantity,
                reason='emergency_close_no_risk_management'
            )

            if result:
                print(f"  [OK] {symbol} {side} closed")
                success_count += 1
            else:
                print(f"  [FAIL] {symbol} {side} failed to close")
                fail_count += 1

        except Exception as e:
            print(f"  [ERROR] {pos['symbol']}: {e}")
            fail_count += 1

    print('\n' + '=' * 80)
    print(f'Results: {success_count} success, {fail_count} failed')
    print('=' * 80)

    cursor.close()
    conn.close()

if __name__ == '__main__':
    asyncio.run(emergency_close_all_v2())
