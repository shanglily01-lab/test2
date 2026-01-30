#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析最近24小时所有信号的表现,包括黑名单信号"""

import pymysql
import sys
import io
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta
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
print('24小时信号综合分析报告')
print('=' * 120)
print()

try:
    # 1. 获取最近24小时的所有已平仓持仓
    cursor.execute("""
        SELECT
            entry_signal_type as signal_type,
            position_side,
            COUNT(*) as total_trades,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) as losses,
            SUM(realized_pnl) as total_pnl,
            AVG(realized_pnl) as avg_pnl,
            MAX(realized_pnl) as max_win,
            MIN(realized_pnl) as max_loss,
            AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)) as avg_holding_minutes
        FROM futures_positions
        WHERE close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
        AND status = 'closed'
        AND entry_signal_type IS NOT NULL
        AND entry_signal_type != ''
        GROUP BY entry_signal_type, position_side
        ORDER BY total_pnl DESC
    """)

    signals = cursor.fetchall()

    if not signals:
        print("⚠️ 最近24小时没有已平仓的交易记录\n")
        cursor.close()
        conn.close()
        sys.exit(0)

    print(f"📊 共找到 {len(signals)} 种信号组合\n")

    # 2. 检查哪些信号在黑名单中
    cursor.execute("""
        SELECT signal_type, position_side, reason, created_at
        FROM signal_blacklist
        WHERE is_active = 1
    """)

    blacklist = cursor.fetchall()
    blacklist_set = {(b['signal_type'], b['position_side']) for b in blacklist}

    print(f"🚫 当前黑名单中有 {len(blacklist_set)} 个信号组合\n")

    # 3. 分类统计
    profitable_signals = []
    losing_signals = []
    low_winrate_signals = []
    blacklisted_performing = []

    total_pnl_all = 0
    total_trades_all = 0
    total_wins_all = 0

    print('=' * 120)
    print('详细信号分析')
    print('=' * 120)
    print()

    for sig in signals:
        signal_type = sig['signal_type']
        position_side = sig['position_side']
        total_trades = int(sig['total_trades'])
        wins = int(sig['wins'])
        losses = int(sig['losses'])
        total_pnl = float(sig['total_pnl'])
        avg_pnl = float(sig['avg_pnl'])
        max_win = float(sig['max_win'])
        max_loss = float(sig['max_loss'])
        avg_holding = float(sig['avg_holding_minutes']) if sig['avg_holding_minutes'] else 0

        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

        total_pnl_all += total_pnl
        total_trades_all += total_trades
        total_wins_all += wins

        is_blacklisted = (signal_type, position_side) in blacklist_set
        status_emoji = '🚫' if is_blacklisted else '✅' if total_pnl > 0 else '❌'

        # 简化信号名称显示
        signal_short = signal_type[:80] if len(signal_type) > 80 else signal_type

        print(f"{status_emoji} {signal_short}")
        print(f"   方向: {position_side} | 交易: {total_trades}次 | 胜率: {win_rate:.1f}% ({wins}胜{losses}败)")
        print(f"   总盈亏: ${total_pnl:+.2f} | 平均: ${avg_pnl:+.2f} | 最大盈: ${max_win:.2f} | 最大亏: ${max_loss:.2f}")
        print(f"   平均持仓: {avg_holding:.1f}分钟")

        if is_blacklisted:
            print(f"   ⚠️ 已在黑名单")

        print()

        # 分类
        signal_data = {
            'signal_type': signal_type,
            'position_side': position_side,
            'total_trades': total_trades,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'avg_pnl': avg_pnl,
            'max_win': max_win,
            'max_loss': max_loss,
            'avg_holding': avg_holding,
            'is_blacklisted': is_blacklisted
        }

        if is_blacklisted and total_pnl > 0:
            blacklisted_performing.append(signal_data)

        if total_pnl > 10:  # 盈利>$10
            profitable_signals.append(signal_data)
        elif total_pnl < -30:  # 亏损>$30
            losing_signals.append(signal_data)

        if win_rate < 35 and total_trades >= 3:  # 胜率<35%且至少3次交易
            low_winrate_signals.append(signal_data)

    # 4. 总体统计
    overall_winrate = (total_wins_all / total_trades_all * 100) if total_trades_all > 0 else 0

    print('=' * 120)
    print('24小时总体表现')
    print('=' * 120)
    print()
    print(f"总交易次数: {total_trades_all}")
    print(f"总胜率: {overall_winrate:.2f}%")
    print(f"总盈亏: ${total_pnl_all:+.2f}")
    print(f"平均每笔: ${total_pnl_all/total_trades_all:+.2f}" if total_trades_all > 0 else "")
    print()

    # 5. 优化建议
    print('=' * 120)
    print('📋 优化建议')
    print('=' * 120)
    print()

    # 5.1 表现优秀的信号
    if profitable_signals:
        print(f"✅ 表现优秀的信号 ({len(profitable_signals)}个, 总盈利>${sum(s['total_pnl'] for s in profitable_signals):.2f}):")
        print('-' * 120)
        profitable_signals.sort(key=lambda x: x['total_pnl'], reverse=True)
        for i, s in enumerate(profitable_signals[:10], 1):
            print(f"{i}. {s['signal_type'][:70]} ({s['position_side']})")
            print(f"   交易:{s['total_trades']}次 | 胜率:{s['win_rate']:.1f}% | 盈利:${s['total_pnl']:+.2f}")
            if s['is_blacklisted']:
                print(f"   ⚠️ 建议: 从黑名单移除")
        print()

    # 5.2 需要加入黑名单的信号
    candidates_for_blacklist = [
        s for s in losing_signals
        if not s['is_blacklisted'] and (s['total_pnl'] < -30 or s['win_rate'] < 30)
    ]

    if candidates_for_blacklist:
        print(f"❌ 建议加入黑名单的信号 ({len(candidates_for_blacklist)}个):")
        print('-' * 120)
        candidates_for_blacklist.sort(key=lambda x: x['total_pnl'])
        for i, s in enumerate(candidates_for_blacklist, 1):
            print(f"{i}. {s['signal_type'][:70]} ({s['position_side']})")
            print(f"   交易:{s['total_trades']}次 | 胜率:{s['win_rate']:.1f}% | 亏损:${s['total_pnl']:+.2f}")
            reason = []
            if s['total_pnl'] < -50:
                reason.append(f"严重亏损${s['total_pnl']:.2f}")
            elif s['total_pnl'] < -30:
                reason.append(f"亏损${s['total_pnl']:.2f}")
            if s['win_rate'] < 25:
                reason.append(f"极低胜率{s['win_rate']:.1f}%")
            elif s['win_rate'] < 35:
                reason.append(f"低胜率{s['win_rate']:.1f}%")
            print(f"   原因: {', '.join(reason)}")
        print()

    # 5.3 黑名单中但表现良好的信号
    if blacklisted_performing:
        print(f"🔄 黑名单中但24H表现良好的信号 ({len(blacklisted_performing)}个):")
        print('-' * 120)
        blacklisted_performing.sort(key=lambda x: x['total_pnl'], reverse=True)
        for i, s in enumerate(blacklisted_performing, 1):
            print(f"{i}. {s['signal_type'][:70]} ({s['position_side']})")
            print(f"   交易:{s['total_trades']}次 | 胜率:{s['win_rate']:.1f}% | 盈利:${s['total_pnl']:+.2f}")
            print(f"   建议: 观察更长时间,如持续盈利可考虑移出黑名单")
        print()

    # 5.4 胜率分布
    print("📊 胜率分布:")
    print('-' * 120)
    winrate_ranges = {
        '0-20%': 0,
        '20-40%': 0,
        '40-60%': 0,
        '60-80%': 0,
        '80-100%': 0
    }

    for sig in signals:
        win_rate = (int(sig['wins']) / int(sig['total_trades']) * 100) if int(sig['total_trades']) > 0 else 0
        if win_rate < 20:
            winrate_ranges['0-20%'] += 1
        elif win_rate < 40:
            winrate_ranges['20-40%'] += 1
        elif win_rate < 60:
            winrate_ranges['40-60%'] += 1
        elif win_rate < 80:
            winrate_ranges['60-80%'] += 1
        else:
            winrate_ranges['80-100%'] += 1

    for range_name, count in winrate_ranges.items():
        pct = (count / len(signals) * 100) if len(signals) > 0 else 0
        bar = '█' * int(pct / 5)
        print(f"{range_name:10} | {bar:<20} {count}个 ({pct:.1f}%)")
    print()

    # 5.5 盈利分布
    print("💰 盈利分布:")
    print('-' * 120)
    pnl_ranges = {
        '亏损>$50': 0,
        '亏损$30-50': 0,
        '亏损$10-30': 0,
        '亏损$0-10': 0,
        '盈利$0-10': 0,
        '盈利$10-30': 0,
        '盈利>$30': 0
    }

    for sig in signals:
        pnl = float(sig['total_pnl'])
        if pnl < -50:
            pnl_ranges['亏损>$50'] += 1
        elif pnl < -30:
            pnl_ranges['亏损$30-50'] += 1
        elif pnl < -10:
            pnl_ranges['亏损$10-30'] += 1
        elif pnl < 0:
            pnl_ranges['亏损$0-10'] += 1
        elif pnl < 10:
            pnl_ranges['盈利$0-10'] += 1
        elif pnl < 30:
            pnl_ranges['盈利$10-30'] += 1
        else:
            pnl_ranges['盈利>$30'] += 1

    for range_name, count in pnl_ranges.items():
        pct = (count / len(signals) * 100) if len(signals) > 0 else 0
        bar = '█' * int(pct / 5)
        emoji = '❌' if '亏损' in range_name else '✅'
        print(f"{emoji} {range_name:12} | {bar:<20} {count}个 ({pct:.1f}%)")
    print()

    # 6. 生成执行建议
    print('=' * 120)
    print('🎯 执行建议')
    print('=' * 120)
    print()

    if candidates_for_blacklist:
        print(f"1️⃣ 立即加入黑名单: {len(candidates_for_blacklist)} 个信号")
        print(f"   预期减少亏损: ${abs(sum(s['total_pnl'] for s in candidates_for_blacklist)):.2f}/天")
        print()

    if blacklisted_performing:
        print(f"2️⃣ 考虑移出黑名单: {len(blacklisted_performing)} 个信号 (需观察更长时间)")
        print(f"   潜在增加盈利: ${sum(s['total_pnl'] for s in blacklisted_performing):.2f}/天")
        print()

    profitable_count = len([s for s in signals if s['total_pnl'] > 0])
    losing_count = len([s for s in signals if s['total_pnl'] < 0])

    print(f"3️⃣ 信号质量总结:")
    print(f"   盈利信号: {profitable_count}个 ({profitable_count/len(signals)*100:.1f}%)")
    print(f"   亏损信号: {losing_count}个 ({losing_count/len(signals)*100:.1f}%)")
    print(f"   建议: {'继续优化亏损信号' if losing_count > profitable_count else '整体表现良好,保持策略'}")
    print()

    # 7. 保存结果到JSON
    import json
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_file = os.path.join(script_dir, '24h_signal_analysis.json')

    result_data = {
        'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'period': '24 hours',
        'summary': {
            'total_signals': len(signals),
            'total_trades': total_trades_all,
            'overall_winrate': round(overall_winrate, 2),
            'total_pnl': round(total_pnl_all, 2),
            'profitable_signals': profitable_count,
            'losing_signals': losing_count
        },
        'add_to_blacklist': [
            {
                'signal_type': s['signal_type'],
                'position_side': s['position_side'],
                'trades': s['total_trades'],
                'win_rate': round(s['win_rate'], 1),
                'total_pnl': round(s['total_pnl'], 2),
                'reason': f"胜率{s['win_rate']:.1f}%, 亏损${s['total_pnl']:.2f}"
            }
            for s in candidates_for_blacklist
        ],
        'remove_from_blacklist': [
            {
                'signal_type': s['signal_type'],
                'position_side': s['position_side'],
                'trades': s['total_trades'],
                'win_rate': round(s['win_rate'], 1),
                'total_pnl': round(s['total_pnl'], 2)
            }
            for s in blacklisted_performing
        ]
    }

    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result_data, f, indent=2, ensure_ascii=False)
        print(f"✅ 分析结果已保存到: {output_file}")
    except Exception as e:
        print(f"⚠️ 保存结果失败: {e}")

    print()
    print('=' * 120)

except Exception as e:
    print(f"✗ 分析失败: {e}")
    import traceback
    traceback.print_exc()
finally:
    cursor.close()
    conn.close()
