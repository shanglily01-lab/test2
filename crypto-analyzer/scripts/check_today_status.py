#!/usr/bin/env python3
"""查询今日交易状态"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymysql
from datetime import datetime

def get_db_connection():
    """获取数据库连接"""
    return pymysql.connect(
        host='13.212.252.171',
        port=3306,
        user='admin',
        password='Tonny@1000',
        database='binance-data',
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

def main():
    print("=" * 80)
    print(f"今日交易统计 ({datetime.now().strftime('%Y-%m-%d')})")
    print("=" * 80)

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 今日交易统计
        cursor.execute("""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
                SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losing_trades,
                SUM(realized_pnl) as total_pnl,
                AVG(realized_pnl) as avg_pnl,
                MIN(realized_pnl) as worst_loss,
                MAX(realized_pnl) as best_profit
            FROM futures_positions
            WHERE status = 'closed'
            AND account_id = 2
            AND DATE(close_time) = CURDATE()
        """)

        stats = cursor.fetchone()
        if stats and stats['total_trades']:
            win_rate = (stats['winning_trades'] / stats['total_trades'] * 100)
            print(f"总交易: {stats['total_trades']} 笔")
            print(f"盈利: {stats['winning_trades']} 笔, 亏损: {stats['losing_trades']} 笔")
            print(f"胜率: {win_rate:.1f}%")
            print(f"总盈亏: ${stats['total_pnl']:.2f}")
            print(f"平均盈亏: ${stats['avg_pnl']:.2f}")
            print(f"最大亏损: ${stats['worst_loss']:.2f}")
            print(f"最大盈利: ${stats['best_profit']:.2f}")
        else:
            print("今日暂无交易")

        # 当前持仓
        print("\n" + "=" * 80)
        print("当前持仓")
        print("=" * 80)
        cursor.execute("""
            SELECT
                symbol, position_side, entry_price, mark_price,
                unrealized_pnl, unrealized_pnl_pct,
                TIMESTAMPDIFF(MINUTE, open_time, NOW()) as holding_minutes
            FROM futures_positions
            WHERE status = 'open' AND account_id = 2
            ORDER BY unrealized_pnl_pct ASC
        """)

        positions = cursor.fetchall()
        if positions:
            print(f"共 {len(positions)} 个持仓:\n")
            for pos in positions:
                pnl_pct = float(pos['unrealized_pnl_pct'] or 0)
                status = "危险" if pnl_pct < -2.0 else "警告" if pnl_pct < 0 else "正常"
                print(f"[{status}] {pos['symbol']} {pos['position_side']}: "
                      f"入场${pos['entry_price']:.4f}, "
                      f"当前${pos['mark_price']:.4f}, "
                      f"盈亏{pnl_pct:.2f}%, "
                      f"持仓{pos['holding_minutes']}分钟")
        else:
            print("无持仓")

        # 账户状态
        print("\n" + "=" * 80)
        print("账户状态")
        print("=" * 80)
        cursor.execute("""
            SELECT current_balance, total_equity, realized_pnl,
                   total_profit_loss, total_profit_loss_pct, win_rate
            FROM paper_trading_accounts WHERE id = 2
        """)
        account = cursor.fetchone()
        if account:
            print(f"可用余额: ${account['current_balance']:.2f}")
            print(f"总权益: ${account['total_equity']:.2f}")
            print(f"已实现盈亏: ${account['realized_pnl']:.2f}")
            print(f"总盈亏: ${account['total_profit_loss']:.2f} ({account['total_profit_loss_pct']:.2f}%)")
            print(f"历史胜率: {account['win_rate']:.1f}%")

    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    main()
