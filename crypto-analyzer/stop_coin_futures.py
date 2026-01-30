#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""停止币本位服务并清理后台任务"""

import pymysql
import sys
import io
from dotenv import load_dotenv
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME')
)

cursor = conn.cursor(pymysql.cursors.DictCursor)

print('=' * 100)
print('停止币本位服务 - 清理异常状态')
print('=' * 100)
print()

try:
    # 1. 检查币本位账户的持仓
    print('🔍 检查币本位账户(account_id=3)的持仓:')
    print('-' * 100)

    cursor.execute("""
        SELECT id, symbol, position_side, quantity, status, open_time
        FROM futures_positions
        WHERE account_id = 3
        AND status = 'open'
        ORDER BY open_time DESC
    """)

    positions = cursor.fetchall()

    if positions:
        print(f'找到 {len(positions)} 个开启持仓:\n')
        for pos in positions:
            print(f'  ID:{pos["id"]:>5} | {pos["symbol"]:<12} | {pos["position_side"]:<5} | '
                  f'数量:{float(pos["quantity"]):>10.4f} | '
                  f'开仓:{pos["open_time"]}')

        print()
        print('⚠️ 建议操作:')
        print('  1. 手动关闭这些持仓 (如果有实际交易)')
        print('  2. 或将status改为closed (如果只是测试/错误记录)')
        print()

        choice = input('是否将这些持仓标记为closed? (y/n): ').strip().lower()

        if choice == 'y':
            for pos in positions:
                cursor.execute("""
                    UPDATE futures_positions
                    SET status = 'closed',
                        close_time = NOW(),
                        realized_pnl = IFNULL(unrealized_pnl, 0),
                        notes = CONCAT(IFNULL(notes, ''), ' | 币本位服务停用,系统自动关闭')
                    WHERE id = %s
                """, (pos['id'],))

                print(f'  ✓ ID:{pos["id"]} 已标记为关闭')

            conn.commit()
            print('\n✅ 已提交更改')
        else:
            print('\n跳过关闭持仓')
    else:
        print('  ✓ 没有开启的持仓\n')

    print('=' * 100)
    print('📝 后续操作建议')
    print('=' * 100)
    print()
    print('1. 停止币本位服务进程:')
    print('   pm2 stop coin_futures_trader')
    print('   pm2 delete coin_futures_trader')
    print()
    print('2. 禁用币本位配置 (config.yaml):')
    print('   # coin_futures_symbols:  # 暂时禁用')
    print('   # - BTCUSD_PERP')
    print('   # ...')
    print()
    print('3. 重启U本位服务:')
    print('   pm2 restart smart_trader')
    print()
    print('原因:')
    print('- 币本位交易对没有K线数据')
    print('- 无法获取实时价格')
    print('- 分批建仓持续失败')
    print('- 不断产生错误日志')
    print()

except Exception as e:
    print(f'✗ 操作失败: {e}')
    import traceback
    traceback.print_exc()
    conn.rollback()
finally:
    cursor.close()
    conn.close()

print('=' * 100)
