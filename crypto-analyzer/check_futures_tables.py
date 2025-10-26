#!/usr/bin/env python3
"""
检查合约持仓量和多空比表的数据
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database.db_service import DatabaseService
from sqlalchemy import text
import yaml

def check_futures_tables():
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    db_service = DatabaseService(config.get('database', {}))
    session = None

    try:
        session = db_service.get_session()

        print("=" * 80)
        print("检查合约持仓量和多空比数据表")
        print("=" * 80)
        print()

        # 检查 futures_open_interest 表
        print("1. futures_open_interest（持仓量）表")
        print("-" * 80)

        result = session.execute(text("""
            SELECT COUNT(*) as count
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
            AND table_name = 'futures_open_interest'
        """))

        if result.fetchone()[0] > 0:
            result = session.execute(text("SELECT COUNT(*) FROM futures_open_interest"))
            count = result.fetchone()[0]
            print(f"✅ 表存在，记录数: {count}")

            if count > 0:
                # 查看最新的几条记录
                result = session.execute(text("""
                    SELECT symbol, open_interest, open_interest_value, timestamp
                    FROM futures_open_interest
                    ORDER BY timestamp DESC
                    LIMIT 5
                """))

                print("\n最新5条记录:")
                for row in result.fetchall():
                    print(f"  {row[0]}: OI={row[1]}, 价值=${row[2]}, 时间={row[3]}")

                # 统计有多少个币种
                result = session.execute(text("""
                    SELECT COUNT(DISTINCT symbol) FROM futures_open_interest
                """))
                symbol_count = result.fetchone()[0]
                print(f"\n包含币种数: {symbol_count}")
        else:
            print("❌ 表不存在")

        print()
        print()

        # 检查 futures_long_short_ratio 表
        print("2. futures_long_short_ratio（多空比）表")
        print("-" * 80)

        result = session.execute(text("""
            SELECT COUNT(*) as count
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
            AND table_name = 'futures_long_short_ratio'
        """))

        if result.fetchone()[0] > 0:
            result = session.execute(text("SELECT COUNT(*) FROM futures_long_short_ratio"))
            count = result.fetchone()[0]
            print(f"✅ 表存在，记录数: {count}")

            if count > 0:
                # 查看最新的几条记录
                result = session.execute(text("""
                    SELECT symbol, long_account, short_account, long_short_ratio, timestamp
                    FROM futures_long_short_ratio
                    ORDER BY timestamp DESC
                    LIMIT 5
                """))

                print("\n最新5条记录:")
                for row in result.fetchall():
                    print(f"  {row[0]}: 多头={row[1]:.2f}%, 空头={row[2]:.2f}%, 比率={row[3]:.2f}, 时间={row[4]}")

                # 统计有多少个币种
                result = session.execute(text("""
                    SELECT COUNT(DISTINCT symbol) FROM futures_long_short_ratio
                """))
                symbol_count = result.fetchone()[0]
                print(f"\n包含币种数: {symbol_count}")
        else:
            print("❌ 表不存在")

        print()
        print("=" * 80)
        print("测试 get_latest_futures_data 方法")
        print("=" * 80)
        print()

        # 测试获取BTC的合约数据
        test_symbol = 'BTC/USDT'
        print(f"测试币种: {test_symbol}")
        futures_data = db_service.get_latest_futures_data(test_symbol)

        if futures_data:
            print(f"✅ 数据获取成功:")
            print(f"  持仓量: {futures_data.get('open_interest')}")
            print(f"  多空比: {futures_data.get('long_short_ratio')}")
            print(f"  时间戳: {futures_data.get('timestamp')}")
        else:
            print(f"❌ 未获取到数据")

        print()
        print("=" * 80)

    except Exception as e:
        print(f"❌ 检查失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if session:
            session.close()

if __name__ == "__main__":
    check_futures_tables()
