import pymysql, os
from dotenv import load_dotenv

load_dotenv()
conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'),
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)
cursor = conn.cursor()

# Key metrics
cursor.execute('''
    SELECT
        COUNT(*) as total,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl
    FROM futures_positions
    WHERE account_id = 2 AND status = "closed"
    AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
''')
s = cursor.fetchone()

cursor.execute('''
    SELECT realized_pnl FROM futures_positions
    WHERE account_id = 2 AND status = "closed"
    AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
    AND notes LIKE "%止损%"
''')
stop_losses = [float(r['realized_pnl']) for r in cursor.fetchall()]

cursor.execute('''
    SELECT realized_pnl FROM futures_positions
    WHERE account_id = 2 AND status = "closed"
    AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
    AND notes LIKE "%止盈%"
''')
take_profits = [float(r['realized_pnl']) for r in cursor.fetchall()]

print('=' * 80)
print('24H TRADING SUMMARY')
print('=' * 80)
print(f'Total Orders: {s["total"]}')
print(f'Wins: {s["wins"]} | Losses: {s["total"]-s["wins"]}')
print(f'Win Rate: {s["wins"]/s["total"]*100:.1f}%')
print(f'Total PNL: ${float(s["total_pnl"]):.2f}')
print()
print('=' * 80)
print('STOP LOSS ANALYSIS')
print('=' * 80)
print(f'Stop Loss Orders: {len(stop_losses)}')
print(f'Total Loss from Stop Loss: ${sum(stop_losses):.2f}')
print(f'Average Loss per Stop: ${sum(stop_losses)/len(stop_losses):.2f}')
print()
print('=' * 80)
print('TAKE PROFIT ANALYSIS')
print('=' * 80)
print(f'Take Profit Orders: {len(take_profits)}')
print(f'Total Profit from TP: ${sum(take_profits):.2f}')
print(f'Average Profit per TP: ${sum(take_profits)/len(take_profits):.2f}')
print()
print('=' * 80)
print('PROFIT/LOSS RATIO')
print('=' * 80)
avg_loss = abs(sum(stop_losses) / len(stop_losses))
avg_profit = sum(take_profits) / len(take_profits)
ratio = avg_profit / avg_loss
required_wr = avg_loss/(avg_loss+avg_profit)*100
actual_wr = s["wins"]/s["total"]*100

print(f'Average Loss: ${avg_loss:.2f}')
print(f'Average Profit: ${avg_profit:.2f}')
print(f'Profit/Loss Ratio: {ratio:.2f}:1')
print(f'')
print(f'Required Win Rate (breakeven): {required_wr:.1f}%')
print(f'Actual Win Rate: {actual_wr:.1f}%')
print(f'')
if actual_wr < required_wr:
    print(f'!!! WARNING: Win rate TOO LOW !!!')
    print(f'Need {required_wr:.1f}% but only have {actual_wr:.1f}%')
    print(f'Gap: {required_wr - actual_wr:.1f}%')
else:
    print(f'OK: Win rate is sufficient')

cursor.close()
conn.close()
