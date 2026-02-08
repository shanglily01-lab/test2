#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
更新数据库默认止盈止损参数
执行方式: python update_params.py
"""

import pymysql
import os
import sys
from dotenv import load_dotenv

# 设置控制台编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

def update_params():
    """更新默认参数"""
    conn = pymysql.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME'),
        cursorclass=pymysql.cursors.DictCursor
    )
    cursor = conn.cursor()

    print("\n" + "="*80)
    print("更新默认止盈止损参数")
    print("目标: 止盈6% vs 止损3%，盈亏比2:1")
    print("="*80 + "\n")

    try:
        # 更新LONG参数
        cursor.execute("""
            UPDATE adaptive_params
            SET param_value = '0.03', updated_at = NOW()
            WHERE param_type = 'long' AND param_key = 'long_stop_loss_pct'
        """)
        print(f"✅ 更新LONG止损: 3%")

        cursor.execute("""
            UPDATE adaptive_params
            SET param_value = '0.06', updated_at = NOW()
            WHERE param_type = 'long' AND param_key = 'long_take_profit_pct'
        """)
        print(f"✅ 更新LONG止盈: 6%")

        # 更新SHORT参数
        cursor.execute("""
            UPDATE adaptive_params
            SET param_value = '0.03', updated_at = NOW()
            WHERE param_type = 'short' AND param_key = 'short_stop_loss_pct'
        """)
        print(f"✅ 更新SHORT止损: 3%")

        cursor.execute("""
            UPDATE adaptive_params
            SET param_value = '0.06', updated_at = NOW()
            WHERE param_type = 'short' AND param_key = 'short_take_profit_pct'
        """)
        print(f"✅ 更新SHORT止盈: 6%")

        # 如果不存在则插入
        params = [
            ('long', 'long_stop_loss_pct', '0.03'),
            ('long', 'long_take_profit_pct', '0.06'),
            ('short', 'short_stop_loss_pct', '0.03'),
            ('short', 'short_take_profit_pct', '0.06')
        ]

        for param_type, param_key, param_value in params:
            cursor.execute("""
                INSERT IGNORE INTO adaptive_params
                (param_type, param_key, param_value, created_at, updated_at)
                VALUES (%s, %s, %s, NOW(), NOW())
            """, (param_type, param_key, param_value))

        conn.commit()
        print("\n✅ 所有参数更新成功")

        # 验证更新
        print("\n" + "="*80)
        print("当前参数:")
        print("="*80)
        cursor.execute("""
            SELECT param_type, param_key, param_value
            FROM adaptive_params
            WHERE param_key LIKE '%stop_loss_pct' OR param_key LIKE '%take_profit_pct'
            ORDER BY param_type, param_key
        """)

        rows = cursor.fetchall()
        for row in rows:
            pct = float(row['param_value']) * 100
            print(f"{row['param_type']:<6} {row['param_key']:<25} {pct:.1f}%")

        print("="*80 + "\n")

    except Exception as e:
        print(f"\n❌ 更新失败: {e}")
        conn.rollback()

    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    update_params()
