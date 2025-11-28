#!/usr/bin/env python3
"""
清理所有数据脚本
删除策略数据、回测数据、合约数据、现货数据，并重置账号
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

# 确保控制台输出使用UTF-8编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def load_config():
    """加载配置文件"""
    config_path = project_root / 'config.yaml'
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    return config.get('database', {}).get('mysql', {})

def clear_all_data(account_id: int = 2, reset_balance: float = 10000.0):
    """
    清理所有数据并重置账号
    
    Args:
        account_id: 账户ID，默认2
        reset_balance: 重置后的余额，默认10000 USDT
    """
    db_config = load_config()
    
    connection = pymysql.connect(
        host=db_config.get('host', 'localhost'),
        port=db_config.get('port', 3306),
        user=db_config.get('user', 'root'),
        password=db_config.get('password', ''),
        database=db_config.get('database', 'binance-data'),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )
    cursor = connection.cursor()
    
    try:
        print("=" * 60)
        print("开始清理所有数据...")
        print("=" * 60)
        
        deleted_counts = {}
        
        # 1. 清理策略交易记录
        print("\n[1/8] 清理策略交易记录...")
        cursor.execute("DELETE FROM strategy_trade_records")
        deleted_counts['strategy_trade_records'] = cursor.rowcount
        print(f"  ✓ 已删除 {cursor.rowcount} 条策略交易记录")
        
        # 2. 清理策略测试记录
        print("\n[2/8] 清理策略测试记录...")
        cursor.execute("DELETE FROM strategy_test_records")
        deleted_counts['strategy_test_records'] = cursor.rowcount
        print(f"  ✓ 已删除 {cursor.rowcount} 条策略测试记录")
        
        # 3. 清理策略执行结果详情
        print("\n[3/8] 清理策略执行结果详情...")
        try:
            cursor.execute("DELETE FROM strategy_execution_result_details")
            deleted_counts['strategy_execution_result_details'] = cursor.rowcount
            print(f"  ✓ 已删除 {cursor.rowcount} 条策略执行结果详情")
        except Exception as e:
            print(f"  ⚠ 表 strategy_execution_result_details 不存在或删除失败: {e}")
            deleted_counts['strategy_execution_result_details'] = 0
        
        # 4. 清理策略执行结果
        print("\n[4/8] 清理策略执行结果...")
        try:
            cursor.execute("DELETE FROM strategy_execution_results")
            deleted_counts['strategy_execution_results'] = cursor.rowcount
            print(f"  ✓ 已删除 {cursor.rowcount} 条策略执行结果")
        except Exception as e:
            print(f"  ⚠ 表 strategy_execution_results 不存在或删除失败: {e}")
            deleted_counts['strategy_execution_results'] = 0
        
        # 5. 清理策略命中记录
        print("\n[5/8] 清理策略命中记录...")
        try:
            cursor.execute("DELETE FROM strategy_hits")
            deleted_counts['strategy_hits'] = cursor.rowcount
            print(f"  ✓ 已删除 {cursor.rowcount} 条策略命中记录")
        except Exception as e:
            print(f"  ⚠ 表 strategy_hits 不存在或删除失败: {e}")
            deleted_counts['strategy_hits'] = 0
        
        # 6. 清理策略资金管理记录
        print("\n[6/8] 清理策略资金管理记录...")
        try:
            cursor.execute("DELETE FROM strategy_capital_management")
            deleted_counts['strategy_capital_management'] = cursor.rowcount
            print(f"  ✓ 已删除 {cursor.rowcount} 条策略资金管理记录")
        except Exception as e:
            print(f"  ⚠ 表 strategy_capital_management 不存在或删除失败: {e}")
            deleted_counts['strategy_capital_management'] = 0
        
        # 7. 清理合约数据
        print("\n[7/8] 清理合约数据...")
        
        # 清理合约持仓
        try:
            cursor.execute("DELETE FROM futures_positions WHERE account_id = %s", (account_id,))
            deleted_counts['futures_positions'] = cursor.rowcount
            print(f"  ✓ 已删除 {cursor.rowcount} 条合约持仓记录")
        except Exception as e:
            print(f"  ⚠ 表 futures_positions 不存在或删除失败: {e}")
            deleted_counts['futures_positions'] = 0
        
        # 清理合约交易
        try:
            cursor.execute("DELETE FROM futures_trades WHERE account_id = %s", (account_id,))
            deleted_counts['futures_trades'] = cursor.rowcount
            print(f"  ✓ 已删除 {cursor.rowcount} 条合约交易记录")
        except Exception as e:
            print(f"  ⚠ 表 futures_trades 不存在或删除失败: {e}")
            deleted_counts['futures_trades'] = 0
        
        # 8. 清理现货数据
        print("\n[8/8] 清理现货数据...")
        
        # 清理价格数据
        cursor.execute("DELETE FROM price_data")
        deleted_counts['price_data'] = cursor.rowcount
        print(f"  ✓ 已删除 {cursor.rowcount} 条价格数据")
        
        # 清理K线数据
        cursor.execute("DELETE FROM kline_data")
        deleted_counts['kline_data'] = cursor.rowcount
        print(f"  ✓ 已删除 {cursor.rowcount} 条K线数据")
        
        # 清理交易数据
        cursor.execute("DELETE FROM trade_data")
        deleted_counts['trade_data'] = cursor.rowcount
        print(f"  ✓ 已删除 {cursor.rowcount} 条交易数据")
        
        # 清理订单簿数据
        cursor.execute("DELETE FROM orderbook_data")
        deleted_counts['orderbook_data'] = cursor.rowcount
        print(f"  ✓ 已删除 {cursor.rowcount} 条订单簿数据")
        
        # 9. 重置账户余额
        print("\n[9/9] 重置账户余额...")
        cursor.execute("""
            UPDATE paper_trading_accounts 
            SET current_balance = %s,
                frozen_balance = 0,
                total_equity = %s
            WHERE id = %s
        """, (reset_balance, reset_balance, account_id))
        
        if cursor.rowcount > 0:
            print(f"  ✓ 账户 {account_id} 余额已重置为 {reset_balance} USDT")
            print(f"  ✓ 冻结余额已重置为 0")
            print(f"  ✓ 总权益已重置为 {reset_balance} USDT")
        else:
            print(f"  ⚠ 账户 {account_id} 不存在，跳过重置")
        
        # 提交事务
        connection.commit()
        
        # 打印总结
        print("\n" + "=" * 60)
        print("数据清理完成！")
        print("=" * 60)
        print("\n删除统计：")
        total_deleted = 0
        for table, count in deleted_counts.items():
            if count > 0:
                print(f"  - {table}: {count} 条")
                total_deleted += count
        
        print(f"\n总计删除: {total_deleted} 条记录")
        print(f"账户 {account_id} 余额已重置为: {reset_balance} USDT")
        print("=" * 60)
        
    except Exception as e:
        connection.rollback()
        print(f"\n❌ 清理数据时发生错误: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        cursor.close()
        connection.close()

def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='清理所有数据并重置账号')
    parser.add_argument(
        '--account-id',
        type=int,
        default=2,
        help='账户ID（默认: 2）'
    )
    parser.add_argument(
        '--reset-balance',
        type=float,
        default=10000.0,
        help='重置后的余额，单位USDT（默认: 10000）'
    )
    parser.add_argument(
        '--confirm',
        action='store_true',
        help='确认执行（必须指定此参数才会真正执行）'
    )
    
    args = parser.parse_args()
    
    if not args.confirm:
        print("⚠️  警告：此操作将删除所有策略数据、回测数据、合约数据、现货数据，并重置账号！")
        print(f"   账户ID: {args.account_id}")
        print(f"   重置余额: {args.reset_balance} USDT")
        print("\n   如果确定要执行，请添加 --confirm 参数")
        print("   例如: python scripts/clear_all_data.py --confirm")
        return
    
    try:
        clear_all_data(account_id=args.account_id, reset_balance=args.reset_balance)
    except Exception as e:
        print(f"\n❌ 执行失败: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()

