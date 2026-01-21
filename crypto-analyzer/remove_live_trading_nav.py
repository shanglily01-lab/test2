#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从所有HTML模板中删除"实盘合约"导航项
"""
import os
import sys
import glob
import re

# 修复Windows控制台编码问题
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 模板目录
TEMPLATE_DIR = 'templates'

def remove_live_trading_nav(file_path):
    """从指定文件中删除实盘合约导航项"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        original_content = content

        # 删除实盘合约导航项（完整的<a>标签）
        # 匹配各种可能的格式
        patterns = [
            # 标准格式
            r'\s*<a href="/live_trading" class="nav-link[^"]*">\s*<i class="bi bi-currency-exchange"></i>\s*实盘合约\s*</a>\s*',
            # 可能有额外空格或换行
            r'\s*<a href="/live_trading" class="nav-link[^"]*">\s*<i[^>]*></i>\s*实盘合约\s*</a>\s*',
        ]

        for pattern in patterns:
            content = re.sub(pattern, '\n', content, flags=re.MULTILINE)

        # 如果内容有变化，写回文件
        if content != original_content:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        return False

    except Exception as e:
        print(f"错误处理文件 {file_path}: {e}")
        return False

def main():
    print("="*80)
    print("删除实盘合约导航项工具")
    print("="*80)

    # 获取所有HTML模板文件
    html_files = glob.glob(os.path.join(TEMPLATE_DIR, '*.html'))

    updated_count = 0
    skipped_count = 0

    print(f"\n找到 {len(html_files)} 个HTML文件\n")

    for file_path in sorted(html_files):
        file_name = os.path.basename(file_path)

        # 跳过不包含导航栏的文件
        skip_files = ['index.html', 'login.html', 'register.html', 'api-keys.html']
        if file_name in skip_files:
            print(f"⏭  跳过: {file_name} (无导航栏)")
            skipped_count += 1
            continue

        if remove_live_trading_nav(file_path):
            print(f"✓ 更新: {file_name}")
            updated_count += 1
        else:
            print(f"- 未变化: {file_name}")
            skipped_count += 1

    print("\n" + "="*80)
    print(f"完成! 更新了 {updated_count} 个文件, 跳过 {skipped_count} 个文件")
    print("="*80)

if __name__ == '__main__':
    main()
