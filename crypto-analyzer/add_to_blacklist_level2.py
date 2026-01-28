#!/usr/bin/env python3
"""
添加表现差的交易对到黑名单2级
"""
import mysql.connector
from datetime import datetime

DB_CONFIG = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

# 需要加入黑名单2级的交易对
BLACKLIST_SYMBOLS = [
    ('FLUID/USDT', '6单0胜率,亏损$-68.09,平均ROI -13.62%'),
    ('ZRO/USDT', '10单20%胜率,亏损$-97.27,平均ROI -15.85%'),
    ('ROSE/USDT', '6单16.7%胜率,亏损$-108.53,平均ROI -30.41%'),
    ('SHELL/USDT', '8单12.5%胜率,亏损$-115.47,平均ROI -20.30%')
]

def add_to_blacklist_level2():
    """添加交易对到黑名单2级"""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    print(f"开始添加 {len(BLACKLIST_SYMBOLS)} 个交易对到黑名单2级\n")

    for symbol, reason in BLACKLIST_SYMBOLS:
        # 检查是否已存在
        cursor.execute("""
            SELECT id, rating_level, reason
            FROM trading_symbol_rating
            WHERE symbol = %s
        """, (symbol,))

        existing = cursor.fetchone()

        if existing:
            old_level = existing['rating_level']
            if old_level == 2:
                print(f"✓ {symbol} 已经在黑名单2级")
                continue
            else:
                # 更新等级
                cursor.execute("""
                    UPDATE trading_symbol_rating
                    SET rating_level = 2,
                        reason = %s,
                        previous_level = %s,
                        level_changed_at = NOW(),
                        level_change_reason = %s,
                        updated_at = NOW()
                    WHERE symbol = %s
                """, (reason, old_level, '手动调整到黑名单2级', symbol))
                print(f"↑ {symbol} 从等级{old_level}升级到等级2")
        else:
            # 插入新记录
            cursor.execute("""
                INSERT INTO trading_symbol_rating
                (symbol, rating_level, reason, margin_multiplier,
                 stats_start_date, stats_end_date, created_at, updated_at)
                VALUES (%s, 2, %s, 0.5, CURDATE(), CURDATE(), NOW(), NOW())
            """, (symbol, reason))
            print(f"+ {symbol} 添加到黑名单2级")

        print(f"  原因: {reason}\n")

    conn.commit()
    cursor.close()
    conn.close()

    print("✅ 完成!")

if __name__ == '__main__':
    add_to_blacklist_level2()
