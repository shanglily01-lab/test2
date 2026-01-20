#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
部署信号评分字段到 futures_positions 表
"""

import pymysql
import sys
from dotenv import load_dotenv
import os

# 加载环境变量
load_dotenv()

# 数据库配置
db_config = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', '3306')),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data')
}

print("=" * 80)
print("🚀 部署信号评分字段到 futures_positions 表")
print("=" * 80)

try:
    # 连接数据库
    print("\n📡 连接数据库...")
    conn = pymysql.connect(**db_config, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()
    print("✅ 数据库连接成功")

    # 检查字段是否已存在
    print("\n🔍 检查现有字段...")
    cursor.execute("SHOW COLUMNS FROM futures_positions LIKE 'entry_score'")
    entry_score_exists = cursor.fetchone() is not None

    cursor.execute("SHOW COLUMNS FROM futures_positions LIKE 'signal_components'")
    signal_components_exists = cursor.fetchone() is not None

    if entry_score_exists:
        print("  ℹ️  entry_score 字段已存在")
    else:
        print("  ⚠️  entry_score 字段不存在，准备添加...")

    if signal_components_exists:
        print("  ℹ️  signal_components 字段已存在")
    else:
        print("  ⚠️  signal_components 字段不存在，准备添加...")

    # 添加 entry_score 字段
    if not entry_score_exists:
        print("\n📝 添加 entry_score 字段...")
        try:
            cursor.execute("""
                ALTER TABLE futures_positions
                ADD COLUMN entry_score INT COMMENT '开仓得分' AFTER entry_signal_type
            """)
            conn.commit()
            print("✅ entry_score 字段添加成功")
        except pymysql.err.OperationalError as e:
            if "Duplicate column name" in str(e):
                print("  ℹ️  entry_score 字段已存在（可能在并发操作中添加）")
            else:
                raise
    else:
        print("  ⏭️  跳过 entry_score 字段（已存在）")

    # 添加 signal_components 字段
    if not signal_components_exists:
        print("\n📝 添加 signal_components 字段...")
        try:
            cursor.execute("""
                ALTER TABLE futures_positions
                ADD COLUMN signal_components TEXT COMMENT '信号组成（JSON格式）' AFTER entry_score
            """)
            conn.commit()
            print("✅ signal_components 字段添加成功")
        except pymysql.err.OperationalError as e:
            if "Duplicate column name" in str(e):
                print("  ℹ️  signal_components 字段已存在（可能在并发操作中添加）")
            else:
                raise
    else:
        print("  ⏭️  跳过 signal_components 字段（已存在）")

    # 验证字段
    print("\n🔍 验证字段...")
    cursor.execute("SHOW COLUMNS FROM futures_positions WHERE Field IN ('entry_score', 'signal_components')")
    columns = cursor.fetchall()

    if len(columns) == 2:
        print("✅ 所有字段验证通过")
        print("\n字段详情:")
        for col in columns:
            print(f"  • {col['Field']}: {col['Type']} - {col['Comment']}")
    else:
        print("❌ 字段验证失败，只找到 {} 个字段".format(len(columns)))
        sys.exit(1)

    cursor.close()
    conn.close()

    print("\n" + "=" * 80)
    print("🎉 部署完成！")
    print("=" * 80)
    print("\n📋 后续步骤:")
    print("  1. 重启 smart_trader_service.py")
    print("  2. 系统会开始记录 entry_score 和 signal_components")
    print("  3. 运行测试: python test_scoring_weight_system.py")
    print("  4. 等待数据积累，每日凌晨2点自动优化权重")

except Exception as e:
    print(f"\n❌ 部署失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
