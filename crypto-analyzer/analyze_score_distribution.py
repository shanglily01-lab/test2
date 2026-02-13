#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析实际交易中的分数分布"""
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
    database='crypto_analyzer',  # 强制使用crypto_analyzer数据库
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

cursor = conn.cursor()

print('=' * 120)
print('最近7天交易信号分数分布分析')
print('=' * 120)

# 查询signal_history表（记录了所有信号）
cursor.execute('''
    SELECT
        signal_type,
        position_side,
        score,
        symbol,
        created_at
    FROM signal_history
    WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
      AND score IS NOT NULL
    ORDER BY created_at DESC
    LIMIT 1000
''')

signals = cursor.fetchall()

if not signals:
    print('\n❌ 没有找到信号记录')
    cursor.close()
    conn.close()
    exit(0)

print(f'\n📊 最近7天共 {len(signals)} 条信号记录\n')

# 统计分数分布
score_ranges = {
    '35-49分': (35, 49),
    '50-69分': (50, 69),
    '70-89分': (70, 89),
    '90-109分': (90, 109),
    '110-129分': (110, 129),
    '130-149分': (130, 149),
    '150+分': (150, 999),
}

long_dist = {k: 0 for k in score_ranges.keys()}
short_dist = {k: 0 for k in score_ranges.keys()}

max_score = 0
min_score = 999

for sig in signals:
    score = sig['score']
    side = sig['position_side']

    max_score = max(max_score, score)
    min_score = min(min_score, score)

    for range_name, (low, high) in score_ranges.items():
        if low <= score <= high:
            if side == 'LONG':
                long_dist[range_name] += 1
            else:
                short_dist[range_name] += 1
            break

print('分数分布统计:\n')
print(f'{"分数段":<15} {"LONG信号数":<15} {"SHORT信号数":<15} {"总计"}')
print('-' * 120)

for range_name in score_ranges.keys():
    long_count = long_dist[range_name]
    short_count = short_dist[range_name]
    total = long_count + short_count

    long_pct = long_count / len(signals) * 100 if signals else 0
    short_pct = short_count / len(signals) * 100 if signals else 0
    total_pct = total / len(signals) * 100 if signals else 0

    print(f'{range_name:<15} {long_count:>5} ({long_pct:>5.1f}%)   {short_count:>5} ({short_pct:>5.1f}%)   {total:>5} ({total_pct:>5.1f}%)')

print(f'\n分数范围: {min_score} - {max_score} 分')

# 显示高分信号样本
print('\n' + '=' * 120)
print('高分信号样本（>100分）')
print('=' * 120)

high_score_signals = [s for s in signals if s['score'] > 100]
if high_score_signals:
    print(f'\n共 {len(high_score_signals)} 个高分信号（占比{len(high_score_signals)/len(signals)*100:.1f}%）\n')

    for i, sig in enumerate(high_score_signals[:10], 1):
        time_str = sig['created_at'].strftime('%m-%d %H:%M')
        side_emoji = '🟢' if sig['position_side'] == 'LONG' else '🔴'
        print(f"{i}. {side_emoji} {sig['symbol']:12} {sig['position_side']:5} | 分数:{sig['score']:>3} | {time_str}")

    if len(high_score_signals) > 10:
        print(f'\n... 还有 {len(high_score_signals) - 10} 个高分信号未显示')
else:
    print('\n✅ 没有超过100分的信号')

# 建议
print('\n' + '=' * 120)
print('分析与建议')
print('=' * 120)

avg_score = sum(s['score'] for s in signals) / len(signals)
median_idx = len(signals) // 2
median_score = sorted(s['score'] for s in signals)[median_idx]

print(f'\n平均分数: {avg_score:.1f}分')
print(f'中位数: {median_score}分')
print(f'最高分: {max_score}分 (理论最大值: SHORT=232, LONG=185)')

high_score_pct = len(high_score_signals) / len(signals) * 100

if max_score > 150:
    print(f'\n⚠️  存在极高分信号（{max_score}分），可能的问题：')
    print(f'   1. 评分项权重配置过高')
    print(f'   2. 多个强信号叠加（市场极端情况）')
    print(f'   3. 可能是追高/追跌信号')

if high_score_pct > 20:
    print(f'\n⚠️  高分信号占比过高（{high_score_pct:.1f}%），建议：')
    print(f'   - 考虑提高开仓阈值到50-60分')
    print(f'   - 或降低各评分项的权重值')
elif high_score_pct > 10:
    print(f'\n📊 高分信号占比适中（{high_score_pct:.1f}%）')
else:
    print(f'\n✅ 高分信号占比正常（{high_score_pct:.1f}%）')

# 分析35分附近的信号密度
near_threshold = len([s for s in signals if 35 <= s['score'] <= 45])
near_threshold_pct = near_threshold / len(signals) * 100

print(f'\n35-45分（阈值附近）信号数: {near_threshold} ({near_threshold_pct:.1f}%)')
if near_threshold_pct > 30:
    print(f'⚠️  大量信号集中在阈值附近，可能需要提高阈值')
elif near_threshold_pct < 10:
    print(f'✅ 阈值附近信号较少，当前阈值35分合理')

cursor.close()
conn.close()
