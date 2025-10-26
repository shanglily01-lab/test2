#!/usr/bin/env python3
"""
检查合约数据（持仓量、多空比）的可用性
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database.db_service import DatabaseService
from sqlalchemy import text
import yaml

def check_futures_data():
    # 加载配置
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    db_config = config.get('database', {})
    db_service = DatabaseService(db_config)
    session = None

    try:
        session = db_service.get_session()

        print("=" * 80)
        print("检查合约相关数据表")
        print("=" * 80)
        print()

        # 检查可能包含持仓量和多空比的表
        possible_tables = [
            'futures_data',
            'futures_position_data',
            'open_interest_data',
            'long_short_ratio_data',
            'binance_futures_data',
            'funding_rate_data',
        ]

        existing_tables = []

        for table_name in possible_tables:
            result = session.execute(text(f"""
                SELECT COUNT(*) as count
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                AND table_name = '{table_name}'
            """))
            row = result.fetchone()

            if row[0] > 0:
                # 表存在，检查记录数
                result = session.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
                count_row = result.fetchone()
                count = count_row[0]

                if count > 0:
                    existing_tables.append(table_name)
                    print(f"✅ {table_name}: {count} 条记录")

                    # 查看表结构
                    result = session.execute(text(f"DESCRIBE {table_name}"))
                    columns = result.fetchall()
                    column_names = [col[0] for col in columns]
                    print(f"   字段: {', '.join(column_names)}")
                    print()

        if not existing_tables:
            print("❌ 没有找到包含合约数据的表")
            print()
            print("可能的原因：")
            print("1. 系统未启用合约数据采集")
            print("2. Scheduler未运行，导致合约数据未采集")
            print("3. 数据库中只有资金费率数据，没有持仓量和多空比")

        print()
        print("=" * 80)
        print("检查 funding_rate_stats 缓存表的字段")
        print("=" * 80)
        print()

        result = session.execute(text("DESCRIBE funding_rate_stats"))
        columns = result.fetchall()

        print("字段列表：")
        for col in columns:
            field_name = col[0]
            field_type = col[1]
            print(f"  - {field_name} ({field_type})")

        print()
        print("=" * 80)
        print("建议")
        print("=" * 80)
        print()

        has_oi = any('open_interest' in col[0].lower() for col in columns)
        has_lsr = any('long_short' in col[0].lower() or 'ratio' in col[0].lower() for col in columns)

        if not has_oi and not has_lsr:
            print("funding_rate_stats 表中没有 open_interest 和 long_short_ratio 字段")
            print()
            print("选项1：只显示资金费率（当前方案）")
            print("  ✅ 无需修改，资金费率已经能正常显示")
            print()
            print("选项2：扩展缓存表，添加持仓量和多空比")
            print("  1. 修改 funding_rate_stats 表结构，添加字段")
            print("  2. 修改 cache_update_service.py 写入这些数据")
            print("  3. 修改 _get_futures_from_cache 读取这些数据")
            print()
            print("选项3：从原始表直接读取（如果原始表有数据）")
            if existing_tables:
                print(f"  可用的表: {', '.join(existing_tables)}")
            else:
                print("  ⚠️ 但原始数据表也不存在")

    except Exception as e:
        print(f"❌ 检查失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if session:
            session.close()

if __name__ == "__main__":
    check_futures_data()
