"""
修复 strategy_trade_records 表中 strategy_id 字段类型
将 Integer 改为 BigInteger 以支持更大的策略ID值
"""

import pymysql
import yaml
from pathlib import Path

def fix_strategy_id_type():
    """修改 strategy_trade_records 表的 strategy_id 字段类型"""
    
    # 读取配置文件
    config_path = Path('config.yaml')
    if not config_path.exists():
        print("[ERROR] 配置文件不存在: config.yaml")
        return False
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    db_config = config.get('database', {}).get('mysql', {})
    if not db_config:
        print("[ERROR] 数据库配置不存在")
        return False
    
    try:
        # 连接数据库
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor()
        
        try:
            # 检查当前字段类型
            cursor.execute("""
                SELECT COLUMN_TYPE 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'strategy_trade_records' 
                AND COLUMN_NAME = 'strategy_id'
            """)
            result = cursor.fetchone()
            
            if result:
                current_type = result[0]
                print(f"当前 strategy_id 字段类型: {current_type}")
                
                if 'bigint' in current_type.lower():
                    print("[OK] strategy_id 字段已经是 BIGINT 类型，无需修改")
                    return True
                
                # 修改字段类型
                print("正在修改 strategy_id 字段类型为 BIGINT...")
                cursor.execute("""
                    ALTER TABLE strategy_trade_records 
                    MODIFY COLUMN strategy_id BIGINT NOT NULL
                """)
                connection.commit()
                print("[OK] strategy_id 字段类型已成功修改为 BIGINT")
                return True
            else:
                print("[ERROR] 未找到 strategy_trade_records 表或 strategy_id 字段")
                return False
                
        except Exception as e:
            connection.rollback()
            print(f"[ERROR] 修改字段类型失败: {e}")
            return False
        finally:
            cursor.close()
            connection.close()
            
    except Exception as e:
        print(f"[ERROR] 数据库连接失败: {e}")
        return False

if __name__ == '__main__':
    print("开始修复 strategy_id 字段类型...")
    success = fix_strategy_id_type()
    if success:
        print("\n[OK] 修复完成！")
    else:
        print("\n[ERROR] 修复失败！")
