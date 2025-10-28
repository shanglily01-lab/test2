#!/usr/bin/env python3
"""
修复 Paper Trading 账户的胜率计算
重新统计卖出交易，修正 total_trades, winning_trades, losing_trades 和 win_rate
"""

import mysql.connector
import yaml

# 从配置文件读取数据库配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = {
    'host': config['database']['mysql']['host'],
    'port': config['database']['mysql']['port'],
    'user': config['database']['mysql']['user'],
    'password': config['database']['mysql']['password'],
    'database': config['database']['mysql']['database']
}

try:
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor(dictionary=True)

    print('=' * 80)
    print('修复 Paper Trading 胜率计算')
    print('=' * 80)
    print()

    # 获取所有账户
    cursor.execute("SELECT * FROM paper_trading_accounts")
    accounts = cursor.fetchall()

    for account in accounts:
        account_id = account['id']
        account_name = account['account_name']

        print(f"处理账户: {account_name} (ID: {account_id})")
        print('-' * 80)

        # 统计所有卖出交易（只有卖出才算完成一笔交易）
        cursor.execute("""
            SELECT
                COUNT(*) as total_sell_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
                SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losing_trades,
                SUM(CASE WHEN realized_pnl = 0 THEN 1 ELSE 0 END) as break_even_trades
            FROM paper_trading_trades
            WHERE account_id = %s AND side = 'SELL'
        """, (account_id,))

        stats = cursor.fetchone()

        total_trades = stats['total_sell_trades'] or 0
        winning_trades = stats['winning_trades'] or 0
        losing_trades = stats['losing_trades'] or 0
        break_even_trades = stats['break_even_trades'] or 0

        # 计算胜率
        if total_trades > 0:
            win_rate = (winning_trades / total_trades) * 100
        else:
            win_rate = 0

        print(f"  当前数据库记录:")
        print(f"    总交易数: {account['total_trades']}")
        print(f"    盈利笔数: {account['winning_trades']}")
        print(f"    亏损笔数: {account['losing_trades']}")
        print(f"    胜率: {account['win_rate']:.2f}%")
        print()
        print(f"  实际统计（仅卖出交易）:")
        print(f"    总交易数: {total_trades}")
        print(f"    盈利笔数: {winning_trades}")
        print(f"    亏损笔数: {losing_trades}")
        print(f"    盈亏平衡: {break_even_trades}")
        print(f"    正确胜率: {win_rate:.2f}%")
        print()

        # 更新账户数据
        cursor.execute("""
            UPDATE paper_trading_accounts
            SET total_trades = %s,
                winning_trades = %s,
                losing_trades = %s,
                win_rate = %s
            WHERE id = %s
        """, (total_trades, winning_trades, losing_trades, win_rate, account_id))

        print(f"  ✅ 已更新账户数据")
        print()

    conn.commit()
    print('=' * 80)
    print('✅ 所有账户胜率已修复')
    print('=' * 80)

except Exception as e:
    print(f"❌ 错误: {e}")
    import traceback
    traceback.print_exc()
finally:
    if conn:
        conn.close()