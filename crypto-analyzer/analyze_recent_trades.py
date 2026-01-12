"""
分析最近7天的交易表现
"""
import pymysql
from datetime import datetime

# 服务端的数据库配置（从 futures_database_schema.md）
db = pymysql.connect(
    host='13.212.252.171',
    port=3306,
    user='admin',
    password='Tonny@1000',
    database='binance-data',
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

cursor = db.cursor()

# 查询最近7天的平仓记录
cursor.execute("""
    SELECT
        symbol,
        position_side,
        entry_price,
        mark_price,
        realized_pnl,
        unrealized_pnl_pct,
        notes,
        close_time
    FROM futures_positions
    WHERE status = 'closed'
    AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
    ORDER BY close_time DESC
    LIMIT 50
""")

positions = cursor.fetchall()

print(f'最近7天平仓记录: {len(positions)}笔')
print()

# 统计数据
total_pnl = 0
win_count = 0
loss_count = 0
hard_stop_count = 0
trailing_tp_count = 0
fixed_tp_count = 0

win_pnl = 0
loss_pnl = 0

for p in positions:
    pnl = float(p['realized_pnl'] or 0)
    pnl_pct = float(p['unrealized_pnl_pct'] or 0)
    total_pnl += pnl

    if pnl > 0:
        win_count += 1
        win_pnl += pnl
    else:
        loss_count += 1
        loss_pnl += pnl

    # 统计平仓类型
    notes = p.get('notes', '') or ''
    if pnl_pct <= -10:
        hard_stop_count += 1
    elif 'trailing_take_profit' in notes:
        trailing_tp_count += 1
    elif 'fixed_take_profit' in notes:
        fixed_tp_count += 1

print('=' * 70)
print('整体表现:')
print(f'总盈亏: ${total_pnl:.2f}')
print(f'盈利笔数: {win_count} (总盈利: ${win_pnl:.2f})')
print(f'亏损笔数: {loss_count} (总亏损: ${loss_pnl:.2f})')
if (win_count+loss_count) > 0:
    print(f'胜率: {win_count/(win_count+loss_count)*100:.1f}%')
if win_count > 0 and loss_count > 0 and loss_pnl != 0:
    avg_win = win_pnl/win_count
    avg_loss = abs(loss_pnl/loss_count)
    print(f'盈亏比: {avg_win/avg_loss:.2f}')
    print(f'平均盈利: ${avg_win:.2f}')
    print(f'平均亏损: ${avg_loss:.2f}')
print()
print('平仓类型统计:')
print(f'硬止损 (亏损>=10%): {hard_stop_count}笔 ({hard_stop_count/(len(positions) if len(positions)>0 else 1)*100:.1f}%)')
print(f'移动止盈: {trailing_tp_count}笔')
print(f'固定止盈: {fixed_tp_count}笔')
print(f'其他: {len(positions) - hard_stop_count - trailing_tp_count - fixed_tp_count}笔')
print('=' * 70)
print()

# 显示最近20笔详情
print('最近20笔交易:')
print('-' * 125)
print(f"{'时间':<19} {'交易对':<12} {'方向':<5} {'入场':<10} {'出场':<10} {'盈亏%':<8} {'盈亏$':<10} {'原因':<35}")
print('-' * 125)

for p in positions[:20]:
    if p['close_time']:
        closed_time = p['close_time'].strftime('%Y-%m-%d %H:%M:%S')
    else:
        closed_time = 'N/A'
    symbol = p['symbol']
    side = p['position_side']
    entry = f"${float(p['entry_price']):.4f}"
    close_p = f"${float(p['mark_price'] or 0):.4f}"
    pnl_pct = float(p['unrealized_pnl_pct'] or 0)
    pnl = float(p['realized_pnl'] or 0)
    notes = (p['notes'] or '')[:33]

    print(f'{closed_time} {symbol:<12} {side:<5} {entry:<10} {close_p:<10} {pnl_pct:>6.2f}% ${pnl:>8.2f} {notes:<35}')

cursor.close()
db.close()
