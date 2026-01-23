#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""应用优化配置数据库Schema"""

import pymysql
from loguru import logger

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def apply_schema():
    """应用数据库Schema"""

    # 读取SQL文件
    with open('app/database/optimization_config_schema.sql', 'r', encoding='utf-8') as f:
        sql_content = f.read()

    # 分割SQL语句 (按分号分割,但要处理CREATE TABLE中的分号)
    statements = []
    current_statement = []
    in_create_table = False

    for line in sql_content.split('\n'):
        line = line.strip()

        # 跳过注释和空行
        if not line or line.startswith('--'):
            continue

        # 检测CREATE TABLE开始
        if 'CREATE TABLE' in line.upper() or 'ALTER TABLE' in line.upper():
            in_create_table = True

        current_statement.append(line)

        # 检测语句结束
        if line.endswith(';'):
            if not in_create_table or (in_create_table and ')' in line):
                statements.append(' '.join(current_statement))
                current_statement = []
                in_create_table = False

    # 连接数据库
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()

    logger.info("开始应用优化配置Schema...")

    success_count = 0
    error_count = 0

    for i, statement in enumerate(statements, 1):
        statement = statement.strip()
        if not statement:
            continue

        try:
            # 对于INSERT语句,需要特殊处理ON DUPLICATE KEY
            if statement.startswith('INSERT'):
                cursor.execute(statement)
                conn.commit()
                logger.info(f"✅ [{i}/{len(statements)}] 执行成功: {statement[:80]}...")
                success_count += 1
            elif statement.startswith('CREATE TABLE'):
                cursor.execute(statement)
                conn.commit()
                # 提取表名
                table_name = statement.split('`')[1] if '`' in statement else 'unknown'
                logger.info(f"✅ [{i}/{len(statements)}] 创建表: {table_name}")
                success_count += 1
            elif statement.startswith('ALTER TABLE'):
                cursor.execute(statement)
                conn.commit()
                table_name = statement.split('`')[1] if '`' in statement else 'unknown'
                logger.info(f"✅ [{i}/{len(statements)}] 修改表: {table_name}")
                success_count += 1
            else:
                cursor.execute(statement)
                conn.commit()
                logger.info(f"✅ [{i}/{len(statements)}] 执行成功")
                success_count += 1

        except pymysql.err.OperationalError as e:
            if 'Duplicate column name' in str(e) or 'Duplicate key name' in str(e):
                logger.warning(f"⚠️ [{i}/{len(statements)}] 字段/索引已存在,跳过")
                success_count += 1
            else:
                logger.error(f"❌ [{i}/{len(statements)}] 执行失败: {e}")
                logger.error(f"SQL: {statement[:200]}...")
                error_count += 1
        except Exception as e:
            logger.error(f"❌ [{i}/{len(statements)}] 执行失败: {e}")
            logger.error(f"SQL: {statement[:200]}...")
            error_count += 1

    cursor.close()
    conn.close()

    logger.info("=" * 80)
    logger.info(f"✅ Schema应用完成: 成功 {success_count}, 失败 {error_count}")
    logger.info("=" * 80)

    return success_count, error_count


if __name__ == '__main__':
    apply_schema()
