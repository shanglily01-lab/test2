#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场环境分析：检测当前是趋势市还是震荡市
"""

import sys
import io
import pymysql
from datetime import datetime, timedelta
from decimal import Decimal

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def analyze_market_regime():
    """分析市场环境特征"""

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

        # 查询今天的交易持仓时长分布
        cursor.execute("""
            SELECT
                fp.id,
                fp.symbol,
                fp.position_side,
                fp.entry_signal_type,
                fp.realized_pnl as pnl,
                fp.unrealized_pnl_pct as pnl_pct,
                fp.notes as close_reason,
                TIMESTAMPDIFF(MINUTE, fp.open_time, fp.close_time) as duration_minutes,
                fp.entry_ema_diff,
                fp.open_time,
                fp.close_time
            FROM futures_positions fp
            WHERE DATE(fp.open_time) = CURDATE()
            AND fp.status = 'closed'
            ORDER BY duration_minutes
        """)

        positions = cursor.fetchall()

        print("=" * 100)
        print("市场环境分析 - 2026-01-17")
        print("=" * 100)

        # 持仓时长分析
        very_short = []  # <30分钟
        short = []       # 30分钟-2小时
        medium = []      # 2-4小时
        long_pos = []    # >4小时

        for pos in positions:
            duration = pos['duration_minutes']
            if duration < 30:
                very_short.append(pos)
            elif duration < 120:
                short.append(pos)
            elif duration < 240:
                medium.append(pos)
            else:
                long_pos.append(pos)

        print(f"\n【持仓时长分布】")
        print(f"  <30分钟（快速反转）: {len(very_short)}笔 ({len(very_short)/len(positions)*100:.1f}%)")
        print(f"  30分钟-2小时（短期震荡）: {len(short)}笔 ({len(short)/len(positions)*100:.1f}%)")
        print(f"  2-4小时（中期震荡）: {len(medium)}笔 ({len(medium)/len(positions)*100:.1f}%)")
        print(f"  >4小时（可能有趋势）: {len(long_pos)}笔 ({len(long_pos)/len(positions)*100:.1f}%)")

        # 平仓原因分析
        print(f"\n【平仓原因特征】")
        reversal_count = 0
        weak_trend_count = 0
        severe_loss_count = 0
        trailing_stop_count = 0

        for pos in positions:
            close_reason = pos['close_reason'] or ''
            if 'reversed' in close_reason:
                reversal_count += 1
            if 'trend_weak' in close_reason:
                weak_trend_count += 1
            if 'severe_loss' in close_reason:
                severe_loss_count += 1
            if '移动止损' in close_reason or 'trailing' in close_reason:
                trailing_stop_count += 1

        print(f"  趋势反转: {reversal_count}笔 ({reversal_count/len(positions)*100:.1f}%)")
        print(f"  趋势减弱: {weak_trend_count}笔 ({weak_trend_count/len(positions)*100:.1f}%)")
        print(f"  严重亏损: {severe_loss_count}笔 ({severe_loss_count/len(positions)*100:.1f}%)")
        print(f"  移动止盈: {trailing_stop_count}笔 ({trailing_stop_count/len(positions)*100:.1f}%)")

        # 入场后价格走势分析
        print(f"\n【入场后价格走势】")
        immediate_reversal = 0  # 入场后立即反转（<30分钟止损）
        delayed_reversal = 0    # 延迟反转（30分钟-2小时止损）
        extended_trend = 0      # 趋势延续（>4小时或盈利）

        for pos in positions:
            duration = pos['duration_minutes']
            pnl = float(pos['pnl'] or 0)

            if duration < 30 and pnl < 0:
                immediate_reversal += 1
            elif duration < 120 and pnl < 0:
                delayed_reversal += 1
            elif duration > 240 or pnl > 0:
                extended_trend += 1

        print(f"  入场后立即反转（<30分钟）: {immediate_reversal}笔 ({immediate_reversal/len(positions)*100:.1f}%)")
        print(f"  入场后延迟反转（30分钟-2小时）: {delayed_reversal}笔 ({delayed_reversal/len(positions)*100:.1f}%)")
        print(f"  趋势延续（>4小时或盈利）: {extended_trend}笔 ({extended_trend/len(positions)*100:.1f}%)")

        # 市场环境判断
        print("\n" + "=" * 100)
        print("【市场环境判断】")
        print("=" * 100)

        total_reversal_rate = (immediate_reversal + delayed_reversal) / len(positions) * 100
        short_holding_rate = (len(very_short) + len(short)) / len(positions) * 100

        print(f"\n关键指标:")
        print(f"  入场后反转率: {total_reversal_rate:.1f}%")
        print(f"  短期持仓率（<2小时）: {short_holding_rate:.1f}%")
        print(f"  趋势延续率: {extended_trend/len(positions)*100:.1f}%")

        print(f"\n市场特征分析:")
        if total_reversal_rate > 70:
            print("  ⚠️ 极高反转率 → 强震荡市")
            print("  原因：入场后价格快速反转，无法形成持续趋势")
        elif total_reversal_rate > 50:
            print("  ⚠️ 高反转率 → 震荡市")
            print("  原因：多数趋势信号是假突破")
        else:
            print("  ✅ 低反转率 → 趋势市")
            print("  原因：趋势信号可靠，价格延续性好")

        if short_holding_rate > 70:
            print("  ⚠️ 极短持仓周期 → 高波动震荡")
            print("  原因：价格在震荡区间内快速来回")
        elif short_holding_rate > 50:
            print("  ⚠️ 短持仓周期 → 震荡环境")
            print("  原因：趋势持续时间不足")

        # 最近10笔交易的详细分析
        print("\n" + "=" * 100)
        print("【典型案例分析】")
        print("=" * 100)

        print("\n最短持仓案例（快速反转）:")
        for i, pos in enumerate(very_short[:3], 1):
            print(f"\n  {i}. {pos['symbol']} {pos['position_side']} - {pos['duration_minutes']}分钟")
            print(f"     盈亏: {float(pos['pnl_pct'] or 0):+.2f}%")
            print(f"     平仓: {pos['close_reason']}")
            print(f"     ➜ 入场后立即遭遇趋势反转，典型震荡市特征")

        if trailing_stop_count > 0:
            print("\n盈利案例（趋势延续）:")
            for pos in positions:
                if '移动止损' in (pos['close_reason'] or ''):
                    print(f"\n  {pos['symbol']} {pos['position_side']} - {pos['duration_minutes']}分钟")
                    print(f"     盈亏: {float(pos['pnl_pct'] or 0):+.2f}%")
                    print(f"     ➜ 趋势延续，成功捕捉趋势")
                    break

        # 结论
        print("\n" + "=" * 100)
        print("【结论与建议】")
        print("=" * 100)

        if total_reversal_rate > 70 and short_holding_rate > 70:
            print("\n当前市场环境: 🔴 强震荡市")
            print("\n特征:")
            print("  - 入场后反转率 > 70%，趋势信号不可靠")
            print("  - 持仓时长 < 2小时占 > 70%，无法形成持续趋势")
            print("  - 价格在震荡区间内快速来回")

            print("\n建议:")
            print("  ✅ 反向操作策略（fade the move）")
            print("     - V3检测到上涨趋势 → 做空（预期回调）")
            print("     - V3检测到下跌趋势 → 做多（预期反弹）")
            print("  ✅ 缩小止损（1%-2%，快速认错）")
            print("  ✅ 快速止盈（回调0.5%-1%即可平仓）")
            print("  ❌ 禁用趋势跟随策略")

        cursor.close()

    finally:
        conn.close()

if __name__ == "__main__":
    print("开始分析市场环境...")
    print()
    analyze_market_regime()
    print("\n✅ 分析完成！")
