#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析昨晚到现在的订单盈亏（参考操作说明.ini）"""
import pymysql
import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from dotenv import load_dotenv

# 设置Windows控制台编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 加载.env配置
load_dotenv()

# 连接数据库
conn = pymysql.connect(
    host=os.getenv('DB_HOST', '13.212.252.171'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER', 'app_user'),
    password=os.getenv('DB_PASSWORD', 'AppUser@2024#Secure'),
    database=os.getenv('DB_NAME', 'crypto_analyzer'),
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

cursor = conn.cursor()

# 时间范围：最近24小时
now = datetime.now()
start_time = now - timedelta(hours=24)

print("=" * 100)
print(f"交易盈亏分析报告")
print(f"时间范围: {start_time.strftime('%Y-%m-%d %H:%M:%S')} 至 {now.strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 100)
print()

# 查询各个交易系统（只看合约，不看现货）
systems = [
    # {
    #     'name': '🟢 纸面现货交易',
    #     'table': 'paper_trading_trades',
    #     'account_id': 1
    # },
    {
        'name': '🔵 U本位合约实盘',
        'table': 'futures_positions',
        'account_id': 2
    },
    {
        'name': '🟡 币本位合约实盘',
        'table': 'coin_futures_positions',
        'account_id': 2
    }
]

total_pnl = Decimal('0')
total_trades = 0
total_wins = 0
total_losses = 0

for system in systems:
    try:
        # 检查表是否存在
        cursor.execute("SHOW TABLES LIKE %s", (system['table'],))
        if not cursor.fetchone():
            continue

        # 检查表结构
        cursor.execute(f"DESCRIBE {system['table']}")
        columns = [row['Field'] for row in cursor.fetchall()]
        has_source = 'source' in columns
        has_unrealized_pnl_pct = 'unrealized_pnl_pct' in columns

        # 构建查询
        account_filter = f"account_id = {system['account_id']}" if system['account_id'] else "1=1"

        # 基础字段（positions表使用不同的字段名）
        select_fields = """
                symbol,
                position_side,
                quantity,
                entry_price,
                realized_pnl,
                margin,
                close_time"""

        # 可选字段
        if has_source:
            select_fields += ",\n                source"

        # positions表使用status='closed'来筛选已平仓记录
        query = f"""
            SELECT
{select_fields}
            FROM {system['table']}
            WHERE {account_filter}
              AND close_time >= %s
              AND close_time <= %s
              AND status = 'closed'
              AND realized_pnl IS NOT NULL
            ORDER BY close_time DESC
        """

        cursor.execute(query, (start_time, now))
        trades = cursor.fetchall()

        if not trades:
            print(f"【{system['name']}】")
            print(f"  ✅ 无已平仓交易")
            print()
            continue

        # 统计
        system_pnl = sum([Decimal(str(t['realized_pnl'])) for t in trades])
        wins = len([t for t in trades if float(t['realized_pnl']) > 0])
        losses = len([t for t in trades if float(t['realized_pnl']) < 0])
        break_even = len(trades) - wins - losses
        win_rate = (wins / len(trades) * 100) if trades else 0

        # 计算平均盈亏
        avg_win = sum([Decimal(str(t['realized_pnl'])) for t in trades if float(t['realized_pnl']) > 0]) / wins if wins > 0 else 0
        avg_loss = sum([Decimal(str(t['realized_pnl'])) for t in trades if float(t['realized_pnl']) < 0]) / losses if losses > 0 else 0

        total_pnl += system_pnl
        total_trades += len(trades)
        total_wins += wins
        total_losses += losses

        pnl_emoji = '🟢' if system_pnl > 0 else '🔴' if system_pnl < 0 else '⚪'

        print(f"【{system['name']}】")
        print(f"  总交易: {len(trades)} 笔")
        print(f"  盈利: {wins} 笔 | 亏损: {losses} 笔 | 持平: {break_even} 笔")
        print(f"  胜率: {win_rate:.1f}%")
        print(f"  平均盈利: +{float(avg_win):.2f} USDT | 平均亏损: {float(avg_loss):.2f} USDT")
        print(f"  {pnl_emoji} 总盈亏: {float(system_pnl):+.2f} USDT")
        print()

        # 显示交易详情
        print(f"  📋 交易明细:")
        display_count = min(len(trades), 15)
        for i, trade in enumerate(trades[:display_count], 1):
            pnl = float(trade['realized_pnl'])
            # 计算盈亏百分比：realized_pnl / margin * 100
            margin = float(trade['margin']) if trade.get('margin') else 0
            pnl_pct = (pnl / margin * 100) if margin > 0 else 0
            emoji = '📈' if pnl > 0 else '📉' if pnl < 0 else '➡️'
            position_side = trade.get('position_side', 'UNKNOWN')
            side_emoji = '🟢' if position_side == 'LONG' else '🔴' if position_side == 'SHORT' else '⚪'
            source = trade.get('source', 'manual')
            source_map = {
                'manual': '手动',
                'signal': '信号',
                'smart_trader': '超脑',
                'smart_trader_batch': '超脑分批',
                'stop_loss': '止损',
                'take_profit': '止盈'
            }
            source_display = source_map.get(source, source[:8])
            time_str = trade['close_time'].strftime('%m-%d %H:%M')

            print(f"    {i:2d}. {emoji} {side_emoji} {trade['symbol']:12} "
                  f"{time_str} | {pnl:+9.2f} USDT ({pnl_pct:+6.2f}%) "
                  f"| {source_display:10}")

        if len(trades) > display_count:
            print(f"    ... 还有 {len(trades) - display_count} 笔交易未显示")

        print()

    except Exception as e:
        print(f"【{system['name']}】")
        print(f"  ❌ 查询失败: {e}")
        import traceback
        traceback.print_exc()
        print()

# 总计
if total_trades > 0:
    total_win_rate = (total_wins / total_trades * 100)
    pnl_color = '🟢' if total_pnl > 0 else '🔴' if total_pnl < 0 else '⚪'

    print("=" * 100)
    print("【📊 总计统计】")
    print(f"  总交易数: {total_trades} 笔")
    print(f"  盈利笔数: {total_wins} | 亏损笔数: {total_losses}")
    print(f"  总胜率: {total_win_rate:.1f}%")
    print(f"  {pnl_color} 净盈亏: {float(total_pnl):+.2f} USDT")

    # 评价
    if total_pnl > 100:
        print(f"  💯 表现优秀！净赚 {float(total_pnl):.2f} USDT")
    elif total_pnl > 0:
        print(f"  ✅ 盈利中，继续保持")
    elif total_pnl > -50:
        print(f"  ⚠️  小幅亏损，注意风控")
    else:
        print(f"  🚨 亏损较大，建议暂停交易复盘")

    print("=" * 100)
else:
    print("=" * 100)
    print("📭 昨晚到现在无已平仓交易记录")
    print("=" * 100)

cursor.close()
conn.close()
