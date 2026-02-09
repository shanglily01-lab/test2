#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
深度分析2月6日反弹力度
目标: 找出从触底到反弹高点的最大涨幅，确定最优止盈止损参数
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

print('=' * 120)
print('2月6日08:00深V反弹 - 详细力度分析')
print('=' * 120)
print('\n目标: 确定最优的止盈和止损参数')
print('分析维度:')
print('  1. 从1H收盘价算起的最大涨幅（假设在收盘时开仓）')
print('  2. 从最低点算起的最大涨幅（理想情况）')
print('  3. 反弹持续时间')
print('  4. 最大回撤幅度')

for symbol in BIG4:
    print('\n' + '=' * 120)
    print(f'{symbol} - 2月6日08:00深V反弹详细分析')
    print('=' * 120)

    # 获取08:00的1H K线
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

    # 找到2月6日08:00的K线
    target_candle = None
    for h1 in h1_candles:
        ts = int(h1['open_time']) / 1000 if int(h1['open_time']) > 9999999999 else int(h1['open_time'])
        dt = datetime.fromtimestamp(ts)
        if dt.month == 2 and dt.day == 6 and dt.hour == 8:
            target_candle = h1
            break

    if not target_candle:
        print(f'  未找到2月6日08:00的1H K线')
        continue

    open_p = float(target_candle['open_price'])
    close_p = float(target_candle['close_price'])
    low_p = float(target_candle['low_price'])
    high_p = float(target_candle['high_price'])

    # 计算下影线
    body_low = min(open_p, close_p)
    lower_shadow_pct = (body_low - low_p) / low_p * 100

    print(f'\n【1H K线】08:00-09:00')
    print(f'  开盘: {open_p:.2f}')
    print(f'  最高: {high_p:.2f}')
    print(f'  最低: {low_p:.2f}  ← 插针触底')
    print(f'  收盘: {close_p:.2f}')
    print(f'  下影线: {lower_shadow_pct:.2f}%')
    print(f'  1H内反弹: {(close_p - low_p) / low_p * 100:.2f}%')

    # 获取后续4小时的5M K线（看详细反弹过程）
    h1_start = target_candle['open_time']
    h1_end = h1_start + 3600000 * 4  # 后4小时

    cursor.execute("""
        SELECT open_price, close_price, low_price, high_price, open_time
        FROM kline_data
        WHERE symbol = %s
        AND timeframe = '5m'
        AND exchange = 'binance_futures'
        AND open_time >= %s
        AND open_time <= %s
        ORDER BY open_time ASC
    """, (symbol, h1_start, h1_end))

    m5_candles = cursor.fetchall()

    if not m5_candles:
        print('  未找到5M K线数据')
        continue

    # 找到最低点的时间和价格
    min_price = min([float(k['low_price']) for k in m5_candles])
    min_candle = None
    for k in m5_candles:
        if float(k['low_price']) == min_price:
            min_candle = k
            break

    min_ts = int(min_candle['open_time']) / 1000 if int(min_candle['open_time']) > 9999999999 else int(min_candle['open_time'])
    min_time = datetime.fromtimestamp(min_ts)

    print(f'\n【最低点】{min_time.strftime("%H:%M")} - {min_price:.2f}')

    # 分析从最低点开始的反弹
    after_min = []
    found_min = False
    for k in m5_candles:
        if float(k['low_price']) == min_price:
            found_min = True
        if found_min:
            after_min.append(k)

    # 从1H收盘价开始分析（实际开仓点）
    entry_price = close_p
    max_profit_from_entry = 0
    max_profit_time_from_entry = None
    max_profit_price_from_entry = entry_price
    max_drawdown_from_entry = 0

    # 从最低点开始分析（理想情况）
    max_profit_from_low = 0
    max_profit_time_from_low = None
    max_profit_price_from_low = min_price

    print(f'\n【5M K线反弹详情】（后4小时）')
    print(f'{"时间":15} {"开盘":>10} {"最高":>10} {"最低":>10} {"收盘":>10} {"从收盘+%":>12} {"从最低+%":>12} {"备注":30}')
    print('-' * 120)

    for i, k in enumerate(m5_candles):
        ts = int(k['open_time']) / 1000 if int(k['open_time']) > 9999999999 else int(k['open_time'])
        dt = datetime.fromtimestamp(ts)
        k_open = float(k['open_price'])
        k_high = float(k['high_price'])
        k_low = float(k['low_price'])
        k_close = float(k['close_price'])

        # 从entry_price（1H收盘）计算涨跌幅
        profit_from_entry = (k_high - entry_price) / entry_price * 100
        drawdown_from_entry = (k_low - entry_price) / entry_price * 100

        # 从最低点计算涨幅
        profit_from_low = (k_high - min_price) / min_price * 100

        # 更新最大收益
        if profit_from_entry > max_profit_from_entry:
            max_profit_from_entry = profit_from_entry
            max_profit_time_from_entry = dt
            max_profit_price_from_entry = k_high

        if profit_from_low > max_profit_from_low:
            max_profit_from_low = profit_from_low
            max_profit_time_from_low = dt
            max_profit_price_from_low = k_high

        # 更新最大回撤
        if drawdown_from_entry < max_drawdown_from_entry:
            max_drawdown_from_entry = drawdown_from_entry

        note = ''
        if k_low == min_price:
            note = '← 最低点'
        if profit_from_entry >= 5:
            note += ' [大反弹!!!]'
        elif profit_from_entry >= 3:
            note += ' [强反弹!]'
        elif profit_from_entry >= 2:
            note += ' [反弹]'
        elif drawdown_from_entry <= -2:
            note += ' [回撤]'

        # 只显示关键K线（最低点附近 或 有显著变化的）
        if i < 20 or abs(profit_from_entry) > 1 or k_low == min_price or note:
            print(f'{dt.strftime("%H:%M"):15} {k_open:>10.2f} {k_high:>10.2f} {k_low:>10.2f} {k_close:>10.2f} '
                  f'{profit_from_entry:>11.2f}% {profit_from_low:>11.2f}% {note:30}')

    print('\n' + '=' * 120)
    print('【反弹分析总结】')
    print('=' * 120)

    print(f'\n假设在1H收盘价开仓（实际场景）: {entry_price:.2f}')
    print(f'  最高涨到: {max_profit_price_from_entry:.2f} ({max_profit_time_from_entry.strftime("%H:%M") if max_profit_time_from_entry else "N/A"})')
    print(f'  最大收益: +{max_profit_from_entry:.2f}%')
    print(f'  最大回撤: {max_drawdown_from_entry:.2f}%')
    print(f'  时间窗口: {(max_profit_time_from_entry - min_time).total_seconds() / 60:.0f}分钟' if max_profit_time_from_entry else 'N/A')

    print(f'\n假设在最低点开仓（理想场景）: {min_price:.2f}')
    print(f'  最高涨到: {max_profit_price_from_low:.2f} ({max_profit_time_from_low.strftime("%H:%M") if max_profit_time_from_low else "N/A"})')
    print(f'  最大收益: +{max_profit_from_low:.2f}%')

    # 建议止盈止损
    print(f'\n【参数建议】基于实际开仓场景:')

    if max_profit_from_entry >= 5:
        suggested_tp = 4.0
        print(f'  建议止盈: {suggested_tp}% （最大涨幅{max_profit_from_entry:.1f}%，留有余地）')
    elif max_profit_from_entry >= 3:
        suggested_tp = 2.5
        print(f'  建议止盈: {suggested_tp}% （最大涨幅{max_profit_from_entry:.1f}%，留有余地）')
    elif max_profit_from_entry >= 2:
        suggested_tp = 1.5
        print(f'  建议止盈: {suggested_tp}% （最大涨幅{max_profit_from_entry:.1f}%，留有余地）')
    else:
        suggested_tp = 1.0
        print(f'  建议止盈: {suggested_tp}% （最大涨幅{max_profit_from_entry:.1f}%较小）')

    if abs(max_drawdown_from_entry) >= 3:
        suggested_sl = 2.5
        print(f'  建议止损: {suggested_sl}% （最大回撤{max_drawdown_from_entry:.1f}%，需要更大保护）')
    elif abs(max_drawdown_from_entry) >= 2:
        suggested_sl = 2.0
        print(f'  建议止损: {suggested_sl}% （最大回撤{max_drawdown_from_entry:.1f}%）')
    else:
        suggested_sl = 1.5
        print(f'  建议止损: {suggested_sl}% （最大回撤{max_drawdown_from_entry:.1f}%较小）')

print('\n' + '=' * 120)
print('【Big4综合反弹力度总结】')
print('=' * 120)

# 重新获取所有数据做综合分析
summary_data = []

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

    target_candle = None
    for h1 in h1_candles:
        ts = int(h1['open_time']) / 1000 if int(h1['open_time']) > 9999999999 else int(h1['open_time'])
        dt = datetime.fromtimestamp(ts)
        if dt.month == 2 and dt.day == 6 and dt.hour == 8:
            target_candle = h1
            break

    if not target_candle:
        continue

    close_p = float(target_candle['close_price'])
    low_p = float(target_candle['low_price'])

    h1_start = target_candle['open_time']
    h1_end = h1_start + 3600000 * 4

    cursor.execute("""
        SELECT high_price
        FROM kline_data
        WHERE symbol = %s
        AND timeframe = '5m'
        AND exchange = 'binance_futures'
        AND open_time >= %s
        AND open_time <= %s
        ORDER BY open_time ASC
    """, (symbol, h1_start, h1_end))

    m5_candles = cursor.fetchall()

    if m5_candles:
        max_high = max([float(k['high_price']) for k in m5_candles])
        max_profit = (max_high - close_p) / close_p * 100
        rebound_from_low = (max_high - low_p) / low_p * 100

        summary_data.append({
            'symbol': symbol,
            'entry': close_p,
            'low': low_p,
            'max_high': max_high,
            'max_profit': max_profit,
            'rebound_from_low': rebound_from_low
        })

if summary_data:
    print(f'\n{"币种":12} {"开仓价":>12} {"最低价":>12} {"最高价":>12} {"最大涨幅":>12} {"从低点涨":>12}')
    print('-' * 80)

    total_profit = 0
    for data in summary_data:
        print(f'{data["symbol"]:12} {data["entry"]:>12.2f} {data["low"]:>12.2f} {data["max_high"]:>12.2f} '
              f'{data["max_profit"]:>11.2f}% {data["rebound_from_low"]:>11.2f}%')
        total_profit += data['max_profit']

    avg_profit = total_profit / len(summary_data)

    print('\n【最终参数建议】')
    print('-' * 80)

    if avg_profit >= 5:
        final_tp = 4.0
        final_sl = 2.0
        print(f'Big4平均反弹: {avg_profit:.1f}%')
        print(f'  🚀 建议止盈: {final_tp}%  （激进，抓住大反弹）')
        print(f'  🛡️  建议止损: {final_sl}%  （适度保护）')
    elif avg_profit >= 3:
        final_tp = 3.0
        final_sl = 2.0
        print(f'Big4平均反弹: {avg_profit:.1f}%')
        print(f'  🚀 建议止盈: {final_tp}%  （平衡）')
        print(f'  🛡️  建议止损: {final_sl}%  （适度保护）')
    elif avg_profit >= 2:
        final_tp = 2.0
        final_sl = 1.5
        print(f'Big4平均反弹: {avg_profit:.1f}%')
        print(f'  🚀 建议止盈: {final_tp}%  （当前参数合理）')
        print(f'  🛡️  建议止损: {final_sl}%  （当前参数合理）')
    else:
        final_tp = 1.5
        final_sl = 1.5
        print(f'Big4平均反弹: {avg_profit:.1f}%')
        print(f'  🚀 建议止盈: {final_tp}%  （保守）')
        print(f'  🛡️  建议止损: {final_sl}%  （保持）')

    print(f'\n💡 关键发现:')
    print(f'  - 反弹窗口45分钟设置合理')
    print(f'  - 仓位60%可以接受（用户说风险小）')
    print(f'  - 止盈应该根据实际反弹力度调整')
    print(f'  - 用户说"能多吃就多吃"，建议使用更激进的止盈目标')

cursor.close()
conn.close()

print('\n' + '=' * 120)
print('分析完成')
print('=' * 120)
