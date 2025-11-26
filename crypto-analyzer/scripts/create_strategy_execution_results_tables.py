#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
创建策略执行结果表的脚本
用于保存策略自动执行的汇总结果和详细信息
"""

import pymysql
import yaml
import os
import sys

# 获取项目根目录
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

def load_config():
    """加载配置文件"""
    config_path = os.path.join(project_root, 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def create_tables():
    """创建策略执行结果表"""
    config = load_config()
    db_config = config.get('database', {}).get('mysql', {})
    
    if not db_config:
        print("[ERROR] 未找到数据库配置")
        return False
    
    try:
        # 读取SQL文件
        sql_file = os.path.join(project_root, 'scripts', 'create_strategy_execution_results_tables.sql')
        with open(sql_file, 'r', encoding='utf-8') as f:
            sql_content = f.read()
        
        # 连接数据库
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor()
        
        try:
            # 执行SQL语句（按分号分割，但保留多行语句）
            # 移除注释行
            lines = []
            for line in sql_content.split('\n'):
                line = line.strip()
                if line and not line.startswith('--'):
                    lines.append(line)
            
            # 合并为完整SQL语句
            sql_text = ' '.join(lines)
            # 按分号分割，但排除空语句
            statements = [s.strip() for s in sql_text.split(';') if s.strip()]
            
            for statement in statements:
                if statement:
                    try:
                        cursor.execute(statement)
                        print(f"[OK] 执行成功: {statement[:80]}...")
                    except Exception as e:
                        # 如果表已存在，忽略错误
                        error_msg = str(e).lower()
                        if 'already exists' in error_msg or 'duplicate' in error_msg or '1050' in error_msg:
                            print(f"[WARN] 表已存在，跳过")
                        else:
                            print(f"[ERROR] 执行失败: {e}")
                            print(f"   SQL: {statement[:200]}...")
                            raise
            
            connection.commit()
            print("\n[OK] 策略执行结果表创建成功！")
            return True
            
        except Exception as e:
            connection.rollback()
            print(f"\n[ERROR] 创建表失败: {e}")
            return False
        finally:
            cursor.close()
            connection.close()
            
    except FileNotFoundError:
        print(f"[ERROR] SQL文件不存在: {sql_file}")
        return False
    except Exception as e:
        print(f"[ERROR] 执行失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    print("=" * 60)
    print("创建策略执行结果表")
    print("=" * 60)
    success = create_tables()
    sys.exit(0 if success else 1)

