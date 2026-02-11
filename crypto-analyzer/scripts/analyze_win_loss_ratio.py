#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析盈亏比 - 为什么胜率60%还亏钱？
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pymysql
from dotenv import load_dotenv
from datetime import datetime, timedelta

# 加载环境变量
load_dotenv()

# 数据库配置
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data')
}

print("=" * 100)
print("盈亏比分析 - 为什么胜率60%还亏钱？")
print("=" * 100)

# 连接数据库
conn = pymysql.connect(**DB_CONFIG, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
cursor = conn.cursor()

# 查询最近24小时的已平仓持仓
cursor.execute("""
    SELECT
        id,
        symbol,
        position_side as side,
        entry_price,
        mark_price as exit_price,
        realized_pnl as profit_loss,
        unrealized_pnl_pct as profit_loss_pct,
        entry_signal_type as signal_combination,
        open_time as entry_time,
        close_time as exit_time,
        notes as exit_reason,
        TIMESTAMPDIFF(MINUTE, open_time, close_time) as holding_minutes
    FROM futures_positions
    WHERE close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
      AND status = 'CLOSED'
    ORDER BY close_time DESC
""")

trades = cursor.fetchall()

if not trades:
    print("没有最近24小时的交易记录")
    sys.exit(0)

# 分类交易
win_trades = [t for t in trades if float(t['profit_loss'] or 0) > 0]
loss_trades = [t for t in trades if float(t['profit_loss'] or 0) <= 0]

# 计算统计数据
total_win_amount = sum(float(t['profit_loss']) for t in win_trades)
total_loss_amount = sum(float(t['profit_loss']) for t in loss_trades)
avg_win = total_win_amount / len(win_trades) if win_trades else 0
avg_loss = total_loss_amount / len(loss_trades) if loss_trades else 0

print(f"\n总交易: {len(trades)}笔")
print(f"胜率: {len(win_trades)/len(trades)*100:.1f}% ({len(win_trades)}胜 / {len(loss_trades)}负)")
print(f"\n【盈利单】")
print(f"  数量: {len(win_trades)}单")
print(f"  总盈利: +{total_win_amount:.2f}U")
print(f"  平均盈利: +{avg_win:.2f}U/单")
print(f"\n【亏损单】")
print(f"  数量: {len(loss_trades)}单")
print(f"  总亏损: {total_loss_amount:.2f}U")
print(f"  平均亏损: {avg_loss:.2f}U/单")
print(f"\n【盈亏比】")
win_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0
print(f"  盈亏比: {win_loss_ratio:.2f} (平均盈利/平均亏损)")
print(f"  总盈亏: {total_win_amount + total_loss_amount:.2f}U")

# 盈亏比分析
print(f"\n【问题诊断】")
if win_loss_ratio < 1.0:
    print(f"  [X] 盈亏比严重失衡！平均亏{abs(avg_loss):.2f}U，但只赚{avg_win:.2f}U")
    print(f"  [X] 亏损单的平均金额是盈利单的 {abs(avg_loss/avg_win):.2f} 倍")
    print(f"  [X] 需要 {1/(1-win_loss_ratio/(1+win_loss_ratio))*100:.1f}% 的胜率才能盈亏平衡")
elif win_loss_ratio < 1.5:
    print(f"  [!] 盈亏比偏低，需要提高盈利单的平均金额或降低亏损单的平均金额")
else:
    print(f"  [OK] 盈亏比健康")

# 按盈利金额排序
print("\n" + "=" * 100)
print("【最大盈利单 TOP 10】")
print("=" * 100)

win_trades_sorted = sorted(win_trades, key=lambda x: float(x['profit_loss']), reverse=True)
for i, trade in enumerate(win_trades_sorted[:10], 1):
    profit = float(trade['profit_loss'])
    profit_pct = float(trade['profit_loss_pct'] or 0)
    holding = trade['holding_minutes'] or 0
    print(f"{i:2d}. {trade['symbol']:12s} | {trade['side']:5s} | +{profit:7.2f}U ({profit_pct:+.2f}%) | "
          f"持仓{holding:3d}分 | {trade['exit_reason'][:50]}")

print("\n" + "=" * 100)
print("【最大亏损单 TOP 10】")
print("=" * 100)

loss_trades_sorted = sorted(loss_trades, key=lambda x: float(x['profit_loss']))
for i, trade in enumerate(loss_trades_sorted[:10], 1):
    profit = float(trade['profit_loss'])
    profit_pct = float(trade['profit_loss_pct'] or 0)
    holding = trade['holding_minutes'] or 0
    print(f"{i:2d}. {trade['symbol']:12s} | {trade['side']:5s} | {profit:7.2f}U ({profit_pct:+.2f}%) | "
          f"持仓{holding:3d}分 | {trade['exit_reason'][:50]}")

# 分析退出原因
print("\n" + "=" * 100)
print("【退出原因分析】")
print("=" * 100)

# 盈利单退出原因
win_exit_reasons = {}
for trade in win_trades:
    reason = trade['exit_reason'] or 'unknown'
    # 简化原因
    if '止盈' in reason or '移动止盈' in reason or '盈利>=2%' in reason:
        key = '止盈/移动止盈'
    elif '持仓时间' in reason or '强制平仓' in reason or '超时' in reason:
        key = '持仓超时强制平仓'
    elif '分批平仓' in reason:
        key = '分批平仓'
    elif '手动平仓' in reason:
        key = '手动平仓'
    else:
        key = '其他'

    if key not in win_exit_reasons:
        win_exit_reasons[key] = {'count': 0, 'total_profit': 0}
    win_exit_reasons[key]['count'] += 1
    win_exit_reasons[key]['total_profit'] += float(trade['profit_loss'])

print("\n盈利单退出原因:")
for reason, data in sorted(win_exit_reasons.items(), key=lambda x: x[1]['total_profit'], reverse=True):
    avg = data['total_profit'] / data['count']
    print(f"  {reason:20s} | {data['count']:3d}单 | 总盈利+{data['total_profit']:7.2f}U | 平均+{avg:6.2f}U")

# 亏损单退出原因
loss_exit_reasons = {}
for trade in loss_trades:
    reason = trade['exit_reason'] or 'unknown'
    # 简化原因
    if '止损' in reason:
        key = '止损'
    elif '持仓时间' in reason or '强制平仓' in reason or '超时' in reason:
        key = '持仓超时强制平仓'
    elif '亏损' in reason:
        key = '亏损相关'
    elif '反转' in reason or '趋势' in reason:
        key = '趋势反转'
    else:
        key = '其他'

    if key not in loss_exit_reasons:
        loss_exit_reasons[key] = {'count': 0, 'total_loss': 0}
    loss_exit_reasons[key]['count'] += 1
    loss_exit_reasons[key]['total_loss'] += float(trade['profit_loss'])

print("\n亏损单退出原因:")
for reason, data in sorted(loss_exit_reasons.items(), key=lambda x: x[1]['total_loss']):
    avg = data['total_loss'] / data['count']
    print(f"  {reason:20s} | {data['count']:3d}单 | 总亏损{data['total_loss']:7.2f}U | 平均{avg:6.2f}U")

# 建议
print("\n" + "=" * 100)
print("【优化建议】")
print("=" * 100)

print("\n1. 【止损问题】")
stop_loss_trades = [t for t in loss_trades if '止损' in (t['exit_reason'] or '')]
if stop_loss_trades:
    avg_stop_loss = sum(float(t['profit_loss']) for t in stop_loss_trades) / len(stop_loss_trades)
    print(f"   - 止损单平均亏损: {avg_stop_loss:.2f}U")
    print(f"   - 建议: 止损点位可能设置过宽，建议收紧止损或提高开仓质量")

print("\n2. 【止盈问题】")
take_profit_trades = [t for t in win_trades if '止盈' in (t['exit_reason'] or '') or '移动止盈' in (t['exit_reason'] or '')]
if take_profit_trades:
    avg_take_profit = sum(float(t['profit_loss']) for t in take_profit_trades) / len(take_profit_trades)
    print(f"   - 止盈单平均盈利: +{avg_take_profit:.2f}U")
    print(f"   - 建议: 止盈过早，建议使用移动止盈让利润奔跑")

print("\n3. 【持仓超时问题】")
timeout_trades = [t for t in trades if '持仓时间' in (t['exit_reason'] or '') or '超时' in (t['exit_reason'] or '') or '强制平仓' in (t['exit_reason'] or '')]
timeout_profit = sum(float(t['profit_loss']) for t in timeout_trades)
print(f"   - 持仓超时强制平仓: {len(timeout_trades)}单，总盈亏{timeout_profit:.2f}U")
if timeout_profit < 0:
    print(f"   - 建议: 持仓超时大多数是亏损的，说明信号质量不够好，应该更早止损")
else:
    print(f"   - 建议: 持仓超时是盈利的，说明持仓时间设置可能合理")

print("\n4. 【核心问题】")
print(f"   - 亏损单平均亏{abs(avg_loss):.2f}U，盈利单平均赚{avg_win:.2f}U")
print(f"   - 亏损是盈利的 {abs(avg_loss/avg_win):.2f} 倍！")
print(f"   - 典型的「截短盈利，放大亏损」问题")
print(f"   - 建议:")
print(f"     1) 收紧止损（目前止损过宽）")
print(f"     2) 放宽止盈（让利润奔跑）")
print(f"     3) 提高开仓质量（禁用高波动追涨信号）")
print(f"     4) 考虑使用移动止盈代替固定止盈")

cursor.close()
conn.close()

print("\n" + "=" * 100)
print("分析完成")
print("=" * 100)
