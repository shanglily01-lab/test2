#!/usr/bin/env python3
"""分析错过的最佳信号 - 为什么超级大脑没有充分捕捉"""
import mysql.connector
from collections import defaultdict
from datetime import datetime, timedelta

DB_CONFIG = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def analyze_missed_signals():
    """分析错过的信号"""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    print("=" * 140)
    print("最佳信号捕获率分析 - 为什么错过了高胜率信号?")
    print("=" * 140)
    print()

    # 定义高胜率信号（从之前的分析中提取）
    best_signals = [
        "breakdown_short + position_low + volatility_high + volume_power_bear",
        "breakdown_short + position_low + volume_power_bear",
        "consecutive_bear + position_high + volatility_high",
        "consecutive_bull + momentum_up_3pct + position_mid + volatility_high + volume_power_1h_bull",
        "momentum_up_3pct + position_mid + volatility_high + volume_power_bear",
        "breakdown_short + momentum_up_3pct + position_low + volatility_high + volume_power_bear",
        "consecutive_bear + position_mid + trend_1d_bear + volume_power_bear",
        "breakout_long + position_high",
        "position_high + volatility_high",
        "breakdown_short + position_low + trend_1d_bear + volatility_high + volume_power_1h_bear",
        "consecutive_bull + momentum_down_3pct + position_low + volatility_high"
    ]

    print("【一】高胜率信号的实际捕获情况")
    print()

    # 查询最近7天的信号数据
    cursor.execute("""
        SELECT
            entry_signal_type,
            COUNT(*) as count,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as win_count,
            AVG(realized_pnl) as avg_pnl,
            AVG(entry_score) as avg_score
        FROM futures_positions
        WHERE status = 'closed'
        AND account_id = 2
        AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
        GROUP BY entry_signal_type
    """)

    signal_data = {row['entry_signal_type']: row for row in cursor.fetchall()}

    print(f"{'信号类型':<80} {'捕获数':<10} {'胜率':<10} {'平均评分':<12}")
    print("-" * 140)

    for signal in best_signals:
        if signal in signal_data:
            data = signal_data[signal]
            count = data['count']
            win_rate = (data['win_count'] / count * 100) if count > 0 else 0
            avg_score = data['avg_score'] or 0

            status = "[OK] 已捕获" if count >= 10 else "[!] 捕获不足"

            print(f"{signal:<80} {count:<10} {win_rate:>8.1f}% {avg_score:>10.1f}  {status}")
        else:
            print(f"{signal:<80} {'0':<10} {'N/A':<10} {'N/A':<12}  [X] 完全错过")

    print()
    print()

    # 分析信号组件的出现频率
    print("【二】信号组件分析 - 哪些组件组合出现频率低?")
    print()

    # 分解信号为组件
    component_stats = defaultdict(lambda: {
        'total': 0,
        'win': 0,
        'combinations': defaultdict(int)
    })

    cursor.execute("""
        SELECT
            entry_signal_type,
            realized_pnl
        FROM futures_positions
        WHERE status = 'closed'
        AND account_id = 2
        AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
    """)

    all_positions = cursor.fetchall()

    for pos in all_positions:
        signal = pos['entry_signal_type'] or ''
        pnl = float(pos['realized_pnl']) if pos['realized_pnl'] else 0
        is_win = pnl > 0

        # 分解信号为组件
        components = [c.strip() for c in signal.split('+') if c.strip()]

        for comp in components:
            component_stats[comp]['total'] += 1
            if is_win:
                component_stats[comp]['win'] += 1

            # 记录组合
            if len(components) > 1:
                for other_comp in components:
                    if other_comp != comp:
                        component_stats[comp]['combinations'][other_comp] += 1

    # 按频率排序
    sorted_components = sorted(component_stats.items(),
                              key=lambda x: x[1]['total'],
                              reverse=True)

    print(f"{'信号组件':<40} {'出现次数':<12} {'胜率':<10} {'状态':<20}")
    print("-" * 90)

    for comp, stats in sorted_components[:30]:  # 显示前30个
        total = stats['total']
        win_rate = (stats['win'] / total * 100) if total > 0 else 0

        if total < 5:
            status = "[!] 极少出现"
        elif total < 20:
            status = "[!] 出现不足"
        else:
            status = "[OK] 充分利用"

        print(f"{comp:<40} {total:<12} {win_rate:>8.1f}% {status:<20}")

    print()
    print()

    # 分析最佳信号的特征模式
    print("【三】最佳信号的特征模式分析")
    print()

    # 统计高胜率信号中的常见模式
    best_signal_components = []
    for signal in best_signals[:6]:  # 分析前6个最佳信号
        if signal in signal_data and signal_data[signal]['count'] >= 2:
            components = [c.strip() for c in signal.split('+') if c.strip()]
            best_signal_components.extend(components)

    # 统计最常见的组件
    from collections import Counter
    component_freq = Counter(best_signal_components)

    print("高胜率信号中的高频组件:")
    print(f"{'组件':<40} {'在最佳信号中出现次数':<25}")
    print("-" * 70)

    for comp, freq in component_freq.most_common(15):
        total_in_system = component_stats[comp]['total'] if comp in component_stats else 0
        capture_status = "[OK] 充分捕获" if total_in_system >= 20 else "[!] 捕获不足"

        print(f"{comp:<40} {freq:<25} (系统总计:{total_in_system}) {capture_status}")

    print()
    print()

    # 分析信号评分阈值
    print("【四】信号评分阈值分析")
    print()

    cursor.execute("""
        SELECT
            entry_signal_type,
            entry_score,
            realized_pnl
        FROM futures_positions
        WHERE status = 'closed'
        AND account_id = 2
        AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
        AND entry_signal_type IN (%s)
    """ % ','.join(['%s'] * len(best_signals)), tuple(best_signals))

    best_signal_positions = cursor.fetchall()

    if best_signal_positions:
        scores = [p['entry_score'] for p in best_signal_positions if p['entry_score']]
        if scores:
            min_score = min(scores)
            max_score = max(scores)
            avg_score = sum(scores) / len(scores)

            print(f"最佳信号的评分范围:")
            print(f"  最低评分: {min_score}")
            print(f"  最高评分: {max_score}")
            print(f"  平均评分: {avg_score:.1f}")
            print()

            # 统计评分分布
            score_ranges = {
                '0-30': 0,
                '30-40': 0,
                '40-50': 0,
                '50-60': 0,
                '60-70': 0,
                '70-80': 0,
                '80-100': 0
            }

            for score in scores:
                if score < 30:
                    score_ranges['0-30'] += 1
                elif score < 40:
                    score_ranges['30-40'] += 1
                elif score < 50:
                    score_ranges['40-50'] += 1
                elif score < 60:
                    score_ranges['50-60'] += 1
                elif score < 70:
                    score_ranges['60-70'] += 1
                elif score < 80:
                    score_ranges['70-80'] += 1
                else:
                    score_ranges['80-100'] += 1

            print(f"评分分布:")
            for range_name, count in score_ranges.items():
                if count > 0:
                    pct = (count / len(scores) * 100)
                    print(f"  {range_name}分: {count}个 ({pct:.1f}%)")

    print()
    print()

    # 关键发现和建议
    print("【五】关键发现与优化建议")
    print()
    print("=" * 140)

    # 统计每个最佳信号的捕获情况
    total_best_signals = len(best_signals)
    captured_signals = sum(1 for s in best_signals if s in signal_data and signal_data[s]['count'] > 0)
    well_captured = sum(1 for s in best_signals if s in signal_data and signal_data[s]['count'] >= 10)

    print(f"1. 信号捕获率:")
    print(f"   - 最佳信号总数: {total_best_signals}")
    print(f"   - 已捕获信号: {captured_signals} ({captured_signals/total_best_signals*100:.1f}%)")
    print(f"   - 充分捕获(≥10次): {well_captured} ({well_captured/total_best_signals*100:.1f}%)")
    print()

    # 找出完全错过的信号
    missed_signals = [s for s in best_signals if s not in signal_data or signal_data[s]['count'] == 0]
    if missed_signals:
        print(f"2. 完全错过的高胜率信号 ({len(missed_signals)}个):")
        for signal in missed_signals[:5]:  # 显示前5个
            print(f"   - {signal}")
        print()

    # 找出捕获不足的信号
    insufficient_signals = [s for s in best_signals if s in signal_data and 0 < signal_data[s]['count'] < 10]
    if insufficient_signals:
        print(f"3. 捕获不足的信号 ({len(insufficient_signals)}个):")
        for signal in insufficient_signals[:5]:
            count = signal_data[signal]['count']
            print(f"   - {signal} (仅{count}次)")
        print()

    # 分析可能的原因
    print("4. 可能错过信号的原因:")
    print()

    # 检查评分阈值
    cursor.execute("""
        SELECT AVG(entry_score) as avg_score
        FROM futures_positions
        WHERE status = 'closed'
        AND account_id = 2
        AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
    """)
    overall_avg_score = cursor.fetchone()['avg_score'] or 0

    print(f"   a) 评分阈值过高:")
    print(f"      - 系统平均入场评分: {overall_avg_score:.1f}")
    print(f"      - 建议: 降低某些高胜率信号类型的评分阈值")
    print()

    print(f"   b) 信号组件组合要求过严:")
    print(f"      - 某些高胜率组合(如'breakdown_short + position_low + volatility_high')")
    print(f"        可能因为需要同时满足多个条件而很少触发")
    print(f"      - 建议: 考虑放宽部分组件的触发条件")
    print()

    print(f"   c) 市场条件限制:")
    print(f"      - 某些信号依赖特定市场环境(如高波动、特定趋势方向)")
    print(f"      - 建议: 在相应市场条件下提高这些信号的权重")
    print()

    print("5. 优化建议:")
    print()
    print("   [1] 降低评分阈值: 将batch1_score_threshold从75降到70")
    print("       (超级大脑刚刚已经调整到75，可以继续优化)")
    print()
    print("   [2] 为高胜率信号类型设置专门的捕获规则:")
    print("       - breakdown_short + position_low + volatility_high 系列")
    print("       - consecutive_bull + momentum_up_3pct 系列")
    print()
    print("   [3] 优化信号组件权重:")
    print("       - 提高'volatility_high'的权重")
    print("       - 提高'position_low'和'position_high'的识别灵敏度")
    print()
    print("   [4] 增加信号变体:")
    print("       - 对于高胜率信号，创建略微宽松的变体版本")
    print("       - 例如: 将'volatility_high'放宽到'volatility_medium_high'")
    print()

    print("=" * 140)

    cursor.close()
    conn.close()

if __name__ == '__main__':
    analyze_missed_signals()
