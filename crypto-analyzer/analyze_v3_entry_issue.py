#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析V3策略入场问题：为什么entry_ema_diff为0导致no_entry_data
"""

import sys
import io
import pymysql
from datetime import datetime, timedelta
from decimal import Decimal

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def analyze_v3_entry_issue():
    """分析V3策略的入场数据问题"""

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

        # 查询最近24小时的V3持仓（所有状态）
        cursor.execute("""
            SELECT
                fp.id,
                fp.symbol,
                fp.position_side,
                fp.entry_price,
                fp.entry_signal_type,
                fp.entry_reason,
                fp.entry_ema_diff,
                fp.notes as close_reason,
                fp.realized_pnl as pnl,
                fp.unrealized_pnl_pct as pnl_pct,
                fp.open_time,
                fp.close_time,
                fp.status
            FROM futures_positions fp
            WHERE fp.entry_signal_type = 'sustained_trend_entry'
            AND fp.open_time >= NOW() - INTERVAL 24 HOUR
            ORDER BY fp.open_time DESC
            LIMIT 30
        """)

        positions = cursor.fetchall()

        print("=" * 100)
        print("V3策略持仓分析 - 最近24小时")
        print("=" * 100)
        print(f"\n总共找到 {len(positions)} 个V3持仓\n")

        # 分类统计
        no_entry_data_count = 0
        has_entry_data_count = 0
        zero_entry_diff_count = 0

        print("\n【详细持仓数据】")
        print("-" * 100)

        for i, pos in enumerate(positions, 1):
            entry_ema_diff = pos['entry_ema_diff']
            close_reason = pos['close_reason'] or 'OPEN'
            pnl_pct = float(pos['pnl_pct'] or 0)

            # 检查是否包含no_entry_data
            has_no_entry_data = 'no_entry_data' in close_reason if close_reason else False

            if has_no_entry_data:
                no_entry_data_count += 1
                status_icon = "⚠️"
            elif entry_ema_diff is None or float(entry_ema_diff) == 0:
                zero_entry_diff_count += 1
                status_icon = "❌"
            else:
                has_entry_data_count += 1
                status_icon = "✅"

            print(f"\n{status_icon} #{i} {pos['symbol']} {pos['position_side']}")
            print(f"   持仓ID: {pos['id']}")
            print(f"   开仓时间: {pos['open_time']}")
            print(f"   入场信号: {pos['entry_signal_type']}")
            print(f"   入场原因: {pos['entry_reason']}")
            print(f"   entry_ema_diff: {entry_ema_diff} {'⚠️ NULL或0' if not entry_ema_diff or float(entry_ema_diff) == 0 else '✅'}")
            print(f"   平仓原因: {close_reason}")
            print(f"   盈亏: {pnl_pct:+.2f}%")
            print(f"   状态: {pos['status']}")

        print("\n" + "=" * 100)
        print("【统计汇总】")
        print("=" * 100)
        print(f"✅ 有entry_ema_diff数据的持仓: {has_entry_data_count} ({has_entry_data_count/len(positions)*100:.1f}%)")
        print(f"❌ entry_ema_diff为NULL或0: {zero_entry_diff_count} ({zero_entry_diff_count/len(positions)*100:.1f}%)")
        print(f"⚠️ 平仓原因包含no_entry_data: {no_entry_data_count} ({no_entry_data_count/len(positions)*100:.1f}%)")

        # 分析entry_ema_diff的分布
        print("\n" + "=" * 100)
        print("【entry_ema_diff 分布分析】")
        print("=" * 100)

        valid_entry_diffs = []
        for pos in positions:
            if pos['entry_ema_diff'] and float(pos['entry_ema_diff']) > 0:
                valid_entry_diffs.append(float(pos['entry_ema_diff']))

        if valid_entry_diffs:
            print(f"\n有效entry_ema_diff统计（共{len(valid_entry_diffs)}个）：")
            print(f"  最小值: {min(valid_entry_diffs):.4f}%")
            print(f"  最大值: {max(valid_entry_diffs):.4f}%")
            print(f"  平均值: {sum(valid_entry_diffs)/len(valid_entry_diffs):.4f}%")
            print(f"  中位数: {sorted(valid_entry_diffs)[len(valid_entry_diffs)//2]:.4f}%")

            # 检查是否符合V3要求（应该≥2.5%）
            below_threshold = [x for x in valid_entry_diffs if x < 2.5]
            if below_threshold:
                print(f"\n⚠️ 警告: {len(below_threshold)}个持仓的entry_ema_diff < 2.5% (V3要求)")
                print(f"   这些持仓的entry_ema_diff: {[f'{x:.4f}%' for x in below_threshold]}")

        # 分析平仓原因
        print("\n" + "=" * 100)
        print("【平仓原因分析】")
        print("=" * 100)

        close_reason_counts = {}
        for pos in positions:
            if pos['status'] == 'closed' and pos['close_reason']:
                # 提取主要原因（去掉参数）
                main_reason = pos['close_reason'].split('|')[0]
                close_reason_counts[main_reason] = close_reason_counts.get(main_reason, 0) + 1

        print(f"\n已平仓持仓的平仓原因分布：")
        for reason, count in sorted(close_reason_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"  {reason}: {count}次")

        cursor.close()

    finally:
        conn.close()

if __name__ == "__main__":
    print("开始分析V3入场问题...")
    print()
    analyze_v3_entry_issue()
    print("\n✅ 分析完成！")
