"""
清理 Gas 统计数据中的测试数据
"""
import sys
import os
import yaml
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import mysql.connector
from mysql.connector import pooling
from loguru import logger

def get_db_config():
    """从 config.yaml 读取数据库配置"""
    config_path = project_root / "config.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    db_config = config.get('database', {}).get('mysql', {})
    return {
        'host': db_config.get('host', 'localhost'),
        'port': db_config.get('port', 3306),
        'user': db_config.get('user', 'root'),
        'password': db_config.get('password', ''),
        'database': db_config.get('database', 'binance-data')
    }

def clean_gas_test_data(delete_all=False, auto_confirm=False):
    """
    清理 Gas 统计数据
    
    Args:
        delete_all: 如果为 True，删除所有数据；否则只删除可能的测试数据
        auto_confirm: 如果为 True，自动确认删除操作
    """
    db_config = get_db_config()
    
    try:
        # 连接数据库
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        
        # 先查看当前数据
        cursor.execute("SELECT COUNT(*) as count FROM blockchain_gas_daily")
        total_count = cursor.fetchone()['count']
        
        logger.info(f"当前 Gas 统计数据总数: {total_count}")
        
        if total_count == 0:
            logger.info("没有数据需要清理")
            cursor.close()
            conn.close()
            return
        
        # 查看数据样本
        cursor.execute("""
            SELECT 
                chain_name, 
                date, 
                data_source, 
                total_gas_used, 
                total_transactions,
                total_gas_value_usd,
                created_at
            FROM blockchain_gas_daily 
            ORDER BY created_at DESC 
            LIMIT 10
        """)
        samples = cursor.fetchall()
        
        logger.info("\n数据样本（最近10条）:")
        logger.info("-" * 100)
        for sample in samples:
            logger.info(f"链: {sample['chain_name']:15} | 日期: {sample['date']} | "
                       f"数据源: {sample['data_source']:10} | "
                       f"Gas使用: {sample['total_gas_used']:>20} | "
                       f"交易数: {sample['total_transactions']:>15} | "
                       f"价值: ${sample['total_gas_value_usd']:>15.2f} | "
                       f"创建时间: {sample['created_at']}")
        logger.info("-" * 100)
        
        if delete_all:
            # 删除所有数据
            logger.warning(f"\n⚠️  将删除所有 {total_count} 条 Gas 统计数据！")
            if not auto_confirm:
                confirm = input("确认删除所有数据？(输入 'YES' 确认): ")
            else:
                confirm = 'YES'
                logger.info("自动确认：将删除所有数据")
            
            if confirm == 'YES':
                cursor.execute("DELETE FROM blockchain_gas_daily")
                deleted_count = cursor.rowcount
                conn.commit()
                logger.info(f"✅ 成功删除 {deleted_count} 条数据")
            else:
                logger.info("❌ 操作已取消")
        else:
            # 尝试识别测试数据
            # 可能的测试数据特征：
            # 1. data_source 包含 'test'、'sample' 或 'estimated'
            # 2. 交易数为 0 或异常小
            # 3. Gas 使用量为 0
            # 4. Gas 价值为 0
            
            # 检查是否是测试数据（data_source 为 'estimated' 且价值为 0）
            cursor.execute("""
                SELECT COUNT(*) as count 
                FROM blockchain_gas_daily 
                WHERE 
                    (data_source LIKE '%test%' OR data_source LIKE '%sample%' OR data_source = 'estimated')
                    OR total_transactions = 0
                    OR total_gas_used = 0
                    OR total_gas_value_usd = 0
            """)
            test_count = cursor.fetchone()['count']
            
            if test_count > 0:
                logger.info(f"\n发现 {test_count} 条可能的测试数据（包括 estimated 数据源）")
                if not auto_confirm:
                    confirm = input("确认删除这些测试数据？(输入 'YES' 确认): ")
                else:
                    confirm = 'YES'
                    logger.info("自动确认：将删除测试数据")
                
                if confirm == 'YES':
                    cursor.execute("""
                        DELETE FROM blockchain_gas_daily 
                        WHERE 
                            (data_source LIKE '%test%' OR data_source LIKE '%sample%' OR data_source = 'estimated')
                            OR total_transactions = 0
                            OR total_gas_used = 0
                            OR total_gas_value_usd = 0
                    """)
                    deleted_count = cursor.rowcount
                    conn.commit()
                    logger.info(f"✅ 成功删除 {deleted_count} 条测试数据")
                else:
                    logger.info("❌ 操作已取消")
            else:
                logger.info("未发现明显的测试数据特征")
                logger.info("如果确定所有数据都是测试数据，请使用 --delete-all 参数")
        
        cursor.close()
        conn.close()
        
    except mysql.connector.Error as e:
        logger.error(f"数据库操作失败: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"清理失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="清理 Gas 统计数据中的测试数据")
    parser.add_argument("--delete-all", action="store_true", 
                       help="删除所有 Gas 统计数据（危险操作）")
    parser.add_argument("--yes", "-y", action="store_true",
                       help="自动确认删除操作（非交互模式）")
    
    args = parser.parse_args()
    
    logger.info("=" * 60)
    logger.info("Gas 统计数据清理工具")
    logger.info("=" * 60)
    
    clean_gas_test_data(delete_all=args.delete_all, auto_confirm=args.yes)
    
    logger.info("\n" + "=" * 60)
    logger.info("清理完成")
    logger.info("=" * 60)

