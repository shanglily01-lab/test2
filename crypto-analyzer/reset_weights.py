#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重置评分权重到合理值"""

import pymysql
import os
import sys
from dotenv import load_dotenv

# 设置输出编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME')
}

# 合理的权重配置（基于代码默认值）
CORRECT_WEIGHTS = {
    'position_low': {'long': 20, 'short': 0},
    'position_mid': {'long': 10, 'short': 5},
    'position_high': {'long': 0, 'short': 20},
    'momentum_down_3pct': {'long': 0, 'short': 15},
    'momentum_up_3pct': {'long': 15, 'short': 0},
    'trend_1h_bull': {'long': 20, 'short': 0},
    'trend_1h_bear': {'long': 0, 'short': 20},
    'trend_1d_bull': {'long': 20, 'short': 0},
    'trend_1d_bear': {'long': 0, 'short': 20},
    'volatility_high': {'long': 10, 'short': 10},
    'consecutive_bull': {'long': 15, 'short': 0},
    'consecutive_bear': {'long': 0, 'short': 15},
    'volume_power_bull': {'long': 25, 'short': 0},
    'volume_power_bear': {'long': 0, 'short': 25},
    'volume_power_1h_bull': {'long': 15, 'short': 0},
    'volume_power_1h_bear': {'long': 0, 'short': 15},
    'breakout_long': {'long': 20, 'short': 0},
    'breakdown_short': {'long': 0, 'short': 20}
}

try:
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()

    print("=== 开始重置权重 ===\n")

    for component, weights in CORRECT_WEIGHTS.items():
        long_w = weights['long']
        short_w = weights['short']

        cursor.execute("""
            UPDATE signal_scoring_weights
            SET weight_long = %s,
                weight_short = %s,
                base_weight = %s,
                last_adjusted = NOW(),
                updated_at = NOW(),
                description = 'Reset to default values'
            WHERE signal_component = %s
        """, (long_w, short_w, max(long_w, short_w), component))

        print(f"✅ {component:25} -> LONG={long_w:2d}  SHORT={short_w:2d}")

    conn.commit()
    print(f"\n✅ 成功重置 {len(CORRECT_WEIGHTS)} 个权重配置")

    cursor.close()
    conn.close()

except Exception as e:
    print(f"❌ 重置失败: {e}")
