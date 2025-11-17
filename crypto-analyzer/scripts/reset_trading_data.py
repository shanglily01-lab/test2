#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
清除模拟合约和模拟现货的数据，并重置初始资金
用于重置所有交易记录，重新开始测试
"""

import sys
import os
import io
from pathlib import Path

# Windows 控制台编码修复
if sys.platform == 'win32':
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import yaml
import pymysql
from loguru import logger
from decimal import Decimal

# 配置日志
logger.remove()
logger.add(sys.stdout, format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>", level="INFO")


def get_db_config():
    """读取数据库配置"""
    config_path = project_root / 'config.yaml'
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    db_config = config.get('database', {}).get('mysql', {})
    return {
        'host': db_config.get('host', 'localhost'),
        'port': db_config.get('port', 3306),
        'user': db_config.get('user', 'root'),
        'password': db_config.get('password', ''),
        'database': db_config.get('database', 'binance-data'),
        'charset': 'utf8mb4'
    }


def reset_trading_data(new_initial_balance: float = None, account_id: int = None):
    """
    清除模拟合约和模拟现货的数据，并重置初始资金
    
    Args:
        new_initial_balance: 新的初始资金（如果为None，则保持原有初始余额）
        account_id: 指定账户ID（如果为None，则重置所有账户）
    """
    db_config = get_db_config()
    
    try:
        # 连接数据库
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor()
        
        logger.info("=" * 60)
        logger.info("开始清除模拟交易数据并重置账户...")
        logger.info("=" * 60)
        
        # 定义要清空的表（按顺序，考虑外键约束）
        tables_to_clear = [
            # 合约交易相关表（先清空子表）
            ('futures_trades', '合约交易历史'),
            ('futures_liquidations', '合约强平记录'),
            ('futures_funding_fees', '合约资金费率记录'),
            ('futures_orders', '合约订单'),
            ('futures_positions', '合约持仓'),
            
            # 现货交易相关表（先清空子表）
            ('paper_trading_trades', '现货交易历史'),
            ('paper_trading_balance_history', '现货余额历史'),
            ('paper_trading_signal_executions', '现货信号执行记录'),
            ('paper_trading_pending_orders', '现货待成交订单'),
            ('paper_trading_orders', '现货订单'),
            ('paper_trading_positions', '现货持仓'),
        ]
        
        # 清空表数据
        total_deleted = 0
        for table_name, table_desc in tables_to_clear:
            try:
                # 检查表是否存在
                cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
                if not cursor.fetchone():
                    logger.warning(f"⚠️  表 {table_name} 不存在，跳过")
                    continue
                
                # 获取删除前的记录数
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                count_before = cursor.fetchone()[0]
                
                if count_before == 0:
                    logger.info(f"✓ {table_desc} ({table_name}): 无数据，跳过")
                    continue
                
                # 删除数据
                cursor.execute(f"DELETE FROM {table_name}")
                deleted_count = cursor.rowcount
                total_deleted += deleted_count
                
                logger.info(f"✓ {table_desc} ({table_name}): 已删除 {deleted_count} 条记录")
                
            except Exception as e:
                logger.error(f"❌ 清空表 {table_name} 失败: {e}")
                continue
        
        # 重置账户余额和统计数据
        logger.info("=" * 60)
        logger.info("重置账户余额和统计数据...")
        logger.info("=" * 60)
        
        try:
            # 构建WHERE条件
            where_clause = ""
            params = []
            
            if account_id is not None:
                where_clause = "WHERE id = %s"
                params.append(account_id)
                logger.info(f"📌 仅重置账户 ID: {account_id}")
            else:
                logger.info("📌 重置所有账户")
            
            # 查询账户信息
            if account_id is not None:
                cursor.execute(f"SELECT id, account_name, account_type, initial_balance FROM paper_trading_accounts WHERE id = %s", (account_id,))
            else:
                cursor.execute(f"SELECT id, account_name, account_type, initial_balance FROM paper_trading_accounts")
            
            accounts = cursor.fetchall()
            
            if not accounts:
                logger.warning("⚠️  未找到任何账户")
            else:
                for account in accounts:
                    acc_id, acc_name, acc_type, old_initial = account
                    
                    # 确定新的初始余额
                    if new_initial_balance is not None:
                        new_initial = Decimal(str(new_initial_balance))
                        logger.info(f"📝 账户 {acc_id} ({acc_name}): 初始资金 {old_initial} → {new_initial} USDT")
                    else:
                        new_initial = Decimal(str(old_initial))
                        logger.info(f"📝 账户 {acc_id} ({acc_name}): 保持初始资金 {old_initial} USDT")
                    
                    # 重置账户
                    cursor.execute("""
                        UPDATE paper_trading_accounts 
                        SET 
                            initial_balance = %s,
                            current_balance = %s,
                            frozen_balance = 0.00,
                            total_equity = %s,
                            total_profit_loss = 0.00,
                            total_profit_loss_pct = 0.00,
                            realized_pnl = 0.00,
                            unrealized_pnl = 0.00,
                            total_trades = 0,
                            winning_trades = 0,
                            losing_trades = 0,
                            win_rate = 0.00,
                            max_balance = %s,
                            max_drawdown = 0.00,
                            max_drawdown_pct = 0.00
                        WHERE id = %s
                    """, (float(new_initial), float(new_initial), float(new_initial), float(new_initial), acc_id))
                    
                    logger.info(f"✓ 账户 {acc_id} ({acc_name}): 已重置")
            
        except Exception as e:
            logger.error(f"❌ 重置账户失败: {e}")
            import traceback
            traceback.print_exc()
        
        # 提交事务
        connection.commit()
        
        logger.info("=" * 60)
        logger.info(f"✅ 数据清除和重置完成！")
        logger.info(f"   共删除 {total_deleted} 条交易记录")
        logger.info(f"   重置了 {len(accounts)} 个账户")
        if new_initial_balance is not None:
            logger.info(f"   新初始资金: {new_initial_balance} USDT")
        logger.info("=" * 60)
        
        cursor.close()
        connection.close()
        
    except Exception as e:
        logger.error(f"❌ 操作失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(
        description='清除模拟合约和模拟现货的数据，并重置初始资金',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 清除所有数据，保持原有初始资金
  python scripts/reset_trading_data.py
  
  # 清除所有数据，并设置新的初始资金为 20000 USDT
  python scripts/reset_trading_data.py --balance 20000
  
  # 仅重置指定账户（ID=1），设置新初始资金为 15000 USDT
  python scripts/reset_trading_data.py --account-id 1 --balance 15000
  
  # 跳过确认，直接执行
  python scripts/reset_trading_data.py --yes
        """
    )
    parser.add_argument('--balance', '-b', type=float, help='新的初始资金（USDT），如果不指定则保持原有初始余额')
    parser.add_argument('--account-id', '-a', type=int, help='指定账户ID，如果不指定则重置所有账户')
    parser.add_argument('--yes', '-y', action='store_true', help='跳过确认，直接执行')
    args = parser.parse_args()
    
    if not args.yes:
        print("\n" + "=" * 60)
        print("⚠️  警告：此操作将清除所有模拟交易数据！")
        print("=" * 60)
        print("将执行以下操作：")
        print("  - 清除所有订单记录")
        print("  - 清除所有持仓记录")
        print("  - 清除所有交易历史")
        print("  - 清除所有待成交订单")
        print("  - 清除所有余额历史记录")
        if args.balance is not None:
            print(f"  - 重置初始资金为 {args.balance} USDT")
        else:
            print("  - 账户余额将重置为初始余额（保持原有初始资金）")
        if args.account_id is not None:
            print(f"  - 仅重置账户 ID: {args.account_id}")
        else:
            print("  - 重置所有账户")
        print("=" * 60)
        
        try:
            confirm = input("\n确认继续？(输入 'yes' 确认): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n操作已取消")
            sys.exit(0)
        
        if confirm != 'yes':
            print("操作已取消")
            sys.exit(0)
    
    reset_trading_data(
        new_initial_balance=args.balance,
        account_id=args.account_id
    )

