#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析调度器日志，查找K线数据采集停止的原因"""
import os
import sys
import io
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from datetime import datetime
import re

log_file = 'logs/scheduler_2025-11-12.log'

print("=" * 80)
print("调度器日志分析")
print("=" * 80)

with open(log_file, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 1. 查找所有K线数据采集相关的日志
print("\n1. K线数据采集任务执行记录:")
print("-" * 80)
kline_patterns = [
    r'开始采集多交易所数据.*\(1m\)',
    r'开始采集多交易所数据.*\(5m\)',
    r'开始采集多交易所数据.*\(15m\)',
    r'开始采集多交易所数据.*\(1h\)',
    r'多交易所数据采集完成.*\(1m\)',
    r'多交易所数据采集完成.*\(5m\)',
    r'多交易所数据采集完成.*\(15m\)',
    r'多交易所数据采集完成.*\(1h\)',
]

kline_logs = []
for line in lines:
    for pattern in kline_patterns:
        if re.search(pattern, line):
            kline_logs.append(line.strip())
            break

if kline_logs:
    print(f"找到 {len(kline_logs)} 条K线采集相关日志:")
    for log in kline_logs[-30:]:  # 显示最后30条
        print(f"  {log}")
else:
    print("  ⚠️  没有找到K线数据采集相关的日志！")

# 2. 查找13:26之后的所有任务执行记录
print("\n2. 13:26之后的任务执行记录:")
print("-" * 80)
after_1326 = []
for line in lines:
    if '2025-11-12 13:2[6-9]' in line or '2025-11-12 1[4-9]:' in line:
        if any(keyword in line for keyword in ['开始采集', '采集完成', '开始更新', '更新完成', '开始监控', '监控完成']):
            after_1326.append(line.strip())

if after_1326:
    print(f"找到 {len(after_1326)} 条任务执行记录:")
    for log in after_1326[:50]:  # 显示前50条
        print(f"  {log}")
else:
    print("  ⚠️  13:26之后没有找到任务执行记录！")

# 3. 查找错误和异常
print("\n3. 13:26之后的错误和异常:")
print("-" * 80)
errors_after_1326 = []
for line in lines:
    if '2025-11-12 13:2[6-9]' in line or '2025-11-12 1[4-9]:' in line:
        if 'ERROR' in line or 'Exception' in line or 'Traceback' in line or '失败' in line:
            errors_after_1326.append(line.strip())

if errors_after_1326:
    print(f"找到 {len(errors_after_1326)} 条错误记录:")
    for log in errors_after_1326:
        print(f"  {log}")
else:
    print("  ✅ 13:26之后没有发现错误记录")

# 4. 分析任务执行时间间隔
print("\n4. K线数据采集任务执行时间间隔分析:")
print("-" * 80)
collection_times = []
for line in lines:
    if '开始采集多交易所数据' in line and '(1m)' in line:
        # 提取时间
        match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
        if match:
            try:
                time_str = match.group(1)
                dt = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
                collection_times.append((dt, '1m'))
            except:
                pass

if len(collection_times) >= 2:
    print(f"找到 {len(collection_times)} 次1m数据采集:")
    for i in range(len(collection_times) - 1):
        time_diff = (collection_times[i+1][0] - collection_times[i][0]).total_seconds()
        print(f"  {collection_times[i][0].strftime('%H:%M:%S')} -> {collection_times[i+1][0].strftime('%H:%M:%S')}: {time_diff:.0f}秒")
    
    if len(collection_times) > 0:
        last_time = collection_times[-1][0]
        now = datetime.now()
        time_since_last = (now - last_time).total_seconds()
        print(f"\n  最后一次采集: {last_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  距离现在: {int(time_since_last/60)} 分钟")
else:
    print("  ⚠️  没有找到足够的采集记录进行分析")

# 5. 检查是否有任务被阻塞
print("\n5. 检查是否有长时间运行的任务:")
print("-" * 80)
# 查找Hyperliquid监控任务的时间
hyperliquid_times = []
for line in lines:
    if '开始监控 Hyperliquid' in line or 'Hyperliquid 钱包监控完成' in line:
        match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
        if match:
            try:
                time_str = match.group(1)
                dt = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
                hyperliquid_times.append((dt, '监控' in line))
            except:
                pass

if hyperliquid_times:
    print(f"Hyperliquid监控任务执行记录: {len(hyperliquid_times)} 条")
    if len(hyperliquid_times) > 0:
        last_hyperliquid = hyperliquid_times[-1][0]
        print(f"  最后一次执行: {last_hyperliquid.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 检查是否在13:26之后还在执行
        cutoff = datetime.strptime('2025-11-12 13:26:00', '%Y-%m-%d %H:%M:%S')
        if last_hyperliquid > cutoff:
            print(f"  ⚠️  Hyperliquid监控任务在13:26之后仍在运行，可能阻塞了其他任务")

print("\n" + "=" * 80)
print("分析完成")
print("=" * 80)

