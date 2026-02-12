#!/usr/bin/env python3
"""更新数据库表结构文档"""
import pymysql
import yaml
from datetime import datetime

# 读取配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config['database']['mysql']

# 连接数据库
conn = pymysql.connect(
    host=db_config['host'],
    user=db_config['user'],
    password=db_config['password'],
    database=db_config['database'],
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

cursor = conn.cursor()

# 获取所有表名
cursor.execute("SHOW TABLES")
tables = [row[f'Tables_in_{db_config["database"]}'] for row in cursor.fetchall()]

print(f"找到 {len(tables)} 个表")

# 生成文档
output = []
output.append("=" * 100)
output.append(f"数据库表结构文档 - {db_config['database']}")
output.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
output.append(f"总表数: {len(tables)}")
output.append("=" * 100)
output.append("")

for table_name in sorted(tables):
    try:
        output.append("-" * 100)
        output.append(f"表名: {table_name}")
        output.append("-" * 100)

        # 获取表结构
        cursor.execute(f"DESCRIBE {table_name}")
        columns = cursor.fetchall()

        # 获取表注释
        cursor.execute(f"""
            SELECT TABLE_COMMENT
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        """, (db_config['database'], table_name))

        comment_row = cursor.fetchone()
        if comment_row and comment_row['TABLE_COMMENT']:
            output.append(f"说明: {comment_row['TABLE_COMMENT']}")
            output.append("")

        # 格式化列信息
        output.append(f"{'字段名':<30} {'类型':<25} {'空':<5} {'键':<5} {'默认值':<15} {'额外':<15}")
        output.append("-" * 100)

        for col in columns:
            field = col['Field']
            type_info = col['Type']
            null_info = col['Null']
            key = col['Key']
            default = str(col['Default']) if col['Default'] is not None else 'NULL'
            extra = col['Extra']

            output.append(f"{field:<30} {type_info:<25} {null_info:<5} {key:<5} {default:<15} {extra:<15}")

        # 获取索引信息
        cursor.execute(f"SHOW INDEX FROM {table_name}")
        indexes = cursor.fetchall()

        if indexes:
            output.append("")
            output.append("索引:")
            index_dict = {}
            for idx in indexes:
                key_name = idx['Key_name']
                if key_name not in index_dict:
                    index_dict[key_name] = {
                        'columns': [],
                        'unique': idx['Non_unique'] == 0,
                        'type': idx['Index_type']
                    }
                index_dict[key_name]['columns'].append(idx['Column_name'])

            for key_name, info in index_dict.items():
                unique_str = "UNIQUE" if info['unique'] else ""
                cols_str = ', '.join(info['columns'])
                output.append(f"  {key_name}: {unique_str} ({cols_str}) [{info['type']}]")

        # 获取行数
        cursor.execute(f"SELECT COUNT(*) as count FROM {table_name}")
        count = cursor.fetchone()['count']
        output.append("")
        output.append(f"行数: {count:,}")
        output.append("")

    except Exception as e:
        output.append(f"❌ 获取表结构失败: {e}")
        output.append("")

cursor.close()
conn.close()

# 写入文件
with open('table_schemas.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(output))

print(f"✅ 表结构文档已更新: table_schemas.txt ({len(output)} 行)")
