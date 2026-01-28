#!/usr/bin/env python3
"""
分析不同交易对的真实振幅特征
查询最近7天的K线数据,计算每个交易对的:
1. 平均振幅 (高低价差/开盘价)
2. ATR (Average True Range)
3. 最大单日振幅
4. 建议的动态止损距离
"""
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

# 重点关注的经常止损的交易对
FOCUS_SYMBOLS = [
    'AXS/USDT', 'NOM/USDT', 'RIVER/USDT', 'ZRO/USDT', 'ROSE/USDT',
    'SOMI/USDT', 'PIPPIN/USDT', 'DUSK/USDT', 'LPT/USDT', 'ENSO/USDT',
    'FARTCOIN/USDT', 'SHELL/USDT', 'FLUID/USDT', 'DEEP/USDT', 'MOVE/USDT'
]

def analyze_symbol_volatility():
    """分析交易对的真实波动率"""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    print(f"{'='*120}")
    print(f"交易对波动率分析 (基于最近7天1小时K线数据)")
    print(f"{'='*120}\n")

    results = []

    for symbol in FOCUS_SYMBOLS:
        # 查询最近7天的1小时K线数据
        cursor.execute("""
            SELECT
                open_time, close_time,
                open_price, high_price, low_price, close_price,
                volume
            FROM futures_klines
            WHERE symbol = %s
            AND timeframe = '1h'
            AND open_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            ORDER BY open_time DESC
            LIMIT 168
        """, (symbol,))

        klines = cursor.fetchall()

        if not klines or len(klines) < 24:
            print(f"{symbol}: 数据不足 (只有{len(klines)}根K线)")
            continue

        # 计算振幅指标
        amplitudes = []  # (high - low) / open
        body_pcts = []   # abs(close - open) / open
        true_ranges = [] # ATR计算用

        prev_close = None

        for k in reversed(klines):  # 从旧到新计算
            open_p = float(k['open_price'])
            high_p = float(k['high_price'])
            low_p = float(k['low_price'])
            close_p = float(k['close_price'])

            # 振幅百分比
            amplitude = (high_p - low_p) / open_p * 100
            amplitudes.append(amplitude)

            # 实体百分比
            body_pct = abs(close_p - open_p) / open_p * 100
            body_pcts.append(body_pct)

            # True Range (用于ATR计算)
            if prev_close is not None:
                tr = max(
                    high_p - low_p,
                    abs(high_p - prev_close),
                    abs(low_p - prev_close)
                )
                true_ranges.append(tr / open_p * 100)

            prev_close = close_p

        # 计算统计指标
        avg_amplitude = sum(amplitudes) / len(amplitudes)
        max_amplitude = max(amplitudes)
        min_amplitude = min(amplitudes)

        avg_body = sum(body_pcts) / len(body_pcts)

        if true_ranges:
            atr = sum(true_ranges) / len(true_ranges)
        else:
            atr = avg_amplitude

        # 计算最近24小时的波动率(更短期)
        recent_amplitudes = amplitudes[-24:] if len(amplitudes) >= 24 else amplitudes
        recent_avg = sum(recent_amplitudes) / len(recent_amplitudes)

        # 建议的止损距离 (基于ATR的1.5倍,或平均振幅的1.2倍,取较大值)
        suggested_sl_1 = atr * 1.5
        suggested_sl_2 = avg_amplitude * 1.2
        suggested_sl = max(suggested_sl_1, suggested_sl_2)

        # 建议的止盈距离 (保持1:2.5的盈亏比)
        suggested_tp = suggested_sl * 2.5

        results.append({
            'symbol': symbol,
            'avg_amplitude': avg_amplitude,
            'recent_avg': recent_avg,
            'max_amplitude': max_amplitude,
            'atr': atr,
            'avg_body': avg_body,
            'suggested_sl': suggested_sl,
            'suggested_tp': suggested_tp,
            'kline_count': len(klines)
        })

    # 按平均振幅排序
    results.sort(key=lambda x: x['avg_amplitude'], reverse=True)

    # 输出结果
    print(f"{'交易对':<15} {'7日均振幅':<12} {'24h振幅':<12} {'ATR':<10} {'最大振幅':<10} {'建议SL':<10} {'建议TP':<10} {'当前SL':<10}")
    print(f"{'-'*115}")

    for r in results:
        current_sl = 2.0  # 当前固定止损2%
        sl_diff = r['suggested_sl'] - current_sl

        line = (f"{r['symbol']:<15} "
                f"{r['avg_amplitude']:>10.2f}% "
                f"{r['recent_avg']:>10.2f}% "
                f"{r['atr']:>8.2f}% "
                f"{r['max_amplitude']:>8.2f}% "
                f"{r['suggested_sl']:>8.2f}% "
                f"{r['suggested_tp']:>8.2f}% "
                f"{current_sl:>8.2f}%")

        if sl_diff > 1.0:
            line += f"  << 止损过紧! (差{sl_diff:.2f}%)"

        print(line)

    print("\n")

    # 分类建议
    print(f"=== 按波动率分类建议 ===\n")

    high_vol = [r for r in results if r['avg_amplitude'] >= 5.0]
    mid_vol = [r for r in results if 2.0 <= r['avg_amplitude'] < 5.0]
    low_vol = [r for r in results if r['avg_amplitude'] < 2.0]

    if high_vol:
        print(f"高波动交易对 (振幅≥5%):")
        for r in high_vol:
            print(f"  {r['symbol']:<15} 振幅{r['avg_amplitude']:>5.2f}% | 建议: SL={r['suggested_sl']:.1f}%, TP={r['suggested_tp']:.1f}%")
        print()

    if mid_vol:
        print(f"中等波动交易对 (振幅2-5%):")
        for r in mid_vol:
            print(f"  {r['symbol']:<15} 振幅{r['avg_amplitude']:>5.2f}% | 建议: SL={r['suggested_sl']:.1f}%, TP={r['suggested_tp']:.1f}%")
        print()

    if low_vol:
        print(f"低波动交易对 (振幅<2%):")
        for r in low_vol:
            print(f"  {r['symbol']:<15} 振幅{r['avg_amplitude']:>5.2f}% | 建议: SL={r['suggested_sl']:.1f}%, TP={r['suggested_tp']:.1f}%")
        print()

    # 计算平均建议
    avg_suggested_sl = sum(r['suggested_sl'] for r in results) / len(results)
    avg_suggested_tp = sum(r['suggested_tp'] for r in results) / len(results)

    print(f"=== 总体建议 ===\n")
    print(f"当前固定设置: SL=2.00%, TP=5.00%")
    print(f"建议平均设置: SL={avg_suggested_sl:.2f}%, TP={avg_suggested_tp:.2f}%")
    print(f"\n问题分析:")
    print(f"1. 当前2%的固定止损对于大部分交易对来说过于激进")
    print(f"2. 高波动交易对(如振幅>5%的)需要至少{avg_suggested_sl*1.5:.1f}%的止损空间")
    print(f"3. 建议实施动态止损策略:")
    print(f"   - 开仓时根据交易对的历史ATR计算止损距离")
    print(f"   - 止损距离 = max(ATR * 1.5, 平均振幅 * 1.2)")
    print(f"   - 止盈距离 = 止损距离 * 2.5 (保持盈亏比)")

    cursor.close()
    conn.close()

if __name__ == '__main__':
    analyze_symbol_volatility()
