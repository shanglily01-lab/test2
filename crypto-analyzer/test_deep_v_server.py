#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
服务器端深V反弹机制验证脚本

功能:
1. 测试2026-02-06 00:00的真实深V案例
2. 验证检测逻辑是否正确
3. 检查bounce_window和emergency_intervention表
4. 手动触发检测（可选）

使用方法:
python test_deep_v_server.py
"""

import sys
import os
from datetime import datetime, timedelta
import pymysql
from dotenv import load_dotenv

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv()

# 数据库配置
db_config = {
    'host': os.getenv('DB_HOST'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4'
}

BIG4 = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']


def print_section(title):
    """打印章节标题"""
    print('\n' + '='*100)
    print(f'{title}')
    print('='*100)


def test_historical_case():
    """测试2026-02-06 00:00的历史案例"""
    print_section('TEST 1: 历史深V案例检测 (2026-02-06 00:00 UTC)')

    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    # 目标时间戳
    target_ts = 1770336000000  # 2026-02-06 00:00:00 UTC

    print(f'\nTarget timestamp: {target_ts}')
    print(f'Target time (UTC): 2026-02-06 00:00:00')
    print(f'Target time (Beijing): 2026-02-06 08:00:00')

    results = {}

    for symbol in BIG4:
        print(f'\n{"-"*100}')
        print(f'Symbol: {symbol}')
        print('-'*100)

        # 获取目标K线
        cursor.execute("""
            SELECT open_time, open_price, high_price, low_price, close_price
            FROM kline_data
            WHERE symbol = %s
            AND timeframe = '1h'
            AND exchange = 'binance_futures'
            AND open_time = %s
        """, (symbol, target_ts))

        h1 = cursor.fetchone()

        if not h1:
            print(f'  [ERROR] No candle found!')
            continue

        o = float(h1['open_price'])
        h = float(h1['high_price'])
        l = float(h1['low_price'])
        c = float(h1['close_price'])

        # 计算影线（使用代码的方法）
        body_low = min(o, c)
        lower_shadow = body_low - l
        total_range = h - l

        # Method 1: 代码使用的方法
        shadow_pct_price = (lower_shadow / l * 100) if l > 0 else 0

        # Method 2: 视觉比例（参考）
        shadow_pct_range = (lower_shadow / total_range * 100) if total_range > 0 else 0

        drop = (o - l) / o * 100
        change = (c - o) / o * 100

        print(f'  Candle data:')
        print(f'    Open:  ${o:,.2f}')
        print(f'    High:  ${h:,.2f}')
        print(f'    Low:   ${l:,.2f}  (drop {drop:.2f}% from open)')
        print(f'    Close: ${c:,.2f}  (change {change:+.2f}%)')
        print(f'    Lower shadow: ${lower_shadow:,.2f}')
        print(f'    Total range:  ${total_range:,.2f}')
        print()
        print(f'  Shadow calculation:')
        print(f'    Code method (shadow/low): {shadow_pct_price:.2f}%  {"[PASS >= 3%]" if shadow_pct_price >= 3 else "[FAIL < 3%]"}')
        print(f'    Visual method (shadow/range): {shadow_pct_range:.2f}%  (reference only)')

        # 检查72H/24H趋势
        cursor.execute("""
            SELECT high_price, low_price, open_time, open_price, close_price
            FROM kline_data
            WHERE symbol = %s
            AND timeframe = '1h'
            AND exchange = 'binance_futures'
            AND open_time <= %s
            ORDER BY open_time DESC
            LIMIT 72
        """, (symbol, target_ts))

        hist = cursor.fetchall()

        if len(hist) >= 24:
            h72 = max([float(k['high_price']) for k in hist])
            h24 = max([float(k['high_price']) for k in hist[:24]])

            drop_72h = (l - h72) / h72 * 100
            drop_24h = (l - h24) / h24 * 100

            # 检查首次触底
            is_first_bottom = True
            prev_long_shadows = []

            for prev in hist[:24]:
                if prev['open_time'] == target_ts:
                    continue

                prev_o = float(prev.get('open_price', 0))
                prev_c = float(prev.get('close_price', 0))
                prev_l = float(prev['low_price'])

                if prev_o > 0 and prev_c > 0 and prev_l > 0:
                    prev_body_low = min(prev_o, prev_c)
                    prev_shadow = (prev_body_low - prev_l) / prev_l * 100

                    if prev_shadow >= 3.0:
                        is_first_bottom = False
                        prev_time = datetime.utcfromtimestamp(int(prev['open_time'])/1000)
                        prev_long_shadows.append(f"{prev_time.strftime('%m-%d %H:%M')} ({prev_shadow:.2f}%)")

            print()
            print(f'  Trend check:')
            print(f'    72H high: ${h72:,.2f} -> drop: {drop_72h:.2f}%  {"[PASS <= -8%]" if drop_72h <= -8 else "[FAIL > -8%]"}')
            print(f'    24H high: ${h24:,.2f} -> drop: {drop_24h:.2f}%  {"[PASS <= -4%]" if drop_24h <= -4 else "[FAIL > -4%]"}')
            print(f'    First bottom: {"[PASS]" if is_first_bottom else f"[FAIL] Previous: {prev_long_shadows[:2]}"}')

            # 综合判断
            is_true_deep_v = (
                shadow_pct_price >= 3.0 and
                drop_72h <= -8.0 and
                drop_24h <= -4.0 and
                is_first_bottom
            )

            print()
            print(f'  RESULT: {">>> TRUE DEEP V DETECTED <<<" if is_true_deep_v else "Not a true deep V"}')

            results[symbol] = {
                'shadow': shadow_pct_price,
                'shadow_visual': shadow_pct_range,
                'drop_72h': drop_72h,
                'drop_24h': drop_24h,
                'first_bottom': is_first_bottom,
                'result': is_true_deep_v
            }
        else:
            print(f'  [ERROR] Insufficient history ({len(hist)} candles)')
            results[symbol] = {'result': False, 'reason': 'Insufficient data'}

    cursor.close()
    conn.close()

    # 总结
    print_section('TEST 1 SUMMARY')

    deep_v_count = sum(1 for r in results.values() if r.get('result'))

    print(f'\nDeep V detected: {deep_v_count}/4 symbols')
    print()

    for symbol, r in results.items():
        if r.get('result'):
            print(f'  [V] {symbol:12} Shadow:{r["shadow"]:5.2f}% (visual:{r["shadow_visual"]:5.1f}%) '
                  f'72H:{r["drop_72h"]:6.2f}% 24H:{r["drop_24h"]:6.2f}% {"First" if r["first_bottom"] else "NotFirst"}')
        else:
            print(f'  [X] {symbol:12} {r.get("reason", "Not qualified")}')

    print()
    if deep_v_count >= 1:
        print('>>> EXPECTED: Should create bounce_window + emergency_intervention records <<<')
    else:
        print('>>> EXPECTED: Should NOT create any records <<<')

    return deep_v_count >= 1


def check_bounce_window_table():
    """检查bounce_window表中的记录"""
    print_section('TEST 2: bounce_window表检查')

    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    # 检查2026-02-06前后的记录
    cursor.execute("""
        SELECT id, symbol, trigger_time, window_start, window_end,
               lower_shadow_pct, trigger_price, bounce_entered,
               entry_price, position_id, created_at
        FROM bounce_window
        WHERE trigger_time >= '2026-02-05 00:00:00'
        AND trigger_time <= '2026-02-07 00:00:00'
        ORDER BY trigger_time DESC
    """)

    records = cursor.fetchall()

    if records:
        print(f'\nFound {len(records)} records:')
        print()

        for r in records:
            window_duration = (r['window_end'] - r['window_start']).total_seconds() / 60

            print(f'  Record ID: {r["id"]}')
            print(f'    Symbol: {r["symbol"]}')
            print(f'    Trigger time: {r["trigger_time"]}')
            print(f'    Window: {r["window_start"].strftime("%H:%M")} - {r["window_end"].strftime("%H:%M")} ({window_duration:.0f} min)')
            print(f'    Lower shadow: {r["lower_shadow_pct"]}%')
            print(f'    Trigger price: ${r["trigger_price"]}')
            print(f'    Bounce entered: {"Yes" if r["bounce_entered"] else "No"}')
            if r["bounce_entered"]:
                print(f'    Entry price: ${r["entry_price"]}')
                print(f'    Position ID: {r["position_id"]}')
            print(f'    Created at: {r["created_at"]}')
            print()
    else:
        print('\n[RESULT] No records found')
        print('\nPossible reasons:')
        print('  1. big4_trend_detector service is not running')
        print('  2. Service was not running during 2026-02-06 00:00')
        print('  3. Detection logic has a bug')
        print('  4. Filtering conditions (first_bottom, etc.) prevented trigger')

    cursor.close()
    conn.close()

    return len(records) > 0


def check_emergency_intervention_table():
    """检查emergency_intervention表"""
    print_section('TEST 3: emergency_intervention表检查')

    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, intervention_type, block_long, block_short,
               trigger_reason, expires_at, created_at
        FROM emergency_intervention
        WHERE created_at >= '2026-02-05 00:00:00'
        AND created_at <= '2026-02-07 00:00:00'
        ORDER BY created_at DESC
    """)

    records = cursor.fetchall()

    if records:
        print(f'\nFound {len(records)} records:')
        print()

        for r in records:
            duration = (r['expires_at'] - r['created_at']).total_seconds() / 3600

            print(f'  Record ID: {r["id"]}')
            print(f'    Type: {r["intervention_type"]}')
            print(f'    Block LONG: {"Yes" if r["block_long"] else "No"}')
            print(f'    Block SHORT: {"Yes" if r["block_short"] else "No"}')
            print(f'    Trigger reason: {r["trigger_reason"]}')
            print(f'    Created: {r["created_at"]}')
            print(f'    Expires: {r["expires_at"]} ({duration:.1f} hours)')
            print()
    else:
        print('\n[RESULT] No records found')

    cursor.close()
    conn.close()

    return len(records) > 0


def check_service_status():
    """检查big4_trend_detector服务是否在运行"""
    print_section('TEST 4: 服务运行状态检查')

    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    # 检查最近的big4_trend_history记录
    cursor.execute("""
        SELECT created_at, overall_signal, signal_strength
        FROM big4_trend_history
        ORDER BY created_at DESC
        LIMIT 5
    """)

    records = cursor.fetchall()

    if records:
        latest = records[0]
        time_diff = (datetime.now() - latest['created_at']).total_seconds()

        print(f'\nLatest big4_trend_history record:')
        print(f'  Time: {latest["created_at"]}')
        print(f'  Signal: {latest["overall_signal"]}')
        print(f'  Strength: {latest["signal_strength"]}')
        print(f'  Age: {time_diff/60:.1f} minutes ago')
        print()

        if time_diff < 300:  # 5 minutes
            print('[STATUS] big4_trend_detector is RUNNING (recent activity)')
            running = True
        else:
            print(f'[WARNING] big4_trend_detector may NOT be running (last activity {time_diff/60:.1f} min ago)')
            running = False
    else:
        print('\n[ERROR] No big4_trend_history records found!')
        print('big4_trend_detector has never run or table is empty')
        running = False

    cursor.close()
    conn.close()

    return running


def manual_trigger_detection():
    """手动触发一次检测（可选）"""
    print_section('TEST 5: 手动触发检测 (Optional)')

    print('\nDo you want to manually trigger big4_trend_detector.detect_market_trend()?')
    print('This will:')
    print('  1. Run the detection logic now')
    print('  2. Create bounce_window records if deep V is detected')
    print('  3. Create emergency_intervention records if needed')
    print()

    choice = input('Trigger detection? (y/n): ').strip().lower()

    if choice == 'y':
        try:
            print('\nImporting big4_trend_detector...')
            from app.services.big4_trend_detector import Big4TrendDetector

            print('Creating detector instance...')
            detector = Big4TrendDetector()

            print('Running detect_market_trend()...')
            result = detector.detect_market_trend()

            print('\nDetection completed!')
            print(f'  Overall signal: {result["overall_signal"]}')
            print(f'  Signal strength: {result["signal_strength"]:.1f}')
            print(f'  Bullish count: {result["bullish_count"]}/4')
            print(f'  Bearish count: {result["bearish_count"]}/4')
            print()

            # 检查紧急干预
            ei = result.get('emergency_intervention', {})

            if ei.get('bottom_detected') or ei.get('top_detected'):
                print('[EMERGENCY INTERVENTION TRIGGERED]')
                print(f'  Bottom detected: {ei.get("bottom_detected")}')
                print(f'  Top detected: {ei.get("top_detected")}')
                print(f'  Block LONG: {ei.get("block_long")}')
                print(f'  Block SHORT: {ei.get("block_short")}')
                print(f'  Details: {ei.get("details")}')
                print(f'  Expires at: {ei.get("expires_at")}')
            else:
                print('[No emergency intervention triggered]')

            # 检查反弹窗口
            if ei.get('bounce_opportunity'):
                print('\n[BOUNCE OPPORTUNITY DETECTED]')
                print(f'  Symbols: {ei.get("bounce_symbols")}')
                print(f'  Window end: {ei.get("bounce_window_end")}')

            return True

        except Exception as e:
            print(f'\n[ERROR] Failed to trigger detection: {e}')
            import traceback
            traceback.print_exc()
            return False
    else:
        print('\nSkipped manual trigger.')
        return False


def main():
    """主函数"""
    print('='*100)
    print('Deep V Detection & Bounce Mechanism - Server Verification')
    print('='*100)
    print(f'\nTest Date: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'Database: {db_config["host"]} / {db_config["database"]}')

    # Test 1: 历史案例检测
    expected_trigger = test_historical_case()

    # Test 2: bounce_window表检查
    has_bounce_records = check_bounce_window_table()

    # Test 3: emergency_intervention表检查
    has_emergency_records = check_emergency_intervention_table()

    # Test 4: 服务状态检查
    service_running = check_service_status()

    # Test 5: 手动触发（可选）
    manual_triggered = manual_trigger_detection()

    # 最终总结
    print_section('FINAL SUMMARY')

    print('\nTest Results:')
    print(f'  1. Historical case (2026-02-06): {"SHOULD TRIGGER" if expected_trigger else "Should NOT trigger"}')
    print(f'  2. bounce_window records: {"FOUND" if has_bounce_records else "NOT FOUND"}')
    print(f'  3. emergency_intervention records: {"FOUND" if has_emergency_records else "NOT FOUND"}')
    print(f'  4. Service running: {"YES" if service_running else "NO"}')
    print(f'  5. Manual trigger: {"TRIGGERED" if manual_triggered else "SKIPPED"}')

    print('\nConclusion:')

    if expected_trigger and not has_bounce_records:
        print('  [WARNING] Detection logic works, but NO records in database!')
        print('  Possible reasons:')
        print('    - big4_trend_detector service was not running on 2026-02-06')
        print('    - Service runs but doesn\'t write to database')
        print('    - First bottom check prevented trigger')
        if not service_running:
            print('  [ACTION NEEDED] Start big4_trend_detector service!')
    elif expected_trigger and has_bounce_records:
        print('  [SUCCESS] Detection logic works AND records exist!')
        print('  System is working correctly.')
    elif not expected_trigger:
        print('  [INFO] Historical case does not meet trigger criteria.')
        print('  This is expected if conditions are not met.')

    print('\n' + '='*100)
    print('Verification complete!')
    print('='*100)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n\nTest interrupted by user.')
    except Exception as e:
        print(f'\n\nFATAL ERROR: {e}')
        import traceback
        traceback.print_exc()
