#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析为什么最近没有做多信号
"""

import pymysql
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'),
    charset='utf8mb4'
)

cursor = conn.cursor(pymysql.cursors.DictCursor)

# 查看最近12小时的信号生成情况
time_12h_ago = datetime.now() - timedelta(hours=12)

# 检查是否有LONG方向的EMA信号
cursor.execute("""
    SELECT
        symbol,
        signal_type,
        signal_strength,
        timestamp,
        price,
        ema_distance_pct,
        volume_ratio,
        price_change_pct
    FROM ema_signals
    WHERE timestamp >= %s
    AND signal_type = 'LONG'
    ORDER BY timestamp DESC
    LIMIT 30
""", (time_12h_ago,))

long_ema_signals = cursor.fetchall()

print("="*100)
print(f"EMA LONG Signals (Last 12H): {len(long_ema_signals)}")
print("="*100)

if long_ema_signals:
    print(f"{'Symbol':<12} {'Strength':<15} {'Time':<20} {'Price':<12} {'EMA Dist%':<12} {'Vol Ratio':<12}")
    print("-"*100)
    for sig in long_ema_signals[:20]:
        print(f"{sig['symbol']:<12} {sig['signal_strength']:<15} "
              f"{sig['timestamp'].strftime('%m-%d %H:%M'):<20} "
              f"{float(sig['price']):<12.4f} {float(sig['ema_distance_pct'] or 0):<12.2f} "
              f"{float(sig['volume_ratio'] or 0):<12.2f}")
else:
    print("NO LONG EMA signals found!")

# 检查SHORT信号数量
cursor.execute("""
    SELECT COUNT(*) as count
    FROM ema_signals
    WHERE timestamp >= %s
    AND signal_type = 'SHORT'
""", (time_12h_ago,))

short_count = cursor.fetchone()['count']
print(f"\nEMA SHORT Signals: {short_count}")

# 检查K线数据 - 看看有没有上涨趋势
print("\n" + "="*100)
print("Recent Kline Trends (1H, Last 24H)")
print("="*100)

cursor.execute("""
    SELECT
        symbol,
        COUNT(*) as total,
        SUM(CASE WHEN close_price > open_price THEN 1 ELSE 0 END) as bullish_count,
        SUM(CASE WHEN close_price < open_price THEN 1 ELSE 0 END) as bearish_count
    FROM kline_data
    WHERE timeframe = '1h'
    AND timestamp >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
    GROUP BY symbol
    HAVING bullish_count > 15  -- 24根K线中超过15根阳线
    ORDER BY bullish_count DESC
    LIMIT 20
""")

bullish_symbols = cursor.fetchall()

if bullish_symbols:
    print(f"{'Symbol':<12} {'Total':<8} {'Bullish':<10} {'Bearish':<10} {'Bullish%':<10}")
    print("-"*60)
    for s in bullish_symbols:
        bullish_pct = s['bullish_count'] / s['total'] * 100
        print(f"{s['symbol']:<12} {s['total']:<8} {s['bullish_count']:<10} {s['bearish_count']:<10} {bullish_pct:<10.1f}")

    print(f"\nFound {len(bullish_symbols)} symbols with strong uptrend (>62.5% bullish)")
    print("But NO LONG positions opened - signals may be filtered out!")
else:
    print("No symbols with strong bullish trend")

# 检查这些上涨币种的价格位置
print("\n" + "="*100)
print("Price Position Analysis (for bullish symbols)")
print("="*100)

if bullish_symbols:
    for sym in bullish_symbols[:10]:
        symbol = sym['symbol']

        # 获取24小时价格范围
        cursor.execute("""
            SELECT
                MIN(low_price) as low_24h,
                MAX(high_price) as high_24h,
                (SELECT close_price FROM kline_data
                 WHERE symbol = %s AND timeframe = '1h'
                 ORDER BY timestamp DESC LIMIT 1) as current
            FROM kline_data
            WHERE symbol = %s
            AND timeframe = '1h'
            AND timestamp >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
        """, (symbol, symbol))

        price_info = cursor.fetchone()
        if price_info and price_info['current']:
            low = float(price_info['low_24h'])
            high = float(price_info['high_24h'])
            current = float(price_info['current'])

            position_pct = (current - low) / (high - low) * 100 if high > low else 50

            print(f"{symbol:<12} Current: {current:<10.4f} Low: {low:<10.4f} High: {high:<10.4f} Position: {position_pct:.1f}%")

            if position_pct > 70:
                print(f"  -> 价格在高位({position_pct:.1f}%),做多信号可能被position_high规则拒绝!")

cursor.close()
conn.close()

print("\n" + "="*100)
print("Conclusion:")
print("="*100)
print("如果有上涨币种但没有做多信号,可能的原因:")
print("1. EMA信号生成器没有识别到LONG信号(EMA交叉不符合)")
print("2. 价格已经在高位(position_high),被代码规则拒绝")
print("3. 信号评分低于35分阈值,没有达到开仓条件")
print("4. 信号组件包含空头信号,被信号一致性检查拒绝")
