"""
直接删除所有问题V2持仓
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

# 先查询
cursor.execute("""
    SELECT id, symbol, position_side, quantity, margin
    FROM futures_positions
    WHERE entry_signal_type = 'kline_pullback_v2'
    AND status = 'open'
    AND stop_loss_price IS NULL
""")

positions = cursor.fetchall()

print('=' * 80)
print(f'Will DELETE {len(positions)} V2 positions:')
print('=' * 80)

for pos in positions:
    print(f"ID:{pos['id']} | {pos['symbol']} {pos['position_side']} | Margin:${pos['margin']:.2f}")

# 直接删除
cursor.execute("""
    DELETE FROM futures_positions
    WHERE entry_signal_type = 'kline_pullback_v2'
    AND status = 'open'
    AND stop_loss_price IS NULL
""")

deleted = cursor.rowcount
conn.commit()

print('\n' + '=' * 80)
print(f'DELETED {deleted} positions from database')
print('=' * 80)
print('\nWARNING: Positions removed from database but NOT closed on exchange!')
print('You need to manually close these positions on Binance!')

cursor.close()
conn.close()
