#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
批量更新所有HTML模板的导航栏
1. 移除"交易策略"导航项
2. 将"模拟合约"改为"合约交易"
3. 将"模拟现货"改为"现货交易"
"""
import os
import sys
import glob

# 修复Windows控制台编码问题
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 模板目录
TEMPLATE_DIR = 'templates'

# 需要修改的内容
REPLACEMENTS = [
    # 移除交易策略导航项(包括整个<a>标签)
    (
        r'                <a href="/trading-strategies" class="nav-link">\n                    <i class="bi bi-diagram-3"></i> 交易策略\n                </a>\n',
        ''
    ),
    # 将"模拟现货"改为"现货交易"
    (
        '<i class="bi bi-journals"></i> 模拟现货',
        '<i class="bi bi-journals"></i> 现货交易'
    ),
    # 将"模拟合约"改为"合约交易"
    (
        '<i class="bi bi-graph-up-arrow"></i> 模拟合约',
        '<i class="bi bi-graph-up-arrow"></i> 合约交易'
    ),
]

def update_file(filepath):
    """更新单个文件"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content
    changes_made = []

    # 应用所有替换
    for old, new in REPLACEMENTS:
        if old in content:
            content = content.replace(old, new)
            if new == '':
                changes_made.append(f"  - 移除: {old[:50]}...")
            else:
                changes_made.append(f"  - {old[:30]}... → {new[:30]}...")

    # 如果有变化则写回文件
    if content != original_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return True, changes_made

    return False, []

def main():
    # 获取所有HTML文件
    html_files = glob.glob(os.path.join(TEMPLATE_DIR, '*.html'))

    print(f"找到 {len(html_files)} 个HTML文件\n")
    print("开始更新导航栏...\n")
    print("="*80)

    updated_count = 0

    for filepath in sorted(html_files):
        filename = os.path.basename(filepath)
        updated, changes = update_file(filepath)

        if updated:
            updated_count += 1
            print(f"\n✓ {filename}")
            for change in changes:
                print(change)
        else:
            print(f"\n○ {filename} (无需更改)")

    print("\n" + "="*80)
    print(f"\n总结: 更新了 {updated_count}/{len(html_files)} 个文件")
    print("\n修改内容:")
    print("  1. ✓ 移除了'交易策略'导航项")
    print("  2. ✓ '模拟现货' → '现货交易'")
    print("  3. ✓ '模拟合约' → '合约交易'")

if __name__ == '__main__':
    main()
