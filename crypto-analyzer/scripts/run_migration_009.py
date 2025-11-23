"""
运行数据库迁移 009：创建策略命中记录表
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pymysql
import yaml
from loguru import logger

# 配置日志
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO",
    colorize=True
)


def run_migration():
    """运行数据库迁移"""
    # 加载配置
    config_path = project_root / "config.yaml"
    if not config_path.exists():
        logger.error("配置文件不存在: config.yaml")
        return False
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    db_config = config.get('database', {}).get('mysql', {})
    
    # 读取迁移SQL文件
    migration_file = project_root / "scripts" / "migrations" / "009_create_strategy_hits_table.sql"
    if not migration_file.exists():
        logger.error(f"迁移文件不存在: {migration_file}")
        return False
    
    with open(migration_file, 'r', encoding='utf-8') as f:
        sql_content = f.read()
    
    # 连接数据库
    try:
        connection = pymysql.connect(
            host=db_config.get('host', 'localhost'),
            port=db_config.get('port', 3306),
            user=db_config.get('user', 'root'),
            password=db_config.get('password', ''),
            database=db_config.get('database', 'binance-data'),
            charset='utf8mb4'
        )
        
        cursor = connection.cursor()
        
        try:
            # 执行SQL（按语句分割）
            statements = [s.strip() for s in sql_content.split(';') if s.strip() and not s.strip().startswith('--')]
            
            for statement in statements:
                if statement:
                    logger.info(f"执行SQL: {statement[:100]}...")
                    cursor.execute(statement)
            
            connection.commit()
            logger.info("✅ 数据库迁移成功完成！")
            return True
            
        except Exception as e:
            connection.rollback()
            logger.error(f"执行迁移失败: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            cursor.close()
            connection.close()
            
    except Exception as e:
        logger.error(f"连接数据库失败: {e}")
        return False


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("开始运行数据库迁移 009：创建策略命中记录表")
    logger.info("=" * 60)
    
    success = run_migration()
    
    if success:
        logger.info("=" * 60)
        logger.info("✅ 迁移完成！")
        logger.info("=" * 60)
    else:
        logger.error("=" * 60)
        logger.error("❌ 迁移失败！")
        logger.error("=" * 60)
        sys.exit(1)

