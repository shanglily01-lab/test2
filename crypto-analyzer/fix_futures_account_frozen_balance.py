#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复合约账户的资金冻结逻辑

问题分析:
1. 开仓时: 只插入 futures_positions,但未冻结资金 (frozen_balance += margin)
2. 平仓时: 解冻资金 (frozen_balance -= margin)
3. 导致: frozen_balance 累计变成负数

修复方案:
1. 创建新表 futures_trading_accounts (专门用于合约交易)
2. 从 paper_trading_accounts 迁移 account_id=2 的数据
3. 根据当前持仓重新计算正确的 frozen_balance
4. 更新代码,确保开仓时冻结资金,平仓时解冻资金

执行时间: 2026-01-23
"""

import pymysql
import sys
import io
from decimal import Decimal

# 修复Windows编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data',
    'charset': 'utf8mb4'
}

def main():
    print("=" * 80)
    print("合约账户资金冻结余额修复脚本")
    print("=" * 80)
    print()

    try:
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # Step 1: 检查当前状态
        print("Step 1: 检查当前账户状态")
        print("-" * 80)

        cursor.execute("""
            SELECT id, account_name, current_balance, frozen_balance, total_equity
            FROM paper_trading_accounts
            WHERE id = 2
        """)
        old_account = cursor.fetchone()

        cursor.execute("""
            SELECT COUNT(*) as count, SUM(margin) as total_margin
            FROM futures_positions
            WHERE account_id = 2 AND status = 'open'
        """)
        position_stats = cursor.fetchone()

        print(f"旧账户 (paper_trading_accounts, id=2):")
        print(f"  账户名称: {old_account['account_name']}")
        print(f"  可用余额: ${old_account['current_balance']:,.2f}")
        print(f"  冻结余额: ${old_account['frozen_balance']:,.2f} ❌ (错误)")
        print(f"  总权益:   ${old_account['total_equity']:,.2f}")
        print()
        print(f"当前持仓统计:")
        print(f"  持仓数量: {position_stats['count']}")
        print(f"  总保证金: ${float(position_stats['total_margin'] or 0):,.2f} ✅ (正确)")
        print()
        print(f"差异分析:")
        correct_frozen = float(position_stats['total_margin'] or 0)
        wrong_frozen = float(old_account['frozen_balance'])
        diff = wrong_frozen - correct_frozen
        print(f"  记录的冻结余额: ${wrong_frozen:,.2f}")
        print(f"  实际应冻结:     ${correct_frozen:,.2f}")
        print(f"  差异:           ${diff:,.2f} (由于平仓时解冻但开仓时未冻结)")
        print()

        # Step 2: 创建新表
        print("Step 2: 创建 futures_trading_accounts 表")
        print("-" * 80)

        # 读取SQL文件
        with open('create_futures_accounts_table.sql', 'r', encoding='utf-8') as f:
            sql_content = f.read()

        # 只执行CREATE TABLE部分
        create_table_sql = sql_content.split('-- 数据迁移')[0]

        # 检查表是否已存在
        cursor.execute("SHOW TABLES LIKE 'futures_trading_accounts'")
        if cursor.fetchone():
            print("  ⚠️  表 futures_trading_accounts 已存在,跳过创建")
        else:
            cursor.execute(create_table_sql)
            print("  ✅ 表 futures_trading_accounts 创建成功")

        # Step 3: 迁移数据
        print()
        print("Step 3: 迁移数据到新表")
        print("-" * 80)

        # 检查是否已迁移
        cursor.execute("SELECT COUNT(*) as count FROM futures_trading_accounts WHERE id = 2")
        if cursor.fetchone()['count'] > 0:
            print("  ⚠️  数据已存在,先删除旧数据")
            cursor.execute("DELETE FROM futures_trading_accounts WHERE id = 2")

        # 迁移数据
        cursor.execute("""
            INSERT INTO futures_trading_accounts (
                id, user_id, account_name,
                initial_balance, current_balance, frozen_balance, total_equity,
                total_profit_loss, total_profit_loss_pct, realized_pnl, unrealized_pnl,
                total_trades, winning_trades, losing_trades, win_rate,
                max_balance, max_drawdown, max_drawdown_pct,
                max_position_size, max_daily_loss,
                default_leverage, default_stop_loss_pct, default_take_profit_pct,
                strategy_name, auto_trading, status, is_default,
                created_at, updated_at
            )
            SELECT
                id, user_id, account_name,
                initial_balance, current_balance, frozen_balance, total_equity,
                total_profit_loss, total_profit_loss_pct, realized_pnl, unrealized_pnl,
                total_trades, winning_trades, losing_trades, win_rate,
                max_balance, max_drawdown, max_drawdown_pct,
                max_position_size, max_daily_loss,
                10 as default_leverage,
                stop_loss_pct as default_stop_loss_pct,
                take_profit_pct as default_take_profit_pct,
                strategy_name, auto_trading, status, is_default,
                created_at, updated_at
            FROM paper_trading_accounts
            WHERE id = 2
        """)
        print(f"  ✅ 数据迁移成功 (迁移了 {cursor.rowcount} 条记录)")

        # Step 4: 修复 frozen_balance
        print()
        print("Step 4: 修复 frozen_balance")
        print("-" * 80)

        cursor.execute("""
            UPDATE futures_trading_accounts
            SET frozen_balance = (
                SELECT COALESCE(SUM(margin), 0)
                FROM futures_positions
                WHERE account_id = 2 AND status = 'open'
            )
            WHERE id = 2
        """)

        print(f"  ✅ frozen_balance 修复成功")

        # Step 5: 验证结果
        print()
        print("Step 5: 验证修复结果")
        print("-" * 80)

        cursor.execute("""
            SELECT
                id, account_name,
                current_balance, frozen_balance, total_equity,
                total_trades, winning_trades, losing_trades, win_rate
            FROM futures_trading_accounts
            WHERE id = 2
        """)
        new_account = cursor.fetchone()

        print(f"新账户 (futures_trading_accounts, id=2):")
        print(f"  账户名称: {new_account['account_name']}")
        print(f"  可用余额: ${new_account['current_balance']:,.2f}")
        print(f"  冻结余额: ${new_account['frozen_balance']:,.2f} ✅ (已修复)")
        print(f"  总权益:   ${new_account['total_equity']:,.2f}")
        print()
        print(f"交易统计:")
        print(f"  总交易: {new_account['total_trades']}")
        print(f"  盈利:   {new_account['winning_trades']}")
        print(f"  亏损:   {new_account['losing_trades']}")
        print(f"  胜率:   {new_account['win_rate']:.1f}%")
        print()

        # 提交
        conn.commit()
        print("=" * 80)
        print("✅ 修复完成!")
        print()
        print("后续步骤:")
        print("1. 更新 smart_trader_service.py:")
        print("   - 开仓时: 冻结资金 (UPDATE futures_trading_accounts SET frozen_balance += margin)")
        print("   - 平仓时: 解冻资金 (UPDATE futures_trading_accounts SET frozen_balance -= margin)")
        print("   - 将所有 paper_trading_accounts 改为 futures_trading_accounts")
        print()
        print("2. 可选: 从 paper_trading_accounts 删除 id=2 (保留现货账户 id=1)")
        print("   DELETE FROM paper_trading_accounts WHERE id = 2;")
        print()
        print("=" * 80)

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()
