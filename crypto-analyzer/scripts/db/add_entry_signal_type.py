#!/usr/bin/env python3
"""
数据库迁移脚本 - 添加 entry_signal_type 字段

功能:
- 为 futures_positions 表添加 entry_signal_type 字段
- 为 live_futures_positions 表添加 entry_signal_type 字段
- 记录开仓信号类型（golden_cross, death_cross, sustained_trend 等）
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import yaml
import pymysql
import os
import re
from dotenv import load_dotenv

# 加载 .env 文件
env_path = project_root / '.env'
if env_path.exists():
    load_dotenv(env_path)
    print(f"已加载环境变量文件: {env_path}")


def parse_env_var(value: str) -> str:
    """
    解析环境变量格式的配置值
    例如: ${DB_HOST:localhost} -> 从环境变量 DB_HOST 读取，默认值为 localhost
    """
    if not isinstance(value, str):
        return str(value)

    # 匹配 ${VAR_NAME:default_value} 格式
    pattern = r'\$\{([^:}]+):([^}]*)\}'
    match = re.match(pattern, value)

    if match:
        env_var_name = match.group(1)
        default_value = match.group(2)
        return os.environ.get(env_var_name, default_value)

    return value


def load_db_config():
    """加载数据库配置"""
    config_path = project_root / 'config.yaml'

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    db_config_raw = config['database']['mysql']

    return {
        'host': parse_env_var(db_config_raw.get('host', 'localhost')),
        'port': int(parse_env_var(db_config_raw.get('port', '3306'))),
        'user': parse_env_var(db_config_raw.get('user', 'root')),
        'password': parse_env_var(db_config_raw.get('password', '')),
        'database': parse_env_var(db_config_raw.get('database', 'binance-data'))
    }


def add_entry_signal_type_column():
    """添加 entry_signal_type 字段"""

    print("\n" + "=" * 80)
    print("数据库迁移：添加 entry_signal_type 字段")
    print("=" * 80 + "\n")

    # 加载配置
    db_config = load_db_config()

    print(f"连接数据库: {db_config['database']}@{db_config['host']}:{db_config['port']}")

    # 连接数据库
    connection = pymysql.connect(**db_config)
    cursor = connection.cursor()

    try:
        # 1. 检查 futures_positions 表是否已有该字段
        print("\n[1] 检查 futures_positions 表...")
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'futures_positions'
              AND COLUMN_NAME = 'entry_signal_type'
        """, (db_config['database'],))

        result = cursor.fetchone()

        if result[0] > 0:
            print("   [OK] futures_positions 表已有 entry_signal_type 字段，跳过")
        else:
            print("   [ADD] futures_positions 表缺少 entry_signal_type 字段，正在添加...")

            cursor.execute("""
                ALTER TABLE futures_positions
                ADD COLUMN entry_signal_type VARCHAR(50) DEFAULT NULL COMMENT '开仓信号类型(golden_cross/death_cross/sustained_trend等)'
                AFTER entry_reason
            """)

            connection.commit()
            print("   [OK] futures_positions 表添加成功")

        # 2. 检查 live_futures_positions 表是否已有该字段
        print("\n[2] 检查 live_futures_positions 表...")
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'live_futures_positions'
              AND COLUMN_NAME = 'entry_signal_type'
        """, (db_config['database'],))

        result = cursor.fetchone()

        if result[0] > 0:
            print("   [OK] live_futures_positions 表已有 entry_signal_type 字段，跳过")
        else:
            print("   [ADD] live_futures_positions 表缺少 entry_signal_type 字段，正在添加...")

            cursor.execute("""
                ALTER TABLE live_futures_positions
                ADD COLUMN entry_signal_type VARCHAR(50) DEFAULT NULL COMMENT '开仓信号类型(golden_cross/death_cross/sustained_trend等)'
                AFTER source
            """)

            connection.commit()
            print("   [OK] live_futures_positions 表添加成功")

        # 3. 验证字段添加成功
        print("\n[3] 验证字段...")

        cursor.execute("""
            SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_COMMENT
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'futures_positions'
              AND COLUMN_NAME = 'entry_signal_type'
        """, (db_config['database'],))

        result = cursor.fetchone()
        if result:
            print(f"   [OK] futures_positions.entry_signal_type: {result[1]} - {result[2]}")

        cursor.execute("""
            SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_COMMENT
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'live_futures_positions'
              AND COLUMN_NAME = 'entry_signal_type'
        """, (db_config['database'],))

        result = cursor.fetchone()
        if result:
            print(f"   [OK] live_futures_positions.entry_signal_type: {result[1]} - {result[2]}")

        print("\n" + "=" * 80)
        print("[SUCCESS] 数据库迁移完成")
        print("=" * 80 + "\n")

        print("说明:")
        print("   entry_signal_type 字段用于记录开仓时的信号类型：")
        print("   - golden_cross: 金叉信号")
        print("   - death_cross: 死叉信号")
        print("   - sustained_trend_FORWARD: 持续趋势（顺向）")
        print("   - sustained_trend_REVERSE: 持续趋势（反向）")
        print("   - manual: 手动开仓")
        print()

    except Exception as e:
        print(f"\n❌ 迁移失败: {e}")
        import traceback
        traceback.print_exc()
        connection.rollback()

    finally:
        cursor.close()
        connection.close()


def main():
    """主函数"""
    add_entry_signal_type_column()


if __name__ == '__main__':
    main()
