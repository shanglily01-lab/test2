"""
深度分析策略失败原因
"""
import pymysql
from datetime import datetime
from collections import defaultdict

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

# 1. 按交易对统计表现
print("=" * 100)
print("1. 各交易对表现统计（最近7天）")
print("=" * 100)

cursor.execute("""
    SELECT
        symbol,
        COUNT(*) as trade_count,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as win_count,
        SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) as loss_count,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl,
        MAX(realized_pnl) as max_win,
        MIN(realized_pnl) as max_loss
    FROM futures_positions
    WHERE status = 'closed'
    AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
    GROUP BY symbol
    ORDER BY total_pnl ASC
""")

symbol_stats = cursor.fetchall()

print(f"{'交易对':<15} {'总笔数':<8} {'胜':<6} {'败':<6} {'胜率%':<8} {'总盈亏$':<12} {'平均$':<10} {'最大赢$':<10} {'最大亏$':<10}")
print("-" * 100)

for s in symbol_stats:
    symbol = s['symbol']
    count = s['trade_count']
    wins = s['win_count']
    losses = s['loss_count']
    win_rate = wins/count*100 if count > 0 else 0
    total = float(s['total_pnl'])
    avg = float(s['avg_pnl'])
    max_w = float(s['max_win']) if s['max_win'] else 0
    max_l = float(s['max_loss']) if s['max_loss'] else 0

    print(f"{symbol:<15} {count:<8} {wins:<6} {losses:<6} {win_rate:>6.1f}% {total:>11.2f} {avg:>9.2f} {max_w:>9.2f} {max_l:>9.2f}")

# 2. 按多空方向统计
print("\n" + "=" * 100)
print("2. 多空方向表现对比")
print("=" * 100)

cursor.execute("""
    SELECT
        position_side,
        COUNT(*) as trade_count,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as win_count,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl
    FROM futures_positions
    WHERE status = 'closed'
    AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
    GROUP BY position_side
""")

direction_stats = cursor.fetchall()

for d in direction_stats:
    side = d['position_side']
    count = d['trade_count']
    wins = d['win_count']
    win_rate = wins/count*100 if count > 0 else 0
    total = float(d['total_pnl'])
    avg = float(d['avg_pnl'])

    print(f"{side:<10} 总笔数:{count:<4} 胜:{wins:<4} 胜率:{win_rate:>5.1f}% 总盈亏:${total:>9.2f} 平均:${avg:>7.2f}")

# 3. 按平仓原因统计
print("\n" + "=" * 100)
print("3. 平仓原因分析")
print("=" * 100)

cursor.execute("""
    SELECT
        symbol,
        position_side,
        realized_pnl,
        unrealized_pnl_pct,
        notes,
        close_time
    FROM futures_positions
    WHERE status = 'closed'
    AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
    ORDER BY close_time DESC
""")

all_positions = cursor.fetchall()

close_reasons = defaultdict(lambda: {'count': 0, 'total_pnl': 0, 'positions': []})

for p in all_positions:
    notes = p['notes'] or ''
    pnl = float(p['realized_pnl'])
    pnl_pct = float(p['unrealized_pnl_pct'] or 0)

    # 分类平仓原因
    reason = 'unknown'
    if pnl_pct <= -10:
        reason = 'hard_stop_loss'
    elif 'stop_loss' in notes:
        reason = 'stop_loss'
    elif 'trailing_take_profit' in notes:
        reason = 'trailing_take_profit'
    elif 'fixed_take_profit' in notes:
        reason = 'fixed_take_profit'
    elif 'trend_reversal' in notes:
        reason = 'trend_reversal'
    elif 'manual' in notes:
        reason = 'manual_close'

    close_reasons[reason]['count'] += 1
    close_reasons[reason]['total_pnl'] += pnl
    if len(close_reasons[reason]['positions']) < 3:  # 记录前3个案例
        close_reasons[reason]['positions'].append({
            'symbol': p['symbol'],
            'side': p['position_side'],
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'time': p['close_time']
        })

print(f"{'平仓原因':<25} {'次数':<8} {'总盈亏$':<12} {'平均$':<10}")
print("-" * 60)

for reason, data in sorted(close_reasons.items(), key=lambda x: x[1]['total_pnl']):
    count = data['count']
    total = data['total_pnl']
    avg = total / count if count > 0 else 0
    print(f"{reason:<25} {count:<8} {total:>11.2f} {avg:>9.2f}")

    # 显示案例
    for pos in data['positions']:
        print(f"  └─ {pos['symbol']:<12} {pos['side']:<5} {pos['pnl_pct']:>6.2f}% ${pos['pnl']:>8.2f} @ {pos['time'].strftime('%m-%d %H:%M')}")

# 4. 查询策略配置
print("\n" + "=" * 100)
print("4. 当前启用的策略配置")
print("=" * 100)

cursor.execute("""
    SELECT
        strategy_name,
        symbols,
        stop_loss_pct,
        hard_stop_loss_pct,
        trailing_activate_pct,
        trailing_callback_pct,
        fixed_take_profit_pct,
        status
    FROM trading_strategies
    WHERE status = 'enabled'
    ORDER BY strategy_name
""")

strategies = cursor.fetchall()

for strat in strategies:
    print(f"\n策略名称: {strat['strategy_name']}")
    print(f"  交易对: {strat['symbols']}")
    print(f"  动态止损: {strat['stop_loss_pct']}%")
    print(f"  硬止损: {strat['hard_stop_loss_pct']}%")
    print(f"  移动止盈激活: {strat['trailing_activate_pct']}%")
    print(f"  移动止盈回调: {strat['trailing_callback_pct']}%")
    print(f"  固定止盈: {strat['fixed_take_profit_pct']}%")

# 5. 分析持仓时长
print("\n" + "=" * 100)
print("5. 持仓时长分析")
print("=" * 100)

cursor.execute("""
    SELECT
        symbol,
        position_side,
        realized_pnl,
        TIMESTAMPDIFF(MINUTE, open_time, close_time) as holding_minutes,
        notes
    FROM futures_positions
    WHERE status = 'closed'
    AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
    ORDER BY holding_minutes ASC
    LIMIT 20
""")

short_holds = cursor.fetchall()

print("\n最短持仓时间的20笔交易（可能是假信号）:")
print(f"{'交易对':<15} {'方向':<6} {'持仓分钟':<10} {'盈亏$':<10} {'原因':<35}")
print("-" * 100)

for p in short_holds:
    symbol = p['symbol']
    side = p['position_side']
    minutes = p['holding_minutes'] or 0
    pnl = float(p['realized_pnl'])
    notes = (p['notes'] or '')[:33]

    print(f"{symbol:<15} {side:<6} {minutes:<10} ${pnl:>8.2f} {notes:<35}")

cursor.close()
db.close()

print("\n" + "=" * 100)
print("分析完成")
print("=" * 100)
