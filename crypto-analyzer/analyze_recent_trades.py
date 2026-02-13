#!/usr/bin/env python3
"""分析最近的交易盈亏"""
import pymysql
import yaml
from datetime import datetime, timedelta
from decimal import Decimal

# 读取配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config['database']['mysql']

# 连接数据库
conn = pymysql.connect(
    host=db_config['host'],
    user=db_config['user'],
    password=db_config['password'],
    database=db_config['database'],
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

cursor = conn.cursor()

# 计算时间范围：昨晚20:00到现在
now = datetime.now()
yesterday = now - timedelta(days=1)
start_time = yesterday.replace(hour=20, minute=0, second=0, microsecond=0)

print("=" * 100)
print(f"交易盈亏分析报告")
print(f"时间范围: {start_time.strftime('%Y-%m-%d %H:%M:%S')} 至 {now.strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 100)
print()

# 查询各个交易系统的数据
systems = [
    {
        'name': '纸面现货交易',
        'table': 'paper_trading_trades',
        'account_filter': 'account_id = 1',
        'time_field': 'trade_time'
    },
    {
        'name': 'U本位合约',
        'table': 'live_futures_trades',
        'account_filter': 'account_id = 2',
        'time_field': 'trade_time'
    }
]

total_pnl = Decimal('0')
total_trades = 0
total_wins = 0
total_losses = 0

for system in systems:
    try:
        # 查询已平仓的交易（有realized_pnl的记录）
        query = f"""
            SELECT
                symbol,
                side,
                quantity,
                price,
                realized_pnl,
                pnl_pct,
                {system['time_field']} as trade_time,
                order_source
            FROM {system['table']}
            WHERE {system['account_filter']}
              AND {system['time_field']} >= %s
              AND {system['time_field']} <= %s
              AND realized_pnl IS NOT NULL
              AND side = 'SELL'
            ORDER BY {system['time_field']} DESC
        """

        cursor.execute(query, (start_time, now))
        trades = cursor.fetchall()

        if not trades:
            print(f"【{system['name']}】")
            print(f"  无已平仓交易")
            print()
            continue

        # 统计
        system_pnl = sum([Decimal(str(t['realized_pnl'])) for t in trades if t['realized_pnl']])
        wins = len([t for t in trades if t['realized_pnl'] and float(t['realized_pnl']) > 0])
        losses = len([t for t in trades if t['realized_pnl'] and float(t['realized_pnl']) < 0])
        win_rate = (wins / len(trades) * 100) if trades else 0

        total_pnl += system_pnl
        total_trades += len(trades)
        total_wins += wins
        total_losses += losses

        print(f"【{system['name']}】")
        print(f"  总交易数: {len(trades)}")
        print(f"  盈利单数: {wins}")
        print(f"  亏损单数: {losses}")
        print(f"  胜率: {win_rate:.1f}%")
        print(f"  总盈亏: {float(system_pnl):+.2f} USDT")
        print()

        # 显示每笔交易详情
        if len(trades) <= 20:
            print(f"  交易详情:")
            for i, trade in enumerate(trades, 1):
                pnl = float(trade['realized_pnl']) if trade['realized_pnl'] else 0
                pnl_pct = float(trade['pnl_pct']) if trade['pnl_pct'] else 0
                emoji = '📈' if pnl > 0 else '📉' if pnl < 0 else '➡️'
                source = trade.get('order_source', 'manual')
                time_str = trade['trade_time'].strftime('%m-%d %H:%M')

                print(f"    {i:2d}. {emoji} {trade['symbol']:12} "
                      f"{time_str} | {pnl:+8.2f} USDT ({pnl_pct:+6.2f}%) "
                      f"| {source:12}")
        else:
            print(f"  (交易过多，仅显示前10笔)")
            for i, trade in enumerate(trades[:10], 1):
                pnl = float(trade['realized_pnl']) if trade['realized_pnl'] else 0
                pnl_pct = float(trade['pnl_pct']) if trade['pnl_pct'] else 0
                emoji = '📈' if pnl > 0 else '📉' if pnl < 0 else '➡️'
                source = trade.get('order_source', 'manual')
                time_str = trade['trade_time'].strftime('%m-%d %H:%M')

                print(f"    {i:2d}. {emoji} {trade['symbol']:12} "
                      f"{time_str} | {pnl:+8.2f} USDT ({pnl_pct:+6.2f}%) "
                      f"| {source:12}")

        print()

    except Exception as e:
        print(f"【{system['name']}】")
        print(f"  ❌ 查询失败: {e}")
        print()

# 总计
if total_trades > 0:
    print("=" * 100)
    print("【总计】")
    print(f"  总交易数: {total_trades}")
    print(f"  盈利单数: {total_wins}")
    print(f"  亏损单数: {total_losses}")
    print(f"  总胜率: {(total_wins/total_trades*100):.1f}%")

    pnl_color = '🟢' if total_pnl > 0 else '🔴' if total_pnl < 0 else '⚪'
    print(f"  {pnl_color} 总盈亏: {float(total_pnl):+.2f} USDT")
    print("=" * 100)
else:
    print("=" * 100)
    print("昨晚到现在无已平仓交易记录")
    print("=" * 100)

cursor.close()
conn.close()
