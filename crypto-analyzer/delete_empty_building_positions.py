"""
删除quantity=0的building状态V2持仓
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

# 先查询要删除的记录
cursor.execute("""
    SELECT id, symbol, position_side, entry_signal_type, created_at
    FROM futures_positions
    WHERE status = 'building'
    AND entry_signal_type = 'kline_pullback_v2'
    AND quantity = 0
    ORDER BY id
""")

positions = cursor.fetchall()

print('=' * 80)
print(f'Found {len(positions)} empty V2 building positions to delete:')
print('=' * 80)

for pos in positions:
    print(f"ID: {pos['id']} | {pos['symbol']} {pos['position_side']} | Created: {pos['created_at']}")

# 执行删除
if positions:
    print('\n' + '=' * 80)
    print('Deleting...')
    print('=' * 80)

    cursor.execute("""
        DELETE FROM futures_positions
        WHERE status = 'building'
        AND entry_signal_type = 'kline_pullback_v2'
        AND quantity = 0
    """)

    deleted_count = cursor.rowcount
    conn.commit()

    print(f'\nDeleted {deleted_count} positions successfully!')
else:
    print('\nNo positions to delete')

cursor.close()
conn.close()
