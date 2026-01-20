"""
超级大脑交易订单综合分析工具
功能:
1. 分析指定时间范围内的交易表现
2. 对比LONG和SHORT策略效果
3. 按交易对、信号类型、开仓时间等维度深入分析
4. 识别表现差的交易对和信号类型
5. 生成超级大脑策略评分
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

def get_time_range(hours_ago=None):
    """获取分析时间范围

    Args:
        hours_ago: 如果指定,则分析最近N小时的数据
                  如果为None,则分析从昨晚8点到现在的数据

    Returns:
        (start_time, end_time, duration_hours)
    """
    end_time = datetime.now()

    if hours_ago:
        start_time = end_time - timedelta(hours=hours_ago)
    else:
        # 昨晚8点逻辑
        yesterday_8pm = end_time.replace(hour=20, minute=0, second=0, microsecond=0) - timedelta(days=1)
        if end_time.hour < 20:
            start_time = yesterday_8pm
        else:
            start_time = end_time.replace(hour=20, minute=0, second=0, microsecond=0)

    duration_hours = (end_time - start_time).total_seconds() / 3600
    return start_time, end_time, duration_hours

def print_section_header(title):
    """打印章节标题"""
    print("\n" + "=" * 120)
    print(title)
    print("=" * 120)

def analyze_overall_performance(cursor, start_time):
    """1. 总体表现分析"""
    print_section_header("1. 总体交易表现汇总")

    cursor.execute("""
        SELECT
            COUNT(*) as total_orders,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as profit_orders,
            SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as loss_orders,
            SUM(CASE WHEN realized_pnl = 0 THEN 1 ELSE 0 END) as breakeven_orders,
            SUM(realized_pnl) as total_pnl,
            SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END) as total_profit,
            SUM(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE 0 END) as total_loss,
            AVG(realized_pnl) as avg_pnl,
            MAX(realized_pnl) as max_profit,
            MIN(realized_pnl) as max_loss
        FROM futures_positions
        WHERE status = 'closed'
        AND close_time >= %s
    """, (start_time,))

    summary = cursor.fetchone()

    if summary and summary['total_orders'] > 0:
        print(f"\n总订单数: {summary['total_orders']}")
        print(f"  盈利订单: {summary['profit_orders']} ({summary['profit_orders']/summary['total_orders']*100:.1f}%)")
        print(f"  亏损订单: {summary['loss_orders']} ({summary['loss_orders']/summary['total_orders']*100:.1f}%)")
        print(f"  持平订单: {summary['breakeven_orders']}")
        print(f"\n总盈亏: ${summary['total_pnl']:.2f}")
        print(f"  总盈利: ${summary['total_profit']:.2f}")
        print(f"  总亏损: ${summary['total_loss']:.2f}")
        print(f"  平均盈亏: ${summary['avg_pnl']:.2f}")
        print(f"  最大单笔盈利: ${summary['max_profit']:.2f}")
        print(f"  最大单笔亏损: ${summary['max_loss']:.2f}")

        if summary['profit_orders'] > 0 and summary['loss_orders'] > 0:
            win_rate = summary['profit_orders'] / summary['total_orders'] * 100
            avg_win = summary['total_profit'] / summary['profit_orders']
            avg_loss = summary['total_loss'] / summary['loss_orders']
            profit_factor = abs(summary['total_profit'] / summary['total_loss']) if summary['total_loss'] != 0 else 0

            print(f"\n关键指标:")
            print(f"  胜率: {win_rate:.1f}%")
            print(f"  盈亏比: {abs(avg_win/avg_loss):.2f}:1" if avg_loss != 0 else "  盈亏比: N/A")
            print(f"  盈利因子: {profit_factor:.2f}")

        return summary
    else:
        print("\n⚠️  时间范围内没有已平仓订单")
        return None

def analyze_long_vs_short(cursor, start_time):
    """2. LONG vs SHORT对比分析"""
    print_section_header("2. LONG vs SHORT 策略对比")

    for side in ['LONG', 'SHORT']:
        cursor.execute("""
            SELECT
                COUNT(*) as total_orders,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as profit_orders,
                SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as loss_orders,
                SUM(realized_pnl) as total_pnl,
                AVG(realized_pnl) as avg_pnl,
                MAX(realized_pnl) as max_profit,
                MIN(realized_pnl) as max_loss,
                AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)) as avg_hold_minutes
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= %s
            AND position_side = %s
        """, (start_time, side))

        data = cursor.fetchone()

        if data and data['total_orders'] > 0:
            win_rate = (data['profit_orders'] / data['total_orders'] * 100) if data['total_orders'] > 0 else 0

            print(f"\n{side}策略:")
            print(f"  订单数: {data['total_orders']}")
            print(f"  胜率: {win_rate:.1f}% ({data['profit_orders']}/{data['total_orders']})")
            print(f"  总盈亏: ${data['total_pnl']:.2f}")
            print(f"  平均盈亏: ${data['avg_pnl']:.2f}")
            print(f"  最大盈利: ${data['max_profit']:.2f}")
            print(f"  最大亏损: ${data['max_loss']:.2f}")
            print(f"  平均持仓: {data['avg_hold_minutes']:.0f}分钟 ({data['avg_hold_minutes']/60:.1f}小时)")

def analyze_by_signal_type(cursor, start_time):
    """3. 按信号类型分析"""
    print_section_header("3. 各信号类型表现分析")

    cursor.execute("""
        SELECT
            entry_signal_type,
            position_side,
            COUNT(*) as order_count,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
            SUM(realized_pnl) as total_pnl,
            AVG(realized_pnl) as avg_pnl,
            AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)) as avg_hold_minutes
        FROM futures_positions
        WHERE status = 'closed'
        AND close_time >= %s
        GROUP BY entry_signal_type, position_side
        ORDER BY total_pnl ASC
    """, (start_time,))

    signal_stats = cursor.fetchall()

    if signal_stats:
        print(f"\n{'信号类型':<20} {'方向':<6} {'订单数':>8} {'胜/负':>10} {'胜率':>8} {'总盈亏':>12} {'平均盈亏':>10} {'平均持仓':>12}")
        print("-" * 120)

        for row in signal_stats:
            signal = row['entry_signal_type'] or 'unknown'
            side = row['position_side']
            count = row['order_count']
            wins = row['wins']
            losses = row['losses']
            win_rate = (wins / count * 100) if count > 0 else 0
            total_pnl = row['total_pnl']
            avg_pnl = row['avg_pnl']
            avg_minutes = row['avg_hold_minutes']

            pnl_str = f"+${total_pnl:.2f}" if total_pnl > 0 else f"${total_pnl:.2f}"

            print(f"{signal:<20} {side:<6} {count:>8} {wins:>4}/{losses:<4} {win_rate:>7.1f}% "
                  f"{pnl_str:>12} ${avg_pnl:>9.2f} {avg_minutes:>9.0f}分钟")

def analyze_by_symbol(cursor, start_time):
    """4. 按交易对分析"""
    print_section_header("4. 各交易对盈亏排名")

    cursor.execute("""
        SELECT
            symbol,
            COUNT(*) as order_count,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
            SUM(realized_pnl) as total_pnl,
            AVG(realized_pnl) as avg_pnl,
            MAX(realized_pnl) as max_profit,
            MIN(realized_pnl) as max_loss
        FROM futures_positions
        WHERE status = 'closed'
        AND close_time >= %s
        GROUP BY symbol
        ORDER BY total_pnl ASC
    """, (start_time,))

    symbol_stats = cursor.fetchall()

    if symbol_stats:
        print(f"\n{'交易对':<15} {'订单数':>8} {'胜/负':>10} {'胜率':>8} {'总盈亏':>12} {'平均':>10} {'最大盈利':>12} {'最大亏损':>12}")
        print("-" * 120)

        for row in symbol_stats:
            symbol = row['symbol']
            count = row['order_count']
            wins = row['wins']
            losses = row['losses']
            win_rate = (wins / count * 100) if count > 0 else 0
            total_pnl = row['total_pnl']
            avg_pnl = row['avg_pnl']
            max_profit = row['max_profit']
            max_loss = row['max_loss']

            pnl_str = f"+${total_pnl:.2f}" if total_pnl > 0 else f"${total_pnl:.2f}"

            print(f"{symbol:<15} {count:>8} {wins:>4}/{losses:<4} {win_rate:>7.1f}% "
                  f"{pnl_str:>12} ${avg_pnl:>9.2f} ${max_profit:>11.2f} ${max_loss:>11.2f}")

def analyze_by_hour(cursor, start_time):
    """5. 按小时分析"""
    print_section_header("5. 交易时段分析")

    cursor.execute("""
        SELECT
            HOUR(close_time) as hour,
            COUNT(*) as order_count,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(realized_pnl) as hourly_pnl,
            AVG(realized_pnl) as avg_pnl
        FROM futures_positions
        WHERE status = 'closed'
        AND close_time >= %s
        GROUP BY HOUR(close_time)
        ORDER BY hourly_pnl ASC
    """, (start_time,))

    hourly_stats = cursor.fetchall()

    if hourly_stats:
        print(f"\n{'小时':<10} {'订单数':>10} {'胜率':>10} {'总盈亏':>12} {'平均盈亏':>12}")
        print("-" * 60)

        for row in hourly_stats:
            hour = row['hour']
            count = row['order_count']
            wins = row['wins']
            win_rate = (wins / count * 100) if count > 0 else 0
            pnl = row['hourly_pnl']
            avg_pnl = row['avg_pnl']

            pnl_str = f"+${pnl:.2f}" if pnl > 0 else f"${pnl:.2f}"

            print(f"{hour:02d}:00{'':<4} {count:>10} {win_rate:>9.1f}% {pnl_str:>12} ${avg_pnl:>11.2f}")

def analyze_current_positions(cursor):
    """6. 当前持仓分析"""
    print_section_header("6. 当前持仓情况")

    cursor.execute("""
        SELECT
            id,
            symbol,
            position_side,
            quantity,
            entry_price,
            unrealized_pnl,
            entry_time,
            TIMESTAMPDIFF(MINUTE, entry_time, NOW()) as hold_minutes
        FROM futures_positions
        WHERE status = 'open'
        ORDER BY entry_time DESC
    """)

    open_positions = cursor.fetchall()

    if open_positions:
        total_unrealized = sum(pos['unrealized_pnl'] or 0 for pos in open_positions)

        print(f"\n当前持仓数: {len(open_positions)}")
        print(f"未实现盈亏总计: ${total_unrealized:.2f}")

        print(f"\n{'ID':<8} {'交易对':<12} {'方向':<6} {'数量':<10} {'开仓价':<10} {'未实现盈亏':<12} {'持仓时间':<15} {'开仓时间':<20}")
        print("-" * 120)

        for pos in open_positions:
            pos_id = pos['id']
            symbol = pos['symbol']
            side = pos['position_side']
            quantity = pos['quantity']
            entry_price = pos['entry_price']
            unrealized_pnl = pos['unrealized_pnl'] or 0
            hold_minutes = pos['hold_minutes']
            entry_time = pos['entry_time'].strftime('%Y-%m-%d %H:%M:%S')

            pnl_str = f"+${unrealized_pnl:.2f}" if unrealized_pnl > 0 else f"${unrealized_pnl:.2f}"
            hold_str = f"{hold_minutes}分钟" if hold_minutes < 60 else f"{hold_minutes/60:.1f}小时"

            print(f"{pos_id:<8} {symbol:<12} {side:<6} {quantity:<10.4f} ${entry_price:<9.2f} "
                  f"{pnl_str:<12} {hold_str:<15} {entry_time:<20}")
    else:
        print("\n✅ 当前无持仓")

def analyze_recent_orders(cursor, start_time, limit=20):
    """7. 最近订单详情"""
    print_section_header(f"7. 最近{limit}条已平仓订单明细")

    cursor.execute("""
        SELECT
            id,
            symbol,
            position_side,
            quantity,
            entry_price,
            mark_price,
            realized_pnl,
            entry_signal_type,
            entry_time,
            close_time,
            TIMESTAMPDIFF(MINUTE, entry_time, close_time) as hold_minutes
        FROM futures_positions
        WHERE status = 'closed'
        AND close_time >= %s
        ORDER BY close_time DESC
        LIMIT %s
    """, (start_time, limit))

    recent_orders = cursor.fetchall()

    if recent_orders:
        print(f"\n{'ID':<8} {'交易对':<12} {'方向':<6} {'数量':<10} {'开仓价':<10} {'平仓价':<10} {'盈亏':<12} {'持仓时间':<10} {'信号':<15} {'平仓时间':<20}")
        print("-" * 130)

        for order in recent_orders:
            order_id = order['id']
            symbol = order['symbol']
            side = order['position_side']
            quantity = order['quantity']
            entry_price = order['entry_price']
            close_price = order['mark_price'] or 0
            pnl = order['realized_pnl']
            signal = order['entry_signal_type'] or 'unknown'
            hold_minutes = order['hold_minutes']
            close_time = order['close_time'].strftime('%Y-%m-%d %H:%M:%S')

            pnl_str = f"+${pnl:.2f}" if pnl > 0 else f"${pnl:.2f}"
            hold_str = f"{hold_minutes}分钟" if hold_minutes < 60 else f"{hold_minutes/60:.1f}小时"

            print(f"{order_id:<8} {symbol:<12} {side:<6} {quantity:<10.4f} ${entry_price:<9.2f} ${close_price:<9.2f} "
                  f"{pnl_str:<12} {hold_str:<10} {signal:<15} {close_time:<20}")

def calculate_strategy_score(summary):
    """8. 超级大脑策略评分"""
    print_section_header("8. 超级大脑策略评分")

    if not summary or summary['total_orders'] == 0:
        print("\n⚠️  数据不足，无法评分")
        return

    win_rate = (summary['profit_orders'] / summary['total_orders'] * 100) if summary['total_orders'] > 0 else 0

    # 胜率评分 (0-40分)
    win_rate_score = min(win_rate / 50 * 40, 40)  # 50%胜率为满分

    # 盈利因子评分 (0-30分)
    profit_factor = abs(summary['total_profit'] / summary['total_loss']) if summary['total_loss'] != 0 else 0
    pf_score = min(profit_factor / 2 * 30, 30)  # 盈利因子2为满分

    # 总盈亏评分 (0-30分)
    pnl_score = min(max(summary['total_pnl'] / 100 * 30, 0), 30)  # 100 USDT为满分

    total_score = win_rate_score + pf_score + pnl_score

    print(f"\n评分维度:")
    print(f"  胜率评分: {win_rate_score:.1f}/40 (胜率 {win_rate:.1f}%)")
    print(f"  盈利因子评分: {pf_score:.1f}/30 (盈利因子 {profit_factor:.2f})")
    print(f"  盈亏评分: {pnl_score:.1f}/30 (总盈亏 ${summary['total_pnl']:.2f})")
    print(f"\n总评分: {total_score:.1f}/100")

    if total_score >= 80:
        grade = "A+ 优秀"
    elif total_score >= 70:
        grade = "A 良好"
    elif total_score >= 60:
        grade = "B 及格"
    elif total_score >= 50:
        grade = "C 一般"
    else:
        grade = "D 需要优化"

    print(f"策略等级: {grade}")

def main():
    """主函数"""
    print("=" * 120)
    print("超级大脑交易订单综合分析")
    print("=" * 120)

    # 获取分析时间范围
    # 可以修改这里来指定分析时间:
    # - get_time_range() - 从昨晚8点到现在
    # - get_time_range(24) - 最近24小时
    # - get_time_range(48) - 最近48小时
    start_time, end_time, duration = get_time_range()

    print(f"\n分析时间范围: {start_time.strftime('%Y-%m-%d %H:%M:%S')} ~ {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"持续时间: {duration:.1f} 小时")

    # 连接数据库
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    try:
        # 执行各项分析
        summary = analyze_overall_performance(cursor, start_time)

        if summary and summary['total_orders'] > 0:
            analyze_long_vs_short(cursor, start_time)
            analyze_by_signal_type(cursor, start_time)
            analyze_by_symbol(cursor, start_time)
            analyze_by_hour(cursor, start_time)
            analyze_current_positions(cursor)
            analyze_recent_orders(cursor, start_time, limit=20)
            calculate_strategy_score(summary)

    finally:
        cursor.close()
        conn.close()

    print("\n" + "=" * 120)
    print("分析完成")
    print("=" * 120)

if __name__ == '__main__':
    main()
