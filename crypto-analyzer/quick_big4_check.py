#!/usr/bin/env python3
"""
快速检查Big4状态和问题诊断
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import pymysql
from dotenv import load_dotenv
import os
from datetime import datetime

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST', '13.212.252.171'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'admin'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME', 'binance-data'),
    'charset': 'utf8mb4'
}

print("=" * 80)
print("Big4 快速诊断")
print("=" * 80)

conn = pymysql.connect(**db_config)
cursor = conn.cursor(pymysql.cursors.DictCursor)

# 1. 检查最新记录
print("\n1. 最新Big4检测:")
print("-" * 80)
cursor.execute('''
    SELECT
        created_at,
        overall_signal,
        signal_strength,
        TIMESTAMPDIFF(MINUTE, created_at, NOW()) as minutes_ago
    FROM big4_trend_history
    ORDER BY created_at DESC
    LIMIT 1
''')

result = cursor.fetchone()
if result:
    print(f"   最后检测: {result['created_at']}")
    print(f"   距今: {result['minutes_ago']} 分钟")
    print(f"   信号: {result['overall_signal']} (强度: {result['signal_strength']})")

    if result['minutes_ago'] > 30:
        print(f"\n   ❌ 严重: Big4检测已停止 {result['minutes_ago']} 分钟!")
        print(f"   预期: 每15分钟检测一次")
    elif result['minutes_ago'] > 20:
        print(f"\n   ⚠️  警告: Big4检测延迟 {result['minutes_ago']} 分钟")
    else:
        print(f"\n   ✅ 正常: Big4检测运行正常")

# 2. 检查重复记录(多实例问题)
print("\n2. 检查重复记录 (多实例冲突):")
print("-" * 80)
cursor.execute('''
    SELECT created_at, COUNT(*) as count
    FROM big4_trend_history
    WHERE created_at >= NOW() - INTERVAL 2 HOUR
    GROUP BY created_at
    HAVING count > 1
    ORDER BY created_at DESC
    LIMIT 10
''')

duplicates = cursor.fetchall()
if duplicates:
    print(f"   ❌ 发现 {len(duplicates)} 个时间点有重复记录!")
    print(f"   这表明可能有多个smart_trader_service实例在运行")
    print(f"\n   最近的重复:")
    for dup in duplicates[:5]:
        print(f"      {dup['created_at']}: {dup['count']}条记录")
    print(f"\n   解决方法:")
    print(f"      ps aux | grep smart_trader_service")
    print(f"      pkill -f 'python.*smart_trader_service.py'")
    print(f"      supervisorctl restart smart-trader")
else:
    print(f"   ✅ 没有发现重复记录")

# 3. 检查K线数据是否正常更新
print("\n3. 检查K线数据 (Big4依赖):")
print("-" * 80)
for symbol in ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']:
    cursor.execute('''
        SELECT
            timeframe,
            MAX(open_time) as latest_time,
            TIMESTAMPDIFF(MINUTE, MAX(open_time), NOW()) as minutes_ago
        FROM kline_data
        WHERE symbol = %s
        AND exchange = 'binance_futures'
        AND timeframe IN ('5m', '15m', '1h')
        GROUP BY timeframe
        ORDER BY timeframe
    ''', (symbol,))

    klines = cursor.fetchall()
    print(f"\n   {symbol}:")
    has_issue = False
    for k in klines:
        status = "✅" if k['minutes_ago'] <= 30 else "❌"
        print(f"      {k['timeframe']:>3}: 最新 {k['latest_time']} ({k['minutes_ago']}分钟前) {status}")
        if k['minutes_ago'] > 30:
            has_issue = True

    if has_issue:
        print(f"      ⚠️  K线数据更新异常,Big4检测可能失败")

# 4. 测试Big4检测器
print("\n4. 测试Big4检测器:")
print("-" * 80)
try:
    from app.services.big4_trend_detector import Big4TrendDetector

    print("   尝试执行检测...")
    detector = Big4TrendDetector()
    test_result = detector.detect_market_trend()

    print(f"   ✅ 检测成功!")
    print(f"   信号: {test_result['overall_signal']} (强度: {test_result['signal_strength']:.1f})")

    # 验证是否写入数据库
    cursor.execute('''
        SELECT created_at, TIMESTAMPDIFF(SECOND, created_at, NOW()) as seconds_ago
        FROM big4_trend_history
        ORDER BY created_at DESC
        LIMIT 1
    ''')
    newest = cursor.fetchone()
    if newest['seconds_ago'] < 10:
        print(f"   ✅ 数据库写入成功 ({newest['seconds_ago']}秒前)")
        print(f"\n   结论: Big4检测器本身工作正常")
        print(f"   问题: smart_trader_service可能:")
        print(f"         1. 进程卡住不再执行主循环")
        print(f"         2. 多个实例相互冲突")
        print(f"         3. 数据库连接池耗尽")
    else:
        print(f"   ⚠️  数据库写入失败 (最新记录是{newest['seconds_ago']}秒前)")

except Exception as e:
    print(f"   ❌ 检测失败: {e}")
    import traceback
    print("\n   详细错误:")
    traceback.print_exc()

cursor.close()
conn.close()

print("\n" + "=" * 80)
print("诊断完成")
print("=" * 80)
