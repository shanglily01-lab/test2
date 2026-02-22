#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检查项目中可能存在连接断开问题的数据库连接
"""
import os
import sys
import re
from pathlib import Path
from collections import defaultdict

# 设置UTF-8输出
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

def find_db_connections(root_dir):
    """查找所有使用数据库连接的文件"""

    # 需要检查的模式
    patterns = {
        '直接创建连接': r'pymysql\.connect\(',
        '未使用ping': r'\.cursor\(\)',
        '长时间持有连接': r'self\.connection\s*=',
    }

    results = defaultdict(list)

    # 遍历所有 Python 文件
    for root, dirs, files in os.walk(root_dir):
        # 跳过虚拟环境和缓存目录
        if 'venv' in root or '__pycache__' in root or '.git' in root or 'cleanup_backup' in root:
            continue

        for file in files:
            if not file.endswith('.py'):
                continue

            file_path = os.path.join(root, file)
            rel_path = os.path.relpath(file_path, root_dir)

            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()

                    # 检查是否使用了连接池
                    uses_pool = 'connection_pool' in content or 'get_db_connection' in content
                    has_ping_reconnect = 'ping(reconnect=True)' in content

                    # 检查各种模式
                    for pattern_name, pattern in patterns.items():
                        if re.search(pattern, content):
                            info = {
                                'file': rel_path,
                                'pattern': pattern_name,
                                'uses_pool': uses_pool,
                                'has_ping': has_ping_reconnect,
                            }
                            results[pattern_name].append(info)

            except Exception as e:
                pass

    return results

def analyze_priority(file_path):
    """分析文件的修改优先级"""
    # 高优先级: 定时任务和长期运行的服务
    high_priority_keywords = [
        'trader_service', 'monitor', 'background', 'collector',
        'detector', 'optimizer', 'analyzer'
    ]

    # 中优先级: API 接口
    medium_priority_keywords = ['api']

    # 低优先级: 脚本
    low_priority_keywords = ['script', 'test', 'check']

    file_lower = file_path.lower()

    for keyword in high_priority_keywords:
        if keyword in file_lower:
            return '🔴 高'

    for keyword in medium_priority_keywords:
        if keyword in file_lower:
            return '🟡 中'

    return '🟢 低'

def print_report(results):
    """打印检查报告"""
    print("\n" + "="*80)
    print("MySQL 连接断开风险检查报告")
    print("="*80 + "\n")

    # 统计
    total_files = set()
    files_with_pool = set()
    files_with_ping = set()

    for pattern_name, items in results.items():
        for item in items:
            total_files.add(item['file'])
            if item['uses_pool']:
                files_with_pool.add(item['file'])
            if item['has_ping']:
                files_with_ping.add(item['file'])

    print(f"📊 总体统计:")
    print(f"  - 使用数据库连接的文件数: {len(total_files)}")
    print(f"  - 已使用连接池的文件数: {len(files_with_pool)} ✅")
    print(f"  - 使用ping重连的文件数: {len(files_with_ping)} ⚠️")
    print(f"  - 可能有风险的文件数: {len(total_files - files_with_pool - files_with_ping)} ❌")
    print()

    # 按优先级分组
    priority_groups = defaultdict(list)
    for file in total_files:
        if file not in files_with_pool and file not in files_with_ping:
            priority = analyze_priority(file)
            priority_groups[priority].append(file)

    # 打印高优先级文件
    if priority_groups.get('🔴 高'):
        print("🔴 高优先级 - 建议立即修改（长期运行的服务）:")
        print("-" * 80)
        for file in sorted(priority_groups['🔴 高']):
            print(f"  ❌ {file}")
        print()

    # 打印中优先级文件
    if priority_groups.get('🟡 中'):
        print("🟡 中优先级 - 建议逐步修改（API接口）:")
        print("-" * 80)
        for file in sorted(priority_groups['🟡 中']):
            print(f"  ⚠️  {file}")
        print()

    # 打印低优先级文件
    if priority_groups.get('🟢 低'):
        print("🟢 低优先级 - 可选修改（独立脚本）:")
        print("-" * 80)
        count = 0
        for file in sorted(priority_groups['🟢 低']):
            if count < 10:  # 只显示前10个
                print(f"  ℹ️  {file}")
                count += 1
        if len(priority_groups['🟢 低']) > 10:
            print(f"  ... 还有 {len(priority_groups['🟢 低']) - 10} 个文件")
        print()

    # 打印已修复的文件
    if files_with_pool:
        print("✅ 已使用连接池的文件:")
        print("-" * 80)
        for file in sorted(files_with_pool):
            print(f"  ✅ {file}")
        print()

    if files_with_ping:
        print("⚠️ 使用ping重连的文件（建议升级到连接池）:")
        print("-" * 80)
        for file in sorted(files_with_ping):
            print(f"  ⚠️  {file}")
        print()

    print("="*80)
    print("💡 修复建议:")
    print("="*80)
    print("1. 优先修改 🔴 高优先级文件")
    print("2. 参考 'MySQL连接断开问题修复指南.md' 进行修改")
    print("3. 使用连接池替代直接创建连接:")
    print("   from app.database.connection_pool import get_db_connection")
    print("   with get_db_connection(db_config) as conn:")
    print("       cursor = conn.cursor()")
    print("       # ... 执行查询")
    print("="*80 + "\n")

if __name__ == '__main__':
    # 获取项目根目录
    root_dir = os.path.dirname(os.path.abspath(__file__))

    print("🔍 正在扫描项目中的数据库连接...")
    results = find_db_connections(root_dir)

    print_report(results)
