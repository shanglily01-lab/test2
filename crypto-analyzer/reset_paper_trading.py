#!/usr/bin/env python3
"""
重置模拟盘数据脚本

功能：
1. 清空模拟盘持仓数据
2. 清空模拟盘交易历史
3. 清空模拟盘订单数据
4. 清空回测数据
5. 重置账户资金为初始值

使用方法：
    python reset_paper_trading.py [--account-id 1] [--initial-balance 10000]
"""

import pymysql
import yaml
import argparse
import os
import re
from pathlib import Path
from datetime import datetime


def load_env_file():
    """加载.env文件中的环境变量"""
    env_file = Path(__file__).parent / '.env'

    if not env_file.exists():
        print("⚠️  警告: 未找到.env文件，将使用config.yaml中的默认值")
        return

    with open(env_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            # 跳过注释和空行
            if not line or line.startswith('#'):
                continue

            # 解析 KEY=VALUE 格式
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                # 设置环境变量（如果尚未设置）
                if key and not os.getenv(key):
                    os.environ[key] = value


def resolve_env_var(value):
    """解析环境变量格式 ${VAR_NAME:default_value}"""
    if not isinstance(value, str):
        return value

    # 匹配 ${VAR_NAME:default_value} 格式
    pattern = r'\$\{([^:}]+)(?::([^}]*))?\}'
    match = re.match(pattern, value)

    if match:
        env_var = match.group(1)
        default_value = match.group(2) or ''
        return os.getenv(env_var, default_value)

    return value


def load_config():
    """加载配置文件"""
    config_file = Path(__file__).parent / 'config.yaml'
    with open(config_file, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config


def get_db_connection(config):
    """创建数据库连接"""
    db_config = config['database']['mysql']

    # 解析环境变量格式的配置值
    host = resolve_env_var(db_config['host'])
    port = resolve_env_var(db_config['port'])
    user = resolve_env_var(db_config['user'])
    password = resolve_env_var(db_config['password'])
    database = resolve_env_var(db_config['database'])

    # 确保端口是整数类型
    if isinstance(port, str):
        port = int(port) if port else 3306

    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )


def confirm_action():
    """确认操作"""
    print("\n" + "=" * 70)
    print("  ⚠️  警告：此操作将清空所有模拟盘数据！")
    print("=" * 70)
    print("\n将要删除的数据包括：")
    print("  1. 模拟盘持仓数据 (futures_positions)")
    print("  2. 模拟盘交易历史 (futures_trades)")
    print("  3. 模拟盘订单数据 (futures_orders)")
    print("  4. 回测结果数据 (backtest_results, backtest_trades)")
    print("  5. 策略资金管理记录 (strategy_capital_management)")
    print("  6. 重置账户资金为初始值")
    print("\n此操作不可恢复！")

    response = input("\n确定要继续吗？(yes/no): ").strip().lower()
    return response in ['yes', 'y']


def reset_paper_trading(account_id=1, initial_balance=10000.0):
    """
    重置模拟盘数据

    Args:
        account_id: 账户ID（默认1）
        initial_balance: 初始资金（默认10000 USDT）
    """
    config = load_config()
    connection = get_db_connection(config)

    try:
        cursor = connection.cursor()

        print("\n" + "=" * 70)
        print("  开始重置模拟盘数据")
        print("=" * 70)
        print(f"\n账户ID: {account_id}")
        print(f"初始资金: {initial_balance} USDT")
        print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()

        # 1. 清空模拟盘持仓数据
        print("1. 清空模拟盘持仓数据...")
        cursor.execute("DELETE FROM futures_positions WHERE account_id = %s", (account_id,))
        deleted_positions = cursor.rowcount
        print(f"   ✓ 已删除 {deleted_positions} 条持仓记录")

        # 2. 清空模拟盘交易历史
        print("2. 清空模拟盘交易历史...")
        cursor.execute("DELETE FROM futures_trades WHERE account_id = %s", (account_id,))
        deleted_trades = cursor.rowcount
        print(f"   ✓ 已删除 {deleted_trades} 条交易记录")

        # 3. 清空模拟盘订单数据
        print("3. 清空模拟盘订单数据...")
        cursor.execute("DELETE FROM futures_orders WHERE account_id = %s", (account_id,))
        deleted_orders = cursor.rowcount
        print(f"   ✓ 已删除 {deleted_orders} 条订单记录")

        # 4. 清空回测结果数据
        print("4. 清空回测数据...")

        deleted_backtest_trades = 0
        deleted_backtests = 0

        # 检查表是否存在
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
            AND table_name = 'backtest_trades'
        """)
        backtest_trades_exists = cursor.fetchone()['count'] > 0

        cursor.execute("""
            SELECT COUNT(*) as count
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
            AND table_name = 'backtest_results'
        """)
        backtest_results_exists = cursor.fetchone()['count'] > 0

        # 先删除回测交易明细（外键关联）
        if backtest_trades_exists and backtest_results_exists:
            cursor.execute("""
                DELETE bt FROM backtest_trades bt
                INNER JOIN backtest_results br ON bt.backtest_id = br.id
                WHERE br.account_id = %s
            """, (account_id,))
            deleted_backtest_trades = cursor.rowcount
            print(f"   ✓ 已删除 {deleted_backtest_trades} 条回测交易记录")
        else:
            print(f"   ⊘ 跳过回测交易记录（表不存在）")

        # 再删除回测结果
        if backtest_results_exists:
            cursor.execute("DELETE FROM backtest_results WHERE account_id = %s", (account_id,))
            deleted_backtests = cursor.rowcount
            print(f"   ✓ 已删除 {deleted_backtests} 条回测结果")
        else:
            print(f"   ⊘ 跳过回测结果（表不存在）")

        # 5. 清空策略资金管理记录
        print("5. 清空策略资金管理记录...")

        deleted_capital = 0

        # 检查表是否存在
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
            AND table_name = 'strategy_capital_management'
        """)
        capital_exists = cursor.fetchone()['count'] > 0

        if capital_exists:
            cursor.execute("DELETE FROM strategy_capital_management WHERE account_id = %s", (account_id,))
            deleted_capital = cursor.rowcount
            print(f"   ✓ 已删除 {deleted_capital} 条资金管理记录")
        else:
            print(f"   ⊘ 跳过资金管理记录（表不存在）")

        # 6. 重置账户资金
        print("6. 重置账户资金...")
        cursor.execute("""
            UPDATE futures_accounts
            SET balance = %s,
                available = %s,
                frozen = 0,
                total_pnl = 0,
                today_pnl = 0,
                total_trades = 0,
                winning_trades = 0,
                losing_trades = 0,
                win_rate = 0,
                total_commission = 0,
                updated_at = NOW()
            WHERE id = %s
        """, (initial_balance, initial_balance, account_id))

        if cursor.rowcount == 0:
            print(f"   ⚠️  警告：账户ID {account_id} 不存在，尝试创建...")
            cursor.execute("""
                INSERT INTO futures_accounts (
                    id, account_name, balance, available, frozen,
                    total_pnl, today_pnl, total_trades, winning_trades, losing_trades,
                    win_rate, total_commission, created_at, updated_at
                ) VALUES (
                    %s, '默认模拟账户', %s, %s, 0,
                    0, 0, 0, 0, 0,
                    0, 0, NOW(), NOW()
                )
            """, (account_id, initial_balance, initial_balance))
            print(f"   ✓ 已创建新账户并设置初始资金")
        else:
            print(f"   ✓ 账户资金已重置为 {initial_balance} USDT")

        # 提交事务
        connection.commit()

        print("\n" + "=" * 70)
        print("  ✅ 模拟盘数据重置完成！")
        print("=" * 70)
        print("\n数据清理汇总：")
        print(f"  • 持仓记录: {deleted_positions} 条")
        print(f"  • 交易记录: {deleted_trades} 条")
        print(f"  • 订单记录: {deleted_orders} 条")
        print(f"  • 回测结果: {deleted_backtests} 条")
        print(f"  • 回测交易: {deleted_backtest_trades} 条")
        print(f"  • 资金管理: {deleted_capital} 条")
        print(f"\n账户状态：")
        print(f"  • 账户ID: {account_id}")
        print(f"  • 当前余额: {initial_balance} USDT")
        print(f"  • 可用资金: {initial_balance} USDT")
        print(f"  • 冻结资金: 0 USDT")
        print()

    except Exception as e:
        connection.rollback()
        print(f"\n❌ 重置失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        cursor.close()
        connection.close()

    return True


def main():
    """主函数"""
    # 首先加载.env文件
    load_env_file()

    parser = argparse.ArgumentParser(
        description='重置模拟盘数据并重置账户资金',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 使用默认值（账户ID=1，初始资金=10000 USDT）
  python reset_paper_trading.py

  # 指定账户ID
  python reset_paper_trading.py --account-id 1

  # 指定初始资金
  python reset_paper_trading.py --initial-balance 50000

  # 同时指定账户ID和初始资金
  python reset_paper_trading.py --account-id 1 --initial-balance 50000
        """
    )

    parser.add_argument(
        '--account-id',
        type=int,
        default=1,
        help='账户ID（默认: 1）'
    )

    parser.add_argument(
        '--initial-balance',
        type=float,
        default=10000.0,
        help='初始资金 USDT（默认: 10000）'
    )

    parser.add_argument(
        '--yes',
        action='store_true',
        help='跳过确认提示，直接执行'
    )

    args = parser.parse_args()

    # 确认操作
    if not args.yes:
        if not confirm_action():
            print("\n操作已取消。")
            return

    # 执行重置
    success = reset_paper_trading(
        account_id=args.account_id,
        initial_balance=args.initial_balance
    )

    if success:
        print("提示：重启交易服务以确保数据同步。\n")

    exit(0 if success else 1)


if __name__ == '__main__':
    main()
