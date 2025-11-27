#!/usr/bin/env python3
"""
账户充值脚本
用于向账户充值并记录到资金管理表
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pymysql
import yaml
from datetime import datetime
from decimal import Decimal

def load_config():
    """加载配置文件"""
    config_path = project_root / 'config.yaml'
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    return config.get('database', {}).get('mysql', {})

def deposit_account(account_id: int, amount: float, reason: str = "账户充值"):
    """
    为账户充值
    
    Args:
        account_id: 账户ID
        amount: 充值金额
        reason: 充值原因
    """
    db_config = load_config()
    
    connection = pymysql.connect(**db_config)
    cursor = connection.cursor(pymysql.cursors.DictCursor)
    
    try:
        # 0. 先修改表结构（如果 strategy_id 不允许 NULL）
        try:
            cursor.execute("""
                ALTER TABLE strategy_capital_management 
                MODIFY COLUMN strategy_id BIGINT NULL COMMENT '策略ID（NULL表示系统操作，如充值、提现）'
            """)
            connection.commit()
        except Exception as alter_error:
            # 如果已经修改过或表不存在，忽略错误
            connection.rollback()
            pass
        
        # 1. 获取当前账户信息
        cursor.execute(
            "SELECT id, current_balance, frozen_balance FROM paper_trading_accounts WHERE id = %s",
            (account_id,)
        )
        account = cursor.fetchone()
        
        if not account:
            print(f"[ERROR] 账户 {account_id} 不存在")
            return False
        
        balance_before = float(account['current_balance'])
        frozen_before = float(account.get('frozen_balance', 0) or 0)
        available_before = balance_before - frozen_before
        
        # 2. 更新账户余额
        cursor.execute(
            "UPDATE paper_trading_accounts SET current_balance = current_balance + %s WHERE id = %s",
            (amount, account_id)
        )
        
        # 3. 获取更新后的账户信息
        cursor.execute(
            "SELECT current_balance, frozen_balance FROM paper_trading_accounts WHERE id = %s",
            (account_id,)
        )
        account_after = cursor.fetchone()
        
        balance_after = float(account_after['current_balance'])
        frozen_after = float(account_after.get('frozen_balance', 0) or 0)
        available_after = balance_after - frozen_after
        
        # 4. 记录到资金管理表（充值记录，strategy_id 可以为 NULL）
        cursor.execute("""
            INSERT INTO strategy_capital_management (
                strategy_id, strategy_name, account_id, symbol,
                change_type, amount_change,
                balance_before, balance_after,
                frozen_before, frozen_after,
                available_before, available_after,
                reason, change_time
            ) VALUES (
                NULL, '系统充值', %s, 'USDT',
                'DEPOSIT', %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, NOW()
            )
        """, (
            account_id,
            amount,
            balance_before,
            balance_after,
            frozen_before,
            frozen_after,
            available_before,
            available_after,
            reason
        ))
        
        connection.commit()
        
        print(f"[SUCCESS] 充值成功！")
        print(f"   账户ID: {account_id}")
        print(f"   充值金额: {amount:.2f} USDT")
        print(f"   充值前余额: {balance_before:.2f} USDT")
        print(f"   充值后余额: {balance_after:.2f} USDT")
        print(f"   可用余额: {available_after:.2f} USDT")
        print(f"   冻结金额: {frozen_after:.2f} USDT")
        
        return True
        
    except Exception as e:
        connection.rollback()
        print(f"[ERROR] 充值失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        cursor.close()
        connection.close()

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='账户充值脚本')
    parser.add_argument('--account-id', type=int, default=2, help='账户ID（默认: 2）')
    parser.add_argument('--amount', type=float, default=10000, help='充值金额（默认: 10000）')
    parser.add_argument('--reason', type=str, default='账户充值', help='充值原因（默认: 账户充值）')
    
    args = parser.parse_args()
    
    print(f"[INFO] 开始充值...")
    print(f"   账户ID: {args.account_id}")
    print(f"   充值金额: {args.amount:.2f} USDT")
    print(f"   充值原因: {args.reason}")
    print()
    
    success = deposit_account(args.account_id, args.amount, args.reason)
    
    if success:
        print("\n[SUCCESS] 充值完成！")
    else:
        print("\n[ERROR] 充值失败！")
        sys.exit(1)

