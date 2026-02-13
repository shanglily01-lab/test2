#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理CHESS异常仓位数据"""
import pymysql
import os
import sys
from dotenv import load_dotenv

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST', '13.212.252.171'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER', 'app_user'),
    password=os.getenv('DB_PASSWORD', 'AppUser@2024#Secure'),
    database=os.getenv('DB_NAME', 'crypto_analyzer'),
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

cursor = conn.cursor()

print('=' * 80)
print('清理CHESS/USDT异常仓位数据')
print('=' * 80)

# 查询CHESS仓位
cursor.execute('''
    SELECT id, symbol, position_side, margin, unrealized_pnl, open_time
    FROM futures_positions
    WHERE account_id = 2
      AND symbol = 'CHESS/USDT'
      AND status = 'open'
''')

chess_positions = cursor.fetchall()

if not chess_positions:
    print('\n✅ 没有找到CHESS/USDT持仓记录')
else:
    print(f'\n找到 {len(chess_positions)} 个CHESS/USDT仓位:\n')

    for pos in chess_positions:
        print(f'  ID: {pos["id"]}')
        print(f'  方向: {pos["position_side"]}')
        print(f'  保证金: {pos["margin"]}')
        print(f'  未实现盈亏: {pos["unrealized_pnl"]}')
        print(f'  开仓时间: {pos["open_time"]}')
        print()

    # 删除这些异常仓位
    cursor.execute('''
        DELETE FROM futures_positions
        WHERE account_id = 2
          AND symbol = 'CHESS/USDT'
          AND status = 'open'
    ''')

    deleted_count = cursor.rowcount
    conn.commit()

    print(f'✅ 已删除 {deleted_count} 个CHESS/USDT异常仓位')

    # 重新查询账户未实现盈亏
    cursor.execute('''
        SELECT
            COUNT(*) as position_count,
            SUM(unrealized_pnl) as total_unrealized_pnl,
            SUM(margin) as total_margin
        FROM futures_positions
        WHERE account_id = 2
          AND status = 'open'
    ''')

    summary = cursor.fetchone()

    print('\n' + '=' * 80)
    print('清理后账户状态:')
    print('=' * 80)
    print(f'持仓数: {summary["position_count"]} 个')
    print(f'总保证金: {float(summary["total_margin"] or 0):,.2f} USDT')
    print(f'未实现盈亏: {float(summary["total_unrealized_pnl"] or 0):+,.2f} USDT')

    unrealized_pnl = float(summary["total_unrealized_pnl"] or 0)
    if abs(unrealized_pnl) < 1000:
        print('\n✅ 未实现盈亏已恢复正常')
    else:
        print(f'\n⚠️ 仍有较大未实现盈亏: {unrealized_pnl:+,.2f} USDT')

cursor.close()
conn.close()

print('\n' + '=' * 80)
print('后续建议:')
print('=' * 80)
print('1. ✅ 已从config.yaml删除CHESS/USDT')
print('2. 📝 需要添加交易对有效性验证（防止开仓不存在的交易对）')
print('3. 📝 需要定期同步币安交易对列表')
print('4. 📝 建议提高开仓阈值到50-60分（当前79个持仓太多）')
