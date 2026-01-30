#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""深度分析黑名单信号的失败原因"""

import pymysql
import sys
import io
from dotenv import load_dotenv
import os
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME')
)

cursor = conn.cursor(pymysql.cursors.DictCursor)

print('=' * 120)
print('黑名单信号失败原因深度分析')
print('=' * 120)
print()

try:
    # 1. 获取所有黑名单信号
    cursor.execute("""
        SELECT signal_type, position_side, reason, total_loss, win_rate, order_count, created_at
        FROM signal_blacklist
        WHERE is_active = 1
        ORDER BY total_loss ASC
    """)

    blacklist_signals = cursor.fetchall()

    print(f"📊 当前黑名单中有 {len(blacklist_signals)} 个信号组合\n")

    # 2. 获取每个黑名单信号的所有历史交易
    all_analysis = []

    for bl_sig in blacklist_signals:
        signal_type = bl_sig['signal_type']
        position_side = bl_sig['position_side']

        print('=' * 120)
        print(f"分析信号: {signal_type[:90]}")
        print(f"方向: {position_side} | 黑名单原因: {bl_sig['reason']}")
        print('-' * 120)

        # 获取该信号的所有历史交易(不限时间)
        cursor.execute("""
            SELECT
                id, symbol, position_side, quantity, leverage,
                entry_price, mark_price, realized_pnl, unrealized_pnl_pct,
                open_time, close_time,
                TIMESTAMPDIFF(MINUTE, open_time, close_time) as holding_minutes,
                entry_signal_type, signal_components, entry_reason,
                stop_loss_pct, take_profit_pct,
                max_profit_pct, max_profit_price, max_profit_time
            FROM futures_positions
            WHERE entry_signal_type = %s
            AND position_side = %s
            AND status = 'closed'
            ORDER BY close_time DESC
            LIMIT 50
        """, (signal_type, position_side))

        trades = cursor.fetchall()

        if not trades:
            print("  ⚠️ 没有找到历史交易记录\n")
            continue

        # 统计分析
        total_trades = len(trades)
        wins = sum(1 for t in trades if float(t['realized_pnl']) > 0)
        losses = sum(1 for t in trades if float(t['realized_pnl']) <= 0)
        total_pnl = sum(float(t['realized_pnl']) for t in trades)
        avg_pnl = total_pnl / total_trades
        win_rate = wins / total_trades * 100 if total_trades > 0 else 0

        # 最大亏损交易
        worst_trades = sorted(trades, key=lambda x: float(x['realized_pnl']))[:5]

        # 计算平均持仓时间
        avg_holding = sum(int(t['holding_minutes']) for t in trades if t['holding_minutes']) / total_trades

        print(f"\n📊 历史表现统计 (最近{total_trades}笔):")
        print(f"   总交易: {total_trades}次 | 胜率: {win_rate:.1f}% ({wins}胜{losses}败)")
        print(f"   总盈亏: ${total_pnl:+.2f} | 平均: ${avg_pnl:+.2f}")
        print(f"   平均持仓: {avg_holding:.1f}分钟")
        print()

        # 分析最大亏损交易
        print(f"💥 最大亏损交易 TOP 5:")
        print('-' * 120)

        for i, trade in enumerate(worst_trades, 1):
            pnl = float(trade['realized_pnl'])
            entry_price = float(trade['entry_price'])
            max_profit_pct = float(trade['max_profit_pct']) if trade['max_profit_pct'] else 0
            holding_min = int(trade['holding_minutes']) if trade['holding_minutes'] else 0

            print(f"{i}. {trade['symbol']:<12} | 亏损: ${pnl:+.2f} | 入场价: ${entry_price:.4f}")
            print(f"   持仓: {holding_min}分钟 | 曾最高盈利: {max_profit_pct:+.2f}%")
            print(f"   开仓: {trade['open_time']} → 平仓: {trade['close_time']}")

            if trade['signal_components']:
                print(f"   信号组成: {trade['signal_components'][:80]}")

            print()

        # 分析信号组成
        signal_parts = signal_type.split(' + ')

        print(f"🔍 信号组成分析:")
        print(f"   信号复杂度: {len(signal_parts)}个组件")
        print(f"   组件列表: {', '.join(signal_parts)}")
        print()

        # 识别潜在问题
        problems = []

        # 问题1: 方向矛盾
        bullish_components = ['breakout_long', 'momentum_up_3pct', 'volume_power_bull',
                             'volume_power_1h_bull', 'trend_1h_bull', 'trend_1d_bull',
                             'consecutive_bull', 'position_high']
        bearish_components = ['breakdown_short', 'momentum_down_3pct', 'volume_power_bear',
                             'volume_power_1h_bear', 'trend_1h_bear', 'trend_1d_bear',
                             'consecutive_bear', 'position_low']

        has_bullish = any(comp in signal_parts for comp in bullish_components)
        has_bearish = any(comp in signal_parts for comp in bearish_components)

        if position_side == 'LONG' and has_bearish:
            bearish_found = [comp for comp in signal_parts if comp in bearish_components]
            problems.append(f"方向矛盾: 做多但包含空头信号 ({', '.join(bearish_found)})")

        if position_side == 'SHORT' and has_bullish:
            bullish_found = [comp for comp in signal_parts if comp in bullish_components]
            problems.append(f"方向矛盾: 做空但包含多头信号 ({', '.join(bullish_found)})")

        # 问题2: 位置风险
        if position_side == 'LONG' and 'position_high' in signal_parts:
            problems.append("追高风险: 在高位(>70%)做多,容易买在顶部")

        if position_side == 'SHORT' and 'position_low' in signal_parts:
            problems.append("追跌风险: 在低位(<30%)做空,容易遇到反弹")

        # 问题3: 信号过于简单
        if len(signal_parts) <= 2:
            problems.append("信号过于简单: 缺乏多重确认,可靠性低")

        # 问题4: 缺乏趋势确认
        has_trend = any('trend' in comp for comp in signal_parts)
        has_volume = any('volume' in comp for comp in signal_parts)
        has_momentum = any('momentum' in comp for comp in signal_parts)

        if not has_trend and not has_volume:
            problems.append("缺乏确认: 没有趋势或量能确认")

        # 问题5: 胜率和盈亏比分析
        if win_rate < 30:
            problems.append(f"极低胜率: {win_rate:.1f}% (低于30%阈值)")

        if avg_pnl < -10:
            problems.append(f"平均大幅亏损: ${avg_pnl:.2f}/笔")

        # 问题6: 止损止盈分析
        avg_max_profit = sum(float(t['max_profit_pct']) for t in trades if t['max_profit_pct']) / total_trades
        if avg_max_profit > 2:  # 曾经盈利过但最终亏损
            problems.append(f"未及时止盈: 平均曾盈利{avg_max_profit:.2f}%但最终亏损")

        print(f"⚠️ 识别的问题 ({len(problems)}个):")
        if problems:
            for p in problems:
                print(f"   • {p}")
        else:
            print(f"   • 无明显逻辑问题,可能是市场环境不适合")
        print()

        # 保存分析结果
        all_analysis.append({
            'signal_type': signal_type,
            'position_side': position_side,
            'total_trades': total_trades,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'avg_pnl': avg_pnl,
            'signal_complexity': len(signal_parts),
            'problems': problems,
            'worst_loss': float(worst_trades[0]['realized_pnl']) if worst_trades else 0
        })

    # 3. 综合分析
    print('=' * 120)
    print('📋 综合失败模式总结')
    print('=' * 120)
    print()

    # 统计问题类型
    problem_types = defaultdict(int)
    for analysis in all_analysis:
        for problem in analysis['problems']:
            problem_type = problem.split(':')[0]
            problem_types[problem_type] += 1

    print("常见失败模式统计:")
    print('-' * 120)
    for problem_type, count in sorted(problem_types.items(), key=lambda x: x[1], reverse=True):
        pct = count / len(all_analysis) * 100
        bar = '█' * int(pct / 5)
        print(f"{problem_type:20} | {bar:<20} {count}次 ({pct:.1f}%)")
    print()

    # 按亏损严重程度排序
    print("最严重的失败信号 TOP 10:")
    print('-' * 120)
    sorted_by_loss = sorted(all_analysis, key=lambda x: x['total_pnl'])[:10]

    for i, sig in enumerate(sorted_by_loss, 1):
        print(f"{i}. {sig['signal_type'][:70]}")
        print(f"   方向: {sig['position_side']} | 总亏损: ${sig['total_pnl']:.2f} | 胜率: {sig['win_rate']:.1f}%")
        print(f"   主要问题: {sig['problems'][0] if sig['problems'] else '未知'}")
        print()

    # 4. 改进建议
    print('=' * 120)
    print('💡 改进建议')
    print('=' * 120)
    print()

    print("1️⃣ 立即修复的逻辑问题:")
    direction_conflicts = [a for a in all_analysis if any('方向矛盾' in p for p in a['problems'])]
    if direction_conflicts:
        print(f"   • 发现 {len(direction_conflicts)} 个方向矛盾信号")
        print(f"   • 建议: 已通过signal_components清理修复")
    else:
        print(f"   • ✅ 未发现方向矛盾问题")
    print()

    print("2️⃣ 规避高风险位置:")
    position_risks = [a for a in all_analysis if any('追高风险' in p or '追跌风险' in p for p in a['problems'])]
    if position_risks:
        print(f"   • 发现 {len(position_risks)} 个位置风险信号")
        print(f"   • 建议: 做多避免position_high,做空避免position_low")
    print()

    print("3️⃣ 增强信号复杂度:")
    simple_signals = [a for a in all_analysis if a['signal_complexity'] <= 2]
    if simple_signals:
        print(f"   • 发现 {len(simple_signals)} 个过于简单的信号")
        print(f"   • 建议: 要求至少3个组件,包含趋势或量能确认")
    print()

    print("4️⃣ 改进止盈策略:")
    no_profit_taking = [a for a in all_analysis if any('未及时止盈' in p for p in a['problems'])]
    if no_profit_taking:
        print(f"   • 发现 {len(no_profit_taking)} 个未及时止盈的信号")
        print(f"   • 建议: 启用智能止盈,盈利>2%时采用移动止损")
    print()

    # 5. 总结
    total_loss = sum(a['total_pnl'] for a in all_analysis)
    avg_win_rate = sum(a['win_rate'] for a in all_analysis) / len(all_analysis)

    print('=' * 120)
    print('📊 黑名单信号总体统计')
    print('=' * 120)
    print()
    print(f"黑名单信号数量: {len(all_analysis)}个")
    print(f"累计总亏损: ${total_loss:.2f}")
    print(f"平均胜率: {avg_win_rate:.1f}%")
    print(f"通过黑名单禁用,预期减少月度亏损: ${abs(total_loss) * 30:.2f}")
    print()

except Exception as e:
    print(f"✗ 分析失败: {e}")
    import traceback
    traceback.print_exc()
finally:
    cursor.close()
    conn.close()

print('=' * 120)
