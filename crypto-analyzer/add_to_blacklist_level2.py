#!/usr/bin/env python3
"""
将表现不佳的交易对加入黑名单2级
POL/USDT, HYPE/USDT, IP/USDT, DASH/USDT
"""

import pymysql
from datetime import datetime

# 数据库配置
DB_CONFIG = {
    'host': '13.212.252.171',
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data',
    'charset': 'utf8mb4'
}

# 要加入黑名单2级的交易对
BLACKLIST_SYMBOLS = [
    {
        'symbol': 'POL/USDT',
        'total_trades': 6,
        'win_rate': 0.333,
        'total_loss_amount': 58.50,
        'reason': '胜率33.3%,6笔交易亏损$58.50'
    },
    {
        'symbol': 'HYPE/USDT',
        'total_trades': 9,
        'win_rate': 0.333,
        'total_loss_amount': 105.23,
        'reason': '胜率33.3%,9笔交易亏损$105.23'
    },
    {
        'symbol': 'IP/USDT',
        'total_trades': 6,
        'win_rate': 0.167,
        'total_loss_amount': 108.81,
        'reason': '胜率16.7%,6笔交易亏损$108.81'
    },
    {
        'symbol': 'DASH/USDT',
        'total_trades': 7,
        'win_rate': 0.143,
        'total_loss_amount': 145.48,
        'reason': '胜率14.3%,7笔交易亏损$145.48'
    }
]

def main():
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    try:
        for item in BLACKLIST_SYMBOLS:
            symbol = item['symbol']

            # 先检查是否已存在
            cursor.execute("""
                SELECT rating_level, margin_multiplier
                FROM trading_symbol_rating
                WHERE symbol = %s
            """, (symbol,))

            existing = cursor.fetchone()

            if existing:
                old_level = existing['rating_level']
                print(f"更新 {symbol}: 黑名单{old_level}级 -> 2级")

                cursor.execute("""
                    UPDATE trading_symbol_rating
                    SET rating_level = 2,
                        margin_multiplier = 0.125,
                        total_loss_amount = %s,
                        win_rate = %s,
                        total_trades = %s,
                        reason = %s,
                        previous_level = %s,
                        level_changed_at = NOW(),
                        stats_end_date = CURDATE(),
                        updated_at = NOW()
                    WHERE symbol = %s
                """, (
                    item['total_loss_amount'],
                    item['win_rate'],
                    item['total_trades'],
                    item['reason'],
                    old_level,
                    symbol
                ))
            else:
                print(f"新增 {symbol}: 加入黑名单2级")

                cursor.execute("""
                    INSERT INTO trading_symbol_rating (
                        symbol, rating_level, margin_multiplier, score_bonus,
                        total_loss_amount, win_rate, total_trades, reason,
                        previous_level, level_changed_at,
                        stats_start_date, stats_end_date,
                        created_at, updated_at
                    ) VALUES (
                        %s, 2, 0.125, 0,
                        %s, %s, %s, %s,
                        0, NOW(),
                        CURDATE(), CURDATE(),
                        NOW(), NOW()
                    )
                """, (
                    symbol,
                    item['total_loss_amount'],
                    item['win_rate'],
                    item['total_trades'],
                    item['reason']
                ))

            conn.commit()

        print("\n黑名单2级更新完成!")
        print("=" * 60)

        # 显示当前所有黑名单2级交易对
        cursor.execute("""
            SELECT symbol, rating_level, margin_multiplier,
                   win_rate, total_trades, total_loss_amount, reason,
                   level_changed_at
            FROM trading_symbol_rating
            WHERE rating_level = 2
            ORDER BY total_loss_amount DESC
        """)

        level2_symbols = cursor.fetchall()
        print(f"\n当前黑名单2级交易对 (共{len(level2_symbols)}个):")
        print("-" * 60)

        for row in level2_symbols:
            print(f"{row['symbol']:15} | "
                  f"胜率:{row['win_rate']*100:5.1f}% | "
                  f"交易:{row['total_trades']:3}笔 | "
                  f"亏损:${row['total_loss_amount']:7.2f} | "
                  f"仓位:12.5%")
            if row['reason']:
                print(f"  原因: {row['reason']}")
            print()

    except Exception as e:
        print(f"错误: {e}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    main()
