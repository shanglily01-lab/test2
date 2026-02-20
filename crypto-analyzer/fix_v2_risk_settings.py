"""
为所有V2持仓补充止损止盈和超时设置
"""
import pymysql
from datetime import datetime, timedelta

conn = pymysql.connect(
    host='13.212.252.171',
    user='admin',
    password='Tonny@1000',
    database='binance-data',
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)
cursor = conn.cursor()

# 查询需要修复的持仓
cursor.execute("""
    SELECT id, symbol, position_side, avg_entry_price, entry_score
    FROM futures_positions
    WHERE entry_signal_type = 'kline_pullback_v2'
    AND status = 'open'
    AND stop_loss_price IS NULL
""")

positions = cursor.fetchall()

print('=' * 80)
print(f'Fixing {len(positions)} V2 positions')
print('=' * 80)

if not positions:
    print('No positions to fix')
    cursor.close()
    conn.close()
    exit(0)

# 默认参数
DEFAULT_STOP_LOSS_PCT = 0.03  # 3%
DEFAULT_TAKE_PROFIT_PCT = 0.02  # 2%
DEFAULT_MAX_HOLD_MINUTES = 180  # 3小时

fixed_count = 0

for pos in positions:
    try:
        entry_price = float(pos['avg_entry_price']) if pos['avg_entry_price'] else 0

        if entry_price <= 0:
            print(f"[SKIP] ID:{pos['id']} {pos['symbol']} - invalid entry price")
            continue

        direction = pos['position_side']

        # 计算止损止盈
        if direction == 'LONG':
            stop_loss_price = entry_price * (1 - DEFAULT_STOP_LOSS_PCT)
            take_profit_price = entry_price * (1 + DEFAULT_TAKE_PROFIT_PCT)
        else:  # SHORT
            stop_loss_price = entry_price * (1 + DEFAULT_STOP_LOSS_PCT)
            take_profit_price = entry_price * (1 - DEFAULT_TAKE_PROFIT_PCT)

        # 计算超时时间（从现在开始计算剩余时间）
        timeout_at = datetime.utcnow() + timedelta(minutes=30)  # 给30分钟时间

        # 更新数据库
        cursor.execute("""
            UPDATE futures_positions
            SET stop_loss_price = %s,
                take_profit_price = %s,
                stop_loss_pct = %s,
                take_profit_pct = %s,
                max_hold_minutes = %s,
                timeout_at = %s,
                updated_at = NOW()
            WHERE id = %s
        """, (
            stop_loss_price,
            take_profit_price,
            DEFAULT_STOP_LOSS_PCT,
            DEFAULT_TAKE_PROFIT_PCT,
            DEFAULT_MAX_HOLD_MINUTES,
            timeout_at,
            pos['id']
        ))

        print(f"[OK] ID:{pos['id']} {pos['symbol']} {direction}")
        print(f"     Entry: ${entry_price:.6f}")
        print(f"     SL: ${stop_loss_price:.6f} ({DEFAULT_STOP_LOSS_PCT*100}%)")
        print(f"     TP: ${take_profit_price:.6f} ({DEFAULT_TAKE_PROFIT_PCT*100}%)")
        print(f"     Timeout: {timeout_at} (30 min from now)")

        fixed_count += 1

    except Exception as e:
        print(f"[ERROR] ID:{pos['id']} {pos['symbol']}: {e}")

conn.commit()
cursor.close()
conn.close()

print('\n' + '=' * 80)
print(f'Fixed {fixed_count}/{len(positions)} positions')
print('=' * 80)
print('\nRisk settings applied:')
print(f'  Stop Loss: {DEFAULT_STOP_LOSS_PCT*100}%')
print(f'  Take Profit: {DEFAULT_TAKE_PROFIT_PCT*100}%')
print(f'  Timeout: 30 minutes from now')
print('\nPositions will now be managed by SmartExitOptimizer')
