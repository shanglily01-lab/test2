#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手动触发Big4评分更新
"""
import sys
import os
from dotenv import load_dotenv
import pymysql
import time

# 设置UTF-8输出
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 加载环境变量
load_dotenv()


def trigger_update():
    """触发Big4评分更新"""
    print("\n" + "="*80)
    print("🔄 手动触发Big4评分更新")
    print("="*80)

    # 数据库配置
    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 3306)),
        'user': os.getenv('DB_USER', 'root'),
        'password': os.getenv('DB_PASSWORD', ''),
        'database': os.getenv('DB_NAME', 'binance-data')
    }

    try:
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 执行更新
        print(f"\n⏳ 正在更新Big4评分...")
        start_time = time.time()

        cursor.execute("CALL calculate_big4_score()")
        conn.commit()

        elapsed = time.time() - start_time
        print(f"✅ 更新完成，耗时 {elapsed:.2f} 秒")

        # 查询结果
        cursor.execute("""
            SELECT total_score, main_score, five_m_bonus, h1_score, m15_score,
                   direction, strength_level, reason, updated_at
            FROM big4_kline_scores
            WHERE exchange = 'binance_futures'
            LIMIT 1
        """)
        result = cursor.fetchone()

        if result:
            print("\n📊 Big4评分结果:")
            print("-"*80)
            print(f"  总分: {result['total_score']:+d}")
            print(f"  主分: {result['main_score']:+d} (1H:{result['h1_score']:+d} + 15M:{result['m15_score']:+d})")
            print(f"  方向: {result['direction']}")
            print(f"  强度: {result['strength_level']}")
            print(f"  原因: {result['reason']}")
            print(f"  更新时间: {result['updated_at']}")
        else:
            print("\n⚠️ 没有找到Big4评分数据")

        cursor.close()
        conn.close()

        print("\n" + "="*80)
        print("✅ Big4评分更新成功")
        print("="*80)

        return True

    except Exception as e:
        print(f"\n❌ 更新失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    trigger_update()
