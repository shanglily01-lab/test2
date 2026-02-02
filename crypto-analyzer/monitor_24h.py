#!/usr/bin/env python3
"""
24小时系统监控脚本
监控内容：
1. Big4趋势检测状态
2. 超级大脑自优化记录
3. 信号盈利情况
4. 黑名单变化
5. 持仓和交易统计
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import pymysql
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST', '13.212.252.171'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'admin'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME', 'binance-data'),
    'charset': 'utf8mb4'
}

def print_section(title):
    """打印章节标题"""
    print("\n" + "=" * 80)
    print(f" {title}")
    print("=" * 80)

def print_subsection(title):
    """打印子章节标题"""
    print(f"\n{title}")
    print("-" * 80)

conn = pymysql.connect(**db_config)
cursor = conn.cursor(pymysql.cursors.DictCursor)

print("=" * 80)
print(f" 24小时系统监控报告")
print(f" 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 80)

# ============================================================================
# 1. Big4 趋势检测状态
# ============================================================================
print_section("1. Big4 趋势检测状态")

print_subsection("1.1 最新检测")
cursor.execute('''
    SELECT
        created_at,
        overall_signal,
        signal_strength,
        bullish_count,
        bearish_count,
        recommendation,
        TIMESTAMPDIFF(MINUTE, created_at, NOW()) as minutes_ago
    FROM big4_trend_history
    ORDER BY created_at DESC
    LIMIT 1
''')

latest_big4 = cursor.fetchone()
if latest_big4:
    print(f"   时间: {latest_big4['created_at']} ({latest_big4['minutes_ago']}分钟前)")
    print(f"   信号: {latest_big4['overall_signal']} (强度: {latest_big4['signal_strength']})")
    print(f"   多空: {latest_big4['bullish_count']}多 / {latest_big4['bearish_count']}空")
    print(f"   建议: {latest_big4['recommendation']}")

    if latest_big4['minutes_ago'] > 30:
        print(f"\n   ⚠️  警告: Big4检测已停止 {latest_big4['minutes_ago']} 分钟!")
    else:
        print(f"\n   ✅ Big4检测运行正常")

print_subsection("1.2 24小时检测统计")
cursor.execute('''
    SELECT
        overall_signal,
        COUNT(*) as count,
        AVG(signal_strength) as avg_strength
    FROM big4_trend_history
    WHERE created_at >= NOW() - INTERVAL 24 HOUR
    GROUP BY overall_signal
    ORDER BY count DESC
''')

big4_stats = cursor.fetchall()
total_detections = sum(s['count'] for s in big4_stats)
print(f"   总检测次数: {total_detections} (预期: 96次, 每15分钟一次)")

for stat in big4_stats:
    percentage = (stat['count'] / total_detections * 100) if total_detections > 0 else 0
    print(f"   {stat['overall_signal']:10} : {stat['count']:3}次 ({percentage:5.1f}%) | 平均强度: {stat['avg_strength']:.1f}")

# ============================================================================
# 2. 超级大脑自优化记录
# ============================================================================
print_section("2. 超级大脑自优化记录")

try:
    print_subsection("2.1 24小时优化记录")
    cursor.execute('''
        SELECT
            optimization_time,
            problem_type,
            action_taken,
            reason,
            old_value,
            new_value
        FROM brain_optimization_log
        WHERE optimization_time >= NOW() - INTERVAL 24 HOUR
        ORDER BY optimization_time DESC
        LIMIT 20
    ''')

    optimizations = cursor.fetchall()
    if optimizations:
        print(f"   共 {len(optimizations)} 条优化记录:\n")
        for opt in optimizations:
            print(f"   [{opt['optimization_time']}] {opt['problem_type']}")
            print(f"      动作: {opt['action_taken']}")
            print(f"      原因: {opt['reason']}")
            if opt['old_value'] and opt['new_value']:
                print(f"      变化: {opt['old_value']} → {opt['new_value']}")
            print()
    else:
        print("   ⚠️  24小时内没有优化记录")

    print_subsection("2.2 优化类型统计")
    cursor.execute('''
        SELECT
            problem_type,
            COUNT(*) as count,
            MAX(optimization_time) as last_optimization
        FROM brain_optimization_log
        WHERE optimization_time >= NOW() - INTERVAL 24 HOUR
        GROUP BY problem_type
        ORDER BY count DESC
    ''')

    opt_stats = cursor.fetchall()
    if opt_stats:
        for stat in opt_stats:
            print(f"   {stat['problem_type']:30} : {stat['count']:3}次 | 最近: {stat['last_optimization']}")
    else:
        print("   没有优化记录")

except pymysql.err.ProgrammingError as e:
    if e.args[0] == 1146:  # Table doesn't exist
        print("   ⚠️  brain_optimization_log 表不存在，无法显示优化记录")
    else:
        raise

# ============================================================================
# 3. 信号盈利情况
# ============================================================================
print_section("3. 信号盈利情况 (24小时)")

print_subsection("3.1 已平仓订单统计")
cursor.execute('''
    SELECT
        COUNT(*) as total_closed,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as profitable,
        SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) as loss,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl,
        AVG(unrealized_pnl_pct) as avg_roi,
        MAX(realized_pnl) as max_profit,
        MIN(realized_pnl) as max_loss
    FROM futures_positions
    WHERE account_id = 2
    AND close_time >= NOW() - INTERVAL 24 HOUR
    AND close_time IS NOT NULL
''')

closed_stats = cursor.fetchone()
if closed_stats and closed_stats['total_closed'] > 0:
    win_rate = (closed_stats['profitable'] / closed_stats['total_closed'] * 100) if closed_stats['total_closed'] > 0 else 0
    print(f"   总单数: {closed_stats['total_closed']}")
    print(f"   盈利单: {closed_stats['profitable']} ({win_rate:.1f}%)")
    print(f"   亏损单: {closed_stats['loss']} ({100-win_rate:.1f}%)")
    print(f"   总盈亏: ${closed_stats['total_pnl']:.2f}")
    print(f"   平均盈亏: ${closed_stats['avg_pnl']:.2f}")
    print(f"   平均ROI: {closed_stats['avg_roi']:.2f}%")
    print(f"   最大盈利: ${closed_stats['max_profit']:.2f}")
    print(f"   最大亏损: ${closed_stats['max_loss']:.2f}")
else:
    print("   ⚠️  24小时内没有平仓记录")

print_subsection("3.2 按信号类型统计 (信号强度分组)")
cursor.execute('''
    SELECT
        CASE
            WHEN entry_score >= 85 THEN '超强信号(≥85)'
            WHEN entry_score >= 75 THEN '强信号(75-84)'
            WHEN entry_score >= 65 THEN '中等信号(65-74)'
            ELSE '弱信号(<65)'
        END as signal_group,
        COUNT(*) as count,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl,
        AVG(unrealized_pnl_pct) as avg_roi
    FROM futures_positions
    WHERE account_id = 2
    AND close_time >= NOW() - INTERVAL 24 HOUR
    AND close_time IS NOT NULL
    GROUP BY signal_group
    ORDER BY MIN(entry_score) DESC
''')

signal_stats = cursor.fetchall()
if signal_stats:
    for stat in signal_stats:
        win_rate = (stat['wins'] / stat['count'] * 100) if stat['count'] > 0 else 0
        print(f"   {stat['signal_group']:20} | {stat['count']:3}单 | 胜率:{win_rate:5.1f}% | 总盈亏:${stat['total_pnl']:8.2f} | 平均:${stat['avg_pnl']:7.2f}")
else:
    print("   没有数据")

print_subsection("3.3 Top10 盈利币种")
cursor.execute('''
    SELECT
        symbol,
        COUNT(*) as trades,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl
    FROM futures_positions
    WHERE account_id = 2
    AND close_time >= NOW() - INTERVAL 24 HOUR
    AND close_time IS NOT NULL
    GROUP BY symbol
    ORDER BY total_pnl DESC
    LIMIT 10
''')

top_profitable = cursor.fetchall()
if top_profitable:
    for i, coin in enumerate(top_profitable, 1):
        win_rate = (coin['wins'] / coin['trades'] * 100) if coin['trades'] > 0 else 0
        print(f"   {i:2}. {coin['symbol']:15} | {coin['trades']:2}单 | 胜率:{win_rate:5.1f}% | ${coin['total_pnl']:8.2f}")
else:
    print("   没有数据")

print_subsection("3.4 Top10 亏损币种")
cursor.execute('''
    SELECT
        symbol,
        COUNT(*) as trades,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl
    FROM futures_positions
    WHERE account_id = 2
    AND close_time >= NOW() - INTERVAL 24 HOUR
    AND close_time IS NOT NULL
    GROUP BY symbol
    ORDER BY total_pnl ASC
    LIMIT 10
''')

top_loss = cursor.fetchall()
if top_loss:
    for i, coin in enumerate(top_loss, 1):
        win_rate = (coin['wins'] / coin['trades'] * 100) if coin['trades'] > 0 else 0
        print(f"   {i:2}. {coin['symbol']:15} | {coin['trades']:2}单 | 胜率:{win_rate:5.1f}% | ${coin['total_pnl']:8.2f}")
else:
    print("   没有数据")

# ============================================================================
# 4. 黑名单情况 (Rating Level)
# ============================================================================
print_section("4. 黑名单情况 (Rating Level)")

print_subsection("4.1 当前黑名单币种 (按等级)")
cursor.execute('''
    SELECT
        rating_level,
        COUNT(*) as count
    FROM trading_symbol_rating
    GROUP BY rating_level
    ORDER BY rating_level
''')

level_stats = cursor.fetchall()
if level_stats:
    print("   等级分布:")
    for stat in level_stats:
        level_name = f"Level {stat['rating_level']}"
        if stat['rating_level'] == 0:
            level_name = "白名单"
        elif stat['rating_level'] >= 3:
            level_name = f"黑名单 Level {stat['rating_level']}"
        print(f"      {level_name:20} : {stat['count']:3} 个币种")

print_subsection("4.2 黑名单币种详情 (Level ≥ 2)")
cursor.execute('''
    SELECT
        symbol,
        rating_level,
        margin_multiplier,
        reason,
        hard_stop_loss_count,
        total_loss_amount,
        win_rate,
        total_trades,
        level_changed_at,
        DATEDIFF(NOW(), level_changed_at) as days_in_level
    FROM trading_symbol_rating
    WHERE rating_level >= 2
    ORDER BY rating_level DESC, level_changed_at DESC
    LIMIT 30
''')

blacklist_coins = cursor.fetchall()
if blacklist_coins:
    print(f"   当前黑名单: {len(blacklist_coins)} 个币种\n")
    for coin in blacklist_coins:
        win_rate = float(coin['win_rate'] * 100) if coin['win_rate'] else 0
        print(f"   Level{coin['rating_level']} | {coin['symbol']:15} | 保证金:{coin['margin_multiplier']:.2f}x | 硬止损:{coin['hard_stop_loss_count']:2}次 | 胜率:{win_rate:5.1f}% | 交易:{coin['total_trades']:3}单")
        print(f"          原因: {coin['reason']}")
        if coin['days_in_level'] is not None:
            print(f"          进入黑名单: {coin['days_in_level']}天前 ({coin['level_changed_at']})")
        print()
else:
    print("   ✅ 当前没有Level≥2的黑名单币种")

print_subsection("4.3 24小时等级变化")
cursor.execute('''
    SELECT
        symbol,
        rating_level,
        previous_level,
        level_change_reason,
        level_changed_at
    FROM trading_symbol_rating
    WHERE level_changed_at >= NOW() - INTERVAL 24 HOUR
    ORDER BY level_changed_at DESC
''')

recent_changes = cursor.fetchall()
if recent_changes:
    print(f"   24小时内等级变化: {len(recent_changes)} 个币种\n")
    for change in recent_changes:
        direction = "⬆️" if change['rating_level'] < change['previous_level'] else "⬇️"
        print(f"   {direction} [{change['level_changed_at']}] {change['symbol']:15}")
        print(f"      Level{change['previous_level']} → Level{change['rating_level']}")
        print(f"      原因: {change['level_change_reason']}")
        print()
else:
    print("   24小时内没有等级变化")

print_subsection("4.4 信号黑名单 (Signal Blacklist)")
try:
    cursor.execute('''
        SELECT
            signal_type,
            position_side,
            reason,
            total_loss,
            win_rate,
            order_count,
            created_at,
            DATEDIFF(NOW(), created_at) as days_in_blacklist
        FROM signal_blacklist
        WHERE is_active = 1
        ORDER BY created_at DESC
        LIMIT 20
    ''')

    signal_blacklist = cursor.fetchall()
    if signal_blacklist:
        print(f"   信号黑名单: {len(signal_blacklist)} 个信号类型\n")
        for item in signal_blacklist:
            win_rate = float(item['win_rate'] * 100) if item['win_rate'] else 0
            total_loss = float(item['total_loss']) if item['total_loss'] else 0
            order_count = item['order_count'] if item['order_count'] is not None else 0
            days = item['days_in_blacklist'] if item['days_in_blacklist'] is not None else 0
            print(f"   {item['signal_type']:20} {item['position_side']:5} | 亏损:${total_loss:.2f} | 胜率:{win_rate:5.1f}% | {order_count:3}单 | {days}天前")
            print(f"      原因: {item['reason']}")
            print()
    else:
        print("   ✅ 信号黑名单为空")
except pymysql.err.ProgrammingError:
    print("   ⚠️  signal_blacklist表不存在")

# ============================================================================
# 5. 当前持仓
# ============================================================================
print_section("5. 当前持仓")

print_subsection("5.1 U本位合约持仓")
cursor.execute('''
    SELECT
        symbol,
        position_side,
        entry_price,
        quantity,
        leverage,
        entry_score,
        TIMESTAMPDIFF(HOUR, open_time, NOW()) as hold_hours
    FROM futures_positions
    WHERE account_id = 2
    AND close_time IS NULL
    ORDER BY open_time DESC
''')

usdt_positions = cursor.fetchall()
if usdt_positions:
    print(f"   当前持仓: {len(usdt_positions)} 个\n")
    for pos in usdt_positions:
        print(f"   {pos['symbol']:15} {pos['position_side']:5} | 价格:${pos['entry_price']:10.4f} | 数量:{pos['quantity']:8.2f} | 杠杆:{pos['leverage']}x | 信号:{pos['entry_score']} | 持仓:{pos['hold_hours']}小时")
else:
    print("   当前无持仓")

print_subsection("5.2 币本位合约持仓")
cursor.execute('''
    SELECT
        symbol,
        position_side,
        entry_price,
        quantity,
        leverage,
        entry_score,
        TIMESTAMPDIFF(HOUR, open_time, NOW()) as hold_hours
    FROM futures_positions
    WHERE account_id = 3
    AND close_time IS NULL
    ORDER BY open_time DESC
''')

coin_positions = cursor.fetchall()
if coin_positions:
    print(f"   当前持仓: {len(coin_positions)} 个\n")
    for pos in coin_positions:
        print(f"   {pos['symbol']:15} {pos['position_side']:5} | 价格:${pos['entry_price']:10.4f} | 数量:{pos['quantity']:8.2f} | 杠杆:{pos['leverage']}x | 信号:{pos['entry_score']} | 持仓:{pos['hold_hours']}小时")
else:
    print("   当前无持仓")

# ============================================================================
# 6. 交易活动统计
# ============================================================================
print_section("6. 24小时交易活动")

cursor.execute('''
    SELECT
        COUNT(*) as total_opened,
        SUM(CASE WHEN position_side = 'LONG' THEN 1 ELSE 0 END) as long_count,
        SUM(CASE WHEN position_side = 'SHORT' THEN 1 ELSE 0 END) as short_count,
        AVG(entry_score) as avg_signal_score
    FROM futures_positions
    WHERE account_id = 2
    AND open_time >= NOW() - INTERVAL 24 HOUR
''')

activity = cursor.fetchone()
if activity and activity['total_opened'] > 0:
    print(f"   新开仓: {activity['total_opened']} 个")
    print(f"   做多: {activity['long_count']} | 做空: {activity['short_count']}")
    print(f"   平均信号强度: {activity['avg_signal_score']:.1f}")
else:
    print("   24小时内没有新开仓")

cursor.execute('''
    SELECT COUNT(*) as closed_count
    FROM futures_positions
    WHERE account_id = 2
    AND close_time >= NOW() - INTERVAL 24 HOUR
''')

closed = cursor.fetchone()
print(f"   已平仓: {closed['closed_count']} 个")

# ============================================================================
# 7. 系统健康检查
# ============================================================================
print_section("7. 系统健康检查")

# 检查K线数据更新
print_subsection("7.1 K线数据更新状态")
cursor.execute('''
    SELECT
        timeframe,
        MAX(open_time) as latest_time,
        TIMESTAMPDIFF(MINUTE, MAX(open_time), NOW()) as minutes_ago
    FROM kline_data
    WHERE symbol = 'BTC/USDT'
    AND exchange = 'binance_futures'
    AND timeframe IN ('5m', '15m', '1h')
    GROUP BY timeframe
    ORDER BY timeframe
''')

kline_status = cursor.fetchall()
for k in kline_status:
    minutes = k['minutes_ago'] if k['minutes_ago'] is not None else 999
    status = "✅" if minutes <= 30 else "❌"
    print(f"   {k['timeframe']:>3} K线: 最新 {k['latest_time']} ({minutes}分钟前) {status}")

# 检查交易控制状态
print_subsection("7.2 交易控制状态")
cursor.execute('''
    SELECT
        account_id,
        trading_type,
        trading_enabled,
        updated_by,
        updated_at
    FROM trading_control
    ORDER BY account_id
''')

trading_controls = cursor.fetchall()
if trading_controls:
    for tc in trading_controls:
        status = "✅ 启用" if tc['trading_enabled'] else "🛑 停止"
        account_name = "U本位合约" if tc['account_id'] == 2 else "币本位合约"
        print(f"   {account_name:12} ({tc['trading_type']:15}): {status} | 更新人: {tc['updated_by']} | 时间: {tc['updated_at']}")
else:
    print("   未找到交易控制记录")

cursor.close()
conn.close()

print("\n" + "=" * 80)
print(f" 报告生成完成 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 80)
