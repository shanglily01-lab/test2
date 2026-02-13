#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析Big4趋势信号的24小时变化时间线"""
import pymysql
import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv

# 设置Windows控制台编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST', '13.212.252.171'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER', 'app_user'),
    password=os.getenv('DB_PASSWORD', 'AppUser@2024#Secure'),
    database=os.getenv('DB_NAME', 'crypto_analyzer'),
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

cursor = conn.cursor()

# 查询最近24小时的Big4趋势（按时间正序）
now = datetime.now()
yesterday = now - timedelta(hours=24)

print('=' * 150)
print(f'Big4 趋势信号 24小时完整时间线')
print(f'时间范围: {yesterday.strftime("%Y-%m-%d %H:%M")} 至 {now.strftime("%Y-%m-%d %H:%M")}')
print('=' * 150)

cursor.execute('''
    SELECT
        created_at,
        overall_signal,
        signal_strength,
        bullish_count,
        bearish_count,
        btc_signal,
        btc_strength,
        eth_signal,
        eth_strength,
        bnb_signal,
        bnb_strength,
        sol_signal,
        sol_strength,
        recommendation
    FROM big4_trend_history
    WHERE created_at >= %s
    ORDER BY created_at ASC
''', (yesterday,))

records = cursor.fetchall()

if not records:
    print('\n❌ 没有找到记录')
    cursor.close()
    conn.close()
    exit(0)

print(f'\n共 {len(records)} 条记录\n')

# 币种权重
COIN_WEIGHTS = {
    'btc': 0.50,
    'eth': 0.30,
    'bnb': 0.10,
    'sol': 0.10
}

# 追踪趋势变化
last_signal = None
signal_changes = 0
signal_durations = {}  # 记录每种信号的持续时间
current_signal_start = None

print('时间线分析（按时间正序）:\n')
print(f'{"时间":<12} {"整体信号":<10} {"强度":<6} {"BTC":<8} {"ETH":<8} {"BNB":<8} {"SOL":<8} {"看涨权重":<10} {"说明"}')
print('-' * 150)

for i, row in enumerate(records):
    time_str = row['created_at'].strftime('%m-%d %H:%M')
    overall = row['overall_signal']
    strength = row['signal_strength']

    # 计算看涨/看跌权重
    bullish_weight = 0
    bearish_weight = 0

    for coin in ['btc', 'eth', 'bnb', 'sol']:
        signal = row[f'{coin}_signal']
        weight = COIN_WEIGHTS[coin]

        if signal and signal.upper() == 'BULLISH':
            bullish_weight += weight
        elif signal and signal.upper() == 'BEARISH':
            bearish_weight += weight

    # 按新逻辑重新计算应该是什么信号
    btc_signal = row['btc_signal']
    if btc_signal and btc_signal.upper() == 'BULLISH' and bullish_weight >= 0.50:
        correct_signal = 'BULLISH'
        reason = 'BTC领涨'
    elif btc_signal and btc_signal.upper() == 'BEARISH' and bearish_weight >= 0.50:
        correct_signal = 'BEARISH'
        reason = 'BTC领跌'
    elif bullish_weight - bearish_weight >= 0.20:
        correct_signal = 'BULLISH'
        reason = f'权重差{(bullish_weight-bearish_weight)*100:.0f}%'
    elif bearish_weight - bullish_weight >= 0.20:
        correct_signal = 'BEARISH'
        reason = f'权重差{(bearish_weight-bullish_weight)*100:.0f}%'
    else:
        correct_signal = 'NEUTRAL'
        reason = '权重差<20%'

    # 信号emoji
    if correct_signal == 'BULLISH':
        emoji = '🟢'
    elif correct_signal == 'BEARISH':
        emoji = '🔴'
    else:
        emoji = '⚪'

    # 检测信号变化
    change_marker = ''
    if last_signal is not None and last_signal != correct_signal:
        signal_changes += 1
        change_marker = ' ⚡️变化'

        # 记录上一个信号的持续时间
        if current_signal_start:
            duration = (row['created_at'] - current_signal_start).total_seconds() / 60
            if last_signal not in signal_durations:
                signal_durations[last_signal] = []
            signal_durations[last_signal].append(duration)

        current_signal_start = row['created_at']
    elif last_signal is None:
        current_signal_start = row['created_at']

    last_signal = correct_signal

    # 各币种信号
    btc_emoji = '🟢' if row['btc_signal'] and row['btc_signal'].upper() == 'BULLISH' else '🔴' if row['btc_signal'] and row['btc_signal'].upper() == 'BEARISH' else '⚪'
    eth_emoji = '🟢' if row['eth_signal'] and row['eth_signal'].upper() == 'BULLISH' else '🔴' if row['eth_signal'] and row['eth_signal'].upper() == 'BEARISH' else '⚪'
    bnb_emoji = '🟢' if row['bnb_signal'] and row['bnb_signal'].upper() == 'BULLISH' else '🔴' if row['bnb_signal'] and row['bnb_signal'].upper() == 'BEARISH' else '⚪'
    sol_emoji = '🟢' if row['sol_signal'] and row['sol_signal'].upper() == 'BULLISH' else '🔴' if row['sol_signal'] and row['sol_signal'].upper() == 'BEARISH' else '⚪'

    btc_str = f"{btc_emoji}{row['btc_strength']:.0f}"
    eth_str = f"{eth_emoji}{row['eth_strength']:.0f}"
    bnb_str = f"{bnb_emoji}{row['bnb_strength']:.0f}"
    sol_str = f"{sol_emoji}{row['sol_strength']:.0f}"

    weight_str = f"{bullish_weight*100:.0f}% vs {bearish_weight*100:.0f}%"

    # 原始信号vs正确信号
    original_marker = '❌' if overall.upper() != correct_signal else ''

    print(f'{time_str:<12} {emoji}{correct_signal:<9} {strength:<6.0f} {btc_str:<8} {eth_str:<8} {bnb_str:<8} {sol_str:<8} {weight_str:<10} {reason}{change_marker} {original_marker}')

# 记录最后一个信号的持续时间
if current_signal_start and last_signal:
    duration = (records[-1]['created_at'] - current_signal_start).total_seconds() / 60
    if last_signal not in signal_durations:
        signal_durations[last_signal] = []
    signal_durations[last_signal].append(duration)

print('\n' + '=' * 150)
print('统计分析:')
print('=' * 150)

# 信号分布统计
bullish_count = len([r for r in records if r['btc_signal'] and r['btc_signal'].upper() == 'BULLISH'])
bearish_count = len([r for r in records if r['btc_signal'] and r['btc_signal'].upper() == 'BEARISH'])

print(f'\n信号变化次数: {signal_changes} 次')
print(f'平均间隔: {len(records)/(signal_changes+1):.1f} 条记录')

print(f'\n各信号持续时间统计:')
for signal, durations in signal_durations.items():
    avg_duration = sum(durations) / len(durations) if durations else 0
    total_duration = sum(durations)
    print(f'  {signal}: 出现{len(durations)}次, 平均持续{avg_duration:.1f}分钟, 总计{total_duration:.0f}分钟')

# 分析趋势切换原因
print(f'\n趋势切换原因分析:')

# 检测BTC/ETH信号变化的频率
btc_changes = 0
eth_changes = 0
last_btc = None
last_eth = None

for row in records:
    btc = row['btc_signal']
    eth = row['eth_signal']

    if last_btc and btc != last_btc:
        btc_changes += 1
    if last_eth and eth != last_eth:
        eth_changes += 1

    last_btc = btc
    last_eth = eth

print(f'  BTC信号变化: {btc_changes} 次')
print(f'  ETH信号变化: {eth_changes} 次')

# 判断稳定性
if signal_changes > 10:
    print(f'\n⚠️  趋势切换过于频繁（{signal_changes}次），可能原因：')
    print(f'  1. 判断阈值过于敏感（当前20%）')
    print(f'  2. 市场处于震荡期，方向不明确')
    print(f'  3. 各币种信号不一致，权重经常在阈值附近波动')
    print(f'\n建议：')
    print(f'  - 提高权重差阈值（从20%提高到30%或更高）')
    print(f'  - 增加信号确认机制（连续N次同向信号才切换）')
    print(f'  - 考虑增加时间窗口过滤（防止短期波动）')
elif signal_changes < 3:
    print(f'\n✅ 趋势相对稳定（{signal_changes}次切换）')
else:
    print(f'\n📊 趋势切换适中（{signal_changes}次切换）')

cursor.close()
conn.close()
