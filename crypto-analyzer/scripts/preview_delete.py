#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
预览删除操作：显示将要删除的数据量，不实际删除
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

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import yaml
import pymysql
from datetime import datetime

def get_db_config():
    """从配置文件读取数据库配置"""
    config_path = project_root / 'config.yaml'
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    db_config = config.get('database', {}).get('mysql', {})
    return {
        'host': db_config.get('host', 'localhost'),
        'port': db_config.get('port', 3306),
        'user': db_config.get('user', 'root'),
        'password': db_config.get('password', ''),
        'database': db_config.get('database', 'binance-data'),
        'charset': 'utf8mb4',
        'cursorclass': pymysql.cursors.DictCursor
    }


def preview_delete(cutoff_date: datetime):
    """预览删除操作"""
    db_config = get_db_config()
    
    print(f"\n{'='*80}")
    print(f"📊 预览删除操作")
    print(f"将删除 {cutoff_date.strftime('%Y-%m-%d %H:%M:%S')} 之前的数据")
    print(f"{'='*80}\n")
    
    try:
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()
        
        tables_to_check = [
            ('price_data', 'timestamp'),
            ('kline_data', 'timestamp'),
        ]
        
        total_to_delete = 0
        
        for table_name, time_column in tables_to_check:
            try:
                # 检查表是否存在
                cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
                if not cursor.fetchone():
                    print(f"⚠️  表 {table_name} 不存在，跳过")
                    continue
                
                # 查询总记录数
                cursor.execute(f"SELECT COUNT(*) as count FROM {table_name}")
                total_count = cursor.fetchone()['count']
                
                # 查询要删除的数据量
                cursor.execute(f"""
                    SELECT COUNT(*) as count 
                    FROM {table_name} 
                    WHERE {time_column} < %s
                """, (cutoff_date,))
                to_delete = cursor.fetchone()['count']
                
                # 查询保留的数据量
                cursor.execute(f"""
                    SELECT COUNT(*) as count 
                    FROM {table_name} 
                    WHERE {time_column} >= %s
                """, (cutoff_date,))
                to_keep = cursor.fetchone()['count']
                
                print(f"📋 {table_name}:")
                print(f"  总记录数: {total_count:,}")
                print(f"  将删除: {to_delete:,} 条 (timestamp < {cutoff_date.strftime('%Y-%m-%d %H:%M:%S')})")
                print(f"  将保留: {to_keep:,} 条 (timestamp >= {cutoff_date.strftime('%Y-%m-%d %H:%M:%S')})")
                
                if to_delete > 0:
                    # 显示最早和最晚的将被删除的数据
                    cursor.execute(f"""
                        SELECT MIN({time_column}) as min_time, MAX({time_column}) as max_time
                        FROM {table_name}
                        WHERE {time_column} < %s
                    """, (cutoff_date,))
                    time_range = cursor.fetchone()
                    if time_range['min_time']:
                        print(f"  删除范围: {time_range['min_time']} 至 {time_range['max_time']}")
                
                total_to_delete += to_delete
                print()
                
            except Exception as e:
                print(f"❌ 检查 {table_name} 失败: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        cursor.close()
        conn.close()
        
        print(f"{'='*80}")
        print(f"📊 总计将删除: {total_to_delete:,} 条数据")
        print(f"{'='*80}\n")
        
    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='预览删除操作')
    parser.add_argument(
        '--date',
        type=str,
        default='2025-06-01',
        help='截止日期，格式: YYYY-MM-DD (默认: 2025-06-01)'
    )
    parser.add_argument(
        '--time',
        type=str,
        default='00:00:00',
        help='时间，格式: HH:MM:SS (默认: 00:00:00)'
    )
    
    args = parser.parse_args()
    
    # 解析日期
    try:
        date_str = f"{args.date} {args.time}"
        cutoff_date = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
    except ValueError as e:
        print(f"❌ 日期格式错误: {e}")
        print("正确格式: --date '2025-06-01' --time '00:00:00'")
        sys.exit(1)
    
    preview_delete(cutoff_date)

