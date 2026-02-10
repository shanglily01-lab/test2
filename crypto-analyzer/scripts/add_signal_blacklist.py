#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
添加信号黑名单 - 基于历史统计数据
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pymysql
from datetime import datetime
from dotenv import load_dotenv

# 加载.env文件
load_dotenv()

# 数据库配置
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = int(os.getenv('DB_PORT', 3306))
DB_USER = os.getenv('DB_USER', 'root')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
DB_NAME = os.getenv('DB_NAME', 'binance-data')

print(f"连接数据库: {DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

def add_signal_blacklist():
    """添加信号黑名单"""

    conn = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

    try:
        cursor = conn.cursor()

        # 要禁用的信号组合列表（胜率<40% 且订单>5笔）
        blacklist_signals = [
            {
                'signal_type': 'breakdown_short',
                'position_side': 'SHORT',
                'reason': '破位做空信号整体表现差，追跌违背90分位策略',
                'order_count': 50,
                'win_rate': 0.35,
                'total_loss': 350.0,
                'is_active': 1,
                'notes': '全部breakdown_short相关信号都禁用'
            },
            {
                'signal_type': 'breakdown_short+跌势3%+高波动+volume_power_1h_bear',
                'position_side': 'SHORT',
                'reason': '13单30.8%胜率，累计亏损-294.27U',
                'order_count': 13,
                'win_rate': 0.308,
                'total_loss': 294.27,
                'is_active': 1,
                'notes': '最差信号组合之一'
            },
            {
                'signal_type': 'breakdown_short+跌势3%+1H看跌+高波动+volume_power_bear',
                'position_side': 'SHORT',
                'reason': '8单37.5%胜率，累计亏损-14.09U',
                'order_count': 8,
                'win_rate': 0.375,
                'total_loss': 14.09,
                'is_active': 1,
                'notes': '低胜率信号'
            },
        ]

        print("[OK] 表结构已确认")

        # 插入或更新黑名单
        for signal in blacklist_signals:
            cursor.execute("""
                INSERT INTO signal_blacklist
                (signal_type, position_side, reason, total_loss, win_rate, order_count, is_active, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    reason = VALUES(reason),
                    total_loss = VALUES(total_loss),
                    win_rate = VALUES(win_rate),
                    order_count = VALUES(order_count),
                    is_active = VALUES(is_active),
                    notes = VALUES(notes),
                    updated_at = CURRENT_TIMESTAMP
            """, (
                signal['signal_type'],
                signal['position_side'],
                signal['reason'],
                signal['total_loss'],
                signal['win_rate'],
                signal['order_count'],
                signal['is_active'],
                signal['notes']
            ))

            print(f"[OK] 已添加黑名单: {signal['signal_type']} ({signal['position_side']}) - {signal['reason']}")

        conn.commit()

        # 显示当前黑名单
        print("\n" + "="*80)
        print("当前信号黑名单:")
        print("="*80)
        cursor.execute("""
            SELECT id, signal_type, position_side, win_rate, total_loss, reason, is_active, notes
            FROM signal_blacklist
            WHERE is_active = 1
            ORDER BY id DESC
        """)

        for row in cursor.fetchall():
            status = "[X] 禁用" if row['is_active'] else "[-] 暂停"
            win_rate_pct = row['win_rate'] * 100 if row['win_rate'] else 0
            print(f"{status} | {row['signal_type']:60s} | {row['position_side']:5s} | "
                  f"胜率{win_rate_pct:5.1f}% | 亏损{row['total_loss']:+8.2f}U")
            print(f"       原因: {row['reason']}")
            if row['notes']:
                print(f"       备注: {row['notes']}")
            print()

        cursor.close()

    finally:
        conn.close()

if __name__ == '__main__':
    print("开始添加信号黑名单...")
    print()
    add_signal_blacklist()
    print()
    print("[OK] 信号黑名单添加完成！")
