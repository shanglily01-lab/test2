"""
修复 strategy_hits 表的 strategy_id 字段类型
将 INT 改为 BIGINT 以支持大数字的策略ID
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pymysql
import yaml

config_file = project_root / 'config.yaml'
with open(config_file, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config.get('database', {}).get('mysql', {})

connection = pymysql.connect(
    host=db_config.get('host', 'localhost'),
    port=db_config.get('port', 3306),
    user=db_config.get('user', 'root'),
    password=db_config.get('password', ''),
    database=db_config.get('database', 'binance-data'),
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

try:
    cursor = connection.cursor()
    
    # 检查当前字段类型
    cursor.execute("DESCRIBE strategy_hits")
    columns = cursor.fetchall()
    strategy_id_col = next((c for c in columns if c['Field'] == 'strategy_id'), None)
    
    if strategy_id_col:
        current_type = strategy_id_col['Type'].upper()
        print(f"当前 strategy_id 字段类型: {current_type}")
        
        if 'INT' in current_type and 'BIGINT' not in current_type:
            print("\n需要修改字段类型为 BIGINT...")
            
            # 修改字段类型
            alter_sql = "ALTER TABLE strategy_hits MODIFY COLUMN strategy_id BIGINT NOT NULL COMMENT '策略ID'"
            cursor.execute(alter_sql)
            connection.commit()
            
            print("[OK] strategy_id 字段已修改为 BIGINT")
            
            # 验证修改
            cursor.execute("DESCRIBE strategy_hits")
            columns = cursor.fetchall()
            strategy_id_col = next((c for c in columns if c['Field'] == 'strategy_id'), None)
            print(f"修改后的字段类型: {strategy_id_col['Type']}")
        else:
            print("[OK] strategy_id 字段类型已经是 BIGINT，无需修改")
    else:
        print("[ERROR] 未找到 strategy_id 字段")
    
    cursor.close()
except Exception as e:
    print(f"[ERROR] 修改字段时出错: {e}")
    import traceback
    traceback.print_exc()
    connection.rollback()
finally:
    connection.close()

print("\n修复完成！现在可以重新运行策略执行器了。")

