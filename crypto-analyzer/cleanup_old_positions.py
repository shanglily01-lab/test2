#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Backup and cleanup old position data (keep only smart_trader data)
"""

import pymysql
from pymysql.cursors import DictCursor
from datetime import datetime

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

try:
    conn = pymysql.connect(**db_config, cursorclass=DictCursor)
    cursor = conn.cursor()

    print('=' * 80)
    print('Position Data Cleanup Script')
    print(f'Started at: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print('=' * 80)

    # Step 1: 统计当前数据
    print('\n[Step 1] Current data statistics...')
    cursor.execute('''
        SELECT source, COUNT(*) as count
        FROM futures_positions
        GROUP BY source
        ORDER BY count DESC
    ''')
    current_stats = cursor.fetchall()

    print('\nCurrent positions by source:')
    total_positions = 0
    non_smart_trader = 0
    for stat in current_stats:
        print(f"  {stat['source']}: {stat['count']}")
        total_positions += stat['count']
        if stat['source'] != 'smart_trader':
            non_smart_trader += stat['count']

    print(f'\nTotal positions: {total_positions}')
    print(f'Non-smart_trader positions to backup/delete: {non_smart_trader}')

    # Step 2: 创建备份表
    print('\n[Step 2] Creating backup table...')
    backup_table_name = f"futures_positions_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    cursor.execute(f'''
        CREATE TABLE {backup_table_name}
        AS SELECT * FROM futures_positions
        WHERE source != 'smart_trader'
    ''')
    conn.commit()

    # 验证备份
    cursor.execute(f'SELECT COUNT(*) as count FROM {backup_table_name}')
    backup_count = cursor.fetchone()['count']
    print(f'Backup table created: {backup_table_name}')
    print(f'Backed up {backup_count} positions')

    if backup_count != non_smart_trader:
        print(f'\n[ERROR] Backup count mismatch!')
        print(f'Expected: {non_smart_trader}, Got: {backup_count}')
        print('Aborting deletion for safety.')
        cursor.close()
        conn.close()
        exit(1)

    # Step 3: 删除非smart_trader数据
    print('\n[Step 3] Deleting non-smart_trader positions...')
    cursor.execute('''
        DELETE FROM futures_positions
        WHERE source != 'smart_trader'
    ''')
    deleted_count = cursor.rowcount
    conn.commit()

    print(f'Deleted {deleted_count} positions')

    # Step 4: 验证清理结果
    print('\n[Step 4] Verifying cleanup...')
    cursor.execute('''
        SELECT source, COUNT(*) as count
        FROM futures_positions
        GROUP BY source
    ''')
    final_stats = cursor.fetchall()

    print('\nFinal positions by source:')
    for stat in final_stats:
        print(f"  {stat['source']}: {stat['count']}")

    cursor.execute('SELECT COUNT(*) as count FROM futures_positions')
    final_total = cursor.fetchone()['count']

    print(f'\nFinal total positions: {final_total}')

    # Step 5: 优化表
    print('\n[Step 5] Optimizing table...')
    cursor.execute('OPTIMIZE TABLE futures_positions')
    print('Table optimized')

    print('\n' + '=' * 80)
    print('Cleanup Summary:')
    print('=' * 80)
    print(f'Backup table: {backup_table_name}')
    print(f'Original total: {total_positions}')
    print(f'Backed up: {backup_count}')
    print(f'Deleted: {deleted_count}')
    print(f'Remaining: {final_total}')
    print(f'\nCompleted at: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print('=' * 80)

    # 显示如何恢复的说明
    print('\n[INFO] To restore backup if needed:')
    print(f'  INSERT INTO futures_positions SELECT * FROM {backup_table_name};')
    print(f'\n[INFO] To drop backup table after confirming cleanup:')
    print(f'  DROP TABLE {backup_table_name};')

    cursor.close()
    conn.close()

    print('\n[SUCCESS] Cleanup completed successfully!')

except Exception as e:
    print(f'\n[ERROR] {e}')
    import traceback
    traceback.print_exc()
    if conn:
        conn.rollback()
        conn.close()
