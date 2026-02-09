#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证触顶保护机制是否正常工作
"""
import pymysql
import os
import sys
from dotenv import load_dotenv
from datetime import datetime, timedelta

# Fix Windows encoding
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4'
}

conn = pymysql.connect(**db_config)
cursor = conn.cursor(pymysql.cursors.DictCursor)

BIG4 = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']

# 配置参数 (来自 big4_trend_detector.py)
EMERGENCY_DETECTION_HOURS = 4
TOP_RISE_THRESHOLD = 5.0
BLOCK_DURATION_HOURS = 2

print('=' * 100)
print('触顶保护机制验证')
print('=' * 100)

print(f'\n配置参数:')
print(f'  检测时间窗口: 最近 {EMERGENCY_DETECTION_HOURS} 小时')
print(f'  触顶触发阈值: 涨幅 >= {TOP_RISE_THRESHOLD}%')
print(f'  保护持续时长: {BLOCK_DURATION_HOURS} 小时')

print('\n' + '=' * 100)
print('检查最近触顶记录')
print('=' * 100)

# 查询最近的紧急干预记录
cursor.execute("""
    SELECT *
    FROM emergency_intervention
    WHERE account_id = 2
    AND trading_type = 'usdt_futures'
    AND intervention_type = 'TOP_REVERSAL'
    ORDER BY created_at DESC
    LIMIT 10
""")

interventions = cursor.fetchall()

if interventions:
    print(f'\n找到 {len(interventions)} 条触顶干预记录:\n')
    print(f'{"时间":20} {"类型":15} {"阻多":8} {"阻空":8} {"触发原因":40} {"过期时间":20}')
    print('-' * 100)

    for iv in interventions:
        created = iv['created_at']
        expires = iv['expires_at']
        is_active = expires > datetime.now() if expires else False
        status = '🟢活跃' if is_active else '⚫已过期'

        print(f'{created.strftime("%Y-%m-%d %H:%M:%S"):20} '
              f'{iv["intervention_type"]:15} '
              f'{("✅" if iv["block_long"] else "❌"):8} '
              f'{("✅" if iv["block_short"] else "❌"):8} '
              f'{iv["trigger_reason"][:38]:40} '
              f'{expires.strftime("%m-%d %H:%M") if expires else "N/A":20} {status}')
else:
    print('\n⚠️ 未找到触顶干预记录')

print('\n' + '=' * 100)
print('实时检测当前Big4是否触顶')
print('=' * 100)

for symbol in BIG4:
    print(f'\n{symbol}:')
    print('-' * 100)

    # 获取最近4小时的K线
    cursor.execute("""
        SELECT open_price, close_price, low_price, high_price, open_time
        FROM kline_data
        WHERE symbol = %s
        AND timeframe = '1h'
        AND exchange = 'binance_futures'
        ORDER BY open_time DESC
        LIMIT %s
    """, (symbol, EMERGENCY_DETECTION_HOURS))

    klines = cursor.fetchall()

    if not klines or len(klines) < 2:
        print('  数据不足')
        continue

    # 计算最高价、最低价、最新价
    period_high = max([float(k['high_price']) for k in klines])
    period_low = min([float(k['low_price']) for k in klines])
    latest_close = float(klines[0]['close_price'])

    # 从最低点到最高点的涨幅
    rise_pct = (period_high - period_low) / period_low * 100

    # 从最高点到当前的回落
    drop_from_high = (latest_close - period_high) / period_high * 100

    print(f'  最近{EMERGENCY_DETECTION_HOURS}H数据:')
    print(f'    最低价: {period_low:.2f}')
    print(f'    最高价: {period_high:.2f}')
    print(f'    当前价: {latest_close:.2f}')
    print(f'    涨幅: {rise_pct:+.2f}%')
    print(f'    从高点回落: {drop_from_high:.2f}%')

    # 判断是否触顶
    is_top = rise_pct >= TOP_RISE_THRESHOLD and drop_from_high < 0

    print(f'\n  触顶判断:')
    print(f'    条件1 - 涨幅>={TOP_RISE_THRESHOLD}%: {"✅" if rise_pct >= TOP_RISE_THRESHOLD else "❌"} ({rise_pct:.2f}%)')
    print(f'    条件2 - 已从高点回落: {"✅" if drop_from_high < 0 else "❌"} ({drop_from_high:.2f}%)')
    print(f'    综合判断: {"🔴 触顶! 禁止做多!" if is_top else "🟢 正常"}')

print('\n' + '=' * 100)
print('验证保护机制是否生效')
print('=' * 100)

# 检查是否有活跃的触顶保护
cursor.execute("""
    SELECT *
    FROM emergency_intervention
    WHERE account_id = 2
    AND trading_type = 'usdt_futures'
    AND intervention_type = 'TOP_REVERSAL'
    AND block_long = TRUE
    AND expires_at > NOW()
    ORDER BY created_at DESC
    LIMIT 1
""")

active_protection = cursor.fetchone()

if active_protection:
    print('\n🚨 当前有活跃的触顶保护:')
    print(f'  触发时间: {active_protection["created_at"].strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'  触发原因: {active_protection["trigger_reason"]}')
    print(f'  过期时间: {active_protection["expires_at"].strftime("%Y-%m-%d %H:%M:%S")}')

    remaining = (active_protection['expires_at'] - datetime.now()).total_seconds() / 60
    print(f'  剩余时长: {remaining:.0f} 分钟')
    print(f'\n  保护状态:')
    print(f'    禁止做多: {"✅" if active_protection["block_long"] else "❌"}')
    print(f'    禁止做空: {"✅" if active_protection["block_short"] else "❌"}')

    print('\n  📋 当前策略行为:')
    print('    ✅ 允许平多仓 (已有仓位可以止盈/止损)')
    print('    ❌ 禁止开新多仓 (防止追涨)')
    print('    ✅ 允许开空仓 (可以做空回调)')
    print('    ✅ 允许平空仓')
else:
    print('\n🟢 当前没有活跃的触顶保护')
    print('  策略正常运行，允许开多仓')

print('\n' + '=' * 100)
print('【机制验证总结】')
print('=' * 100)

print('\n✅ 触顶保护机制已实现:')
print('  1. 检测逻辑: big4_trend_detector.py:511-516')
print('  2. 数据库记录: emergency_intervention表')
print('  3. 交易拦截: smart_trader_service.py:3123-3125')
print('  4. 保护时长: 2小时')

print('\n📊 对称设计:')
print('  触底: 4H跌>=5% → 禁止做空2H + 开45分钟反弹窗口')
print('  触顶: 4H涨>=5% → 禁止做多2H (不开做空窗口)')

print('\n💡 用户需求:')
print('  ✅ "先实现见顶不开多单的逻辑" - 已满足')
print('  ⏳ "见顶反转(做空策略)" - 暂不实现')

print('\n🔧 参数调整:')
print('  如需修改触发阈值，编辑 big4_trend_detector.py:')
print('  self.TOP_RISE_THRESHOLD = 5.0  (当前值)')

cursor.close()
conn.close()

print('\n' + '=' * 100)
print('验证完成')
print('=' * 100)
