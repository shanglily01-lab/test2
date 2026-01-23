#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量修复所有Python文件的时区问题
将 datetime.utcnow() 替换为 datetime.utcnow()
"""
import os
import re
from pathlib import Path

# 要排除的目录
EXCLUDE_DIRS = {'archived_strategies', '.git', 'venv', '__pycache__', 'node_modules'}

# 要处理的文件扩展名
INCLUDE_EXTS = {'.py'}

def should_process_file(file_path):
    """判断是否应该处理该文件"""
    # 检查是否在排除目录中
    for exclude_dir in EXCLUDE_DIRS:
        if exclude_dir in file_path.parts:
            return False

    # 检查文件扩展名
    if file_path.suffix not in INCLUDE_EXTS:
        return False

    return True

def fix_timezone_in_file(file_path):
    """修复单个文件的时区问题"""
    try:
        # 读取文件
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        original_content = content

        # 替换 datetime.utcnow() 为 datetime.utcnow()
        content = re.sub(r'\bdatetime\.now\(\)', 'datetime.utcnow()', content)

        # 如果内容有变化，写回文件
        if content != original_content:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True

        return False

    except Exception as e:
        print(f"❌ 处理文件失败 {file_path}: {e}")
        return False

def main():
    """主函数"""
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    root_dir = Path('.')

    print("=" * 100)
    print("批量修复时区问题: datetime.utcnow() -> datetime.utcnow()")
    print("=" * 100)

    # 遍历所有Python文件
    files_processed = 0
    files_modified = 0

    for py_file in root_dir.rglob('*.py'):
        if should_process_file(py_file):
            files_processed += 1
            if fix_timezone_in_file(py_file):
                files_modified += 1
                print(f"[MODIFIED] {py_file}")

    print("\n" + "=" * 100)
    print(f"处理完成!")
    print(f"处理文件数: {files_processed}")
    print(f"修改文件数: {files_modified}")
    print("=" * 100)

if __name__ == '__main__':
    main()
