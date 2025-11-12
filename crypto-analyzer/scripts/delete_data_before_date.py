#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
删除指定日期之前的数据
根据 price_data 和 kline_data 表的 timestamp 字段判断
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
from loguru import logger

# 配置日志
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO"
)


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


def delete_data_before_date(cutoff_date: datetime, confirm: bool = False):
    """
    删除指定日期之前的数据
    
    Args:
        cutoff_date: 截止日期，删除此日期之前的数据
        confirm: 是否跳过确认（用于脚本调用）
    """
    db_config = get_db_config()
    
    print(f"\n{'='*80}")
    print(f"⚠️  准备删除 {cutoff_date.strftime('%Y-%m-%d %H:%M:%S')} 之前的数据")
    print(f"将删除 price_data 和 kline_data 表中 timestamp < {cutoff_date.strftime('%Y-%m-%d %H:%M:%S')} 的数据")
    print(f"{'='*80}\n")
    
    # 确认删除
    if not confirm:
        try:
            user_input = input("确认删除？(输入 'yes' 确认): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n操作已取消")
            sys.exit(0)
        
        if user_input != 'yes':
            print("操作已取消")
            sys.exit(0)
    
    try:
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()
        
        # 要清理的表（使用timestamp字段判断）
        tables_to_clean = [
            ('price_data', 'timestamp'),
            ('kline_data', 'timestamp'),
        ]
        
        total_deleted = 0
        
        for table_name, time_column in tables_to_clean:
            try:
                # 先检查表是否存在
                cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
                if not cursor.fetchone():
                    print(f"⚠️  表 {table_name} 不存在，跳过")
                    continue
                
                # 先查询要删除的数据量（使用timestamp字段）
                cursor.execute(f"""
                    SELECT COUNT(*) as count 
                    FROM {table_name} 
                    WHERE {time_column} < %s
                """, (cutoff_date,))
                count_result = cursor.fetchone()
                count_before = count_result['count'] if count_result else 0
                
                if count_before > 0:
                    print(f"📊 {table_name}: 找到 {count_before:,} 条需要删除的数据（timestamp < {cutoff_date.strftime('%Y-%m-%d %H:%M:%S')}）")
                    
                    # 执行删除（根据timestamp字段）
                    cursor.execute(f"""
                        DELETE FROM {table_name} 
                        WHERE {time_column} < %s
                    """, (cutoff_date,))
                    
                    deleted_count = cursor.rowcount
                    conn.commit()
                    total_deleted += deleted_count
                    print(f"✅ {table_name}: 已删除 {deleted_count:,} 条数据")
                else:
                    print(f"ℹ️  {table_name}: 无需删除，没有 {cutoff_date.strftime('%Y-%m-%d')} 之前的数据（timestamp >= {cutoff_date.strftime('%Y-%m-%d %H:%M:%S')}）")
                    
            except Exception as e:
                print(f"❌ 删除 {table_name} 数据失败: {e}")
                import traceback
                traceback.print_exc()
                conn.rollback()
                continue
        
        cursor.close()
        conn.close()
        
        print(f"\n{'='*80}")
        print(f"✅ 数据清理完成，共删除 {total_deleted:,} 条数据")
        print(f"{'='*80}\n")
        
    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='删除指定日期之前的数据')
    parser.add_argument(
        '--date',
        type=str,
        default='2024-06-01',
        help='截止日期，格式: YYYY-MM-DD (默认: 2024-06-01)'
    )
    parser.add_argument(
        '--time',
        type=str,
        default='00:00:00',
        help='时间，格式: HH:MM:SS (默认: 00:00:00)'
    )
    parser.add_argument(
        '--yes', '-y',
        action='store_true',
        help='跳过确认，直接执行'
    )
    
    args = parser.parse_args()
    
    # 解析日期
    try:
        date_str = f"{args.date} {args.time}"
        cutoff_date = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
    except ValueError as e:
        print(f"❌ 日期格式错误: {e}")
        print("正确格式: --date '2024-06-01' --time '00:00:00'")
        sys.exit(1)
    
    # 执行删除
    delete_data_before_date(cutoff_date, confirm=args.yes)


if __name__ == '__main__':
    main()

