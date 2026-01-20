#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
部署信号评分字段到 futures_positions 表 (改进版)
增加超时控制和更好的错误处理
"""

import pymysql
import sys
import signal
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
    'database': os.getenv('DB_NAME', 'binance-data'),
    'connect_timeout': 10,  # 连接超时10秒
    'read_timeout': 30,     # 读取超时30秒
    'write_timeout': 30     # 写入超时30秒
}

def timeout_handler(signum, frame):
    raise TimeoutError("操作超时")

print("=" * 80)
print("🚀 部署信号评分字段到 futures_positions 表 (v2)")
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

    print(f"  entry_score: {'✅ 已存在' if entry_score_exists else '❌ 不存在'}")
    print(f"  signal_components: {'✅ 已存在' if signal_components_exists else '❌ 不存在'}")

    if entry_score_exists and signal_components_exists:
        print("\n✅ 所有字段都已存在，无需添加")
        cursor.close()
        conn.close()
        sys.exit(0)

    # 检查表是否被锁定
    print("\n🔍 检查表锁状态...")
    cursor.execute("SHOW OPEN TABLES WHERE `Table` = 'futures_positions' AND In_use > 0")
    locks = cursor.fetchall()
    if locks:
        print(f"  ⚠️  表被 {len(locks)} 个进程使用中，可能需要等待...")

    # 添加 entry_score 字段
    if not entry_score_exists:
        print("\n📝 添加 entry_score 字段...")
        print("  ⏳ 执行 ALTER TABLE（可能需要几秒钟）...")

        try:
            # 设置30秒超时
            if sys.platform != 'win32':
                signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(30)

            cursor.execute("""
                ALTER TABLE futures_positions
                ADD COLUMN entry_score INT COMMENT '开仓得分' AFTER entry_signal_type
            """)

            if sys.platform != 'win32':
                signal.alarm(0)  # 取消超时

            conn.commit()
            print("  ✅ entry_score 字段添加成功")

        except TimeoutError:
            print("  ❌ 操作超时（30秒），可能表正在被其他进程使用")
            print("  💡 建议: 先停止 smart_trader_service.py，然后重试")
            conn.rollback()
            sys.exit(1)

        except pymysql.err.OperationalError as e:
            if "Duplicate column name" in str(e):
                print("  ℹ️  entry_score 字段已存在（可能在并发操作中添加）")
            else:
                print(f"  ❌ 错误: {e}")
                conn.rollback()
                raise
    else:
        print("\n⏭️  跳过 entry_score 字段（已存在）")

    # 添加 signal_components 字段
    if not signal_components_exists:
        print("\n📝 添加 signal_components 字段...")
        print("  ⏳ 执行 ALTER TABLE（可能需要几秒钟）...")

        try:
            # 设置30秒超时
            if sys.platform != 'win32':
                signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(30)

            cursor.execute("""
                ALTER TABLE futures_positions
                ADD COLUMN signal_components TEXT COMMENT '信号组成（JSON格式）' AFTER entry_score
            """)

            if sys.platform != 'win32':
                signal.alarm(0)  # 取消超时

            conn.commit()
            print("  ✅ signal_components 字段添加成功")

        except TimeoutError:
            print("  ❌ 操作超时（30秒），可能表正在被其他进程使用")
            print("  💡 建议: 先停止 smart_trader_service.py，然后重试")
            conn.rollback()
            sys.exit(1)

        except pymysql.err.OperationalError as e:
            if "Duplicate column name" in str(e):
                print("  ℹ️  signal_components 字段已存在（可能在并发操作中添加）")
            else:
                print(f"  ❌ 错误: {e}")
                conn.rollback()
                raise
    else:
        print("\n⏭️  跳过 signal_components 字段（已存在）")

    # 验证字段
    print("\n🔍 验证字段...")
    cursor.execute("SHOW COLUMNS FROM futures_positions WHERE Field IN ('entry_score', 'signal_components')")
    columns = cursor.fetchall()

    if len(columns) >= 2:
        print("✅ 所有字段验证通过")
        print("\n字段详情:")
        for col in columns:
            print(f"  • {col['Field']}: {col['Type']} - {col['Comment']}")
    else:
        print(f"⚠️  只找到 {len(columns)} 个字段")
        for col in columns:
            print(f"  • {col['Field']}: {col['Type']}")

    cursor.close()
    conn.close()

    print("\n" + "=" * 80)
    print("🎉 部署完成！")
    print("=" * 80)
    print("\n📋 后续步骤:")
    print("  1. 重启 smart_trader_service.py:")
    print("     pkill -f smart_trader_service.py")
    print("     nohup python smart_trader_service.py > /dev/null 2>&1 &")
    print("  2. 验证系统: python test_scoring_weight_system.py")
    print("  3. 查看日志: tail -f logs/smart_trader_*.log")

except TimeoutError as e:
    print(f"\n❌ 超时: {e}")
    print("\n💡 可能原因:")
    print("  1. 表被其他进程锁定")
    print("  2. 表数据量太大，ALTER TABLE 操作很慢")
    print("\n💡 解决方案:")
    print("  1. 停止所有使用 futures_positions 表的进程")
    print("  2. 使用 MySQL 直接执行:")
    print("     mysql -h HOST -u USER -p DATABASE < add_signal_fields.sql")
    sys.exit(1)

except pymysql.err.OperationalError as e:
    print(f"\n❌ 数据库操作错误: {e}")

    if "timeout" in str(e).lower():
        print("\n💡 这是超时错误，可能因为:")
        print("  1. 表被锁定（其他进程正在使用）")
        print("  2. 网络延迟")
        print("  3. 表数据量大，操作耗时")
        print("\n💡 建议: 先停止 smart_trader_service.py，然后重试")

    sys.exit(1)

except Exception as e:
    print(f"\n❌ 部署失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
