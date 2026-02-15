#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手动触发评分更新
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
    """触发评分更新"""
    print("\n" + "="*80)
    print("🔄 手动触发评分更新")
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
        cursor = conn.cursor()

        # 检查交易对数量
        cursor.execute("SELECT COUNT(*) FROM trading_symbols WHERE enabled = 1")
        symbol_count = cursor.fetchone()[0]
        print(f"\n📊 共有 {symbol_count} 个启用的交易对需要更新")

        if symbol_count == 0:
            print("\n⚠️ 没有启用的交易对，请先运行 python sync_symbols_to_db.py")
            cursor.close()
            conn.close()
            return False

        # 执行更新
        print(f"\n⏳ 正在更新所有代币评分（预计需要 {symbol_count * 0.3:.0f} 秒）...")
        start_time = time.time()

        cursor.execute("CALL update_all_coin_scores()")
        conn.commit()

        elapsed = time.time() - start_time
        print(f"✅ 更新完成，耗时 {elapsed:.1f} 秒")

        # 检查结果
        cursor.execute("SELECT COUNT(*) FROM coin_kline_scores")
        score_count = cursor.fetchone()[0]
        print(f"\n📊 评分表中共有 {score_count} 条记录")

        # 显示Top 5
        cursor.execute("""
            SELECT symbol, total_score, direction, strength_level, updated_at
            FROM coin_kline_scores
            ORDER BY ABS(total_score) DESC
            LIMIT 5
        """)
        results = cursor.fetchall()

        if results:
            print("\n🏆 Top 5 评分:")
            print("-"*80)
            for r in results:
                print(f"  {r[0]:<15} 总分:{r[1]:+4d}  方向:{r[2]:<8}  强度:{r[3]:<8}  更新:{r[4]}")

        cursor.close()
        conn.close()

        print("\n" + "="*80)
        print("✅ 评分更新成功")
        print("="*80)

        return True

    except Exception as e:
        print(f"\n❌ 更新失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    trigger_update()
