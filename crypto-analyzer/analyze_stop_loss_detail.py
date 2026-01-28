#!/usr/bin/env python3
"""
深度分析止损单的入场时机和止损止盈设置合理性
重点分析:
1. 不同交易对的振幅特征
2. 止损/止盈设置是否匹配交易对特性
3. 入场时机的价格位置分析
4. 持仓时长与价格波动的关系
"""
import mysql.connector
from collections import Counter, defaultdict
from datetime import datetime, timedelta

DB_CONFIG = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def analyze_stop_loss_detail():
    """深度分析止损单"""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    # 查询24小时内所有止损平仓的订单(包含更多字段)
    # 注意: futures_positions表中没有close_price字段,需要通过平仓价格计算
    cursor.execute("""
        SELECT
            p.id, p.symbol, p.position_side,
            p.margin, p.quantity, p.leverage,
            p.entry_price, p.avg_entry_price,
            p.entry_score, p.entry_signal_type,
            p.open_time, p.close_time,
            p.realized_pnl,
            p.stop_loss_price, p.take_profit_price,
            p.notes
        FROM futures_positions p
        WHERE p.status = 'closed'
        AND p.account_id = 2
        AND p.close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
        AND (p.notes LIKE '%止损%' OR p.notes LIKE '%stop_loss%')
        ORDER BY p.close_time DESC
    """)

    stop_loss_positions = cursor.fetchall()

    if not stop_loss_positions:
        print("过去24小时内没有止损单")
        cursor.close()
        conn.close()
        return

    print(f"{'='*100}")
    print(f"止损单深度分析 (共{len(stop_loss_positions)}单)")
    print(f"{'='*100}\n")

    # 按交易对分组分析
    symbol_analysis = defaultdict(lambda: {
        'positions': [],
        'total_pnl': 0,
        'count': 0,
        'avg_hold_minutes': 0,
        'price_movements': []  # 价格变动百分比
    })

    for pos in stop_loss_positions:
        symbol = pos['symbol']
        hold_minutes = (pos['close_time'] - pos['open_time']).total_seconds() / 60

        # 计算价格变动百分比
        # 使用avg_entry_price如果存在,否则使用entry_price
        entry = float(pos['avg_entry_price']) if pos['avg_entry_price'] else float(pos['entry_price'])

        # 从realized_pnl反推close_price (考虑杠杆和手续费的影响)
        # realized_pnl = (close_price - entry_price) * quantity (多单)
        # realized_pnl = (entry_price - close_price) * quantity (空单)
        # 但realized_pnl已经包含手续费,所以这个方法不够精确
        # 我们使用stop_loss_price作为近似的close_price(因为是止损单)

        stop_loss = float(pos['stop_loss_price']) if pos['stop_loss_price'] else 0
        take_profit = float(pos['take_profit_price']) if pos['take_profit_price'] else 0

        # 对于止损单,close_price应该接近stop_loss_price
        close = stop_loss if stop_loss > 0 else entry

        if pos['position_side'] == 'LONG':
            price_change_pct = (close - entry) / entry * 100 if close != entry else 0
            stop_loss_dist_pct = (stop_loss - entry) / entry * 100 if stop_loss > 0 else 0
            take_profit_dist_pct = (take_profit - entry) / entry * 100 if take_profit > 0 else 0
        else:  # SHORT
            price_change_pct = (entry - close) / entry * 100 if close != entry else 0
            stop_loss_dist_pct = (entry - stop_loss) / entry * 100 if stop_loss > 0 else 0
            take_profit_dist_pct = (entry - take_profit) / entry * 100 if take_profit > 0 else 0

        symbol_analysis[symbol]['positions'].append({
            'id': pos['id'],
            'side': pos['position_side'],
            'entry_price': entry,
            'stop_loss_price': stop_loss,
            'take_profit_price': take_profit,
            'price_change_pct': price_change_pct,
            'stop_loss_dist_pct': stop_loss_dist_pct,
            'take_profit_dist_pct': take_profit_dist_pct,
            'hold_minutes': hold_minutes,
            'pnl': pos['realized_pnl'] or 0,
            'roi': (pos['realized_pnl'] / pos['margin'] * 100) if pos['margin'] else 0,
            'entry_score': pos['entry_score'],
            'leverage': pos['leverage']
        })

        symbol_analysis[symbol]['count'] += 1
        symbol_analysis[symbol]['total_pnl'] += pos['realized_pnl'] or 0
        symbol_analysis[symbol]['price_movements'].append(abs(price_change_pct))

    # 计算每个交易对的平均持仓时长和振幅
    for symbol, data in symbol_analysis.items():
        data['avg_hold_minutes'] = sum(p['hold_minutes'] for p in data['positions']) / data['count']
        data['avg_price_movement'] = sum(data['price_movements']) / len(data['price_movements'])
        data['max_price_movement'] = max(data['price_movements'])

    # 按止损次数排序
    sorted_symbols = sorted(symbol_analysis.items(), key=lambda x: x[1]['count'], reverse=True)

    print(f"=== 按交易对分析止损特征 ===\n")
    print(f"{'交易对':<15} {'次数':<6} {'亏损':<12} {'平均持仓':<12} {'平均振幅':<12} {'最大振幅':<12}")
    print(f"{'-'*80}")

    for symbol, data in sorted_symbols[:15]:
        print(f"{symbol:<15} {data['count']:<6} ${data['total_pnl']:>9.2f} "
              f"{data['avg_hold_minutes']:>9.0f}分钟  "
              f"{data['avg_price_movement']:>9.2f}%   "
              f"{data['max_price_movement']:>9.2f}%")

    print("\n")

    # 详细分析前5个问题交易对
    print(f"=== 详细分析前5个问题交易对 ===\n")

    for symbol, data in sorted_symbols[:5]:
        print(f"{'='*100}")
        print(f"{symbol} - {data['count']}次止损, 总亏损${data['total_pnl']:.2f}")
        print(f"{'='*100}")

        # 统计止损距离和盈利目标距离
        stop_losses = [p['stop_loss_dist_pct'] for p in data['positions'] if p['stop_loss_dist_pct'] != 0]
        take_profits = [p['take_profit_dist_pct'] for p in data['positions'] if p['take_profit_dist_pct'] != 0]

        if stop_losses:
            avg_sl = sum(stop_losses) / len(stop_losses)
            print(f"平均止损距离: {avg_sl:.2f}%")

        if take_profits:
            avg_tp = sum(take_profits) / len(take_profits)
            print(f"平均止盈距离: {avg_tp:.2f}%")

        print(f"平均价格变动: {data['avg_price_movement']:.2f}%")
        print(f"最大价格变动: {data['max_price_movement']:.2f}%")

        # 分析止损距离是否合理
        if stop_losses and data['avg_price_movement'] > abs(avg_sl) * 0.8:
            print(f"警告  问题: 平均振幅({data['avg_price_movement']:.2f}%)接近或超过止损距离({abs(avg_sl):.2f}%), 止损过紧!")

        print(f"\n详细订单:")
        print(f"{'ID':<8} {'方向':<6} {'持仓':<8} {'价格变动':<10} {'止损距离':<10} {'止盈距离':<10} {'ROI':<10} {'评分':<6}")
        print(f"{'-'*90}")

        for p in sorted(data['positions'], key=lambda x: x['hold_minutes']):
            print(f"{p['id']:<8} {p['side']:<6} {p['hold_minutes']:>6.0f}分 "
                  f"{p['price_change_pct']:>8.2f}%  "
                  f"{p['stop_loss_dist_pct']:>8.2f}%  "
                  f"{p['take_profit_dist_pct']:>8.2f}%  "
                  f"{p['roi']:>8.2f}%  "
                  f"{p['entry_score']:>4}")

        print("\n")

    # 分析止损距离分布
    print(f"=== 止损距离分布分析 ===\n")

    all_stop_loss_distances = []
    all_take_profit_distances = []
    all_price_movements = []

    for symbol, data in symbol_analysis.items():
        for p in data['positions']:
            if p['stop_loss_dist_pct'] != 0:
                all_stop_loss_distances.append(abs(p['stop_loss_dist_pct']))
            if p['take_profit_dist_pct'] != 0:
                all_take_profit_distances.append(abs(p['take_profit_dist_pct']))
            all_price_movements.append(abs(p['price_change_pct']))

    if all_stop_loss_distances:
        avg_sl_all = sum(all_stop_loss_distances) / len(all_stop_loss_distances)
        min_sl = min(all_stop_loss_distances)
        max_sl = max(all_stop_loss_distances)
        print(f"全部止损距离: 平均{avg_sl_all:.2f}%, 最小{min_sl:.2f}%, 最大{max_sl:.2f}%")

    if all_take_profit_distances:
        avg_tp_all = sum(all_take_profit_distances) / len(all_take_profit_distances)
        min_tp = min(all_take_profit_distances)
        max_tp = max(all_take_profit_distances)
        print(f"全部止盈距离: 平均{avg_tp_all:.2f}%, 最小{min_tp:.2f}%, 最大{max_tp:.2f}%")

    if all_price_movements:
        avg_move_all = sum(all_price_movements) / len(all_price_movements)
        print(f"全部价格变动: 平均{avg_move_all:.2f}%")

    # 分析盈亏比
    if all_stop_loss_distances and all_take_profit_distances:
        risk_reward_ratio = avg_tp_all / avg_sl_all
        print(f"\n当前盈亏比: 1:{risk_reward_ratio:.2f}")
        print(f"价格变动/止损距离比: {avg_move_all / avg_sl_all:.2f}")

        if avg_move_all > avg_sl_all * 0.7:
            print(f"\n警告  严重问题: 平均价格波动({avg_move_all:.2f}%)接近止损距离({avg_sl_all:.2f}%)")
            print(f"    这意味着正常的市场波动就能触发止损!")
            print(f"    建议: 根据不同交易对的波动率动态调整止损距离")

    print("\n")

    # 分析快速止损(2小时内)
    quick_stops = [p for symbol_data in symbol_analysis.values()
                   for p in symbol_data['positions'] if p['hold_minutes'] < 120]

    if quick_stops:
        print(f"=== 2小时内快速止损分析 ({len(quick_stops)}单) ===\n")

        avg_quick_move = sum(abs(p['price_change_pct']) for p in quick_stops) / len(quick_stops)
        avg_quick_sl = sum(abs(p['stop_loss_dist_pct']) for p in quick_stops if p['stop_loss_dist_pct'] != 0)
        avg_quick_sl = avg_quick_sl / len([p for p in quick_stops if p['stop_loss_dist_pct'] != 0]) if avg_quick_sl else 0

        print(f"快速止损平均价格变动: {avg_quick_move:.2f}%")
        print(f"快速止损平均止损距离: {avg_quick_sl:.2f}%")

        if avg_quick_sl > 0 and avg_quick_move / avg_quick_sl > 0.9:
            print(f"警告  快速止损问题: 价格波动几乎达到止损线,止损设置可能过于激进")

    print("\n")

    # 总结建议
    print(f"=== 优化建议 ===\n")
    print(f"1. 动态止损距离:")
    print(f"   - 高波动交易对(振幅>5%): 建议止损距离至少为平均振幅的1.5倍")
    print(f"   - 中等波动交易对(振幅2-5%): 建议止损距离为平均振幅的1.2倍")
    print(f"   - 低波动交易对(振幅<2%): 可以使用固定止损距离")
    print(f"\n2. 针对问题交易对:")
    for symbol, data in sorted_symbols[:5]:
        recommended_sl = data['avg_price_movement'] * 1.5
        print(f"   - {symbol}: 当前平均振幅{data['avg_price_movement']:.2f}%, 建议止损距离≥{recommended_sl:.2f}%")

    print(f"\n3. 入场时机优化:")
    print(f"   - volatility_high信号的持仓应该:")
    print(f"     * 降低杠杆倍数")
    print(f"     * 增加止损距离")
    print(f"     * 或者提高最低入场评分要求")

    cursor.close()
    conn.close()

if __name__ == '__main__':
    analyze_stop_loss_detail()
