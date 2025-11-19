#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
清空模拟合约交易数据
用于重置所有合约交易记录，重新开始测试
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


def clear_futures_data():
    """清空模拟合约交易数据"""
    db_config = get_db_config()
    
    try:
        # 连接数据库
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor()
        
        logger.info("=" * 60)
        logger.info("开始清空模拟合约交易数据...")
        logger.info("=" * 60)
        
        # 定义要清空的表（按顺序，考虑外键约束）
        tables_to_clear = [
            # 合约交易相关表（先清空子表）
            ('futures_trades', '合约交易历史'),
            ('futures_liquidations', '合约强平记录'),
            ('futures_funding_fees', '合约资金费率记录'),
            ('futures_orders', '合约订单'),
            ('futures_positions', '合约持仓'),
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
        
        # 重置合约账户余额和统计数据（保留账户，但重置为初始状态）
        logger.info("=" * 60)
        logger.info("重置合约账户余额和统计数据...")
        logger.info("=" * 60)
        
        try:
            # 重置合约账户
            cursor.execute("""
                UPDATE paper_trading_accounts 
                SET 
                    current_balance = initial_balance,
                    frozen_balance = 0.00,
                    total_equity = initial_balance,
                    total_profit_loss = 0.00,
                    total_profit_loss_pct = 0.00,
                    realized_pnl = 0.00,
                    unrealized_pnl = 0.00,
                    total_trades = 0,
                    winning_trades = 0,
                    losing_trades = 0,
                    win_rate = 0.00,
                    max_balance = initial_balance,
                    max_drawdown = 0.00,
                    max_drawdown_pct = 0.00
                WHERE account_type = 'futures'
            """)
            futures_accounts_reset = cursor.rowcount
            logger.info(f"✓ 合约账户: 已重置 {futures_accounts_reset} 个账户")
            
        except Exception as e:
            logger.error(f"❌ 重置账户失败: {e}")
            futures_accounts_reset = 0
        
        # 提交事务
        connection.commit()
        
        logger.info("=" * 60)
        logger.info(f"✅ 合约数据清空完成！")
        logger.info(f"   共删除 {total_deleted} 条交易记录")
        logger.info(f"   重置了 {futures_accounts_reset} 个合约账户")
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
    
    parser = argparse.ArgumentParser(description='清空模拟合约交易数据')
    parser.add_argument('--yes', '-y', action='store_true', help='跳过确认，直接执行')
    args = parser.parse_args()
    
    if not args.yes:
        print("\n" + "=" * 60)
        print("⚠️  警告：此操作将清空所有模拟合约的交易数据！")
        print("=" * 60)
        print("将清空以下数据：")
        print("  - 所有合约订单记录")
        print("  - 所有合约持仓记录")
        print("  - 所有合约交易历史")
        print("  - 所有合约强平记录")
        print("  - 所有合约资金费率记录")
        print("  - 合约账户余额将重置为初始余额")
        print("=" * 60)
        
        try:
            confirm = input("\n确认继续？(输入 'yes' 确认): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n操作已取消")
            sys.exit(0)
        
        if confirm != 'yes':
            print("操作已取消")
            sys.exit(0)
    
    clear_futures_data()

