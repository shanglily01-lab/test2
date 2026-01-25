#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析昨晚24小时超级大脑交易情况"""

import pymysql
from datetime import datetime, timedelta
import os
import sys
from dotenv import load_dotenv

# 设置UTF-8输出（Windows兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 加载环境变量
load_dotenv()

# 数据库配置
db_config = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data'),
    'cursorclass': pymysql.cursors.DictCursor
}

def main():
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()

    # 查询昨晚24小时的交易（直接从positions表获取数据）
    cursor.execute('''
        SELECT
            DATE_FORMAT(p.open_time, "%Y-%m-%d %H:%i") as open_time,
            DATE_FORMAT(p.close_time, "%H:%i") as close_time,
            p.symbol,
            p.position_side,
            ROUND(p.margin, 2) as margin,
            ROUND(p.entry_price, 4) as entry_price,
            p.mark_price as close_price,
            p.notes as close_reason,
            ROUND(p.realized_pnl, 2) as pnl,
            p.entry_signal_type,
            p.entry_score,
            p.status,
            TIMESTAMPDIFF(MINUTE, p.open_time, IFNULL(p.close_time, NOW())) as holding_minutes
        FROM futures_positions p
        WHERE p.account_id = 2
        AND p.source = "smart_trader"
        AND p.open_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
        ORDER BY p.open_time DESC
    ''')

    results = cursor.fetchall()

    print(f'\n{"="*140}')
    print(f'📊 昨晚24小时超级大脑交易记录 (共{len(results)}笔)')
    print(f'{"="*140}\n')

    if results:
        # 统计数据
        total_pnl = 0
        win_count = 0
        loss_count = 0
        open_count = 0

        for r in results:
            pnl = r['pnl'] or 0
            status = r['status']

            if status == 'open':
                open_count += 1
                status_icon = '📍'
            elif pnl > 0:
                win_count += 1
                total_pnl += pnl
                status_icon = '🟢'
            elif pnl < 0:
                loss_count += 1
                total_pnl += pnl
                status_icon = '🔴'
            else:
                # pnl为0的已平仓单
                status_icon = '⚪'

            close_info = f"{r['close_time'] or '---':5}" if r['close_time'] else '持仓中'
            close_price = f"{float(r['close_price']):8.4f}" if r['close_price'] else '   ---   '

            # 计算盈亏率
            if status == 'closed' and r['margin']:
                pnl_pct = (pnl / r['margin']) * 100
            else:
                pnl_pct = 0

            # 持仓时长
            hold_time = f"{r['holding_minutes']}分钟" if r['holding_minutes'] else '---'

            print(f"{status_icon} {r['open_time']} → {close_info} | "
                  f"{r['symbol']:12} {r['position_side']:5} | "
                  f"保证金:${r['margin']:6.0f} | "
                  f"开仓:{r['entry_price']:8.4f} → 平仓:{close_price} | "
                  f"盈亏:${pnl:7.2f} ({pnl_pct:+5.1f}%) | "
                  f"持仓{hold_time:8} | "
                  f"{r['entry_signal_type'] or 'N/A':30} (分数:{r['entry_score'] or 0:2})")

        # 打印统计信息
        print(f'\n{"="*140}')
        print(f'📈 统计汇总:')
        print(f'   总盈亏: ${total_pnl:+.2f}')
        print(f'   盈利单: {win_count}笔')
        print(f'   亏损单: {loss_count}笔')
        print(f'   持仓中: {open_count}笔')
        if (win_count + loss_count) > 0:
            win_rate = win_count / (win_count + loss_count) * 100
            print(f'   胜率: {win_rate:.1f}%')
        print(f'{"="*140}\n')
    else:
        print('❌ 无交易记录\n')

    cursor.close()
    conn.close()

if __name__ == '__main__':
    main()
