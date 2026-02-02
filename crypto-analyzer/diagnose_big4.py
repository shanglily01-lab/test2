#!/usr/bin/env python3
"""
Big4采集诊断脚本
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
print("Big4 Trend Detection Diagnostic")
print("=" * 80)

conn = pymysql.connect(**db_config)
cursor = conn.cursor(pymysql.cursors.DictCursor)

# 1. 检查最新记录
print("\n1. Latest Big4 Detection:")
print("-" * 80)
cursor.execute('''
    SELECT
        overall_signal,
        signal_strength,
        btc_signal, btc_strength,
        eth_signal, eth_strength,
        bnb_signal, bnb_strength,
        sol_signal, sol_strength,
        created_at,
        TIMESTAMPDIFF(MINUTE, created_at, NOW()) as minutes_ago
    FROM big4_trend_history
    ORDER BY created_at DESC
    LIMIT 1
''')

result = cursor.fetchone()
if result:
    print(f"   Time: {result['created_at']}")
    print(f"   Ago: {result['minutes_ago']} minutes")
    print(f"   Overall: {result['overall_signal']} (Strength: {result['signal_strength']})")
    print(f"   BTC: {result['btc_signal']} ({result['btc_strength']})")
    print(f"   ETH: {result['eth_signal']} ({result['eth_strength']})")
    print(f"   BNB: {result['bnb_signal']} ({result['bnb_strength']})")
    print(f"   SOL: {result['sol_signal']} ({result['sol_strength']})")

    if result['minutes_ago'] > 30:
        print(f"\n   *** ALERT: Collection STOPPED for {result['minutes_ago']} minutes! ***")
        print("   Expected: Detection every 15 minutes")
    elif result['minutes_ago'] > 20:
        print(f"\n   WARNING: Collection delayed {result['minutes_ago']} minutes")
    else:
        print("\n   OK: Collection running normally")

# 2. 检查最近1小时的采集频率
print("\n2. Collection Frequency (Last 1 Hour):")
print("-" * 80)
cursor.execute('''
    SELECT
        DATE_FORMAT(created_at, '%Y-%m-%d %H:%i') as time_slot,
        COUNT(*) as detections
    FROM big4_trend_history
    WHERE created_at >= NOW() - INTERVAL 1 HOUR
    GROUP BY time_slot
    ORDER BY time_slot DESC
''')

records = cursor.fetchall()
if records:
    for r in records:
        print(f"   {r['time_slot']}: {r['detections']} detection(s)")
    print(f"\n   Total in last hour: {sum(r['detections'] for r in records)} detections")
    print("   Expected: 4 detections (every 15 minutes)")
else:
    print("   *** NO DETECTIONS in the last hour! ***")

# 3. 检查最近的开仓记录（看服务是否在运行）
print("\n3. Recent Position Openings (Smart Trader Activity):")
print("-" * 80)
cursor.execute('''
    SELECT
        COUNT(*) as positions,
        MAX(open_time) as latest_open
    FROM futures_positions
    WHERE account_id = 2
    AND open_time >= NOW() - INTERVAL 1 HOUR
''')

pos_result = cursor.fetchone()
if pos_result['positions'] > 0:
    print(f"   Positions opened in last hour: {pos_result['positions']}")
    print(f"   Latest open time: {pos_result['latest_open']}")
    cursor.execute('''
        SELECT TIMESTAMPDIFF(MINUTE, MAX(open_time), NOW()) as minutes_since_last
        FROM futures_positions
        WHERE account_id = 2
    ''')
    last_pos = cursor.fetchone()
    print(f"   Minutes since last position: {last_pos['minutes_since_last']}")
else:
    print("   No positions opened in last hour")
    print("   Service might be stopped or no trading signals")

# 4. 诊断建议
print("\n4. Diagnostic Recommendations:")
print("-" * 80)

if result and result['minutes_ago'] > 30:
    print("   [ACTION REQUIRED]")
    print("   1. Check if smart_trader_service.py is running")
    print("      Command: ps aux | grep smart_trader_service")
    print("      Or: supervisorctl status smart-trader")
    print("      Or: pm2 list")
    print()
    print("   2. Check service logs for errors:")
    print("      tail -f logs/smart_trader.log")
    print()
    print("   3. If service is not running, restart it:")
    print("      supervisorctl restart smart-trader")
    print("      Or: pm2 restart smart-trader")
    print()
    print("   4. Monitor Big4 detection after restart:")
    print("      python diagnose_big4.py")

cursor.close()
conn.close()

print("\n" + "=" * 80)
print("Diagnostic Complete")
print("=" * 80)
