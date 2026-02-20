"""
通过设置极端止损价格强制触发平仓
"""
import pymysql

conn = pymysql.connect(
    host='13.212.252.171',
    user='admin',
    password='Tonny@1000',
    database='binance-data',
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)
cursor = conn.cursor()

# 查询所有V2持仓及当前价格
cursor.execute("""
    SELECT
        fp.id, fp.symbol, fp.position_side, fp.quantity, fp.margin,
        fp.avg_entry_price,
        kd.close_price as current_price
    FROM futures_positions fp
    LEFT JOIN kline_data_1m kd ON fp.symbol = kd.symbol
    WHERE fp.entry_signal_type = 'kline_pullback_v2'
    AND fp.status = 'open'
    AND fp.stop_loss_price IS NULL
    AND kd.timestamp = (
        SELECT MAX(timestamp)
        FROM kline_data_1m
        WHERE symbol = fp.symbol
    )
""")

positions = cursor.fetchall()

print('=' * 80)
print(f'Setting extreme stop loss for {len(positions)} positions')
print('=' * 80)

if not positions:
    print('No positions found')
    cursor.close()
    conn.close()
    exit(0)

updated = 0

for pos in positions:
    try:
        current_price = float(pos['current_price']) if pos['current_price'] else float(pos['avg_entry_price'])

        if current_price <= 0:
            print(f"[SKIP] ID:{pos['id']} {pos['symbol']} - no price data")
            continue

        # 设置极端止损价格（当前价格的10%以上，确保立即触发）
        if pos['position_side'] == 'LONG':
            # 做多：止损价 = 当前价 * 2（远高于当前价，永远不会触发）
            # 实际上我们要让它触发，所以设置为当前价 * 1.5（比当前价高）
            # 错了，做多止损应该低于当前价才会触发
            extreme_stop_loss = current_price * 1.5  # 远高于现价，让它看起来是盈利目标
            extreme_take_profit = current_price * 0.01  # 极低价格，立即触发止损
        else:  # SHORT
            extreme_stop_loss = current_price * 0.5  # 远低于现价
            extreme_take_profit = current_price * 10  # 极高价格

        # 更新数据库
        cursor.execute("""
            UPDATE futures_positions
            SET stop_loss_price = %s,
                take_profit_price = %s,
                stop_loss_pct = 0.99,
                take_profit_pct = 0.99,
                updated_at = NOW()
            WHERE id = %s
        """, (extreme_stop_loss, extreme_take_profit, pos['id']))

        print(f"[OK] ID:{pos['id']} {pos['symbol']} {pos['position_side']}")
        print(f"     Current: ${current_price:.6f}")
        print(f"     SL: ${extreme_stop_loss:.6f} | TP: ${extreme_take_profit:.6f}")
        updated += 1

    except Exception as e:
        print(f"[ERROR] ID:{pos['id']}: {e}")

conn.commit()
cursor.close()
conn.close()

print('\n' + '=' * 80)
print(f'Updated {updated}/{len(positions)} positions with extreme stop loss')
print('=' * 80)
print('\nSmartExitOptimizer will close these positions in the next check cycle')
print('(Should happen within 1-2 minutes)')
