#!/usr/bin/env python3
"""
检查投资建议缓存表的数据
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database.db_service import DatabaseService
from sqlalchemy import text
import json
import yaml

def check_recommendations():
    # 加载配置
    config_path = os.path.join(os.path.dirname(__file__), 'config', 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    db_service = DatabaseService(config.get('database', {}))
    session = None

    try:
        session = db_service.get_session()

        # 1. 检查表是否存在
        print("=" * 80)
        print("1. 检查表是否存在")
        print("=" * 80)
        result = session.execute(text("""
            SELECT COUNT(*) as count
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
            AND table_name = 'investment_recommendations_cache'
        """))
        row = result.fetchone()
        table_exists = row[0] > 0
        print(f"✅ 表存在: {table_exists}")

        if not table_exists:
            print("❌ 表不存在！需要执行SQL脚本创建表")
            return

        # 2. 检查表中的记录数
        print("\n" + "=" * 80)
        print("2. 检查表中的记录数")
        print("=" * 80)
        result = session.execute(text("""
            SELECT COUNT(*) as count
            FROM investment_recommendations_cache
        """))
        row = result.fetchone()
        count = row[0]
        print(f"记录总数: {count}")

        if count == 0:
            print("❌ 表是空的！需要运行缓存更新服务填充数据")
            print("运行: python scripts/管理/update_cache_manual.py")
            return

        # 3. 查看最近的几条记录
        print("\n" + "=" * 80)
        print("3. 查看最近的记录（前5条）")
        print("=" * 80)
        result = session.execute(text("""
            SELECT
                symbol, total_score, `signal`, confidence,
                current_price, entry_price,
                has_technical, has_news, has_funding,
                data_completeness, updated_at
            FROM investment_recommendations_cache
            ORDER BY updated_at DESC
            LIMIT 5
        """))

        rows = result.fetchall()
        for row in rows:
            row_dict = dict(row._mapping)
            print(f"\n币种: {row_dict['symbol']}")
            print(f"  信号: {row_dict['signal']}")
            print(f"  总分: {row_dict['total_score']}")
            print(f"  信心: {row_dict['confidence']}")
            print(f"  当前价格: {row_dict['current_price']}")
            print(f"  入场价格: {row_dict['entry_price']}")
            print(f"  数据完整度: {row_dict['data_completeness']}%")
            print(f"  数据源: 技术={row_dict['has_technical']}, 新闻={row_dict['has_news']}, 资金费率={row_dict['has_funding']}")
            print(f"  更新时间: {row_dict['updated_at']}")

        # 4. 检查signal字段的分布
        print("\n" + "=" * 80)
        print("4. 检查信号分布")
        print("=" * 80)
        result = session.execute(text("""
            SELECT `signal`, COUNT(*) as count
            FROM investment_recommendations_cache
            GROUP BY `signal`
        """))

        rows = result.fetchall()
        for row in rows:
            print(f"  {row[0]}: {row[1]} 条")

        # 5. 检查一条完整记录
        print("\n" + "=" * 80)
        print("5. 查看一条完整记录（JSON格式）")
        print("=" * 80)
        result = session.execute(text("""
            SELECT *
            FROM investment_recommendations_cache
            ORDER BY updated_at DESC
            LIMIT 1
        """))

        row = result.fetchone()
        if row:
            row_dict = dict(row._mapping)
            # 转换为JSON格式，方便查看
            formatted_data = {
                'symbol': row_dict['symbol'],
                'signal': row_dict['signal'],
                'confidence': float(row_dict['confidence']) if row_dict['confidence'] else 0,
                'scores': {
                    'total': float(row_dict['total_score']) if row_dict['total_score'] else 0,
                    'technical': float(row_dict['technical_score']) if row_dict['technical_score'] else 0,
                    'news': float(row_dict['news_score']) if row_dict['news_score'] else 0,
                    'funding': float(row_dict['funding_score']) if row_dict['funding_score'] else 0,
                },
                'prices': {
                    'current': float(row_dict['current_price']) if row_dict['current_price'] else 0,
                    'entry': float(row_dict['entry_price']) if row_dict['entry_price'] else 0,
                    'stop_loss': float(row_dict['stop_loss']) if row_dict['stop_loss'] else 0,
                    'take_profit': float(row_dict['take_profit']) if row_dict['take_profit'] else 0,
                },
                'reasons': json.loads(row_dict['reasons']) if row_dict['reasons'] else [],
                'risk_level': row_dict['risk_level'],
                'data_completeness': float(row_dict['data_completeness']) if row_dict['data_completeness'] else 0,
                'updated_at': str(row_dict['updated_at'])
            }
            print(json.dumps(formatted_data, indent=2, ensure_ascii=False))

        print("\n" + "=" * 80)
        print("✅ 检查完成！")
        print("=" * 80)

    except Exception as e:
        print(f"❌ 检查失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if session:
            session.close()

if __name__ == "__main__":
    check_recommendations()
