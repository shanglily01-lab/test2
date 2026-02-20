"""
快速平仓 - 将V2持仓标记为需要立即平仓
通过设置极端的止损价格，让SmartExitOptimizer立即触发平仓
"""
import pymysql
import requests

conn = pymysql.connect(
    host='13.212.252.171',
    user='admin',
    password='Tonny@1000',
    database='binance-data',
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)
cursor = conn.cursor()

# 查询所有V2持仓
cursor.execute("""
    SELECT id, symbol, position_side, quantity, margin
    FROM futures_positions
    WHERE entry_signal_type = 'kline_pullback_v2'
    AND status = 'open'
    AND stop_loss_price IS NULL
""")

positions = cursor.fetchall()

print('=' * 80)
print(f'Found {len(positions)} V2 positions')
print('=' * 80)

if not positions:
    print('No positions found')
    cursor.close()
    conn.close()
    exit(0)

# 列出所有持仓
for pos in positions:
    print(f"ID:{pos['id']} | {pos['symbol']} {pos['position_side']} | Qty:{pos['quantity']:.2f} | Margin:${pos['margin']:.2f}")

print('\n' + '=' * 80)

# 方法：通过API批量平仓
API_BASE = "http://13.212.252.171:9020"

print('\nAttempting to close via API...')
closed_count = 0
failed = []

for pos in positions:
    try:
        # 调用平仓API
        response = requests.post(
            f"{API_BASE}/api/coin-futures/close",
            json={
                "symbol": pos['symbol'],
                "position_side": pos['position_side'],
                "quantity": float(pos['quantity']),
                "reason": "emergency_close_no_risk_settings"
            },
            timeout=10
        )

        if response.status_code == 200:
            result = response.json()
            if result.get('success'):
                print(f"  [OK] {pos['symbol']} {pos['position_side']}")
                closed_count += 1
            else:
                print(f"  [FAIL] {pos['symbol']}: {result.get('message', 'Unknown error')}")
                failed.append(pos)
        else:
            print(f"  [FAIL] {pos['symbol']}: HTTP {response.status_code}")
            failed.append(pos)

    except Exception as e:
        print(f"  [ERROR] {pos['symbol']}: {e}")
        failed.append(pos)

print('\n' + '=' * 80)
print(f'Closed: {closed_count}/{len(positions)}')

if failed:
    print(f'\nFailed positions: {len(failed)}')
    for pos in failed:
        print(f"  ID:{pos['id']} {pos['symbol']}")

cursor.close()
conn.close()
