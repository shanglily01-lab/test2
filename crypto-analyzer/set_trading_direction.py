#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易方向设置管理工具
快速启用/禁止做多或做空
"""
import pymysql
import os
import sys
from dotenv import load_dotenv

# 设置UTF-8输出
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

load_dotenv()

# 数据库配置
db_config = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'trading_user'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data'),
    'charset': 'utf8mb4'
}

def get_current_settings(cursor):
    """获取当前设置"""
    cursor.execute("""
        SELECT param_key, param_value
        FROM adaptive_params
        WHERE param_key IN ('allow_long', 'allow_short')
        ORDER BY param_key
    """)
    results = cursor.fetchall()
    settings = {}
    for row in results:
        param_key, param_value = row
        settings[param_key] = int(param_value)
    return settings

def show_current_settings(cursor):
    """显示当前设置"""
    settings = get_current_settings(cursor)
    print("\n" + "="*50)
    print("当前交易方向设置:")
    print("="*50)

    long_status = "✅ 允许" if settings.get('allow_long', 1) == 1 else "❌ 禁止"
    short_status = "✅ 允许" if settings.get('allow_short', 1) == 1 else "❌ 禁止"

    print(f"  做多 (LONG):  {long_status}")
    print(f"  做空 (SHORT): {short_status}")
    print("="*50 + "\n")

def update_setting(cursor, param_key, value, conn):
    """更新设置"""
    cursor.execute("""
        UPDATE adaptive_params
        SET param_value = %s, updated_at = NOW()
        WHERE param_key = %s
    """, (value, param_key))
    conn.commit()

def main():
    if len(sys.argv) < 2:
        print("\n使用方法:")
        print("  python set_trading_direction.py <命令>")
        print("\n可用命令:")
        print("  status              - 查看当前设置")
        print("  enable-long         - 启用做多")
        print("  disable-long        - 禁用做多")
        print("  enable-short        - 启用做空")
        print("  disable-short       - 禁用做空")
        print("  enable-both         - 启用做多和做空")
        print("  disable-both        - 禁用做多和做空")
        print("  only-long           - 仅做多（禁用做空）")
        print("  only-short          - 仅做空（禁用做多）")
        print("\n示例:")
        print("  python set_trading_direction.py only-long")
        print("  python set_trading_direction.py disable-short")
        sys.exit(1)

    command = sys.argv[1].lower()

    try:
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()

        if command == 'status':
            show_current_settings(cursor)

        elif command == 'enable-long':
            update_setting(cursor, 'allow_long', 1, conn)
            print("✅ 已启用做多")
            show_current_settings(cursor)

        elif command == 'disable-long':
            update_setting(cursor, 'allow_long', 0, conn)
            print("❌ 已禁用做多")
            show_current_settings(cursor)

        elif command == 'enable-short':
            update_setting(cursor, 'allow_short', 1, conn)
            print("✅ 已启用做空")
            show_current_settings(cursor)

        elif command == 'disable-short':
            update_setting(cursor, 'allow_short', 0, conn)
            print("❌ 已禁用做空")
            show_current_settings(cursor)

        elif command == 'enable-both':
            update_setting(cursor, 'allow_long', 1, conn)
            update_setting(cursor, 'allow_short', 1, conn)
            print("✅ 已启用做多和做空")
            show_current_settings(cursor)

        elif command == 'disable-both':
            update_setting(cursor, 'allow_long', 0, conn)
            update_setting(cursor, 'allow_short', 0, conn)
            print("❌ 已禁用做多和做空")
            show_current_settings(cursor)

        elif command == 'only-long':
            update_setting(cursor, 'allow_long', 1, conn)
            update_setting(cursor, 'allow_short', 0, conn)
            print("✅ 设置为仅做多模式")
            show_current_settings(cursor)

        elif command == 'only-short':
            update_setting(cursor, 'allow_long', 0, conn)
            update_setting(cursor, 'allow_short', 1, conn)
            print("✅ 设置为仅做空模式")
            show_current_settings(cursor)

        else:
            print(f"❌ 未知命令: {command}")
            print("使用 'python set_trading_direction.py' 查看帮助")

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
