#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
禁用consecutive_bull/bear信号
因为逻辑不清,性能评分-166,导致交易混乱
"""

import sys
import io
import pymysql

# 设置标准输出编码为UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def disable_consecutive_signals():
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()

    print("=" * 100)
    print("禁用consecutive_bull/bear信号")
    print("=" * 100)

    # 方案1: 设置权重为0 (保留记录,但不参与评分)
    cursor.execute("""
        UPDATE signal_scoring_weights
        SET weight_long = 0,
            weight_short = 0,
            is_active = FALSE,
            updated_at = NOW()
        WHERE signal_component IN ('consecutive_bull', 'consecutive_bear')
    """)

    affected = cursor.rowcount
    conn.commit()

    print(f"\n✅ 已禁用 {affected} 个consecutive信号")

    # 查看当前状态
    cursor.execute("""
        SELECT signal_component, weight_long, weight_short, is_active, performance_score
        FROM signal_scoring_weights
        WHERE signal_component IN ('consecutive_bull', 'consecutive_bear')
    """)

    results = cursor.fetchall()
    print("\n当前状态:")
    for r in results:
        print(f"  {r[0]}: 做多{r[1]}, 做空{r[2]}, 激活:{r[3]}, 性能:{r[4]}")

    cursor.close()
    conn.close()

    print("\n" + "=" * 100)
    print("原因: consecutive信号逻辑不清(做多做空都加分),性能评分-166.26,是最差的信号")
    print("建议: 等数据积累后,拆分为continuation(延续)和exhaustion(衰竭)两个独立信号")
    print("=" * 100)

if __name__ == '__main__':
    disable_consecutive_signals()
