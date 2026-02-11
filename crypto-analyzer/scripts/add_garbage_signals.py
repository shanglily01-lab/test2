#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
添加垃圾信号到黑名单 - 基于昨晚交易分析
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pymysql
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 数据库配置
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data')
}

# 垃圾信号列表（基于昨晚分析）
garbage_signals = [
    {
        'signal_type': 'volatility_high',
        'position_side': 'LONG',
        'reason': '高波动做多=追涨，4单0%胜率',
        'win_rate': 0.0,
        'total_loss': 152.76,
        'order_count': 4,
        'notes': '昨晚4单全亏，亏损152.76U。高波动时做多容易买在局部高点'
    },
    {
        'signal_type': 'TREND_volatility_high',
        'position_side': 'LONG',
        'reason': '币本位高波动做多，4单0%胜率',
        'win_rate': 0.0,
        'total_loss': 56.56,
        'order_count': 4,
        'notes': '币本位ETC/LINK/DOT/SOL全部亏损，亏损56.56U'
    },
    {
        'signal_type': 'position_mid + volatility_high + volume_power_bear',
        'position_side': 'SHORT',
        'reason': '中位高波动空头信号表现差，2单50%胜率亏损75.95U',
        'win_rate': 0.5,
        'total_loss': 75.95,
        'order_count': 2,
        'notes': '信号矛盾：做空却有量能看跌，但位置在中位'
    },
    {
        'signal_type': 'volatility_high + volume_power_1h_bull',
        'position_side': 'LONG',
        'reason': '高波动+1H量能多头，1单全亏59.80U',
        'win_rate': 0.0,
        'total_loss': 59.80,
        'order_count': 1,
        'notes': 'FRAX/USDT亏损59.80U，持仓超时1H'
    },
    {
        'signal_type': 'position_mid + volume_power_bull',
        'position_side': 'LONG',
        'reason': '中位量能多头，1单全亏51.30U',
        'win_rate': 0.0,
        'total_loss': 51.30,
        'order_count': 1,
        'notes': 'ZEN/USDT亏损51.30U，持仓超时1H'
    },
    {
        'signal_type': 'volatility_high + volume_power_bull',
        'position_side': 'LONG',
        'reason': '高波动+量能多头，1单全亏42.86U',
        'win_rate': 0.0,
        'total_loss': 42.86,
        'order_count': 1,
        'notes': 'ENA/USDT亏损42.86U，持仓超时2H'
    },
    {
        'signal_type': 'TREND_breakdown_short + momentum_down_3pct + volume_power_bear',
        'position_side': 'SHORT',
        'reason': '币本位破位做空，4单25%胜率',
        'win_rate': 0.25,
        'total_loss': 5.93,
        'order_count': 4,
        'notes': 'ATOM/USD连续亏损，胜率过低'
    }
]

print("=" * 100)
print("添加垃圾信号到黑名单（基于昨晚交易分析）")
print("=" * 100)

# 连接数据库
conn = pymysql.connect(**DB_CONFIG, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
cursor = conn.cursor()

print(f"\n数据库: {DB_CONFIG['user']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
print("开始添加信号黑名单...\n")

# 逐个添加黑名单
for signal in garbage_signals:
    try:
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
            1,  # is_active
            signal['notes']
        ))

        conn.commit()
        print(f"[OK] 成功添加黑名单: {signal['signal_type'][:60]} ({signal['position_side']}) - {signal['reason']}")

    except Exception as e:
        print(f"[ERROR] 添加失败: {signal['signal_type']} - {e}")
        conn.rollback()

# 显示当前所有激活的黑名单
print("\n" + "=" * 100)
print("当前信号黑名单:")
print("=" * 100)

cursor.execute("""
    SELECT signal_type, position_side, reason, win_rate, total_loss, order_count, notes, updated_at
    FROM signal_blacklist
    WHERE is_active = 1
    ORDER BY total_loss DESC
""")

blacklist = cursor.fetchall()

for row in blacklist:
    signal = row['signal_type'] or 'NULL'
    side = row['position_side']
    win_rate = float(row['win_rate'] or 0) * 100
    loss = float(row['total_loss'] or 0)
    count = row['order_count'] or 0

    print(f"\n[X] 禁用 | {signal[:70]:<70s} | {side:5s}")
    print(f"       胜率 {win_rate:5.1f}% | 亏损 {loss:+8.2f}U | {count}单")
    print(f"       原因: {row['reason']}")
    if row['notes']:
        print(f"       备注: {row['notes']}")

cursor.close()
conn.close()

print("\n" + "=" * 100)
print(f"完成！共添加 {len(garbage_signals)} 条垃圾信号到黑名单")
print("系统将在5分钟内自动加载黑名单，或调用 brain.reload_config() 立即生效")
print("=" * 100)
