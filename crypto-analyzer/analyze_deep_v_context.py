#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析深V反转的大周期背景
目标: 区分真正的深V反转 vs 震荡市的假信号
"""
import pymysql
import os
import sys
from dotenv import load_dotenv
from datetime import datetime, timedelta

# Fix Windows encoding
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4'
}

conn = pymysql.connect(**db_config)
cursor = conn.cursor(pymysql.cursors.DictCursor)

BIG4 = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']

LOWER_SHADOW_THRESHOLD = 3.0  # 下影线阈值

print('=' * 120)
print('深V反转 vs 震荡假信号 - 大周期背景分析')
print('=' * 120)
print('\n目标: 找出真正深V反转的特征，过滤震荡市假信号')
print('\n分析维度:')
print('  1. 前3天趋势 (72根1H K线)')
print('  2. 持续下跌幅度')
print('  3. 是否首次触底')
print('  4. 下跌加速度')

for symbol in BIG4:
    print('\n' + '=' * 120)
    print(f'{symbol} - 寻找长下影线并分析大周期背景')
    print('=' * 120)

    # 获取最近100根1H K线
    cursor.execute("""
        SELECT open_price, close_price, low_price, high_price, open_time
        FROM kline_data
        WHERE symbol = %s
        AND timeframe = '1h'
        AND exchange = 'binance_futures'
        ORDER BY open_time DESC
        LIMIT 100
    """, (symbol,))

    h1_candles = cursor.fetchall()

    detected = []

    for i, h1 in enumerate(h1_candles):
        ts = int(h1['open_time']) / 1000 if int(h1['open_time']) > 9999999999 else int(h1['open_time'])
        dt = datetime.fromtimestamp(ts)

        # 只看2月5-7日
        if dt.month != 2 or dt.day < 5 or dt.day > 7:
            continue

        open_p = float(h1['open_price'])
        close_p = float(h1['close_price'])
        high_p = float(h1['high_price'])
        low_p = float(h1['low_price'])

        # 计算下影线
        body_low = min(open_p, close_p)
        lower_shadow_pct = (body_low - low_p) / low_p * 100

        if lower_shadow_pct >= LOWER_SHADOW_THRESHOLD:
            # 找到长下影线，分析前72小时 (3天)
            previous_72h = []
            for j in range(i, min(i + 72, len(h1_candles))):
                previous_72h.append(h1_candles[j])

            if len(previous_72h) < 24:  # 至少需要24小时数据
                continue

            # 计算前3天的趋势
            prices_72h = [float(k['close_price']) for k in previous_72h]
            high_72h = max([float(k['high_price']) for k in previous_72h])
            low_72h = min([float(k['low_price']) for k in previous_72h])

            # 从最高点到当前的跌幅
            drop_from_high_72h = (low_p - high_72h) / high_72h * 100

            # 最近24小时的跌幅
            prices_24h = prices_72h[:24]
            high_24h = max([float(k['high_price']) for k in previous_72h[:24]])
            drop_from_high_24h = (low_p - high_24h) / high_24h * 100

            # 计算趋势方向 (线性回归斜率简化版)
            # 用前24小时和前72小时的价格变化判断
            trend_24h = (prices_24h[0] - prices_24h[-1]) / prices_24h[-1] * 100  # 最近24H变化
            trend_72h = (prices_72h[0] - prices_72h[-1]) / prices_72h[-1] * 100  # 前72H变化

            # 判断是否持续下跌
            is_sustained_drop = drop_from_high_72h <= -8  # 3天内从高点跌超8%
            is_accelerating = drop_from_high_24h <= -4    # 最近24H加速下跌超4%

            # 判断是否首次触底 (前24H内没有其他长下影线)
            is_first_bottom = True
            for j in range(1, min(24, i)):
                prev_candle = h1_candles[i + j]
                prev_open = float(prev_candle['open_price'])
                prev_close = float(prev_candle['close_price'])
                prev_low = float(prev_candle['low_price'])
                prev_body_low = min(prev_open, prev_close)
                prev_shadow = (prev_body_low - prev_low) / prev_low * 100 if prev_low > 0 else 0

                if prev_shadow >= LOWER_SHADOW_THRESHOLD:
                    is_first_bottom = False
                    break

            detected.append({
                'time': dt,
                'shadow': lower_shadow_pct,
                'low': low_p,
                'drop_72h': drop_from_high_72h,
                'drop_24h': drop_from_high_24h,
                'trend_24h': trend_24h,
                'trend_72h': trend_72h,
                'sustained_drop': is_sustained_drop,
                'accelerating': is_accelerating,
                'first_bottom': is_first_bottom
            })

    if detected:
        print(f'\n检测到 {len(detected)} 个长下影线:')
        print(f'\n{"时间":20} {"下影%":>8} {"72H跌幅":>10} {"24H跌幅":>10} {"持续下跌":>10} {"加速下跌":>10} {"首次触底":>10} {"判断":15}')
        print('-' * 120)

        for d in detected:
            sustained = '✅' if d['sustained_drop'] else '❌'
            accelerating = '✅' if d['accelerating'] else '❌'
            first_bottom = '✅' if d['first_bottom'] else '❌'

            # 综合判断: 真深V反转需要同时满足
            is_true_deep_v = d['sustained_drop'] and d['accelerating'] and d['first_bottom']
            judgment = '🚀 真深V反转!' if is_true_deep_v else '⚠️ 震荡假信号'

            print(f'{d["time"].strftime("%Y-%m-%d %H:%M"):20} {d["shadow"]:>7.1f}% '
                  f'{d["drop_72h"]:>9.1f}% {d["drop_24h"]:>9.1f}% '
                  f'{sustained:>10} {accelerating:>10} {first_bottom:>10} {judgment:15}')

print('\n' + '=' * 120)
print('【综合分析】真深V反转的特征')
print('=' * 120)

print('\n✅ 真深V反转必须同时满足:')
print('  1. 持续下跌: 72H从高点跌超8%')
print('  2. 加速下跌: 24H从高点跌超4%')
print('  3. 首次触底: 24H内首次出现长下影线')
print('  4. 长下影线: 单根1H下影线>3%')

print('\n❌ 震荡假信号特征:')
print('  - 前72H没有大幅下跌 (跌幅<8%)')
print('  - 频繁出现小下影线 (不是首次触底)')
print('  - 无明显加速下跌')

print('\n💡 建议的过滤逻辑:')
print('  if (72H跌幅 <= -8%) and (24H跌幅 <= -4%) and (首次触底):')
print('      触发反弹交易')
print('  else:')
print('      跳过 (可能是震荡假信号)')

# 统计2月5-7日的真假信号比例
print('\n' + '=' * 120)
print('【2月5-7日统计】')
print('=' * 120)

true_signals = 0
false_signals = 0

for symbol in BIG4:
    cursor.execute("""
        SELECT open_price, close_price, low_price, high_price, open_time
        FROM kline_data
        WHERE symbol = %s
        AND timeframe = '1h'
        AND exchange = 'binance_futures'
        ORDER BY open_time DESC
        LIMIT 100
    """, (symbol,))

    h1_candles = cursor.fetchall()

    for i, h1 in enumerate(h1_candles):
        ts = int(h1['open_time']) / 1000 if int(h1['open_time']) > 9999999999 else int(h1['open_time'])
        dt = datetime.fromtimestamp(ts)

        if dt.month != 2 or dt.day < 5 or dt.day > 7:
            continue

        open_p = float(h1['open_price'])
        close_p = float(h1['close_price'])
        low_p = float(h1['low_price'])

        body_low = min(open_p, close_p)
        lower_shadow_pct = (body_low - low_p) / low_p * 100

        if lower_shadow_pct >= LOWER_SHADOW_THRESHOLD:
            previous_72h = []
            for j in range(i, min(i + 72, len(h1_candles))):
                previous_72h.append(h1_candles[j])

            if len(previous_72h) < 24:
                continue

            high_72h = max([float(k['high_price']) for k in previous_72h])
            high_24h = max([float(k['high_price']) for k in previous_72h[:24]])

            drop_from_high_72h = (low_p - high_72h) / high_72h * 100
            drop_from_high_24h = (low_p - high_24h) / high_24h * 100

            is_sustained_drop = drop_from_high_72h <= -8
            is_accelerating = drop_from_high_24h <= -4

            is_first_bottom = True
            for j in range(1, min(24, i)):
                prev_candle = h1_candles[i + j]
                prev_open = float(prev_candle['open_price'])
                prev_close = float(prev_candle['close_price'])
                prev_low = float(prev_candle['low_price'])
                prev_body_low = min(prev_open, prev_close)
                prev_shadow = (prev_body_low - prev_low) / prev_low * 100 if prev_low > 0 else 0

                if prev_shadow >= LOWER_SHADOW_THRESHOLD:
                    is_first_bottom = False
                    break

            is_true_deep_v = is_sustained_drop and is_accelerating and is_first_bottom

            if is_true_deep_v:
                true_signals += 1
            else:
                false_signals += 1

print(f'\n真深V反转信号: {true_signals}')
print(f'震荡假信号: {false_signals}')
if (true_signals + false_signals) > 0:
    print(f'信号质量: {true_signals}/{true_signals + false_signals} = {true_signals / (true_signals + false_signals) * 100:.1f}%')
else:
    print('信号质量: 无数据 (检查时间戳格式)')

print('\n💡 结论:')
print('  - 添加大周期过滤后，可以有效减少震荡市假信号')
print('  - 保留真正的深V反转机会')
print('  - 提高反弹交易的成功率')

cursor.close()
conn.close()

print('\n' + '=' * 120)
print('分析完成')
print('=' * 120)
