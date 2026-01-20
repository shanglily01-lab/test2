"""
深入分析做多信号失败的原因
"""
import pymysql
from datetime import datetime, timedelta
import sys

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data',
    'charset': 'utf8mb4'
}

conn = pymysql.connect(**db_config)
cursor = conn.cursor(pymysql.cursors.DictCursor)

print("=" * 120)
print("做多(LONG)信号失败原因分析")
print("=" * 120)

# 计算昨晚8点
now_local = datetime.now()
yesterday_8pm = now_local.replace(hour=20, minute=0, second=0, microsecond=0) - timedelta(days=1)
if now_local.hour < 20:
    yesterday_8pm = yesterday_8pm
else:
    yesterday_8pm = now_local.replace(hour=20, minute=0, second=0, microsecond=0)

# 1. 做多订单总体分析
print("\n1. 做多订单总体统计")
print("-" * 120)

cursor.execute("""
    SELECT
        COUNT(*) as total_orders,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as profit_orders,
        SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as loss_orders,
        SUM(CASE WHEN realized_pnl = 0 THEN 1 ELSE 0 END) as breakeven_orders,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl,
        MAX(realized_pnl) as max_profit,
        MIN(realized_pnl) as max_loss,
        AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)) as avg_hold_minutes
    FROM futures_positions
    WHERE status = 'closed'
    AND close_time >= %s
    AND position_side = 'LONG'
""", (yesterday_8pm,))

long_summary = cursor.fetchone()

print(f"\n总订单数: {long_summary['total_orders']}")
print(f"  盈利: {long_summary['profit_orders']} ({long_summary['profit_orders']/long_summary['total_orders']*100:.1f}%)")
print(f"  亏损: {long_summary['loss_orders']} ({long_summary['loss_orders']/long_summary['total_orders']*100:.1f}%)")
print(f"  持平: {long_summary['breakeven_orders']}")
print(f"\n总盈亏: ${long_summary['total_pnl']:.2f}")
print(f"平均盈亏: ${long_summary['avg_pnl']:.2f}")
print(f"最大盈利: ${long_summary['max_profit']:.2f}")
print(f"最大亏损: ${long_summary['max_loss']:.2f}")
print(f"平均持仓时间: {long_summary['avg_hold_minutes']:.1f}分钟 ({long_summary['avg_hold_minutes']/60:.1f}小时)")

# 2. 做空订单对比
print("\n2. 做空订单对比")
print("-" * 120)

cursor.execute("""
    SELECT
        COUNT(*) as total_orders,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as profit_orders,
        SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as loss_orders,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl,
        AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)) as avg_hold_minutes
    FROM futures_positions
    WHERE status = 'closed'
    AND close_time >= %s
    AND position_side = 'SHORT'
""", (yesterday_8pm,))

short_summary = cursor.fetchone()

print(f"\n总订单数: {short_summary['total_orders']}")
print(f"  盈利: {short_summary['profit_orders']} ({short_summary['profit_orders']/short_summary['total_orders']*100:.1f}%)")
print(f"  亏损: {short_summary['loss_orders']} ({short_summary['loss_orders']/short_summary['total_orders']*100:.1f}%)")
print(f"\n总盈亏: ${short_summary['total_pnl']:.2f}")
print(f"平均盈亏: ${short_summary['avg_pnl']:.2f}")
print(f"平均持仓时间: {short_summary['avg_hold_minutes']:.1f}分钟 ({short_summary['avg_hold_minutes']/60:.1f}小时)")

# 3. 按信号类型分析LONG的失败原因
print("\n3. 各信号类型的LONG表现")
print("-" * 120)

cursor.execute("""
    SELECT
        entry_signal_type,
        COUNT(*) as count,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl,
        AVG((entry_price - mark_price) / entry_price * 100) as avg_price_change_pct,
        AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)) as avg_hold_minutes
    FROM futures_positions
    WHERE status = 'closed'
    AND close_time >= %s
    AND position_side = 'LONG'
    GROUP BY entry_signal_type
    ORDER BY total_pnl ASC
""", (yesterday_8pm,))

long_signals = cursor.fetchall()

print(f"\n{'信号类型':<20} {'订单数':>8} {'胜/负':>10} {'胜率':>8} {'总盈亏':>12} {'平均盈亏':>10} {'平均涨幅':>10} {'持仓时长':>10}")
print("-" * 120)

for row in long_signals:
    signal = row['entry_signal_type'] or 'unknown'
    count = row['count']
    wins = row['wins']
    losses = row['losses']
    win_rate = (wins / count * 100) if count > 0 else 0
    total_pnl = row['total_pnl']
    avg_pnl = row['avg_pnl']
    avg_change = row['avg_price_change_pct']
    avg_minutes = row['avg_hold_minutes']

    pnl_str = f"+${total_pnl:.2f}" if total_pnl > 0 else f"${total_pnl:.2f}"
    change_str = f"{avg_change:+.2f}%" if avg_change else "N/A"

    print(f"{signal:<20} {count:>8} {wins:>4}/{losses:<4} {win_rate:>7.1f}% {pnl_str:>12} ${avg_pnl:>9.2f} {change_str:>10} {avg_minutes:>7.0f}分钟")

# 4. 最差的LONG订单详细分析
print("\n4. 最差的20笔LONG订单详细分析")
print("-" * 120)

cursor.execute("""
    SELECT
        id,
        symbol,
        entry_signal_type,
        entry_price,
        mark_price,
        realized_pnl,
        open_time,
        close_time,
        TIMESTAMPDIFF(MINUTE, open_time, close_time) as hold_minutes,
        (mark_price - entry_price) / entry_price * 100 as price_change_pct
    FROM futures_positions
    WHERE status = 'closed'
    AND close_time >= %s
    AND position_side = 'LONG'
    ORDER BY realized_pnl ASC
    LIMIT 20
""", (yesterday_8pm,))

worst_longs = cursor.fetchall()

print(f"\n{'ID':<8} {'交易对':<12} {'信号':<18} {'开仓价':<12} {'平仓价':<12} {'涨幅':>8} {'盈亏':>12} {'持仓时长':>12}")
print("-" * 120)

for order in worst_longs:
    print(f"{order['id']:<8} {order['symbol']:<12} {order['entry_signal_type'] or 'N/A':<18} "
          f"${order['entry_price']:<11.2f} ${order['mark_price'] or 0:<11.2f} "
          f"{order['price_change_pct']:>7.2f}% ${order['realized_pnl']:>11.2f} {order['hold_minutes']:>9.0f}分钟")

# 5. 止损分析
print("\n5. LONG订单止损分析")
print("-" * 120)

cursor.execute("""
    SELECT
        entry_signal_type,
        COUNT(*) as total,
        SUM(CASE WHEN (mark_price - entry_price) / entry_price * 100 < -2 THEN 1 ELSE 0 END) as stopped_out,
        AVG(CASE WHEN realized_pnl < 0 THEN (mark_price - entry_price) / entry_price * 100 ELSE NULL END) as avg_loss_pct
    FROM futures_positions
    WHERE status = 'closed'
    AND close_time >= %s
    AND position_side = 'LONG'
    AND realized_pnl < 0
    GROUP BY entry_signal_type
    ORDER BY stopped_out DESC
""", (yesterday_8pm,))

stop_loss_analysis = cursor.fetchall()

print(f"\n{'信号类型':<20} {'亏损订单数':>12} {'触发止损数':>12} {'止损比例':>12} {'平均亏损幅度':>15}")
print("-" * 120)

for row in stop_loss_analysis:
    signal = row['entry_signal_type'] or 'unknown'
    total = row['total']
    stopped = row['stopped_out']
    stop_rate = (stopped / total * 100) if total > 0 else 0
    avg_loss_pct = row['avg_loss_pct']

    print(f"{signal:<20} {total:>12} {stopped:>12} {stop_rate:>11.1f}% {avg_loss_pct:>14.2f}%")

# 6. 开仓时机分析
print("\n6. LONG订单开仓时间分布")
print("-" * 120)

cursor.execute("""
    SELECT
        HOUR(open_time) as hour,
        COUNT(*) as count,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl
    FROM futures_positions
    WHERE status = 'closed'
    AND close_time >= %s
    AND position_side = 'LONG'
    GROUP BY HOUR(open_time)
    ORDER BY total_pnl ASC
""", (yesterday_8pm,))

hourly_analysis = cursor.fetchall()

print(f"\n{'开仓小时':<12} {'订单数':>10} {'胜率':>10} {'总盈亏':>12} {'平均盈亏':>12}")
print("-" * 120)

for row in hourly_analysis:
    hour = row['hour']
    count = row['count']
    wins = row['wins']
    win_rate = (wins / count * 100) if count > 0 else 0
    total_pnl = row['total_pnl']
    avg_pnl = row['avg_pnl']

    pnl_str = f"+${total_pnl:.2f}" if total_pnl > 0 else f"${total_pnl:.2f}"

    print(f"{hour:02d}:00{'':<6} {count:>10} {win_rate:>9.1f}% {pnl_str:>12} ${avg_pnl:>11.2f}")

# 7. 交易对分析
print("\n7. LONG订单各交易对表现")
print("-" * 120)

cursor.execute("""
    SELECT
        symbol,
        COUNT(*) as count,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl,
        AVG((mark_price - entry_price) / entry_price * 100) as avg_price_change
    FROM futures_positions
    WHERE status = 'closed'
    AND close_time >= %s
    AND position_side = 'LONG'
    GROUP BY symbol
    HAVING count >= 2
    ORDER BY total_pnl ASC
    LIMIT 15
""", (yesterday_8pm,))

symbol_analysis = cursor.fetchall()

print(f"\n{'交易对':<15} {'订单数':>10} {'胜率':>10} {'总盈亏':>12} {'平均盈亏':>12} {'平均涨幅':>12}")
print("-" * 120)

for row in symbol_analysis:
    symbol = row['symbol']
    count = row['count']
    wins = row['wins']
    win_rate = (wins / count * 100) if count > 0 else 0
    total_pnl = row['total_pnl']
    avg_pnl = row['avg_pnl']
    avg_change = row['avg_price_change']

    pnl_str = f"+${total_pnl:.2f}" if total_pnl > 0 else f"${total_pnl:.2f}"

    print(f"{symbol:<15} {count:>10} {win_rate:>9.1f}% {pnl_str:>12} ${avg_pnl:>11.2f} {avg_change:>11.2f}%")

cursor.close()
conn.close()

print("\n" + "=" * 120)
print("分析完成")
print("=" * 120)
