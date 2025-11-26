"""
创建 trading_strategies 表的脚本
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pymysql
import yaml

# 加载配置
config_path = project_root / 'config.yaml'
with open(config_path, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config.get('database', {}).get('mysql', {})

# 读取SQL文件
sql_file = project_root / 'scripts' / 'create_trading_strategies_table.sql'
with open(sql_file, 'r', encoding='utf-8') as f:
    sql_content = f.read()

# 连接数据库并执行SQL
try:
    connection = pymysql.connect(**db_config)
    cursor = connection.cursor()
    
    print("正在创建 trading_strategies 表...")
    
    # 移除注释行，然后执行CREATE TABLE语句
    lines = []
    for line in sql_content.split('\n'):
        line = line.strip()
        if line and not line.startswith('--'):
            lines.append(line)
    
    # 找到CREATE TABLE语句
    create_table_sql = ''
    in_create_table = False
    for line in lines:
        if 'CREATE TABLE' in line.upper():
            in_create_table = True
            create_table_sql = line
        elif in_create_table:
            create_table_sql += ' ' + line
            if line.endswith(';'):
                break
    
    if create_table_sql:
        try:
            cursor.execute(create_table_sql)
            print(f"  [OK] 执行成功")
        except Exception as e:
            error_msg = str(e)
            if "already exists" in error_msg.lower() or "Duplicate" in error_msg or "1050" in error_msg:
                print(f"  [SKIP] 表已存在，跳过")
            else:
                print(f"  [ERROR] 执行失败: {e}")
                raise
    else:
        print("  [ERROR] 未找到CREATE TABLE语句")
    
    connection.commit()
    print("\n[OK] SQL语句执行完成")
    
    # 验证表是否存在
    cursor.execute("SHOW TABLES LIKE 'trading_strategies'")
    result = cursor.fetchone()
    if result:
        print("[OK] 表验证成功：trading_strategies 表已存在")
        
        # 显示表结构
        cursor.execute("DESCRIBE trading_strategies")
        columns = cursor.fetchall()
        print("\n表结构：")
        for col in columns:
            null_info = 'NULL' if col[2] == 'YES' else 'NOT NULL'
            default_info = f" DEFAULT {col[4]}" if col[4] else ""
            print(f"  - {col[0]}: {col[1]} ({null_info}{default_info})")
        
        # 检查是否有数据
        cursor.execute("SELECT COUNT(*) as count FROM trading_strategies")
        count = cursor.fetchone()[0]
        print(f"\n当前记录数: {count}")
    else:
        print("[ERROR] 表创建失败，验证时未找到表")
        print("请检查SQL语句是否正确执行")
    
    cursor.close()
    connection.close()
    
except Exception as e:
    print(f"\n[ERROR] 创建表失败: {e}")
    import traceback
    traceback.print_exc()

