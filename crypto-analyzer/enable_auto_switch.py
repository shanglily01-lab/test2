#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')
import pymysql

db = {'host': '13.212.252.171', 'port': 3306, 'user': 'admin', 'password': 'Tonny@1000', 'database': 'binance-data'}
conn = pymysql.connect(**db)
cursor = conn.cursor()

print("Enabling auto mode switching...")

# Enable auto switch
cursor.execute("""
    UPDATE trading_mode_config
    SET auto_switch_enabled = 1,
        updated_at = NOW()
    WHERE account_id = 2 AND trading_type = 'usdt_futures'
""")

conn.commit()

# Verify
cursor.execute("""
    SELECT mode_type, auto_switch_enabled, switch_cooldown_minutes, range_min_score
    FROM trading_mode_config
    WHERE account_id = 2 AND trading_type = 'usdt_futures'
""")

result = cursor.fetchone()
print(f"\nUpdated configuration:")
print(f"  Mode: {result[0]}")
print(f"  Auto switch enabled: {result[1]}")
print(f"  Cooldown: {result[2]} minutes")
print(f"  Range min score: {result[3]}")

print(f"\nAuto switching rules:")
print(f"  -> RANGE mode: Big4 = NEUTRAL AND strength < 50")
print(f"  -> TREND mode: Big4 = BULLISH/BEARISH AND strength >= 60")
print(f"  Cooldown between switches: {result[2]} minutes")

cursor.close()
conn.close()

print(f"\n✅ Auto mode switching is now ENABLED")
print(f"System will automatically switch between trend and range modes based on Big4 signals")
