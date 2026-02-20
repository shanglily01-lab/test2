#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复 futures_trading_accounts 表的数据
根据 futures_positions 表重新计算所有关键指标
"""
import sys
import os
import pymysql
import yaml

# 设置UTF-8输出
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 添加项目路径
sys.path.insert(0, os.path.dirname(__file__))

from decimal import Decimal
import re

# 加载 .env 文件
try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    load_dotenv(env_path)
except ImportError:
    print("警告: 未安装 python-dotenv，尝试直接从环境变量读取")

def get_db_config():
    """从配置文件读取数据库配置"""
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config['database']['mysql']

def parse_env_var(value, default=None):
    """解析环境变量格式的值 ${ENV_VAR:default_value}"""
    if not isinstance(value, str):
        return value

    # 匹配 ${VAR:default} 格式
    match = re.match(r'\$\{([^:}]+):?([^}]*)\}', value)
    if match:
        env_var = match.group(1)
        default_val = match.group(2)
        # 优先使用环境变量，否则使用默认值
        return os.environ.get(env_var, default_val or default)
    return value

def get_db_connection():
    """获取数据库连接"""
    db_config = get_db_config()

    # 解析配置中的环境变量
    host = parse_env_var(db_config.get('host'), 'localhost')
    port = parse_env_var(db_config.get('port'), '3306')
    user = parse_env_var(db_config.get('user'), 'root')
    password = parse_env_var(db_config.get('password'), '')
    database = parse_env_var(db_config.get('database'), 'binance-data')

    return pymysql.connect(
        host=host,
        port=int(port),
        user=user,
        password=password,
        database=database,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

def fix_account_data(account_id=2):
    """修复账户数据"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        print('=' * 100)
        print(f'开始修复账户 {account_id} 的数据...')
        print('=' * 100)
        print()

        # 1. 获取账户初始余额
        cursor.execute(
            "SELECT id, account_name, initial_balance FROM futures_trading_accounts WHERE id = %s",
            (account_id,)
        )
        account = cursor.fetchone()
        if not account:
            print(f"❌ 未找到账户 {account_id}")
            return

        initial_balance = Decimal(str(account['initial_balance']))
        print(f"账户名称: {account['account_name']}")
        print(f"初始余额: {initial_balance:.2f} USDT")
        print()

        # 2. 从 futures_positions 表统计已关闭的持仓
        cursor.execute("""
            SELECT
                COUNT(*) as total_closed,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning_count,
                SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losing_count,
                SUM(CASE WHEN realized_pnl = 0 THEN 1 ELSE 0 END) as breakeven_count,
                COALESCE(SUM(realized_pnl), 0) as total_realized_pnl
            FROM futures_positions
            WHERE account_id = %s AND status = 'closed'
        """, (account_id,))

        stats = cursor.fetchone()

        total_trades = int(stats['total_closed'])
        winning_trades = int(stats['winning_count'])
        losing_trades = int(stats['losing_count'])
        breakeven_trades = int(stats['breakeven_count'])
        realized_pnl = Decimal(str(stats['total_realized_pnl']))

        # 计算胜率
        if total_trades > 0:
            win_rate = (winning_trades / total_trades) * 100
        else:
            win_rate = Decimal('0')

        # 计算当前余额
        current_balance = initial_balance + realized_pnl

        print("📊 从 futures_positions 表统计的数据:")
        print(f"  总交易数: {total_trades}")
        print(f"  盈利交易: {winning_trades}")
        print(f"  亏损交易: {losing_trades}")
        print(f"  保本交易: {breakeven_trades}")
        print(f"  胜率: {win_rate:.2f}%")
        print(f"  已实现盈亏: {realized_pnl:+.2f} USDT")
        print(f"  应有余额: {current_balance:.2f} USDT")
        print()

        # 3. 查看当前账户表中的数据
        cursor.execute("""
            SELECT total_trades, winning_trades, losing_trades, win_rate,
                   realized_pnl, current_balance, frozen_balance
            FROM futures_trading_accounts
            WHERE id = %s
        """, (account_id,))

        old_data = cursor.fetchone()
        print("📋 当前 futures_trading_accounts 表中的数据:")
        print(f"  总交易数: {old_data['total_trades']}")
        print(f"  盈利交易: {old_data['winning_trades']}")
        print(f"  亏损交易: {old_data['losing_trades']}")
        print(f"  胜率: {old_data['win_rate']:.2f}%")
        print(f"  已实现盈亏: {old_data['realized_pnl']:+.2f} USDT")
        print(f"  当前余额: {old_data['current_balance']:.2f} USDT")
        print(f"  冻结余额: {old_data['frozen_balance']:.2f} USDT")
        print()

        # 4. 显示差异
        print("⚠️  数据差异:")
        print(f"  总交易数差异: {total_trades - old_data['total_trades']}")
        print(f"  盈利交易差异: {winning_trades - old_data['winning_trades']}")
        print(f"  亏损交易差异: {losing_trades - old_data['losing_trades']}")
        print(f"  胜率差异: {win_rate - float(old_data['win_rate']):.2f}%")
        pnl_diff = realized_pnl - Decimal(str(old_data['realized_pnl']))
        balance_diff = current_balance - Decimal(str(old_data['current_balance']))
        print(f"  已实现盈亏差异: {pnl_diff:+.2f} USDT")
        print(f"  当前余额差异: {balance_diff:+.2f} USDT")
        print()

        # 5. 更新账户数据
        print("🔧 开始修复数据...")
        cursor.execute("""
            UPDATE futures_trading_accounts
            SET total_trades = %s,
                winning_trades = %s,
                losing_trades = %s,
                win_rate = %s,
                realized_pnl = %s,
                current_balance = %s
            WHERE id = %s
        """, (
            total_trades,
            winning_trades,
            losing_trades,
            float(win_rate),
            float(realized_pnl),
            float(current_balance),
            account_id
        ))

        # 6. 更新总权益
        cursor.execute("""
            UPDATE futures_trading_accounts a
            SET a.total_equity = a.current_balance + a.frozen_balance + COALESCE((
                SELECT SUM(p.unrealized_pnl)
                FROM futures_positions p
                WHERE p.account_id = a.id AND p.status = 'open'
            ), 0)
            WHERE a.id = %s
        """, (account_id,))

        # 7. 更新总盈亏百分比
        cursor.execute("""
            UPDATE futures_trading_accounts
            SET total_profit_loss_pct = ((total_equity - initial_balance) / initial_balance) * 100
            WHERE id = %s
        """, (account_id,))

        conn.commit()

        print("✅ 数据修复完成！")
        print()

        # 8. 显示修复后的数据
        cursor.execute("""
            SELECT total_trades, winning_trades, losing_trades, win_rate,
                   realized_pnl, current_balance, frozen_balance,
                   total_equity, total_profit_loss_pct
            FROM futures_trading_accounts
            WHERE id = %s
        """, (account_id,))

        new_data = cursor.fetchone()
        print("✨ 修复后的数据:")
        print(f"  总交易数: {new_data['total_trades']}")
        print(f"  盈利交易: {new_data['winning_trades']}")
        print(f"  亏损交易: {new_data['losing_trades']}")
        print(f"  胜率: {new_data['win_rate']:.2f}%")
        print(f"  已实现盈亏: {new_data['realized_pnl']:+.2f} USDT")
        print(f"  当前余额: {new_data['current_balance']:.2f} USDT")
        print(f"  冻结余额: {new_data['frozen_balance']:.2f} USDT")
        print(f"  总权益: {new_data['total_equity']:.2f} USDT")
        print(f"  总盈亏率: {new_data['total_profit_loss_pct']:+.2f}%")
        print()
        print('=' * 100)

    except Exception as e:
        conn.rollback()
        print(f"❌ 修复失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    # 默认修复账户ID=2（U本位合约账户）
    fix_account_data(account_id=2)
