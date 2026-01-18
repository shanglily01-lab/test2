#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
监控限价单成交情况和反向操作执行
"""

import sys
import io
import pymysql
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def monitor_limit_orders():
    """监控限价单和反向操作"""

    db_config = {
        'host': '13.212.252.171',
        'port': 3306,
        'user': 'admin',
        'password': 'Tonny@1000',
        'database': 'binance-data',
        'charset': 'utf8mb4'
    }

    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)

    try:
        cursor = conn.cursor()

        print("=" * 80)
        print("限价单成交和反向操作监控")
        print("=" * 80)
        print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

        # 1. 配置检查
        cursor.execute("""
            SELECT config FROM trading_strategies WHERE enabled = 1
        """)

        import json
        config = json.loads(cursor.fetchone()['config'])

        print("📋 当前配置:")
        print(f"  限价单偏移: {config.get('longPrice')} / {config.get('shortPrice')}")
        print(f"  反向操作: {'启用' if config.get('contrarianEnabled') else '禁用'}")
        print(f"  反向偏移: {config.get('contrarianRisk', {}).get('limitOrderOffset', 'N/A')}%\n")

        # 2. 今天的交易统计
        cursor.execute("""
            SELECT
                entry_signal_type,
                COUNT(*) as count,
                SUM(CASE WHEN unrealized_pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                AVG(unrealized_pnl_pct) as avg_pnl,
                SUM(unrealized_pnl_pct) as total_pnl
            FROM futures_positions
            WHERE DATE(open_time) = CURDATE()
            GROUP BY entry_signal_type
        """)

        stats = cursor.fetchall()

        if stats:
            print("📊 今日交易统计:\n")
            for s in stats:
                win_rate = s['wins'] / s['count'] * 100 if s['count'] > 0 else 0
                print(f"{s['entry_signal_type']:25s} | {s['count']:2d}笔 | 胜率{win_rate:5.1f}% | "
                      f"总盈亏{s['total_pnl']:+7.2f}% | 平均{s['avg_pnl']:+6.2f}%")
        else:
            print("📊 今天还没有交易\n")

        # 3. PENDING限价单
        cursor.execute("""
            SELECT
                fo.symbol,
                fo.side,
                fo.created_at,
                TIMESTAMPDIFF(MINUTE, fo.created_at, NOW()) as pending_minutes
            FROM futures_orders fo
            WHERE fo.status = 'PENDING'
            ORDER BY fo.created_at DESC
            LIMIT 10
        """)

        pending = cursor.fetchall()

        print(f"\n🕐 当前PENDING限价单: {len(pending)}个\n")
        if pending:
            for p in pending:
                print(f"  {p['symbol']:12s} {p['side']:12s} | 等待{p['pending_minutes']:3d}分钟 | {p['created_at']}")

        # 4. 反向操作交易
        cursor.execute("""
            SELECT
                symbol,
                position_side,
                entry_reason,
                unrealized_pnl_pct,
                open_time,
                status
            FROM futures_positions
            WHERE DATE(open_time) = CURDATE()
            AND (entry_reason LIKE '%反向%' OR entry_reason LIKE '%contrarian%')
            ORDER BY open_time DESC
        """)

        contrarian_trades = cursor.fetchall()

        print(f"\n🔄 今日反向操作交易: {len(contrarian_trades)}笔\n")
        if contrarian_trades:
            for t in contrarian_trades:
                pnl = float(t['unrealized_pnl_pct'] or 0)
                pnl_icon = '💚' if pnl > 0 else '💔'
                status_icon = '🟢' if t['status'] == 'open' else '🔴'

                print(f"{status_icon} {t['symbol']:12s} {t['position_side']:5s} {pnl_icon} {pnl:+6.2f}%")
                print(f"   {t['entry_reason'][:70]}")
                print(f"   {t['open_time']}\n")

        # 5. 最近成交的限价单
        cursor.execute("""
            SELECT
                symbol,
                position_side,
                entry_signal_type,
                entry_reason,
                open_time,
                CASE
                    WHEN entry_reason LIKE '%反向%' OR entry_reason LIKE '%contrarian%' THEN '反向'
                    ELSE '常规'
                END as trade_type
            FROM futures_positions
            WHERE entry_signal_type = 'limit_order_trend'
            AND open_time >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
            ORDER BY open_time DESC
            LIMIT 10
        """)

        recent_fills = cursor.fetchall()

        print(f"\n✅ 最近1小时成交的限价单: {len(recent_fills)}笔\n")
        if recent_fills:
            for f in recent_fills:
                type_icon = '🔄' if f['trade_type'] == '反向' else '➡️'
                print(f"{type_icon} {f['symbol']:12s} {f['position_side']:5s} | {f['trade_type']} | {f['open_time']}")

        cursor.close()

    finally:
        conn.close()

if __name__ == "__main__":
    try:
        monitor_limit_orders()
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
