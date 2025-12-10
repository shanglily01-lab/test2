#!/usr/bin/env python3
"""
应用数据库迁移
"""
import pymysql
from pathlib import Path
from app.utils.config_loader import load_config

def apply_migration():
    """应用迁移"""
    # 加载配置
    config_path = Path(__file__).parent / 'config.yaml'
    config = load_config(config_path)

    db_config = config['database']

    print("=" * 70)
    print("  数据库迁移: 添加 canceled_at 字段")
    print("=" * 70)
    print()

    # 读取SQL文件
    sql_file = Path(__file__).parent / 'migrations' / 'add_canceled_at_to_futures_orders.sql'

    if not sql_file.exists():
        print(f"❌ SQL文件不存在: {sql_file}")
        return False

    with open(sql_file, 'r', encoding='utf-8') as f:
        sql = f.read()

    print(f"📄 SQL文件: {sql_file}")
    print()

    # 连接数据库
    try:
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()

        print("✅ 数据库连接成功")
        print(f"   数据库: {db_config['database']}")
        print()

        # 检查字段是否已存在
        cursor.execute("""
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'futures_orders'
              AND COLUMN_NAME = 'canceled_at'
        """, (db_config['database'],))

        exists = cursor.fetchone()[0] > 0

        if exists:
            print("⚠️  字段 'canceled_at' 已存在，跳过迁移")
            return True

        # 执行迁移
        print("🔧 执行迁移...")

        # 分割并执行每条SQL语句
        statements = [s.strip() for s in sql.split(';') if s.strip() and not s.strip().startswith('--')]

        for i, statement in enumerate(statements, 1):
            print(f"   [{i}/{len(statements)}] 执行: {statement[:60]}...")
            cursor.execute(statement)

        conn.commit()

        print()
        print("=" * 70)
        print("  ✅ 迁移完成")
        print("=" * 70)
        print()

        # 验证
        cursor.execute("""
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'futures_orders'
              AND COLUMN_NAME = 'canceled_at'
        """, (db_config['database'],))

        if cursor.fetchone()[0] > 0:
            print("✅ 验证成功: 字段 'canceled_at' 已添加")

            # 显示字段信息
            cursor.execute("""
                SELECT COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_COMMENT
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME = 'futures_orders'
                  AND COLUMN_NAME = 'canceled_at'
            """, (db_config['database'],))

            col_info = cursor.fetchone()
            print(f"   类型: {col_info[0]}")
            print(f"   可空: {col_info[1]}")
            print(f"   默认值: {col_info[2]}")
            print(f"   注释: {col_info[3]}")
        else:
            print("❌ 验证失败: 字段未添加")
            return False

        cursor.close()
        conn.close()

        return True

    except Exception as e:
        print(f"❌ 迁移失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    success = apply_migration()
    exit(0 if success else 1)
