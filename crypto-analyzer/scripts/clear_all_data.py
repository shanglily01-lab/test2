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
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
        read_timeout=300,  # 5分钟读取超时
        write_timeout=300  # 5分钟写入超时
    )
    cursor = connection.cursor()
    
    def safe_delete(table_name, use_truncate=False):
        """安全删除表数据"""
        try:
            # 先查询数据量
            cursor.execute(f"SELECT COUNT(*) as count FROM {table_name}")
            count_result = cursor.fetchone()
            count = count_result['count'] if count_result else 0
            
            if count == 0:
                print(f"  ⚠ 表 {table_name} 为空，跳过")
                return 0
            
            print(f"  📊 表 {table_name} 共有 {count} 条记录，开始删除...")
            sys.stdout.flush()
            
            if use_truncate and count > 1000:
                # 对于大表使用TRUNCATE（更快）
                cursor.execute(f"TRUNCATE TABLE {table_name}")
                deleted_count = count
            else:
                # 小表使用DELETE
                cursor.execute(f"DELETE FROM {table_name}")
                deleted_count = cursor.rowcount
            
            connection.commit()  # 每步都提交，避免长时间锁定
            print(f"  ✓ 已删除 {deleted_count} 条记录")
            sys.stdout.flush()
            return deleted_count
        except Exception as e:
            connection.rollback()
            print(f"  ⚠ 表 {table_name} 删除失败: {e}")
            sys.stdout.flush()
            return 0
    
    try:
        print("=" * 60)
        print("开始清理所有数据...")
        print("=" * 60)
        sys.stdout.flush()
        
        deleted_counts = {}
        
        # 1. 清理策略交易记录
        print("\n[1/9] 清理策略交易记录...")
        sys.stdout.flush()
        deleted_counts['strategy_trade_records'] = safe_delete('strategy_trade_records')
        
        # 2. 清理策略测试记录
        print("\n[2/9] 清理策略测试记录...")
        sys.stdout.flush()
        deleted_counts['strategy_test_records'] = safe_delete('strategy_test_records')
        
        # 3. 清理策略执行结果详情
        print("\n[3/9] 清理策略执行结果详情...")
        sys.stdout.flush()
        try:
            deleted_counts['strategy_execution_result_details'] = safe_delete('strategy_execution_result_details')
        except Exception as e:
            print(f"  ⚠ 表 strategy_execution_result_details 不存在: {e}")
            deleted_counts['strategy_execution_result_details'] = 0
        
        # 4. 清理策略执行结果
        print("\n[4/9] 清理策略执行结果...")
        sys.stdout.flush()
        try:
            deleted_counts['strategy_execution_results'] = safe_delete('strategy_execution_results')
        except Exception as e:
            print(f"  ⚠ 表 strategy_execution_results 不存在: {e}")
            deleted_counts['strategy_execution_results'] = 0
        
        # 5. 清理策略命中记录
        print("\n[5/9] 清理策略命中记录...")
        sys.stdout.flush()
        try:
            deleted_counts['strategy_hits'] = safe_delete('strategy_hits')
        except Exception as e:
            print(f"  ⚠ 表 strategy_hits 不存在: {e}")
            deleted_counts['strategy_hits'] = 0
        
        # 6. 清理策略资金管理记录
        print("\n[6/9] 清理策略资金管理记录...")
        sys.stdout.flush()
        try:
            deleted_counts['strategy_capital_management'] = safe_delete('strategy_capital_management')
        except Exception as e:
            print(f"  ⚠ 表 strategy_capital_management 不存在: {e}")
            deleted_counts['strategy_capital_management'] = 0
        
        # 7. 清理合约数据
        print("\n[7/9] 清理合约数据...")
        sys.stdout.flush()
        
        # 清理合约持仓
        try:
            cursor.execute("SELECT COUNT(*) as count FROM futures_positions WHERE account_id = %s", (account_id,))
            count_result = cursor.fetchone()
            count = count_result['count'] if count_result else 0
            if count > 0:
                print(f"  📊 合约持仓共有 {count} 条记录，开始删除...")
                sys.stdout.flush()
                cursor.execute("DELETE FROM futures_positions WHERE account_id = %s", (account_id,))
                deleted_counts['futures_positions'] = cursor.rowcount
                connection.commit()
                print(f"  ✓ 已删除 {cursor.rowcount} 条合约持仓记录")
            else:
                print(f"  ⚠ 合约持仓为空，跳过")
                deleted_counts['futures_positions'] = 0
            sys.stdout.flush()
        except Exception as e:
            print(f"  ⚠ 表 futures_positions 不存在或删除失败: {e}")
            deleted_counts['futures_positions'] = 0
            sys.stdout.flush()
        
        # 清理合约交易
        try:
            cursor.execute("SELECT COUNT(*) as count FROM futures_trades WHERE account_id = %s", (account_id,))
            count_result = cursor.fetchone()
            count = count_result['count'] if count_result else 0
            if count > 0:
                print(f"  📊 合约交易共有 {count} 条记录，开始删除...")
                sys.stdout.flush()
                cursor.execute("DELETE FROM futures_trades WHERE account_id = %s", (account_id,))
                deleted_counts['futures_trades'] = cursor.rowcount
                connection.commit()
                print(f"  ✓ 已删除 {cursor.rowcount} 条合约交易记录")
            else:
                print(f"  ⚠ 合约交易为空，跳过")
                deleted_counts['futures_trades'] = 0
            sys.stdout.flush()
        except Exception as e:
            print(f"  ⚠ 表 futures_trades 不存在或删除失败: {e}")
            deleted_counts['futures_trades'] = 0
            sys.stdout.flush()
        
        # 8. 清理现货数据（使用TRUNCATE，因为可能数据量很大）
        print("\n[8/9] 清理现货数据...")
        sys.stdout.flush()
        
        # 清理价格数据
        deleted_counts['price_data'] = safe_delete('price_data', use_truncate=True)
        
        # 清理K线数据
        deleted_counts['kline_data'] = safe_delete('kline_data', use_truncate=True)
        
        # 清理交易数据
        deleted_counts['trade_data'] = safe_delete('trade_data', use_truncate=True)
        
        # 清理订单簿数据
        deleted_counts['orderbook_data'] = safe_delete('orderbook_data', use_truncate=True)
        
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
        
        # 打印总结
        print("\n" + "=" * 60)
        print("数据清理完成！")
        print("=" * 60)
        sys.stdout.flush()
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

