#!/usr/bin/env python3
"""分析所有开仓信号和平仓方式的胜率"""
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

def analyze_signal_winrate():
    """分析信号胜率"""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    print("=" * 140)
    print("开仓信号与平仓方式胜率统计分析")
    print("=" * 140)
    print()

    # 1. 查询所有已平仓的持仓（最近7天）
    cursor.execute("""
        SELECT
            id, symbol, position_side,
            entry_signal_type,
            margin, realized_pnl, leverage,
            entry_score,
            notes,
            open_time, close_time,
            TIMESTAMPDIFF(MINUTE, open_time, close_time) as hold_minutes
        FROM futures_positions
        WHERE status = 'closed'
        AND account_id = 2
        AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
        ORDER BY close_time DESC
    """)

    positions = cursor.fetchall()

    if not positions:
        print("最近7天没有已平仓持仓")
        cursor.close()
        conn.close()
        return

    print(f"样本数据: 最近7天 {len(positions)} 个已平仓持仓")
    print()

    # 2. 按开仓信号类型统计
    print("=" * 140)
    print("【一】按开仓信号类型统计胜率")
    print("=" * 140)
    print()

    signal_stats = defaultdict(lambda: {
        'total': 0,
        'win': 0,
        'loss': 0,
        'total_pnl': 0,
        'win_pnl': 0,
        'loss_pnl': 0,
        'positions': []
    })

    for pos in positions:
        signal_type = pos['entry_signal_type'] or 'UNKNOWN'
        pnl = float(pos['realized_pnl']) if pos['realized_pnl'] else 0
        is_win = pnl > 0

        signal_stats[signal_type]['total'] += 1
        signal_stats[signal_type]['total_pnl'] += pnl
        signal_stats[signal_type]['positions'].append(pos)

        if is_win:
            signal_stats[signal_type]['win'] += 1
            signal_stats[signal_type]['win_pnl'] += pnl
        else:
            signal_stats[signal_type]['loss'] += 1
            signal_stats[signal_type]['loss_pnl'] += pnl

    # 按胜率排序
    sorted_signals = sorted(signal_stats.items(),
                           key=lambda x: x[1]['win'] / x[1]['total'] if x[1]['total'] > 0 else 0,
                           reverse=True)

    print(f"{'信号类型':<30} {'总数':<8} {'盈利':<8} {'亏损':<8} {'胜率':<10} {'总盈亏':<12} {'平均盈亏':<12} {'盈亏比':<10}")
    print("-" * 140)

    for signal_type, stats in sorted_signals:
        total = stats['total']
        win = stats['win']
        loss = stats['loss']
        win_rate = (win / total * 100) if total > 0 else 0
        total_pnl = stats['total_pnl']
        avg_pnl = total_pnl / total if total > 0 else 0

        # 计算盈亏比 (平均盈利/平均亏损)
        avg_win = stats['win_pnl'] / win if win > 0 else 0
        avg_loss = abs(stats['loss_pnl'] / loss) if loss > 0 else 0
        profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0

        pnl_sign = '+' if total_pnl >= 0 else ''
        avg_sign = '+' if avg_pnl >= 0 else ''

        print(f"{signal_type:<30} "
              f"{total:<8} "
              f"{win:<8} "
              f"{loss:<8} "
              f"{win_rate:>8.1f}% "
              f"{pnl_sign}{total_pnl:>10.2f} "
              f"{avg_sign}{avg_pnl:>10.2f} "
              f"1:{profit_loss_ratio:>7.2f}")

    print()

    # 3. 按平仓方式统计
    print("=" * 140)
    print("【二】按平仓方式统计胜率")
    print("=" * 140)
    print()

    # 对平仓原因进行分类
    def classify_close_reason(notes):
        """分类平仓原因"""
        if not notes:
            return "未知原因"

        notes_lower = notes.lower()

        # 止盈
        if '止盈' in notes or 'take_profit' in notes_lower:
            return "止盈"
        # 止损
        elif '止损' in notes or 'stop_loss' in notes_lower or 'hard_stop' in notes_lower:
            return "止损"
        # 反向信号
        elif 'reverse_signal' in notes_lower or '反向信号' in notes:
            return "反向信号平仓"
        # 超时强制平仓
        elif '超时强制平仓' in notes or '强制平仓' in notes:
            return "超时强制平仓"
        # 分阶段超时
        elif '分阶段超时' in notes:
            return "分阶段超时"
        # K线反转
        elif 'K线反转' in notes or 'k线反转' in notes:
            return "K线反转"
        # 强度超过阈值
        elif '强度超过阈值' in notes or '强度反转' in notes:
            return "强度反转"
        # 保证金不足
        elif '保证金不足' in notes:
            return "保证金不足"
        # 近似止盈
        elif '近似止盈' in notes:
            return "近似止盈"
        # 建仓清理
        elif 'manual_close_building' in notes_lower or '建仓' in notes:
            return "建仓清理"
        # 对冲平仓
        elif 'hedge' in notes_lower or '对冲' in notes:
            return "对冲平仓"
        # 其他
        else:
            return "其他原因"

    close_stats = defaultdict(lambda: {
        'total': 0,
        'win': 0,
        'loss': 0,
        'total_pnl': 0,
        'win_pnl': 0,
        'loss_pnl': 0,
        'avg_hold_minutes': []
    })

    for pos in positions:
        close_reason = classify_close_reason(pos['notes'])
        pnl = float(pos['realized_pnl']) if pos['realized_pnl'] else 0
        is_win = pnl > 0
        hold_minutes = pos['hold_minutes'] or 0

        close_stats[close_reason]['total'] += 1
        close_stats[close_reason]['total_pnl'] += pnl
        close_stats[close_reason]['avg_hold_minutes'].append(hold_minutes)

        if is_win:
            close_stats[close_reason]['win'] += 1
            close_stats[close_reason]['win_pnl'] += pnl
        else:
            close_stats[close_reason]['loss'] += 1
            close_stats[close_reason]['loss_pnl'] += pnl

    # 按数量排序
    sorted_closes = sorted(close_stats.items(),
                          key=lambda x: x[1]['total'],
                          reverse=True)

    print(f"{'平仓方式':<20} {'总数':<8} {'盈利':<8} {'亏损':<8} {'胜率':<10} {'总盈亏':<12} {'平均盈亏':<12} {'平均持仓':<12} {'盈亏比':<10}")
    print("-" * 140)

    for close_reason, stats in sorted_closes:
        total = stats['total']
        win = stats['win']
        loss = stats['loss']
        win_rate = (win / total * 100) if total > 0 else 0
        total_pnl = stats['total_pnl']
        avg_pnl = total_pnl / total if total > 0 else 0
        avg_hold = sum(stats['avg_hold_minutes']) / len(stats['avg_hold_minutes']) if stats['avg_hold_minutes'] else 0

        # 计算盈亏比
        avg_win = stats['win_pnl'] / win if win > 0 else 0
        avg_loss = abs(stats['loss_pnl'] / loss) if loss > 0 else 0
        profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0

        pnl_sign = '+' if total_pnl >= 0 else ''
        avg_sign = '+' if avg_pnl >= 0 else ''

        print(f"{close_reason:<20} "
              f"{total:<8} "
              f"{win:<8} "
              f"{loss:<8} "
              f"{win_rate:>8.1f}% "
              f"{pnl_sign}{total_pnl:>10.2f} "
              f"{avg_sign}{avg_pnl:>10.2f} "
              f"{avg_hold/60:>10.1f}H "
              f"1:{profit_loss_ratio:>7.2f}")

    print()

    # 4. 按方向统计
    print("=" * 140)
    print("【三】按持仓方向统计胜率")
    print("=" * 140)
    print()

    direction_stats = defaultdict(lambda: {
        'total': 0,
        'win': 0,
        'loss': 0,
        'total_pnl': 0
    })

    for pos in positions:
        direction = pos['position_side']
        pnl = float(pos['realized_pnl']) if pos['realized_pnl'] else 0
        is_win = pnl > 0

        direction_stats[direction]['total'] += 1
        direction_stats[direction]['total_pnl'] += pnl

        if is_win:
            direction_stats[direction]['win'] += 1
        else:
            direction_stats[direction]['loss'] += 1

    print(f"{'方向':<10} {'总数':<10} {'盈利':<10} {'亏损':<10} {'胜率':<12} {'总盈亏':<15}")
    print("-" * 70)

    for direction, stats in sorted(direction_stats.items()):
        total = stats['total']
        win = stats['win']
        loss = stats['loss']
        win_rate = (win / total * 100) if total > 0 else 0
        total_pnl = stats['total_pnl']

        pnl_sign = '+' if total_pnl >= 0 else ''

        print(f"{direction:<10} "
              f"{total:<10} "
              f"{win:<10} "
              f"{loss:<10} "
              f"{win_rate:>10.1f}% "
              f"{pnl_sign}{total_pnl:>13.2f}")

    print()

    # 5. 按评分区间统计
    print("=" * 140)
    print("【四】按入场评分区间统计胜率")
    print("=" * 140)
    print()

    score_ranges = [
        (0, 30, "0-30分(极低)"),
        (30, 40, "30-40分(低)"),
        (40, 50, "40-50分(中低)"),
        (50, 60, "50-60分(中)"),
        (60, 70, "60-70分(中高)"),
        (70, 80, "70-80分(高)"),
        (80, 100, "80-100分(极高)")
    ]

    score_stats = defaultdict(lambda: {
        'total': 0,
        'win': 0,
        'loss': 0,
        'total_pnl': 0
    })

    for pos in positions:
        score = pos['entry_score'] or 0
        pnl = float(pos['realized_pnl']) if pos['realized_pnl'] else 0
        is_win = pnl > 0

        # 找到对应的评分区间
        for min_score, max_score, label in score_ranges:
            if min_score <= score < max_score:
                score_stats[label]['total'] += 1
                score_stats[label]['total_pnl'] += pnl
                if is_win:
                    score_stats[label]['win'] += 1
                else:
                    score_stats[label]['loss'] += 1
                break

    print(f"{'评分区间':<20} {'总数':<10} {'盈利':<10} {'亏损':<10} {'胜率':<12} {'总盈亏':<15}")
    print("-" * 90)

    for _, _, label in score_ranges:
        if label in score_stats:
            stats = score_stats[label]
            total = stats['total']
            win = stats['win']
            loss = stats['loss']
            win_rate = (win / total * 100) if total > 0 else 0
            total_pnl = stats['total_pnl']

            pnl_sign = '+' if total_pnl >= 0 else ''

            print(f"{label:<20} "
                  f"{total:<10} "
                  f"{win:<10} "
                  f"{loss:<10} "
                  f"{win_rate:>10.1f}% "
                  f"{pnl_sign}{total_pnl:>13.2f}")

    print()

    # 6. 总体统计
    print("=" * 140)
    print("【五】总体统计")
    print("=" * 140)
    print()

    total_positions = len(positions)
    total_win = sum(1 for p in positions if (float(p['realized_pnl']) if p['realized_pnl'] else 0) > 0)
    total_loss = total_positions - total_win
    overall_win_rate = (total_win / total_positions * 100) if total_positions > 0 else 0
    total_pnl = sum(float(p['realized_pnl']) if p['realized_pnl'] else 0 for p in positions)
    avg_pnl = total_pnl / total_positions if total_positions > 0 else 0

    # 计算总盈亏比
    win_positions = [p for p in positions if (float(p['realized_pnl']) if p['realized_pnl'] else 0) > 0]
    loss_positions = [p for p in positions if (float(p['realized_pnl']) if p['realized_pnl'] else 0) <= 0]

    total_win_pnl = sum(float(p['realized_pnl']) for p in win_positions if p['realized_pnl'])
    total_loss_pnl = sum(abs(float(p['realized_pnl'])) for p in loss_positions if p['realized_pnl'])

    avg_win_pnl = total_win_pnl / len(win_positions) if win_positions else 0
    avg_loss_pnl = total_loss_pnl / len(loss_positions) if loss_positions else 0
    overall_profit_loss_ratio = avg_win_pnl / avg_loss_pnl if avg_loss_pnl > 0 else 0

    print(f"样本周期: 最近7天")
    print(f"总持仓数: {total_positions}")
    print(f"盈利单数: {total_win}")
    print(f"亏损单数: {total_loss}")
    print(f"总体胜率: {overall_win_rate:.2f}%")
    print(f"总盈亏: {'+'if total_pnl >= 0 else ''}{total_pnl:.2f} USDT")
    print(f"平均盈亏: {'+'if avg_pnl >= 0 else ''}{avg_pnl:.2f} USDT")
    print(f"平均盈利: +{avg_win_pnl:.2f} USDT")
    print(f"平均亏损: -{avg_loss_pnl:.2f} USDT")
    print(f"盈亏比: 1:{overall_profit_loss_ratio:.2f}")

    print()
    print("=" * 140)

    cursor.close()
    conn.close()

if __name__ == '__main__':
    analyze_signal_winrate()
