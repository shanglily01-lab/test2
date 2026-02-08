#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析重构后的实际效果 vs 预期效果
对比文档中的预测和实际数据
"""

import pymysql
import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv

# 设置控制台编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

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

def analyze_refactoring_results():
    """分析重构后的实际效果"""
    conn = get_db_connection()
    cursor = conn.cursor()

    print("\n" + "="*100)
    print("重构效果分析报告")
    print("重构时间: 2026-02-07")
    print("分析时间: " + datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print("="*100 + "\n")

    # 获取重构后的数据 (2026-02-07之后)
    refactor_date = '2026-02-07 00:00:00'

    try:
        # 1. 基础统计
        cursor.execute("""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
                SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) as losing_trades,
                SUM(realized_pnl) as total_pnl,
                AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE NULL END) as avg_win,
                AVG(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE NULL END) as avg_loss,
                MAX(realized_pnl) as max_win,
                MIN(realized_pnl) as max_loss
            FROM futures_positions
            WHERE created_at >= %s
            AND status = 'closed'
        """, (refactor_date,))

        stats = cursor.fetchone()

        print("📊 基础统计数据")
        print("-" * 100)
        print(f"总交易次数: {stats['total_trades']}")
        print(f"盈利次数: {stats['winning_trades']}")
        print(f"亏损次数: {stats['losing_trades']}")

        if stats['total_trades'] > 0:
            win_rate = (stats['winning_trades'] / stats['total_trades']) * 100
            print(f"胜率: {win_rate:.2f}%")

        print(f"总盈亏: ${stats['total_pnl']:.2f}")
        print(f"平均盈利: ${stats['avg_win']:.2f}" if stats['avg_win'] else "平均盈利: N/A")
        print(f"平均亏损: ${stats['avg_loss']:.2f}" if stats['avg_loss'] else "平均亏损: N/A")
        print(f"最大盈利: ${stats['max_win']:.2f}" if stats['max_win'] else "最大盈利: N/A")
        print(f"最大亏损: ${stats['max_loss']:.2f}" if stats['max_loss'] else "最大亏损: N/A")

        if stats['avg_win'] and stats['avg_loss']:
            risk_reward = abs(stats['avg_win'] / stats['avg_loss'])
            print(f"盈亏比: {risk_reward:.2f}:1")

        print()

        # 2. 移动止盈效果分析
        cursor.execute("""
            SELECT
                symbol,
                position_side,
                entry_price,
                mark_price,
                realized_pnl,
                unrealized_pnl_pct,
                notes,
                created_at,
                close_time,
                TIMESTAMPDIFF(MINUTE, created_at, close_time) as holding_minutes
            FROM futures_positions
            WHERE created_at >= %s
            AND status = 'closed'
            AND notes LIKE CONCAT('%%', '移动止盈', '%%')
            ORDER BY unrealized_pnl_pct DESC
            LIMIT 20
        """, (refactor_date,))

        trailing_stops = cursor.fetchall()

        print("🎯 移动止盈效果 (Top 20)")
        print("-" * 100)
        print(f"{'交易对':<15} {'方向':<6} {'盈利率':<10} {'盈利额':<12} {'持仓时长':<12} 平仓原因")
        print("-" * 100)

        total_protected_profit = 0
        for pos in trailing_stops:
            holding_time = f"{pos['holding_minutes']}分钟" if pos['holding_minutes'] else "N/A"
            pnl_pct = float(pos['unrealized_pnl_pct']) if pos['unrealized_pnl_pct'] else 0.0
            print(f"{pos['symbol']:<15} {pos['position_side']:<6} "
                  f"{pnl_pct:>8.2f}% ${pos['realized_pnl']:>10.2f} "
                  f"{holding_time:<12} {pos['notes'][:50]}")
            total_protected_profit += float(pos['realized_pnl'])

        print("-" * 100)
        print(f"移动止盈保护总利润: ${total_protected_profit:.2f}")
        print(f"移动止盈触发次数: {len(trailing_stops)}")
        print()

        # 3. 快速止损效果分析
        cursor.execute("""
            SELECT
                symbol,
                position_side,
                realized_pnl,
                unrealized_pnl_pct,
                notes,
                TIMESTAMPDIFF(MINUTE, created_at, close_time) as holding_minutes
            FROM futures_positions
            WHERE created_at >= %s
            AND status = 'closed'
            AND notes LIKE CONCAT('%%', '快速止损', '%%')
            ORDER BY realized_pnl ASC
            LIMIT 20
        """, (refactor_date,))

        fast_stops = cursor.fetchall()

        print("⚡ 快速止损效果 (Top 20)")
        print("-" * 100)
        print(f"{'交易对':<15} {'方向':<6} {'亏损率':<10} {'亏损额':<12} {'持仓时长':<12} 止损原因")
        print("-" * 100)

        total_fast_stop_loss = 0
        for pos in fast_stops:
            holding_time = f"{pos['holding_minutes']}分钟" if pos['holding_minutes'] else "N/A"
            pnl_pct = float(pos['unrealized_pnl_pct']) if pos['unrealized_pnl_pct'] else 0.0
            print(f"{pos['symbol']:<15} {pos['position_side']:<6} "
                  f"{pnl_pct:>8.2f}% ${pos['realized_pnl']:>10.2f} "
                  f"{holding_time:<12} {pos['notes'][:50]}")
            total_fast_stop_loss += float(pos['realized_pnl'])

        print("-" * 100)
        print(f"快速止损总亏损: ${total_fast_stop_loss:.2f}")
        print(f"快速止损触发次数: {len(fast_stops)}")
        if len(fast_stops) > 0:
            avg_fast_stop_loss = total_fast_stop_loss / len(fast_stops)
            print(f"平均快速止损亏损: ${avg_fast_stop_loss:.2f}")
        print()

        # 4. 对比预期 vs 实际
        print("📈 预期 vs 实际对比")
        print("-" * 100)
        print(f"{'指标':<20} {'重构前':<15} {'预期':<15} {'实际':<15} {'达成'}")
        print("-" * 100)

        # 日交易次数
        days_since_refactor = (datetime.now() - datetime.strptime(refactor_date, '%Y-%m-%d %H:%M:%S')).days
        if days_since_refactor == 0:
            days_since_refactor = 1
        actual_daily_trades = stats['total_trades'] / days_since_refactor
        print(f"{'日交易次数':<20} {'300笔':<15} {'10-20笔':<15} {actual_daily_trades:<15.1f} "
              f"{'✅' if actual_daily_trades <= 30 else '❌'}")

        # 胜率
        if stats['total_trades'] > 0:
            actual_win_rate = (stats['winning_trades'] / stats['total_trades']) * 100
            print(f"{'胜率':<20} {'34%':<15} {'50-55%':<15} {actual_win_rate:<15.2f}% "
                  f"{'✅' if actual_win_rate >= 50 else '⚠️' if actual_win_rate >= 40 else '❌'}")

        # 平均盈利
        if stats['avg_win']:
            print(f"{'平均盈利':<20} {'$17':<15} {'$50+':<15} ${stats['avg_win']:<14.2f} "
                  f"{'✅' if stats['avg_win'] >= 50 else '⚠️' if stats['avg_win'] >= 30 else '❌'}")

        # 平均止损
        if stats['avg_loss']:
            print(f"{'平均止损':<20} {'-$31':<15} {'-$15':<15} ${stats['avg_loss']:<14.2f} "
                  f"{'✅' if abs(stats['avg_loss']) <= 15 else '⚠️' if abs(stats['avg_loss']) <= 25 else '❌'}")

        # 盈亏比
        if stats['avg_win'] and stats['avg_loss']:
            actual_rr = abs(stats['avg_win'] / stats['avg_loss'])
            print(f"{'盈亏比':<20} {'1:1.78':<15} {'2:1':<15} {actual_rr:<15.2f}:1 "
                  f"{'✅' if actual_rr >= 2 else '⚠️' if actual_rr >= 1.5 else '❌'}")

        print("-" * 100)
        print()

        # 5. 每日盈亏趋势
        cursor.execute("""
            SELECT
                DATE(created_at) as trade_date,
                COUNT(*) as trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(realized_pnl) as daily_pnl,
                AVG(realized_pnl) as avg_pnl
            FROM futures_positions
            WHERE created_at >= %s
            AND status = 'closed'
            GROUP BY DATE(created_at)
            ORDER BY trade_date DESC
            LIMIT 7
        """, (refactor_date,))

        daily_stats = cursor.fetchall()

        print("📅 每日盈亏趋势")
        print("-" * 100)
        print(f"{'日期':<15} {'交易次数':<10} {'胜率':<10} {'日盈亏':<15} 平均盈亏")
        print("-" * 100)

        for day in daily_stats:
            win_rate = (day['wins'] / day['trades'] * 100) if day['trades'] > 0 else 0
            status = "✅" if day['daily_pnl'] > 0 else "❌"
            print(f"{day['trade_date']} {day['trades']:<10} {win_rate:<9.1f}% "
                  f"${day['daily_pnl']:>13.2f} ${day['avg_pnl']:>10.2f} {status}")

        print("-" * 100)
        print()

        # 6. 总结
        print("🎯 重构效果总结")
        print("-" * 100)

        success_count = 0
        total_metrics = 5

        if actual_daily_trades <= 30:
            print("✅ 交易频率: 成功控制在合理范围")
            success_count += 1
        else:
            print("❌ 交易频率: 仍然偏高")

        if stats['total_trades'] > 0 and (stats['winning_trades'] / stats['total_trades']) >= 0.50:
            print("✅ 胜率: 达到或超过目标")
            success_count += 1
        elif stats['total_trades'] > 0 and (stats['winning_trades'] / stats['total_trades']) >= 0.40:
            print("⚠️ 胜率: 有所改善但未达目标")
        else:
            print("❌ 胜率: 仍需改进")

        if stats['avg_win'] and stats['avg_win'] >= 50:
            print("✅ 平均盈利: 达到或超过目标")
            success_count += 1
        elif stats['avg_win'] and stats['avg_win'] >= 30:
            print("⚠️ 平均盈利: 有所改善但未达目标")
        else:
            print("❌ 平均盈利: 仍需改进")

        if stats['avg_loss'] and abs(stats['avg_loss']) <= 15:
            print("✅ 平均止损: 成功控制在目标范围")
            success_count += 1
        elif stats['avg_loss'] and abs(stats['avg_loss']) <= 25:
            print("⚠️ 平均止损: 有所改善但未达目标")
        else:
            print("❌ 平均止损: 仍需改进")

        if stats['avg_win'] and stats['avg_loss'] and abs(stats['avg_win'] / stats['avg_loss']) >= 2:
            print("✅ 盈亏比: 达到或超过2:1目标")
            success_count += 1
        elif stats['avg_win'] and stats['avg_loss'] and abs(stats['avg_win'] / stats['avg_loss']) >= 1.5:
            print("⚠️ 盈亏比: 有所改善但未达目标")
        else:
            print("❌ 盈亏比: 仍需改进")

        print(f"\n达成度: {success_count}/{total_metrics} ({success_count/total_metrics*100:.1f}%)")
        print("="*100 + "\n")

    except Exception as e:
        print(f"❌ 分析失败: {e}")
        import traceback
        traceback.print_exc()

    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    analyze_refactoring_results()
