#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
执行超级大脑自我优化
根据24小时信号分析结果自动优化
"""

import pymysql
import sys
import io
import json
from datetime import datetime
from dotenv import load_dotenv
import os

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'),
    cursorclass=pymysql.cursors.DictCursor,
    charset='utf8mb4'
)

cursor = conn.cursor()

print("=" * 100)
print("超级大脑自我优化执行")
print("=" * 100)
print()

# 读取优化建议
try:
    with open('optimization_actions.json', 'r', encoding='utf-8') as f:
        optimization_data = json.load(f)
except FileNotFoundError:
    print("错误: 未找到 optimization_actions.json，请先运行 analyze_24h_signals.py")
    sys.exit(1)

actions = optimization_data.get('actions', [])

print(f"分析时间: {optimization_data['timestamp']}")
print(f"分析周期: {optimization_data['analysis_period']}")
print(f"待执行操作: {len(actions)} 个")
print()

if not actions:
    print("✓ 没有需要优化的操作")
    sys.exit(0)

blacklisted_signals = []
threshold_raised_signals = []

for action in actions:
    action_type = action['action']
    signal_type = action['signal_type'][:200]  # 截断到200字符（数据库字段可能有限制）
    side = action['side']
    reason = action['reason'][:200]  # 截断原因

    if action_type == 'BLACKLIST_SIGNAL':
        # 禁用信号 - 添加到信号黑名单
        cursor.execute('''
            INSERT INTO signal_blacklist (signal_type, position_side, reason, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, TRUE, NOW(), NOW())
            ON DUPLICATE KEY UPDATE
                is_active = TRUE,
                reason = %s,
                updated_at = NOW()
        ''', (signal_type, side, reason, reason))

        blacklisted_signals.append(f"{signal_type} {side}")
        print(f"✓ 已禁用信号: {signal_type} {side}")
        print(f"  原因: {reason}")

    elif action_type == 'RAISE_THRESHOLD':
        # 提高阈值
        current_avg_score = action.get('current_avg_score', 0)
        new_threshold = max(50, current_avg_score + 10)  # 至少50分，或当前平均分+10

        # 记录到信号阈值调整表
        cursor.execute('''
            INSERT INTO signal_threshold_overrides (signal_type, position_side, min_score, reason, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, TRUE, NOW(), NOW())
            ON DUPLICATE KEY UPDATE
                min_score = %s,
                reason = %s,
                is_active = TRUE,
                updated_at = NOW()
        ''', (signal_type, side, new_threshold, reason, new_threshold, reason))

        threshold_raised_signals.append(f"{signal_type} {side} → {new_threshold}分")
        print(f"✓ 已提高阈值: {signal_type} {side}")
        print(f"  新阈值: {new_threshold}分 (之前平均: {current_avg_score:.1f}分)")
        print(f"  原因: {reason}")

conn.commit()

print()
print("=" * 100)
print("优化结果总结")
print("=" * 100)
print()

if blacklisted_signals:
    print(f"### 已禁用信号 ({len(blacklisted_signals)}个)")
    for signal in blacklisted_signals:
        print(f"  • {signal}")
    print()

if threshold_raised_signals:
    print(f"### 已提高阈值 ({len(threshold_raised_signals)}个)")
    for signal in threshold_raised_signals:
        print(f"  • {signal}")
    print()

print("### 下一步")
print("  1. 重启 smart_trader 服务使配置生效")
print("  2. 观察下一个交易周期的表现")
print("  3. 如果效果不佳，可以回滚优化（设置 is_active=FALSE）")
print()

# 查询当前所有黑名单信号
cursor.execute('''
    SELECT signal_type, position_side, reason, updated_at
    FROM signal_blacklist
    WHERE is_active = TRUE
    ORDER BY updated_at DESC
''')

blacklist = cursor.fetchall()

if blacklist:
    print(f"### 当前生效的信号黑名单 (共{len(blacklist)}个)")
    print(f"{'信号类型':<60} {'方向':<8} {'原因':<50}")
    print("-" * 120)
    for item in blacklist:
        signal = item['signal_type'][:58] if len(item['signal_type']) > 58 else item['signal_type']
        print(f"{signal:<60} {item['position_side']:<8} {item['reason']:<50}")
else:
    print("### 当前没有生效的信号黑名单")

print()
print("=" * 100)

cursor.close()
conn.close()
