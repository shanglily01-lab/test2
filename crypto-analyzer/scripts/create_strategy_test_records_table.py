"""
创建 strategy_test_records 表
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import yaml
from urllib.parse import quote_plus
from sqlalchemy import create_engine
from app.database.models import Base, StrategyTestRecord

def create_table():
    """创建 strategy_test_records 表"""
    
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
        # 构建数据库连接字符串
        host = db_config.get('host', 'localhost')
        port = db_config.get('port', 3306)
        user = db_config.get('user', 'root')
        password = db_config.get('password', '')
        database = db_config.get('database', 'binance-data')
        
        # 对密码进行 URL 编码，处理特殊字符
        encoded_password = quote_plus(str(password))
        db_uri = f"mysql+pymysql://{user}:{encoded_password}@{host}:{port}/{database}?charset=utf8mb4"
        
        print(f"正在连接数据库: {host}:{port}/{database}")
        engine = create_engine(db_uri, echo=False)
        
        # 创建表
        print("正在创建 strategy_test_records 表...")
        Base.metadata.create_all(engine, tables=[StrategyTestRecord.__table__])
        
        print("[OK] strategy_test_records 表创建成功！")
        return True
        
    except Exception as e:
        print(f"[ERROR] 创建表失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    print("=" * 60)
    print("创建 strategy_test_records 表")
    print("=" * 60)
    print()
    
    success = create_table()
    
    if success:
        print("\n[OK] 完成！")
    else:
        print("\n[ERROR] 失败！")
        sys.exit(1)

