#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
添加交易对到黑名单2级
"""
import pymysql
import os
import sys
from dotenv import load_dotenv

# 设置UTF-8输出
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

load_dotenv()

# 数据库配置（使用.env中的DB_NAME）
db_config = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'trading_user'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data'),
    'charset': 'utf8mb4'
}

# 要添加到黑名单2级的交易对
blacklist_level2 = [
    ('ACE/USDT', '12次交易0胜,亏损-42.14U'),
    ('ARB/USDT', '8次交易0胜,亏损-49.00U'),
    ('1000CHEEMS/USDT', '8次交易1胜7负,胜率12.5%,亏损-50.30U'),
    ('S/USDT', '9次交易0胜,亏损-53.31U'),
    ('OM/USDT', '18次交易2胜16负,胜率11.1%,亏损-76.42U'),
    ('INIT/USDT', '4次交易0胜,亏损-76.68U'),
    ('ALLO/USDT', '14次交易2胜12负,胜率14.3%,亏损-150.90U'),
]

try:
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()

    print(f"添加 {len(blacklist_level2)} 个交易对到黑名单2级...")

    for symbol, reason in blacklist_level2:
        cursor.execute("""
            INSERT INTO trading_symbol_rating (symbol, rating_level, reason)
            VALUES (%s, 2, %s)
            ON DUPLICATE KEY UPDATE
                rating_level = 2,
                reason = %s
        """, (symbol, reason, reason))
        print(f"  OK {symbol} - {reason}")

    conn.commit()

    # 查询确认
    print("\n黑名单2级交易对列表:")
    cursor.execute("""
        SELECT symbol, rating_level, reason
        FROM trading_symbol_rating
        WHERE rating_level = 2
        ORDER BY symbol
    """)

    results = cursor.fetchall()
    for row in results:
        symbol, level, reason = row
        print(f"  Level{level} | {symbol:20s} | {reason}")

    print(f"\n成功! 共 {len(results)} 个交易对在黑名单2级")
    print("黑名单2级使用30U小仓位")

    cursor.close()
    conn.close()

except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
