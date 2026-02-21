#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量将表现差的交易对加入黑名单2级"""
import sys
import os
from datetime import datetime, date
import mysql.connector
from dotenv import load_dotenv

# 设置UTF-8输出
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 加载环境变量
load_dotenv()

def get_db_connection():
    """获取数据库连接"""
    return mysql.connector.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=int(os.getenv('DB_PORT', 3306)),
        user=os.getenv('DB_USER', 'root'),
        password=os.getenv('DB_PASSWORD', ''),
        database=os.getenv('DB_NAME', 'crypto_trading')
    )

# 需要加入黑名单2级的交易对数据
# 格式: (交易对, 开单数, 盈利单, 亏损单, 平仓, 胜率%, 总盈亏, 总盈利, 平均盈亏, 最大盈利, 最大亏损)
blacklist_symbols = [
    ('EUL/USDT', 4, 0, 4, 0, 0, -12.10, 0.00, -3.02, -1.72, -5.25),
    ('LINK/USDT', 1, 0, 1, 0, 0, -12.25, 0.00, -12.25, -12.25, -12.25),
    ('FIL/USDT', 3, 0, 3, 0, 0, -12.66, 0.00, -4.22, -3.89, -4.39),
    ('CYBER/USDT', 3, 1, 2, 0, 33.3, -13.17, 10.08, -11.62, 10.08, -18.65),
    ('GPS/USDT', 3, 1, 2, 0, 33.3, -21.73, 10.09, -15.91, 10.09, -19.83),
    ('DYDX/USDT', 3, 1, 2, 0, 33.3, -25.39, 4.17, -14.78, 4.17, -16.29),
    ('ETC/USDT', 4, 0, 4, 0, 0, -33.83, 0.00, -8.46, -7.62, -10.34),
    ('LDO/USDT', 1, 0, 1, 0, 0, -35.34, 0.00, -35.34, -35.34, -35.34),
    ('STRK/USDT', 1, 0, 1, 0, 0, -35.42, 0.00, -35.42, -35.42, -35.42),
    ('ARKM/USDT', 5, 0, 5, 0, 0, -36.07, 0.00, -7.21, -6.15, -8.97),
    ('ACE/USDT', 4, 0, 4, 0, 0, -36.51, 0.00, -9.13, -8.37, -10.49),
    ('W/USDT', 1, 0, 1, 0, 0, -38.98, 0.00, -38.98, -38.98, -38.98),
    ('C98/USDT', 6, 1, 5, 0, 16.7, -40.16, 2.21, -8.47, 2.21, -9.26),
    ('SOLV/USDT', 4, 0, 4, 0, 0, -40.73, 0.00, -10.18, -9.39, -10.54),
    ('NEWT/USDT', 1, 0, 1, 0, 0, -42.61, 0.00, -42.61, -42.61, -42.61),
    ('MANTA/USDT', 3, 0, 3, 0, 0, -45.64, 0.00, -15.21, -3.02, -25.96),
    ('INIT/USDT', 4, 0, 4, 0, 0, -76.68, 0.00, -19.17, -17.28, -20.00),
]

def add_to_blacklist():
    """批量添加到黑名单2级"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 统计日期（假设统计最近一个月）
        stats_end_date = date.today()
        stats_start_date = date(stats_end_date.year, stats_end_date.month - 1 if stats_end_date.month > 1 else 12, 1)

        success_count = 0
        update_count = 0

        for symbol_data in blacklist_symbols:
            symbol = symbol_data[0]
            total_trades = symbol_data[1]
            win_trades = symbol_data[2]
            loss_trades = symbol_data[3]
            win_rate = symbol_data[5] / 100.0  # 转换为小数
            total_pnl = symbol_data[6]
            total_profit = symbol_data[7]
            avg_pnl = symbol_data[8]
            max_profit = symbol_data[9]
            max_loss = abs(symbol_data[10])  # 转换为正数

            # 总亏损 = 总盈利 - 总盈亏
            total_loss = total_profit - total_pnl

            # 评级原因
            reason = f"批量加入黑名单2级: 总交易{total_trades}次, 胜率{win_rate*100:.1f}%, 总亏损{total_pnl:.2f}U, 平均亏损{avg_pnl:.2f}U, 最大亏损{max_loss:.2f}U"

            # 检查是否已存在
            cursor.execute("SELECT id, rating_level FROM trading_symbol_rating WHERE symbol = %s", (symbol,))
            existing = cursor.fetchone()

            if existing:
                # 更新现有记录
                old_level = existing[1]
                cursor.execute("""
                    UPDATE trading_symbol_rating
                    SET
                        rating_level = 2,
                        reason = %s,
                        hard_stop_loss_count = %s,
                        total_loss_amount = %s,
                        total_profit_amount = %s,
                        win_rate = %s,
                        total_trades = %s,
                        previous_level = %s,
                        level_changed_at = %s,
                        level_change_reason = %s,
                        stats_start_date = %s,
                        stats_end_date = %s
                    WHERE symbol = %s
                """, (
                    reason,
                    loss_trades,
                    total_loss,
                    total_profit,
                    win_rate,
                    total_trades,
                    old_level,
                    datetime.now(),
                    f"从等级{old_level}调整为黑名单2级（表现差）",
                    stats_start_date,
                    stats_end_date,
                    symbol
                ))
                print(f"✅ 更新 {symbol}: 等级 {old_level} -> 2 (黑名单2级)")
                update_count += 1
            else:
                # 插入新记录
                cursor.execute("""
                    INSERT INTO trading_symbol_rating
                    (symbol, rating_level, reason, hard_stop_loss_count, total_loss_amount,
                     total_profit_amount, win_rate, total_trades, stats_start_date, stats_end_date,
                     previous_level, level_changed_at, level_change_reason)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    symbol,
                    2,  # 黑名单2级
                    reason,
                    loss_trades,
                    total_loss,
                    total_profit,
                    win_rate,
                    total_trades,
                    stats_start_date,
                    stats_end_date,
                    None,  # 之前没有等级
                    datetime.now(),
                    "新增黑名单2级（表现差）"
                ))
                print(f"✅ 新增 {symbol}: 黑名单2级")
                success_count += 1

        conn.commit()

        print()
        print('=' * 100)
        print(f'批量加入黑名单2级完成')
        print(f'新增: {success_count} 个')
        print(f'更新: {update_count} 个')
        print(f'总计: {success_count + update_count} 个交易对')
        print('=' * 100)

        cursor.close()

    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    add_to_blacklist()
