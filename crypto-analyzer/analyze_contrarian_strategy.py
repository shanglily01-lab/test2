#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析反向操作策略的有效性：如果我们反向操作V3信号会怎样？
"""

import sys
import io
import pymysql
from datetime import datetime, timedelta
from decimal import Decimal

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def analyze_contrarian_strategy():
    """分析如果反向操作今天的交易结果会如何"""

    # 连接数据库（服务器端）
    conn = pymysql.connect(
        host='13.212.252.171',
        user='admin',
        password='Tonny@1000',
        database='binance-data',
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

    try:
        cursor = conn.cursor()

        # 查询今天所有已平仓的交易
        cursor.execute("""
            SELECT
                fp.id,
                fp.symbol,
                fp.position_side,
                fp.entry_price,
                fp.entry_signal_type,
                fp.realized_pnl as pnl,
                fp.unrealized_pnl_pct as pnl_pct,
                fp.notes as close_reason,
                fp.open_time,
                fp.close_time,
                TIMESTAMPDIFF(MINUTE, fp.open_time, fp.close_time) as duration_minutes
            FROM futures_positions fp
            WHERE DATE(fp.open_time) = CURDATE()
            AND fp.status = 'closed'
            ORDER BY fp.open_time
        """)

        positions = cursor.fetchall()

        print("=" * 100)
        print("反向操作策略分析 - 2026-01-17")
        print("=" * 100)
        print(f"\n今天共 {len(positions)} 笔已平仓交易\n")

        # 统计原始交易结果
        original_pnl = 0
        original_wins = 0
        original_losses = 0

        # 统计反向操作结果
        contrarian_pnl = 0
        contrarian_wins = 0
        contrarian_losses = 0

        print("\n【详细对比分析】")
        print("-" * 100)

        for i, pos in enumerate(positions, 1):
            pnl = float(pos['pnl'] or 0)
            pnl_pct = float(pos['pnl_pct'] or 0)

            # 原始交易统计
            original_pnl += pnl
            if pnl > 0:
                original_wins += 1
            else:
                original_losses += 1

            # 反向操作：做多变做空，做空变做多
            # 盈亏正负号反转
            contrarian_pnl_this = -pnl
            contrarian_pnl_pct_this = -pnl_pct

            contrarian_pnl += contrarian_pnl_this
            if contrarian_pnl_this > 0:
                contrarian_wins += 1
            else:
                contrarian_losses += 1

            # 计算改善程度
            improvement = contrarian_pnl_this - pnl
            improvement_pct = (improvement / abs(pnl) * 100) if pnl != 0 else 0

            # 标记
            if improvement > 0:
                icon = "✅"
            elif improvement < 0:
                icon = "❌"
            else:
                icon = "➖"

            print(f"\n{icon} #{i} {pos['symbol']} 原始:{pos['position_side']}")
            print(f"   开仓: {pos['open_time']}")
            print(f"   平仓: {pos['close_time']} (持仓{pos['duration_minutes']}分钟)")
            print(f"   信号类型: {pos['entry_signal_type']}")
            print(f"   平仓原因: {pos['close_reason']}")
            print(f"   原始方向: {pos['position_side']} → 盈亏: ${pnl:+.2f} ({pnl_pct:+.2f}%)")

            # 反向操作
            reverse_side = "SHORT" if pos['position_side'] == "LONG" else "LONG"
            print(f"   反向方向: {reverse_side} → 盈亏: ${contrarian_pnl_this:+.2f} ({contrarian_pnl_pct_this:+.2f}%)")
            print(f"   改善幅度: ${improvement:+.2f} ({improvement_pct:+.1f}%)")

        print("\n" + "=" * 100)
        print("【汇总对比】")
        print("=" * 100)

        print(f"\n📊 原始策略表现:")
        print(f"   总盈亏: ${original_pnl:+.2f}")
        print(f"   胜率: {original_wins}/{len(positions)} = {original_wins/len(positions)*100:.1f}%")
        print(f"   平均盈利: ${original_pnl/len(positions):+.2f}/笔")

        print(f"\n🔄 反向操作表现:")
        print(f"   总盈亏: ${contrarian_pnl:+.2f}")
        print(f"   胜率: {contrarian_wins}/{len(positions)} = {contrarian_wins/len(positions)*100:.1f}%")
        print(f"   平均盈利: ${contrarian_pnl/len(positions):+.2f}/笔")

        print(f"\n💡 改善效果:")
        improvement_total = contrarian_pnl - original_pnl
        improvement_pct = ((contrarian_pnl - original_pnl) / abs(original_pnl) * 100) if original_pnl != 0 else 0
        win_rate_change = (contrarian_wins - original_wins) / len(positions) * 100

        print(f"   盈亏改善: ${improvement_total:+.2f} ({improvement_pct:+.1f}%)")
        print(f"   胜率变化: {win_rate_change:+.1f}%")

        if contrarian_pnl > 0 and original_pnl < 0:
            print(f"\n⭐ 关键发现: 反向操作将亏损转为盈利！")
            print(f"   从亏损${abs(original_pnl):.2f} → 盈利${contrarian_pnl:.2f}")
        elif contrarian_pnl > original_pnl:
            print(f"\n✅ 反向操作表现更好")
        else:
            print(f"\n❌ 反向操作表现更差")

        # 按策略类型分析
        print("\n" + "=" * 100)
        print("【按策略类型分析】")
        print("=" * 100)

        cursor.execute("""
            SELECT
                fp.entry_signal_type,
                COUNT(*) as count,
                SUM(fp.realized_pnl) as total_pnl,
                AVG(fp.unrealized_pnl_pct) as avg_pnl_pct
            FROM futures_positions fp
            WHERE DATE(fp.open_time) = CURDATE()
            AND fp.status = 'closed'
            GROUP BY fp.entry_signal_type
            ORDER BY total_pnl
        """)

        strategy_results = cursor.fetchall()

        for strategy in strategy_results:
            strategy_type = strategy['entry_signal_type']
            count = strategy['count']
            total_pnl = float(strategy['total_pnl'] or 0)
            avg_pnl_pct = float(strategy['avg_pnl_pct'] or 0)

            contrarian_total_pnl = -total_pnl
            contrarian_avg_pnl_pct = -avg_pnl_pct

            print(f"\n策略: {strategy_type}")
            print(f"   交易数: {count}")
            print(f"   原始: 总盈亏=${total_pnl:+.2f}, 平均{avg_pnl_pct:+.2f}%")
            print(f"   反向: 总盈亏=${contrarian_total_pnl:+.2f}, 平均{contrarian_avg_pnl_pct:+.2f}%")

            if contrarian_total_pnl > total_pnl:
                improvement = contrarian_total_pnl - total_pnl
                print(f"   ✅ 反向操作改善 ${improvement:+.2f}")
            else:
                print(f"   ❌ 反向操作更差")

        cursor.close()

    finally:
        conn.close()

if __name__ == "__main__":
    print("开始分析反向操作策略...")
    print()
    analyze_contrarian_strategy()
    print("\n✅ 分析完成！")
