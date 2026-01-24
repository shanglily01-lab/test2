#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析信号组合的合理性
"""

import sys
import io
import pymysql
import json
from collections import defaultdict

# 设置标准输出编码为UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def analyze_signal_combinations():
    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    print("=" * 120)
    print("信号组合合理性分析")
    print("=" * 120)

    # 查询最近的交易
    cursor.execute("""
        SELECT
            symbol, position_side, entry_score,
            signal_components, realized_pnl,
            open_time, close_time
        FROM futures_positions
        WHERE close_time >= CONCAT(CURDATE() - INTERVAL 3 DAY, ' 20:00:00')
        AND status = 'closed'
        AND signal_components IS NOT NULL
        ORDER BY close_time DESC
        LIMIT 100
    """)

    trades = cursor.fetchall()

    # 统计每种信号组合的表现
    combination_stats = defaultdict(lambda: {'count': 0, 'pnl': 0, 'wins': 0, 'trades': []})

    for trade in trades:
        pnl = float(trade['realized_pnl'])

        try:
            components = json.loads(trade['signal_components'])
            # 生成组合键(按信号名称排序,忽略具体分数)
            signal_names = sorted(components.keys())
            combo_key = " + ".join(signal_names)

            combination_stats[combo_key]['count'] += 1
            combination_stats[combo_key]['pnl'] += pnl
            if pnl > 0:
                combination_stats[combo_key]['wins'] += 1

            combination_stats[combo_key]['trades'].append({
                'symbol': trade['symbol'],
                'side': trade['position_side'],
                'pnl': pnl,
                'components': components
            })
        except:
            continue

    # 按出现次数排序
    sorted_combos = sorted(combination_stats.items(), key=lambda x: x[1]['count'], reverse=True)

    print("\n常见信号组合及其表现:")
    print("=" * 120)

    for combo, stats in sorted_combos[:15]:
        count = stats['count']
        pnl = stats['pnl']
        wins = stats['wins']
        win_rate = (wins / count * 100) if count > 0 else 0
        avg_pnl = pnl / count if count > 0 else 0

        status = "✅" if pnl > 0 else "❌"

        print(f"\n{status} 组合: {combo}")
        print(f"   出现次数: {count} | 胜率: {win_rate:.1f}% ({wins}胜/{count-wins}负) | 总盈亏: ${pnl:.2f} | 平均: ${avg_pnl:.2f}")

    # 逻辑矛盾分析
    print("\n" + "=" * 120)
    print("信号组合逻辑矛盾分析")
    print("=" * 120)

    contradictions = []

    for combo, stats in combination_stats.items():
        signals = combo.split(" + ")

        # 检查1: 做多信号组合中包含做空倾向的信号
        if any(s in signals for s in ['position_low', 'momentum_down_3pct', 'trend_1h_bull', 'trend_1d_bull']):
            # 这是做多组合
            for trade in stats['trades']:
                if trade['side'] == 'LONG':
                    # 检查是否包含矛盾信号
                    if 'position_high' in signals:
                        contradictions.append({
                            'type': '位置矛盾',
                            'desc': f"做多但包含position_high(高位做空信号)",
                            'combo': combo,
                            'example': trade
                        })
                    if 'trend_1h_bear' in signals or 'trend_1d_bear' in signals:
                        contradictions.append({
                            'type': '趋势矛盾',
                            'desc': f"做多但包含看跌趋势信号",
                            'combo': combo,
                            'example': trade
                        })

        # 检查2: 做空信号组合中包含做多倾向的信号
        if any(s in signals for s in ['position_high', 'momentum_up_3pct', 'trend_1h_bear', 'trend_1d_bear']):
            # 这是做空组合
            for trade in stats['trades']:
                if trade['side'] == 'SHORT':
                    if 'position_low' in signals:
                        contradictions.append({
                            'type': '位置矛盾',
                            'desc': f"做空但包含position_low(低位做多信号)",
                            'combo': combo,
                            'example': trade
                        })
                    if 'trend_1h_bull' in signals or 'trend_1d_bull' in signals:
                        contradictions.append({
                            'type': '趋势矛盾',
                            'desc': f"做空但包含看涨趋势信号",
                            'combo': combo,
                            'example': trade
                        })

        # 检查3: consecutive_bull/bear 的奇怪逻辑
        if 'consecutive_bull' in signals:
            for trade in stats['trades']:
                contradictions.append({
                    'type': '逻辑不清',
                    'desc': f"consecutive_bull(连续看涨)的作用不明确",
                    'combo': combo,
                    'example': trade,
                    'note': '连续看涨后应该等回调做多,还是突破做多?还是做空?'
                })

        if 'consecutive_bear' in signals:
            for trade in stats['trades']:
                contradictions.append({
                    'type': '逻辑不清',
                    'desc': f"consecutive_bear(连续看跌)的作用不明确",
                    'combo': combo,
                    'example': trade,
                    'note': '连续看跌后应该等反弹做空,还是突破做空?还是做多?'
                })

    # 去重并统计
    seen = set()
    unique_contradictions = []
    for c in contradictions:
        key = (c['type'], c['combo'])
        if key not in seen:
            seen.add(key)
            unique_contradictions.append(c)

    if unique_contradictions:
        print(f"\n发现 {len(unique_contradictions)} 种逻辑矛盾的信号组合:\n")

        for i, c in enumerate(unique_contradictions[:20], 1):
            print(f"{i}. 【{c['type']}】 {c['desc']}")
            print(f"   组合: {c['combo']}")
            if 'note' in c:
                print(f"   ⚠️  {c['note']}")
            print(f"   示例: {c['example']['symbol']} {c['example']['side']} -> ${c['example']['pnl']:.2f}")
            print()

    # 分析position_high的表现
    print("=" * 120)
    print("position_high信号分析(理论上是最好的信号,但实际表现差)")
    print("=" * 120)

    position_high_trades = []
    for trade in trades:
        try:
            components = json.loads(trade['signal_components'])
            if 'position_high' in components:
                position_high_trades.append({
                    'symbol': trade['symbol'],
                    'side': trade['position_side'],
                    'pnl': float(trade['realized_pnl']),
                    'components': components,
                    'time': trade['open_time']
                })
        except:
            continue

    print(f"\n包含position_high的交易: {len(position_high_trades)}笔")

    if position_high_trades:
        wins = sum(1 for t in position_high_trades if t['pnl'] > 0)
        total_pnl = sum(t['pnl'] for t in position_high_trades)
        print(f"胜率: {wins/len(position_high_trades)*100:.1f}% | 总盈亏: ${total_pnl:.2f}")

        print("\n最近10笔position_high交易:")
        for i, t in enumerate(position_high_trades[:10], 1):
            status = "✅" if t['pnl'] > 0 else "❌"
            print(f"{i}. {status} {t['symbol']} {t['side']} ${t['pnl']:.2f} | {t['time']}")
            print(f"   信号: {', '.join([f'{k}:{v}' for k,v in t['components'].items()])}")

    cursor.close()
    conn.close()

    print("\n" + "=" * 120)

if __name__ == '__main__':
    analyze_signal_combinations()
