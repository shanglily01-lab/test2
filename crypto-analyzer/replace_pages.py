#!/usr/bin/env python3
"""
批量替换HTML页面为新版本
备份原文件到 templates/backup/ 目录
"""

import os
import shutil
from pathlib import Path

# 页面映射：旧文件名 -> 新文件名
PAGE_MAPPINGS = {
    'dashboard.html': 'dashboard_new.html',
    'contract_trading.html': 'contract_trading_new.html',
    'futures_trading.html': 'futures_trading_new.html',
    'paper_trading.html': 'paper_trading_new.html',
}

TEMPLATES_DIR = Path('templates')
BACKUP_DIR = TEMPLATES_DIR / 'backup'

def backup_and_replace():
    """备份并替换页面文件"""

    # 创建备份目录
    BACKUP_DIR.mkdir(exist_ok=True)
    print(f"✅ 备份目录已创建: {BACKUP_DIR}")

    # 处理每个页面
    for old_file, new_file in PAGE_MAPPINGS.items():
        old_path = TEMPLATES_DIR / old_file
        new_path = TEMPLATES_DIR / new_file
        backup_path = BACKUP_DIR / old_file

        # 检查新文件是否存在
        if not new_path.exists():
            print(f"⚠️  跳过 {old_file}: 新文件 {new_file} 不存在")
            continue

        # 备份原文件
        if old_path.exists():
            shutil.copy2(old_path, backup_path)
            print(f"📦 已备份: {old_file} -> backup/{old_file}")

        # 替换文件
        shutil.copy2(new_path, old_path)
        print(f"✅ 已替换: {old_file} (使用 {new_file})")

    print("\n" + "="*60)
    print("✅ 所有页面替换完成！")
    print("="*60)
    print("\n已替换的页面:")
    for old_file in PAGE_MAPPINGS.keys():
        print(f"  - {old_file}")

    print(f"\n原始文件已备份到: {BACKUP_DIR}")
    print("\n如需恢复原文件，请从 backup/ 目录复制回来")

if __name__ == '__main__':
    print("""\n╔════════════════════════════════════════════════════════════╗
║          批量替换页面为新版本                                ║
╠════════════════════════════════════════════════════════════╣
║  将会替换以下页面:                                          ║
║    - dashboard.html                                        ║
║    - contract_trading.html                                 ║
║    - futures_trading.html                                  ║
║    - paper_trading.html                                    ║
║                                                            ║
║  原文件将备份到 templates/backup/ 目录                      ║
╚════════════════════════════════════════════════════════════╝\n""")

    response = input("确定要继续吗? (yes/no): ")
    if response.lower() in ['yes', 'y']:
        backup_and_replace()
    else:
        print("操作已取消")
