#!/usr/bin/env python3
"""
分析交易对的方向性波动特征
重点分析:
1. 向上波动幅度 vs 向下波动幅度
2. 多单应该设置多少止损/止盈
3. 空单应该设置多少止损/止盈
4. 不同方向的风险差异
"""
import mysql.connector
from collections import defaultdict
from datetime import datetime, timedelta
import statistics

DB_CONFIG = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

# 重点关注的经常止损的交易对
FOCUS_SYMBOLS = [
    'AXS/USDT', 'NOM/USDT', 'RIVER/USDT', 'ZRO/USDT', 'ROSE/USDT',
    'SOMI/USDT', 'PIPPIN/USDT', 'DUSK/USDT', 'LPT/USDT', 'ENSO/USDT',
    'FARTCOIN/USDT', 'SHELL/USDT', 'FLUID/USDT', 'DEEP/USDT', 'MOVE/USDT'
]

def analyze_directional_volatility():
    """分析方向性波动"""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    print(f"{'='*140}")
    print(f"交易对方向性波动分析 (基于最近7天1小时K线)")
    print(f"{'='*140}\n")

    results = []

    for symbol in FOCUS_SYMBOLS:
        # 查询最近7天的1小时K线数据
        cursor.execute("""
            SELECT
                open_time,
                open_price, high_price, low_price, close_price
            FROM futures_klines
            WHERE symbol = %s
            AND timeframe = '1h'
            AND open_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            ORDER BY open_time ASC
        """, (symbol,))

        klines = cursor.fetchall()

        if not klines or len(klines) < 24:
            continue

        # 分析每根K线的方向性波动
        upside_moves = []    # 向上波动: (high - open) / open
        downside_moves = []  # 向下波动: (open - low) / open

        # 分析实体方向
        bullish_body = []    # 阳线实体: (close - open) / open
        bearish_body = []    # 阴线实体: (open - close) / open

        # 分析上下影线
        upper_wicks = []     # 上影线: (high - max(open, close))
        lower_wicks = []     # 下影线: (min(open, close) - low)

        for k in klines:
            open_p = float(k['open_price'])
            high_p = float(k['high_price'])
            low_p = float(k['low_price'])
            close_p = float(k['close_price'])

            # 向上波动 (从开盘到最高点)
            upside_pct = (high_p - open_p) / open_p * 100
            upside_moves.append(upside_pct)

            # 向下波动 (从开盘到最低点)
            downside_pct = (open_p - low_p) / open_p * 100
            downside_moves.append(downside_pct)

            # 实体方向
            if close_p > open_p:
                bullish_body.append((close_p - open_p) / open_p * 100)
            else:
                bearish_body.append((open_p - close_p) / open_p * 100)

            # 上下影线
            upper_wick = (high_p - max(open_p, close_p)) / open_p * 100
            lower_wick = (min(open_p, close_p) - low_p) / open_p * 100
            upper_wicks.append(upper_wick)
            lower_wicks.append(lower_wick)

        # 计算统计指标
        avg_upside = statistics.mean(upside_moves)
        max_upside = max(upside_moves)
        p95_upside = statistics.quantiles(upside_moves, n=20)[18]  # 95分位数
        p75_upside = statistics.quantiles(upside_moves, n=4)[2]    # 75分位数

        avg_downside = statistics.mean(downside_moves)
        max_downside = max(downside_moves)
        p95_downside = statistics.quantiles(downside_moves, n=20)[18]
        p75_downside = statistics.quantiles(downside_moves, n=4)[2]

        avg_upper_wick = statistics.mean(upper_wicks)
        avg_lower_wick = statistics.mean(lower_wicks)

        # 方向偏好 (正值表示向上偏好,负值表示向下偏好)
        directional_bias = avg_upside - avg_downside

        # 计算建议的止损/止盈距离
        # 多单: 风险是向下波动,收益是向上波动
        long_sl = max(p75_downside * 1.3, avg_downside * 1.5)  # 止损要覆盖75%的向下波动
        long_tp = min(p75_upside * 0.8, avg_upside * 2.0)      # 止盈目标设在可达成范围内

        # 空单: 风险是向上波动,收益是向下波动
        short_sl = max(p75_upside * 1.3, avg_upside * 1.5)
        short_tp = min(p75_downside * 0.8, avg_downside * 2.0)

        # 计算盈亏比
        long_rr = long_tp / long_sl if long_sl > 0 else 0
        short_rr = short_tp / short_sl if short_sl > 0 else 0

        results.append({
            'symbol': symbol,
            'avg_upside': avg_upside,
            'max_upside': max_upside,
            'p95_upside': p95_upside,
            'p75_upside': p75_upside,
            'avg_downside': avg_downside,
            'max_downside': max_downside,
            'p95_downside': p95_downside,
            'p75_downside': p75_downside,
            'directional_bias': directional_bias,
            'avg_upper_wick': avg_upper_wick,
            'avg_lower_wick': avg_lower_wick,
            'long_sl': long_sl,
            'long_tp': long_tp,
            'long_rr': long_rr,
            'short_sl': short_sl,
            'short_tp': short_tp,
            'short_rr': short_rr,
            'kline_count': len(klines)
        })

    # 按平均波动排序
    results.sort(key=lambda x: (x['avg_upside'] + x['avg_downside']) / 2, reverse=True)

    # 输出结果
    print(f"=== 方向性波动统计 ===\n")
    print(f"{'交易对':<15} {'向上均值':<10} {'向上75%':<10} {'向上最大':<10} {'向下均值':<10} {'向下75%':<10} {'向下最大':<10} {'方向偏好':<10}")
    print(f"{'-'*110}")

    for r in results:
        bias_str = f"+{r['directional_bias']:.2f}%" if r['directional_bias'] > 0 else f"{r['directional_bias']:.2f}%"
        bias_label = "向上" if r['directional_bias'] > 0.3 else "向下" if r['directional_bias'] < -0.3 else "中性"

        print(f"{r['symbol']:<15} "
              f"{r['avg_upside']:>8.2f}% "
              f"{r['p75_upside']:>8.2f}% "
              f"{r['max_upside']:>8.2f}% "
              f"{r['avg_downside']:>8.2f}% "
              f"{r['p75_downside']:>8.2f}% "
              f"{r['max_downside']:>8.2f}% "
              f"{bias_str:>8s} ({bias_label})")

    print("\n")

    # 详细的止损止盈建议
    print(f"=== 多单(LONG)止损止盈建议 ===\n")
    print(f"{'交易对':<15} {'建议止损':<10} {'建议止盈':<10} {'盈亏比':<10} {'当前SL':<10} {'当前TP':<10} {'评价':<20}")
    print(f"{'-'*100}")

    for r in results:
        current_sl = 2.0
        current_tp = 5.0

        sl_safety = "安全" if r['long_sl'] <= current_sl * 1.1 else f"过紧{r['long_sl'] - current_sl:.1f}%"

        print(f"{r['symbol']:<15} "
              f"{r['long_sl']:>8.2f}% "
              f"{r['long_tp']:>8.2f}% "
              f"1:{r['long_rr']:>6.2f} "
              f"{current_sl:>8.2f}% "
              f"{current_tp:>8.2f}% "
              f"{sl_safety:<20}")

    print("\n")

    print(f"=== 空单(SHORT)止损止盈建议 ===\n")
    print(f"{'交易对':<15} {'建议止损':<10} {'建议止盈':<10} {'盈亏比':<10} {'当前SL':<10} {'当前TP':<10} {'评价':<20}")
    print(f"{'-'*100}")

    for r in results:
        current_sl = 2.0
        current_tp = 5.0

        sl_safety = "安全" if r['short_sl'] <= current_sl * 1.1 else f"过紧{r['short_sl'] - current_sl:.1f}%"

        print(f"{r['symbol']:<15} "
              f"{r['short_sl']:>8.2f}% "
              f"{r['short_tp']:>8.2f}% "
              f"1:{r['short_rr']:>6.2f} "
              f"{current_sl:>8.2f}% "
              f"{current_tp:>8.2f}% "
              f"{sl_safety:<20}")

    print("\n")

    # 分析止损单的实际情况
    print(f"=== 24小时止损单方向分析 ===\n")

    cursor.execute("""
        SELECT
            symbol, position_side,
            COUNT(*) as count,
            AVG(realized_pnl) as avg_pnl
        FROM futures_positions
        WHERE status = 'closed'
        AND account_id = 2
        AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
        AND (notes LIKE '%止损%' OR notes LIKE '%stop_loss%')
        GROUP BY symbol, position_side
        ORDER BY count DESC
    """)

    stop_loss_stats = cursor.fetchall()

    print(f"{'交易对':<15} {'方向':<6} {'次数':<6} {'平均亏损':<12} {'建议':<50}")
    print(f"{'-'*100}")

    for stat in stop_loss_stats:
        symbol = stat['symbol']
        side = stat['position_side']
        count = stat['count']
        avg_pnl = stat['avg_pnl'] or 0

        # 找到对应的分析结果
        r = next((x for x in results if x['symbol'] == symbol), None)

        if r:
            if side == 'LONG':
                suggested = f"SL={r['long_sl']:.1f}% TP={r['long_tp']:.1f}% (盈亏比1:{r['long_rr']:.1f})"
                if r['directional_bias'] < -0.5:
                    suggested += " [注意:此币向下波动大]"
            else:  # SHORT
                suggested = f"SL={r['short_sl']:.1f}% TP={r['short_tp']:.1f}% (盈亏比1:{r['short_rr']:.1f})"
                if r['directional_bias'] > 0.5:
                    suggested += " [注意:此币向上波动大]"
        else:
            suggested = "无数据"

        print(f"{symbol:<15} {side:<6} {count:<6} ${avg_pnl:>9.2f}   {suggested:<50}")

    print("\n")

    # 总结
    print(f"=== 核心发现 ===\n")

    # 计算多空止损的平均值
    avg_long_sl = statistics.mean([r['long_sl'] for r in results])
    avg_short_sl = statistics.mean([r['short_sl'] for r in results])

    print(f"1. 当前固定设置: 所有方向都是 SL=2.00%, TP=5.00%")
    print(f"2. 数据分析结果:")
    print(f"   - 多单建议平均: SL={avg_long_sl:.2f}%, TP={statistics.mean([r['long_tp'] for r in results]):.2f}%")
    print(f"   - 空单建议平均: SL={avg_short_sl:.2f}%, TP={statistics.mean([r['short_tp'] for r in results]):.2f}%")
    print(f"3. 问题:")
    print(f"   - 2%的固定止损对多单来说过紧 {avg_long_sl - 2.0:.2f}%")
    print(f"   - 2%的固定止损对空单来说过紧 {avg_short_sl - 2.0:.2f}%")
    print(f"4. 方向性差异:")

    up_biased = [r for r in results if r['directional_bias'] > 0.5]
    down_biased = [r for r in results if r['directional_bias'] < -0.5]

    if up_biased:
        print(f"   - 向上偏好币种({len(up_biased)}个): 做多相对安全,做空需要更大止损")
        for r in up_biased[:3]:
            print(f"     * {r['symbol']}: 向上{r['avg_upside']:.2f}% vs 向下{r['avg_downside']:.2f}%")

    if down_biased:
        print(f"   - 向下偏好币种({len(down_biased)}个): 做空相对安全,做多需要更大止损")
        for r in down_biased[:3]:
            print(f"     * {r['symbol']}: 向上{r['avg_upside']:.2f}% vs 向下{r['avg_downside']:.2f}%")

    print(f"\n5. 实施建议:")
    print(f"   - 根据开仓方向(LONG/SHORT)和交易对,动态计算止损止盈")
    print(f"   - 止损距离应覆盖75%分位数的反向波动 + 30%安全边际")
    print(f"   - 止盈目标设在75%分位数的同向波动的80%处 (可达成)")

    cursor.close()
    conn.close()

if __name__ == '__main__':
    analyze_directional_volatility()
