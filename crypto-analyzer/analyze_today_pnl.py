#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
今日交易盈亏分析脚本

使用方法:
    python analyze_today_pnl.py
"""

import pymysql
import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv

# 设置控制台编码为UTF-8
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 加载环境变量
load_dotenv()

def get_db_connection():
    """获取数据库连接"""
    return pymysql.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME'),
        cursorclass=pymysql.cursors.DictCursor
    )

def analyze_today_pnl():
    """分析今日盈亏"""

    conn = get_db_connection()
    cursor = conn.cursor()

    print("="*80)
    print(f"📊 今日交易盈亏分析报告")
    print(f"⏰ 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (本地时间)")
    print(f"🌍 数据库时间: UTC+0")
    print("="*80)
    print()

    # 1. 今日交易总览
    print("📈 【今日交易总览】")
    print("-"*80)

    cursor.execute("""
        SELECT
            COUNT(*) as total_trades,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
            SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) as losing_trades,
            ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate,
            ROUND(SUM(realized_pnl), 2) as total_pnl,
            ROUND(SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END), 2) as total_profit,
            ROUND(SUM(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE 0 END), 2) as total_loss,
            ROUND(AVG(realized_pnl), 2) as avg_pnl,
            ROUND(MAX(realized_pnl), 2) as max_profit,
            ROUND(MIN(realized_pnl), 2) as min_loss
        FROM futures_positions
        WHERE DATE(close_time) = CURDATE()
        AND status = 'closed'
    """)

    overview = cursor.fetchone()

    if overview and overview['total_trades'] > 0:
        print(f"总交易次数: {overview['total_trades']} 笔")
        print(f"盈利次数: {overview['winning_trades']} 笔")
        print(f"亏损次数: {overview['losing_trades']} 笔")
        print(f"胜率: {overview['win_rate']}%")
        print()
        print(f"💰 总盈亏: ${overview['total_pnl']:.2f}")
        print(f"✅ 总盈利: ${overview['total_profit']:.2f}")
        print(f"❌ 总亏损: ${overview['total_loss']:.2f}")
        print(f"📊 平均盈亏: ${overview['avg_pnl']:.2f}")
        print(f"🔝 最大盈利: ${overview['max_profit']:.2f}")
        print(f"🔻 最大亏损: ${overview['min_loss']:.2f}")
    else:
        print("⚠️ 今日暂无已平仓交易")

    print()
    print()

    # 2. 按方向分组统计
    print("🎯 【做多/做空分析】")
    print("-"*80)

    cursor.execute("""
        SELECT
            position_side,
            COUNT(*) as trades,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) as losses,
            ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate,
            ROUND(SUM(realized_pnl), 2) as pnl,
            ROUND(AVG(realized_pnl), 2) as avg_pnl
        FROM futures_positions
        WHERE DATE(close_time) = CURDATE()
        AND status = 'closed'
        GROUP BY position_side
    """)

    side_stats = cursor.fetchall()

    if side_stats:
        print(f"{'方向':<10} {'交易次数':<10} {'盈利':<10} {'亏损':<10} {'胜率':<12} {'总盈亏':<15} {'平均盈亏':<15}")
        print("-" * 90)
        for row in side_stats:
            print(f"{row['position_side']:<10} {row['trades']:<10} {row['wins']:<10} {row['losses']:<10} "
                  f"{row['win_rate']:<12.2f}% ${row['pnl']:<14.2f} ${row['avg_pnl']:<14.2f}")
    else:
        print("⚠️ 暂无数据")

    print()
    print()

    # 3. 交易对盈亏Top20
    print("🏆 【交易对盈亏排行榜 Top 20】")
    print("-"*80)

    cursor.execute("""
        SELECT
            symbol,
            COUNT(*) as trades,
            ROUND(SUM(realized_pnl), 2) as total_pnl,
            ROUND(AVG(realized_pnl), 2) as avg_pnl,
            ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate
        FROM futures_positions
        WHERE DATE(close_time) = CURDATE()
        AND status = 'closed'
        GROUP BY symbol
        ORDER BY total_pnl DESC
        LIMIT 20
    """)

    symbol_stats = cursor.fetchall()

    if symbol_stats:
        print(f"{'排名':<6} {'交易对':<15} {'次数':<8} {'胜率':<12} {'总盈亏':<15} {'平均盈亏':<15}")
        print("-" * 90)
        for idx, row in enumerate(symbol_stats, 1):
            pnl_emoji = "✅" if row['total_pnl'] > 0 else "❌"
            print(f"{idx:<6} {row['symbol']:<15} {row['trades']:<8} {row['win_rate']:<12.2f}% "
                  f"{pnl_emoji} ${row['total_pnl']:<13.2f} ${row['avg_pnl']:<13.2f}")
    else:
        print("⚠️ 暂无数据")

    print()
    print()

    # 4. 亏损交易对Top10
    print("⚠️ 【今日亏损最多的交易对 Top 10】")
    print("-"*80)

    cursor.execute("""
        SELECT
            symbol,
            COUNT(*) as trades,
            ROUND(SUM(realized_pnl), 2) as total_pnl,
            ROUND(AVG(realized_pnl), 2) as avg_pnl,
            ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate
        FROM futures_positions
        WHERE DATE(close_time) = CURDATE()
        AND status = 'closed'
        GROUP BY symbol
        HAVING total_pnl < 0
        ORDER BY total_pnl ASC
        LIMIT 10
    """)

    loss_symbols = cursor.fetchall()

    if loss_symbols:
        print(f"{'排名':<6} {'交易对':<15} {'次数':<8} {'胜率':<12} {'总亏损':<15} {'平均亏损':<15}")
        print("-" * 90)
        for idx, row in enumerate(loss_symbols, 1):
            print(f"{idx:<6} {row['symbol']:<15} {row['trades']:<8} {row['win_rate']:<12.2f}% "
                  f"❌ ${row['total_pnl']:<13.2f} ${row['avg_pnl']:<13.2f}")
    else:
        print("✅ 今日没有亏损的交易对！")

    print()
    print()

    # 5. 入场信号类型统计
    print("📋 【入场信号类型分析】")
    print("-"*80)

    cursor.execute("""
        SELECT
            entry_signal_type,
            COUNT(*) as count,
            ROUND(SUM(realized_pnl), 2) as total_pnl,
            ROUND(AVG(realized_pnl), 2) as avg_pnl,
            ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate
        FROM futures_positions
        WHERE DATE(close_time) = CURDATE()
        AND status = 'closed'
        AND entry_signal_type IS NOT NULL
        GROUP BY entry_signal_type
        ORDER BY count DESC
        LIMIT 15
    """)

    signal_types = cursor.fetchall()

    if signal_types:
        print(f"{'入场信号类型':<50} {'次数':<8} {'胜率':<12} {'总盈亏':<15} {'平均盈亏':<15}")
        print("-" * 110)
        for row in signal_types:
            pnl_emoji = "✅" if row['total_pnl'] > 0 else "❌"
            signal_type = row['entry_signal_type'][:48] if row['entry_signal_type'] else 'N/A'
            print(f"{signal_type:<50} {row['count']:<8} {row['win_rate']:<12.2f}% "
                  f"{pnl_emoji} ${row['total_pnl']:<13.2f} ${row['avg_pnl']:<13.2f}")
    else:
        print("⚠️ 暂无数据")

    print()
    print()

    # 6. 当前持仓情况
    print("💼 【当前持仓情况】")
    print("-"*80)

    cursor.execute("""
        SELECT
            symbol,
            position_side,
            ROUND(unrealized_pnl, 2) as unrealized_pnl,
            ROUND(unrealized_pnl_pct, 2) as unrealized_pnl_pct,
            TIMESTAMPDIFF(MINUTE, created_at, NOW()) as holding_minutes,
            entry_score
        FROM futures_positions
        WHERE status = 'open'
        ORDER BY unrealized_pnl DESC
    """)

    open_positions = cursor.fetchall()

    if open_positions:
        print(f"{'交易对':<15} {'方向':<8} {'未实现盈亏':<15} {'盈亏%':<10} {'持仓时长':<12} {'评分':<8}")
        print("-" * 80)
        for row in open_positions:
            pnl_emoji = "✅" if row['unrealized_pnl'] > 0 else "❌"
            hours = row['holding_minutes'] // 60
            minutes = row['holding_minutes'] % 60
            print(f"{row['symbol']:<15} {row['position_side']:<8} {pnl_emoji} ${row['unrealized_pnl']:<13.2f} "
                  f"{row['unrealized_pnl_pct']:<10.2f}% {hours}h{minutes}m{'':<7} {row['entry_score']:<8}")

        # 持仓统计
        total_unrealized = sum(row['unrealized_pnl'] for row in open_positions)
        print()
        print(f"总持仓数: {len(open_positions)} 个")
        print(f"未实现盈亏合计: ${total_unrealized:.2f}")
    else:
        print("✅ 当前无持仓")

    print()
    print("="*80)

    cursor.close()
    conn.close()

if __name__ == '__main__':
    try:
        analyze_today_pnl()
    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
