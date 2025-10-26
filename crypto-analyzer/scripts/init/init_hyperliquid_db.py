#!/usr/bin/env python3
"""
初始化 Hyperliquid 数据库表
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.database.hyperliquid_db import HyperliquidDB


def main():
    """主函数"""

    print("="*80)
    print("初始化 Hyperliquid 数据库")
    print("="*80)
    print()

    try:
        with HyperliquidDB() as db:
            print("正在创建数据库表...")
            db.init_tables()

        print("\n✅ 数据库初始化完成!")
        print("\n下一步:")
        print("  1. 运行数据同步: python3 sync_hyperliquid_leaderboard.py")
        print("  2. 查看排行榜: python3 view_hyperliquid_leaderboard.py")

    except Exception as e:
        print(f"\n❌ 初始化失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
