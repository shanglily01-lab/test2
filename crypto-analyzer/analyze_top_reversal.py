#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析见顶反转（上影线）做空机会
对称于深V反弹策略
"""
import pymysql
import os
import sys
from dotenv import load_dotenv
from datetime import datetime

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

UPPER_SHADOW_THRESHOLD = 3.0  # 上影线阈值

print('=' * 120)
print('见顶反转分析 - 寻找1H长上影线（对称于深V反弹）')
print('=' * 120)
print('\n目标: 找出大涨后的见顶回调机会，做空获利')
print('\n对称逻辑:')
print('  深V反转: 大跌后插针触底 -> 做多反弹')
print('  见顶反转: 大涨后插针触顶 -> 做空回调')

for symbol in BIG4:
    print('\n' + '=' * 120)
    print(f'{symbol} - 寻找长上影线')
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

        # 只看最近7天
        if (datetime.now() - dt).days > 7:
            continue

        open_p = float(h1['open_price'])
        close_p = float(h1['close_price'])
        high_p = float(h1['high_price'])
        low_p = float(h1['low_price'])

        # 计算上影线长度 = (最高点 - 实体顶部) / 实体顶部
        body_high = max(open_p, close_p)
        upper_shadow_pct = (high_p - body_high) / body_high * 100 if body_high > 0 else 0

        if upper_shadow_pct >= UPPER_SHADOW_THRESHOLD:
            # 获取前72H历史数据
            history_72h = []
            for j in range(i, min(i + 72, len(h1_candles))):
                history_72h.append(h1_candles[j])

            if len(history_72h) < 24:
                continue

            # 计算前72H和24H的最低点
            low_72h = min([float(k['low_price']) for k in history_72h])
            low_24h = min([float(k['low_price']) for k in history_72h[:24]])

            # 从低点到当前高点的涨幅
            rise_from_low_72h = (high_p - low_72h) / low_72h * 100
            rise_from_low_24h = (high_p - low_24h) / low_24h * 100

            # 判断条件 (对称于深V):
            # 1. 72H持续上涨 >= 8%
            # 2. 24H加速上涨 >= 4%
            # 3. 首次触顶 (24H内首次出现长上影线)
            is_sustained_rise = rise_from_low_72h >= 8.0
            is_accelerating = rise_from_low_24h >= 4.0

            # 检查24H内是否首次触顶
            is_first_top = True
            for k in history_72h[:24]:
                if k['open_time'] == h1['open_time']:
                    continue
                k_open = float(k['open_price'])
                k_close = float(k['close_price'])
                k_high = float(k['high_price'])
                k_body_high = max(k_open, k_close)
                k_shadow = (k_high - k_body_high) / k_body_high * 100 if k_body_high > 0 else 0
                if k_shadow >= UPPER_SHADOW_THRESHOLD:
                    is_first_top = False
                    break

            # 综合判断
            is_true_top_reversal = is_sustained_rise and is_accelerating and is_first_top

            detected.append({
                'time': dt,
                'shadow': upper_shadow_pct,
                'high': high_p,
                'close': close_p,
                'rise_72h': rise_from_low_72h,
                'rise_24h': rise_from_low_24h,
                'sustained_rise': is_sustained_rise,
                'accelerating': is_accelerating,
                'first_top': is_first_top,
                'is_true': is_true_top_reversal
            })

    if detected:
        print(f'\n检测到 {len(detected)} 个长上影线:')
        print(f'\n{"时间":20} {"上影%":>8} {"72H涨幅":>10} {"24H涨幅":>10} {"持续上涨":>10} {"加速上涨":>10} {"首次触顶":>10} {"判断":15}')
        print('-' * 120)

        for d in detected:
            sustained = '✅' if d['sustained_rise'] else '❌'
            accelerating = '✅' if d['accelerating'] else '❌'
            first_top = '✅' if d['first_top'] else '❌'
            judgment = '🔻 真见顶反转!' if d['is_true'] else '⚠️ 震荡假信号'

            print(f'{d["time"].strftime("%Y-%m-%d %H:%M"):20} {d["shadow"]:>7.1f}% '
                  f'{d["rise_72h"]:>9.1f}% {d["rise_24h"]:>9.1f}% '
                  f'{sustained:>10} {accelerating:>10} {first_top:>10} {judgment:15}')

            # 如果是真见顶，分析后续回调幅度
            if d['is_true']:
                h1_time = d['time']
                h1_open_time = None

                # 找到对应的K线
                for k in h1_candles:
                    k_ts = int(k['open_time']) / 1000 if int(k['open_time']) > 9999999999 else int(k['open_time'])
                    k_dt = datetime.fromtimestamp(k_ts)
                    if k_dt == h1_time:
                        h1_open_time = k['open_time']
                        break

                if h1_open_time:
                    # 查看后续4小时的5M K线，分析回调
                    cursor.execute("""
                        SELECT open_price, close_price, low_price, high_price, open_time
                        FROM kline_data
                        WHERE symbol = %s
                        AND timeframe = '5m'
                        AND exchange = 'binance_futures'
                        AND open_time >= %s
                        AND open_time <= %s
                        ORDER BY open_time ASC
                    """, (symbol, h1_open_time, h1_open_time + 3600000 * 4))

                    m5_candles = cursor.fetchall()

                    if m5_candles:
                        entry_price = d['close']  # 1H收盘价开仓
                        min_low = min([float(k['low_price']) for k in m5_candles])
                        max_drop = (min_low - entry_price) / entry_price * 100

                        print(f'\n  后续回调分析:')
                        print(f'    开仓价(1H收盘): {entry_price:.2f}')
                        print(f'    最低回落到: {min_low:.2f}')
                        print(f'    最大回调: {max_drop:.2f}%')

                        if max_drop <= -3.5:
                            print(f'    结果: ✅ 做空成功! 达到3.5%止盈')
                        elif max_drop <= -2.0:
                            print(f'    结果: ✅ 做空盈利 (未达3.5%)')
                        elif max_drop <= -1.0:
                            print(f'    结果: ⚠️ 小幅盈利')
                        else:
                            print(f'    结果: ❌ 回调不足')

print('\n' + '=' * 120)
print('【见顶反转策略总结】')
print('=' * 120)

print('\n对称逻辑:')
print('  ✅ 深V反弹(做多): 72H跌>8% + 24H跌>4% + 首次触底 + 长下影线>3%')
print('  ✅ 见顶回调(做空): 72H涨>8% + 24H涨>4% + 首次触顶 + 长上影线>3%')

print('\n参数对称:')
print('  反弹交易(LONG):')
print('    - 仓位: 70%')
print('    - 止盈: 3.5%')
print('    - 止损: 2.5%')
print('    - 窗口: 45分钟')
print('\n  回调交易(SHORT):')
print('    - 仓位: 70% (对称)')
print('    - 止盈: 3.5% (对称)')
print('    - 止损: 2.5% (对称)')
print('    - 窗口: 45分钟 (对称)')

print('\n💡 建议:')
print('  1. 如果见顶反转数据支持，可以开启做空反弹策略')
print('  2. 使用相同的过滤条件和参数')
print('  3. 同时运行两个策略：做多深V + 做空见顶')

cursor.close()
conn.close()

print('\n' + '=' * 120)
print('分析完成')
print('=' * 120)
